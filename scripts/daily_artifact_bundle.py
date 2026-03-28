#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOCAL_TZ = timezone(timedelta(hours=8))
API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class Artifact:
    artifact_id: int
    name: str
    created_at: datetime
    archive_download_url: str
    expired: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download previous-day token artifacts and build a single zip bundle."
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="GitHub repository in owner/name format.",
    )
    parser.add_argument(
        "--token",
        default="",
        help="GitHub token with actions:read. Falls back to GITHUB_TOKEN env.",
    )
    parser.add_argument(
        "--local-date",
        default="",
        help="Target local date in UTC+8, format YYYY-MM-DD. Defaults to previous local day.",
    )
    parser.add_argument(
        "--artifact-prefix",
        default="tokens-",
        help="Only include artifacts whose name starts with this prefix.",
    )
    parser.add_argument(
        "--output-dir",
        default="daily_bundle",
        help="Where to place the final generated zip.",
    )
    parser.add_argument(
        "--github-output",
        default="",
        help="Optional path to append GitHub Actions step outputs.",
    )
    return parser.parse_args()


def parse_github_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def sanitize(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return cleaned or "artifact"


def github_request(url: str, token: str) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "github-register-action/daily-bundle",
        },
    )
    with urlopen(request, timeout=60) as response:
        return response.read()


def iter_artifacts(owner: str, repo: str, token: str) -> Iterable[Artifact]:
    page = 1
    while True:
        url = f"https://api.github.com/repos/{owner}/{repo}/actions/artifacts?per_page=100&page={page}"
        payload = json.loads(github_request(url, token).decode("utf-8"))
        artifacts = payload.get("artifacts", [])
        if not artifacts:
            return

        for item in artifacts:
            yield Artifact(
                artifact_id=int(item["id"]),
                name=str(item["name"]),
                created_at=parse_github_datetime(item["created_at"]),
                archive_download_url=str(item["archive_download_url"]),
                expired=bool(item.get("expired", False)),
            )

        if len(artifacts) < 100:
            return
        page += 1


def resolve_target_date(raw: str) -> date:
    if raw:
        return date.fromisoformat(raw)
    return (datetime.now(timezone.utc).astimezone(LOCAL_TZ).date() - timedelta(days=1))


def write_outputs(path: str, outputs: dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in outputs.items():
            fh.write(f"{key}={value}\n")


def build_bundle(
    *,
    artifacts: list[Artifact],
    token: str,
    output_dir: Path,
    target_date: date,
) -> tuple[Path | None, int]:
    with tempfile.TemporaryDirectory(prefix="daily-bundle-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        extract_root = temp_dir / "extract"
        extract_root.mkdir(parents=True, exist_ok=True)

        total_json = 0
        for artifact in artifacts:
            archive_bytes = github_request(artifact.archive_download_url, token)
            artifact_zip = temp_dir / f"{artifact.artifact_id}.zip"
            artifact_zip.write_bytes(archive_bytes)

            artifact_dir = extract_root / (
                f"{artifact.created_at:%Y%m%dT%H%M%SZ}_{sanitize(artifact.name)}_{artifact.artifact_id}"
            )
            artifact_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(artifact_zip) as archive:
                archive.extractall(artifact_dir)

            total_json += sum(1 for _ in artifact_dir.rglob("*.json"))

        if total_json == 0:
            return None, 0

        output_dir.mkdir(parents=True, exist_ok=True)
        zip_path = output_dir / f"tokens_daily_utc8_{target_date.isoformat()}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for json_path in sorted(extract_root.rglob("*.json")):
                bundle.write(json_path, json_path.relative_to(extract_root))

        return zip_path, total_json


def main() -> int:
    args = parse_args()
    token = args.token.strip() or os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("Missing GitHub token. Provide --token or set GITHUB_TOKEN.", file=sys.stderr)
        return 1

    if "/" not in args.repo:
        print("--repo must be in owner/name format.", file=sys.stderr)
        return 1
    owner, repo = args.repo.split("/", 1)

    try:
        target_date = resolve_target_date(args.local_date.strip())
    except ValueError as exc:
        print(f"Invalid --local-date value: {exc}", file=sys.stderr)
        return 1
    window_start_local = datetime.combine(target_date, time.min, tzinfo=LOCAL_TZ)
    window_end_local = window_start_local + timedelta(days=1)
    window_start_utc = window_start_local.astimezone(timezone.utc)
    window_end_utc = window_end_local.astimezone(timezone.utc)

    try:
        matched_artifacts = sorted(
            [
                artifact
                for artifact in iter_artifacts(owner, repo, token)
                if not artifact.expired
                and artifact.name.startswith(args.artifact_prefix)
                and window_start_utc <= artifact.created_at < window_end_utc
            ],
            key=lambda item: item.created_at,
        )

        zip_path, json_count = build_bundle(
            artifacts=matched_artifacts,
            token=token,
            output_dir=Path(args.output_dir),
            target_date=target_date,
        )
    except (HTTPError, URLError, TimeoutError, OSError, zipfile.BadZipFile) as exc:
        print(f"Failed to build bundle: {exc}", file=sys.stderr)
        return 1

    outputs = {
        "target_date": target_date.isoformat(),
        "artifact_count": str(len(matched_artifacts)),
        "json_count": str(json_count),
        "window_start_utc": window_start_utc.isoformat().replace("+00:00", "Z"),
        "window_end_utc": window_end_utc.isoformat().replace("+00:00", "Z"),
        "zip_path": str(zip_path.resolve()) if zip_path else "",
    }
    write_outputs(args.github_output, outputs)

    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
