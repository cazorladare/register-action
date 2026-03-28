import json
import os
import re
import sys
import time
import uuid
import math
import random
import string
import secrets
import hashlib
import base64
import threading
import argparse
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, quote
from dataclasses import dataclass
from typing import Any, Dict, Optional
import urllib.parse
import urllib.request
import urllib.error

from curl_cffi import requests

# ==========================================
# TempMail.lol API
# ==========================================

# GitHub Actions 配置建议：
#   Secrets:
#     - TEMPMAIL_LOL_BASE  (可选)

DEFAULT_TEMPMAIL_LOL_BASE = "https://api.tempmail.lol/v2"


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if not value or not value.strip():
        return default
    return value.strip()


TEMPMAIL_LOL_BASE = _env("TEMPMAIL_LOL_BASE", DEFAULT_TEMPMAIL_LOL_BASE)


def validate_mail_config() -> None:
    if not TEMPMAIL_LOL_BASE:
        raise RuntimeError(
            "缺少 TempMail.lol GitHub 配置: TEMPMAIL_LOL_BASE。"
            "请在仓库 Settings -> Secrets and variables -> Actions 的 Secrets 中设置。"
        )


def _create_mail_session(proxies: Any = None) -> requests.Session:
    session = requests.Session(proxies=proxies, impersonate="chrome")
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    return session


def create_tempmail_lol_email(session: requests.Session, proxies: Any = None) -> tuple[str, str]:
    validate_mail_config()

    try:
        resp = session.post(
            f"{TEMPMAIL_LOL_BASE.rstrip('/')}" + "/inbox/create",
            json={},
            timeout=15,
        )
    except Exception as e:
        raise RuntimeError(f"TempMail.lol 请求异常: {e}")

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"TempMail.lol 创建失败: {resp.status_code} - {resp.text[:200]}")

    data = resp.json() if resp.content else {}
    email = str(data.get("address") or data.get("email") or "").strip()
    mail_token = str(data.get("token") or "").strip()
    if not email or not mail_token:
        raise RuntimeError("TempMail.lol 返回数据不完整（address/email 或 token 为空）")

    print(f"[tempmail_lol] 创建邮箱成功: {email}")
    return email, mail_token


def _fetch_emails_tempmail_lol(
    session: requests.Session, mail_token: str, proxies: Any = None
) -> list[dict[str, Any]]:
    try:
        resp = session.get(
            f"{TEMPMAIL_LOL_BASE.rstrip('/')}" + "/inbox",
            params={"token": mail_token},
            timeout=15,
        )
        if resp.status_code != 200:
            return []
        data = resp.json() if resp.content else {}
        emails = data.get("emails") if isinstance(data, dict) else []
        return emails if isinstance(emails, list) else []
    except Exception:
        return []


def _poll_tempmail_lol_otp(
    session: requests.Session,
    mail_token: str,
    email_addr: str,
    proxies: Any = None,
    timeout: int = 120,
    seen_ids: Optional[set[str]] = None,
) -> str:
    regex = r"(?<![#&])\b(\d{6})\b"
    if seen_ids is None:
        seen_ids = set()

    print(f"[TempMailLol] 等待验证码邮件 (最多 {timeout}s)...")
    deadline = time.time() + timeout

    while time.time() < deadline:
        messages = _fetch_emails_tempmail_lol(session, mail_token, proxies) or []
        for msg in messages:
            msg_id = str(msg.get("id") or msg.get("_id") or id(msg))
            if msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)

            content = " ".join(
                [
                    str(msg.get("subject") or ""),
                    str(msg.get("body") or ""),
                    str(msg.get("html") or ""),
                ]
            )
            if "openai" not in content.lower() and "chatgpt" not in content.lower():
                continue

            for code in re.findall(regex, content):
                if code == "177010":
                    continue
                print(f"[TempMailLol] 验证码: {code}")
                return code

        elapsed = int(timeout - max(0, deadline - time.time()))
        print(f"[TempMailLol] OTP 等待中... ({elapsed}s/{timeout}s)")
        time.sleep(3)

    print(f"[TempMailLol] 超时 ({timeout}s)")
    return ""


# ==========================================
# OAuth 授权与辅助函数
# ==========================================

AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_REDIRECT_URI = f"http://localhost:1455/auth/callback"
DEFAULT_SCOPE = "openid email profile offline_access"


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _sha256_b64url_no_pad(s: str) -> str:
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())


def _random_state(nbytes: int = 16) -> str:
    return secrets.token_urlsafe(nbytes)


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _parse_callback_url(callback_url: str) -> Dict[str, str]:
    candidate = callback_url.strip()
    if not candidate:
        return {"code": "", "state": "", "error": "", "error_description": ""}

    if "://" not in candidate:
        if candidate.startswith("?"):
            candidate = f"http://localhost{candidate}"
        elif any(ch in candidate for ch in "/?#") or ":" in candidate:
            candidate = f"http://{candidate}"
        elif "=" in candidate:
            candidate = f"http://localhost/?{candidate}"

    parsed = urllib.parse.urlparse(candidate)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    fragment = urllib.parse.parse_qs(parsed.fragment, keep_blank_values=True)

    for key, values in fragment.items():
        if key not in query or not query[key] or not (query[key][0] or "").strip():
            query[key] = values

    def get1(k: str) -> str:
        v = query.get(k, [""])
        return (v[0] or "").strip()

    code = get1("code")
    state = get1("state")
    error = get1("error")
    error_description = get1("error_description")

    if code and not state and "#" in code:
        code, state = code.split("#", 1)

    if not error and error_description:
        error, error_description = error_description, ""

    return {
        "code": code,
        "state": state,
        "error": error,
        "error_description": error_description,
    }


def _jwt_claims_no_verify(id_token: str) -> Dict[str, Any]:
    if not id_token or id_token.count(".") < 2:
        return {}
    payload_b64 = id_token.split(".")[1]
    pad = "=" * ((4 - (len(payload_b64) % 4)) % 4)
    try:
        payload = base64.urlsafe_b64decode((payload_b64 + pad).encode("ascii"))
        return json.loads(payload.decode("utf-8"))
    except Exception:
        return {}


def _decode_jwt_segment(seg: str) -> Dict[str, Any]:
    raw = (seg or "").strip()
    if not raw:
        return {}
    pad = "=" * ((4 - (len(raw) % 4)) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        return json.loads(decoded.decode("utf-8"))
    except Exception:
        return {}


def _to_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _generate_password(length: int = 12) -> str:
    """生成指定长度的随机密码（包含大小写字母和数字）"""
    chars = string.ascii_letters + string.digits
    return ''.join(secrets.choice(chars) for _ in range(length))


def _random_profile() -> str:
    """生成随机姓名和成年出生日期"""
    first = ''.join(random.choices(string.ascii_lowercase, k=random.randint(4, 8))).capitalize()
    last  = ''.join(random.choices(string.ascii_lowercase, k=random.randint(4, 8))).capitalize()
    today = time.gmtime()
    age   = random.randint(18, 55)
    year  = today.tm_year - age
    month = random.randint(1, 12)
    max_day = [31,28,31,30,31,30,31,31,30,31,30,31][month - 1]
    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) and month == 2:
        max_day = 29
    day = random.randint(1, max_day)
    return json.dumps({"name": f"{first} {last}", "birthdate": f"{year}-{month:02d}-{day:02d}"}, separators=(",", ":"))


def _get_sentinel(did: str, flow: str, proxies: Any) -> str:
    """获取 sentinel token（简化版，直接使用 challenge token 作为 c 值）。"""
    body = json.dumps({"p": "", "id": did, "flow": flow})
    resp = requests.post(
        "https://sentinel.openai.com/backend-api/sentinel/req",
        headers={
            "origin": "https://sentinel.openai.com",
            "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
            "content-type": "text/plain;charset=UTF-8",
        },
        data=body,
        proxies=proxies,
        impersonate="chrome",
        timeout=15,
    )
    c_token = resp.json()["token"]
    return json.dumps({"p": "", "t": "", "c": c_token, "id": did, "flow": flow})


def _post_form(url: str, data: Dict[str, str], timeout: int = 30) -> Dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if resp.status != 200:
                raise RuntimeError(
                    f"token exchange failed: {resp.status}: {raw.decode('utf-8', 'replace')}"
                )
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise RuntimeError(
            f"token exchange failed: {exc.code}: {raw.decode('utf-8', 'replace')}"
        ) from exc


@dataclass(frozen=True)
class OAuthStart:
    auth_url: str
    state: str
    code_verifier: str
    redirect_uri: str


def generate_oauth_url(
    *, redirect_uri: str = DEFAULT_REDIRECT_URI, scope: str = DEFAULT_SCOPE
) -> OAuthStart:
    state = _random_state()
    code_verifier = _pkce_verifier()
    code_challenge = _sha256_b64url_no_pad(code_verifier)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return OAuthStart(
        auth_url=auth_url,
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
    )


def submit_callback_url(
    *,
    callback_url: str,
    expected_state: str,
    code_verifier: str,
    redirect_uri: str = DEFAULT_REDIRECT_URI,
) -> str:
    cb = _parse_callback_url(callback_url)
    if cb["error"]:
        desc = cb["error_description"]
        raise RuntimeError(f"oauth error: {cb['error']}: {desc}".strip())

    if not cb["code"]:
        raise ValueError("callback url missing ?code=")
    if not cb["state"]:
        raise ValueError("callback url missing ?state=")
    if cb["state"] != expected_state:
        raise ValueError("state mismatch")

    token_resp = _post_form(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": cb["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
    )

    access_token = (token_resp.get("access_token") or "").strip()
    refresh_token = (token_resp.get("refresh_token") or "").strip()
    id_token = (token_resp.get("id_token") or "").strip()
    expires_in = _to_int(token_resp.get("expires_in"))

    claims = _jwt_claims_no_verify(id_token)
    email = str(claims.get("email") or "").strip()
    auth_claims = claims.get("https://api.openai.com/auth") or {}
    account_id = str(auth_claims.get("chatgpt_account_id") or "").strip()

    now = int(time.time())
    expired_rfc3339 = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + max(expires_in, 0))
    )
    now_rfc3339 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    config = {
        "id_token": id_token,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "account_id": account_id,
        "last_refresh": now_rfc3339,
        "email": email,
        "type": "codex",
        "expired": expired_rfc3339,
    }

    return json.dumps(config, ensure_ascii=False, separators=(",", ":"))


# ==========================================
# 核心注册逻辑
# ==========================================


def run(proxy: Optional[str]) -> Optional[str]:
    proxies: Any = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    s = requests.Session(proxies=proxies, impersonate="chrome")
    mail_session = _create_mail_session(proxies)
    email = ""
    mail_token = ""
    registration_seen_ids: set[str] = set()

    try:
        trace = s.get("https://cloudflare.com/cdn-cgi/trace", timeout=10)
        trace = trace.text
        loc_re = re.search(r"^loc=(.+)$", trace, re.MULTILINE)
        loc = loc_re.group(1) if loc_re else None
        print(f"[*] 当前 IP 所在地: {loc}")
        if loc == "CN" or loc == "HK":
            raise RuntimeError("检查代理哦w - 所在地不支持")
    except Exception as e:
        print(f"[Error] 网络连接检查失败: {e}")
        return None

    try:
        email, mail_token = create_tempmail_lol_email(mail_session, proxies)
    except Exception as e:
        print(f"[Error] 获取 TempMail.lol 临时邮箱失败: {e}")
        return None

    print(f"[*] 成功获取 TempMail.lol 临时邮箱: {email}")

    oauth = generate_oauth_url()
    url = oauth.auth_url

    try:
        s.get(url, timeout=15)
        did = s.cookies.get("oai-did")
        print(f"[*] Device ID: {did}")

        signup_body = f'{{"username":{{"value":"{email}","kind":"email"}},"screen_hint":"signup"}}'
        sen_req_body = f'{{"p":"","id":"{did}","flow":"authorize_continue"}}'

        sen_resp = requests.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req_body,
            proxies=proxies,
            impersonate="chrome",
            timeout=15,
        )

        if sen_resp.status_code != 200:
            print(f"[Error] Sentinel 异常拦截，状态码: {sen_resp.status_code}")
            return None

        sen_token = sen_resp.json()["token"]
        sentinel = f'{{"p": "", "t": "", "c": "{sen_token}", "id": "{did}", "flow": "authorize_continue"}}'

        signup_resp = s.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "referer": "https://auth.openai.com/create-account",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel,
            },
            data=signup_body,
        )
        print(f"[*] 提交注册表单状态: {signup_resp.status_code}")

        password = _generate_password()
        print(f"[*] 生成密码: {password}")

        sentinel_register = _get_sentinel(did, "signup_password", proxies)
        register_body = json.dumps({"password": password, "username": email})
        pwd_resp = s.post(
            "https://auth.openai.com/api/accounts/user/register",
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
                "content-type": "application/json",
                "oai-device-id": did,
                "openai-sentinel-token": sentinel_register,
            },
            data=register_body,
        )
        print(f"[*] 提交密码状态: {pwd_resp.status_code}")

        otp_resp = s.get(
            "https://auth.openai.com/api/accounts/email-otp/send",
            headers={
                "referer": "https://auth.openai.com/create-account/password",
                "accept": "application/json",
            },
        )
        print(f"[*] 验证码发送状态: {otp_resp.status_code}")

        code = _poll_tempmail_lol_otp(
            mail_session,
            mail_token,
            email,
            proxies,
            timeout=120,
            seen_ids=registration_seen_ids,
        )
        if not code:
            return None

        oauth_seen_ids = set(registration_seen_ids)

        sentinel_otp = _get_sentinel(did, "email_otp_verification", proxies)
        code_resp = s.post(
            "https://auth.openai.com/api/accounts/email-otp/validate",
            headers={
                "referer": "https://auth.openai.com/email-verification",
                "accept": "application/json",
                "content-type": "application/json",
                "oai-device-id": did,
                "openai-sentinel-token": sentinel_otp,
            },
            data=f'{{"code":"{code}"}}',
        )
        print(f"[*] 验证码校验状态: {code_resp.status_code} | 返回信息: {code_resp.text}")

        sentinel_create = _get_sentinel(did, "create_account", proxies)
        create_account_resp = s.post(
            "https://auth.openai.com/api/accounts/create_account",
            headers={
                "referer": "https://auth.openai.com/about-you",
                "accept": "application/json",
                "content-type": "application/json",
                "oai-device-id": did,
                "openai-sentinel-token": sentinel_create,
            },
            data=_random_profile(),
        )
        create_account_status = create_account_resp.status_code
        print(f"[*] 账户创建状态: {create_account_status}")

        if create_account_status != 200:
            print(create_account_resp.text)
            return None

        print("[*] 账号创建成功，切换全新 session 做 OAuth 登录")

    except Exception as e:
        print(f"[Error] 运行时发生错误: {e}")
        return None

    try:
        s2 = requests.Session(proxies=proxies, impersonate="chrome")
        oauth2 = generate_oauth_url()

        s2.get(oauth2.auth_url, timeout=15)
        did2 = s2.cookies.get("oai-did")
        if not did2:
            print("[Error] OAuth 新 session 未获取到 oai-did")
            return None

        sen_req2 = f'{{"p":"","id":"{did2}","flow":"authorize_continue"}}'
        sen_resp2 = requests.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req2,
            proxies=proxies,
            impersonate="chrome",
            timeout=15,
        )
        if sen_resp2.status_code != 200:
            print(f"[Error] OAuth Sentinel 失败: {sen_resp2.status_code}")
            return None
        _tok2 = sen_resp2.json()["token"]
        sentinel2 = f'{{"p": "", "t": "", "c": "{_tok2}", "id": "{did2}", "flow": "authorize_continue"}}'

        login_body = f'{{"username":{{"value":"{email}","kind":"email"}}}}'
        cont_resp = s2.post(
            "https://auth.openai.com/api/accounts/authorize/continue",
            headers={
                "referer": "https://auth.openai.com/log-in",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel2,
            },
            data=login_body,
        )
        print(f"[OAuth] 账号识别 -> {cont_resp.status_code}")
        if cont_resp.status_code != 200:
            print(f"[Error] OAuth authorize/continue 失败: {cont_resp.text[:200]}")
            return None

        sen_req3 = f'{{"p":"","id":"{did2}","flow":"password_verify"}}'
        sen_resp3 = requests.post(
            "https://sentinel.openai.com/backend-api/sentinel/req",
            headers={
                "origin": "https://sentinel.openai.com",
                "referer": "https://sentinel.openai.com/backend-api/sentinel/frame.html?sv=20260219f9f6",
                "content-type": "text/plain;charset=UTF-8",
            },
            data=sen_req3,
            proxies=proxies,
            impersonate="chrome",
            timeout=15,
        )
        if sen_resp3.status_code != 200:
            print(f"[Error] OAuth password Sentinel 失败: {sen_resp3.status_code}")
            return None
        _tok3 = sen_resp3.json()["token"]
        sentinel3 = f'{{"p": "", "t": "", "c": "{_tok3}", "id": "{did2}", "flow": "password_verify"}}'

        verify_resp = s2.post(
            "https://auth.openai.com/api/accounts/password/verify",
            headers={
                "referer": "https://auth.openai.com/log-in/password",
                "accept": "application/json",
                "content-type": "application/json",
                "openai-sentinel-token": sentinel3,
            },
            json={"password": password},
        )
        print(f"[OAuth] 密码验证 -> {verify_resp.status_code}")
        if verify_resp.status_code != 200:
            print(f"[Error] OAuth 密码验证失败: {verify_resp.text[:200]}")
            return None

        verify_data = verify_resp.json()
        continue_url = verify_data.get("continue_url", "")
        page_type = (verify_data.get("page") or {}).get("type", "")
        print(f"[OAuth] 验证页类型={page_type or '-'}")
        if not continue_url:
            print("[Error] OAuth 未获取到 continue_url")
            return None
        if continue_url.startswith("/"):
            continue_url = f"https://auth.openai.com{continue_url}"

        if page_type == "email_otp_verification" or "email-verification" in continue_url or "email-otp" in continue_url:
            print("[OAuth] 检测到登录 OTP，等待验证码...")
            login_otp = _poll_tempmail_lol_otp(
                mail_session,
                mail_token,
                email,
                proxies,
                timeout=120,
                seen_ids=oauth_seen_ids,
            )
            if not login_otp:
                print("[Error] OAuth OTP 等待超时")
                return None
            otp_resp2 = s2.post(
                "https://auth.openai.com/api/accounts/email-otp/validate",
                headers={
                    "referer": "https://auth.openai.com/email-verification",
                    "accept": "application/json",
                    "content-type": "application/json",
                },
                json={"code": login_otp},
            )
            print(f"[OAuth] 登录OTP验证 -> {otp_resp2.status_code}")
            if otp_resp2.status_code != 200:
                print(f"[Error] OAuth OTP 验证失败: {otp_resp2.text[:200]}")
                return None
            otp_data = otp_resp2.json()
            continue_url = otp_data.get("continue_url", continue_url)
            if continue_url.startswith("/"):
                continue_url = f"https://auth.openai.com{continue_url}"

        auth_cookie2 = s2.cookies.get("oai-client-auth-session")
        workspace_id = None
        if auth_cookie2:
            try:
                auth_json2 = _decode_jwt_segment(auth_cookie2.split(".")[0])
                workspaces2 = auth_json2.get("workspaces") or []
                if workspaces2:
                    workspace_id = str((workspaces2[0] or {}).get("id") or "").strip() or None
            except Exception:
                pass

        if not workspace_id:
            print("[*] 未获取到 workspace_id，尝试直接跟随重定向")
            final = s2.get(continue_url, allow_redirects=True, timeout=15)
            cbk = str(final.url)
            if "code=" not in cbk:
                for hr in getattr(final, "history", []):
                    loc2 = hr.headers.get("Location", "")
                    if "code=" in loc2:
                        cbk = loc2
                        break
            if "code=" not in cbk:
                print(f"[Error] 未获取到授权码: {cbk[:200]}")
                return None
        else:
            select_body2 = f'{{"workspace_id":"{workspace_id}"}}'
            select_resp2 = s2.post(
                "https://auth.openai.com/api/accounts/workspace/select",
                headers={
                    "referer": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                    "content-type": "application/json",
                },
                data=select_body2,
            )
            print(f"[OAuth] 工作区选择 -> {select_resp2.status_code}")
            if select_resp2.status_code != 200:
                print(f"[Error] workspace/select 失败: {select_resp2.text[:200]}")
                return None
            next_url = select_resp2.json().get("continue_url", "")
            if next_url.startswith("/"):
                next_url = f"https://auth.openai.com{next_url}"
            r3 = s2.get(next_url, allow_redirects=False, timeout=15)
            r4 = s2.get(r3.headers.get("Location", next_url), allow_redirects=False, timeout=15)
            r5 = s2.get(r4.headers.get("Location", ""), allow_redirects=False, timeout=15)
            cbk = r5.headers.get("Location", "")
            if not cbk:
                print("[Error] 未获取到最终 Callback URL")
                return None

        return submit_callback_url(
            callback_url=cbk,
            code_verifier=oauth2.code_verifier,
            redirect_uri=oauth2.redirect_uri,
            expected_state=oauth2.state,
        )

    except Exception as e:
        print(f"[Error] OAuth 登录时发生错误: {e}")
        return None


def save_token_json(token_json: str) -> str:
    try:
        t_data = json.loads(token_json)
        fname_email = t_data.get("email", "unknown").replace("@", "_")
    except Exception:
        fname_email = "unknown"

    save_dir = "codex"
    os.makedirs(save_dir, exist_ok=True)
    file_name = f"token_{fname_email}_{int(time.time())}.json"
    file_path = os.path.join(save_dir, file_name)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(token_json)

    return file_path


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAI 自动注册脚本 (TempMail.lol 版本)")
    parser.add_argument(
        "--proxy", default=None, help="代理地址，如 http://127.0.0.1:7890"
    )
    parser.add_argument("--once", action="store_true", help="只运行一次")
    parser.add_argument(
        "--target-successes",
        type=int,
        default=0,
        help="达到指定成功注册数量后停止；0 表示无限循环（与 --once 互斥）",
    )
    parser.add_argument("--sleep-min", type=int, default=5, help="循环模式最短等待秒数")
    parser.add_argument(
        "--sleep-max", type=int, default=30, help="循环模式最长等待秒数"
    )
    args = parser.parse_args()

    sleep_min = max(1, args.sleep_min)
    sleep_max = max(sleep_min, args.sleep_max)

    try:
        validate_mail_config()
    except Exception as e:
        print(f"[ConfigError] {e}")
        return 1

    target_successes = 1 if args.once else max(0, args.target_successes)
    attempt_count = 0
    success_count = 0
    print("[Info] Yasal's Seamless OpenAI Auto-Registrar Started for ZJH (TempMail.lol Edition)")

    while True:
        attempt_count += 1
        print(
            f"\n[{datetime.now().strftime('%H:%M:%S')}] >>> 开始第 {attempt_count} 次注册流程（成功 {success_count}/{target_successes or '∞'}）<<<"
        )

        try:
            token_json = run(args.proxy)

            if token_json:
                file_path = save_token_json(token_json)
                success_count += 1
                print(f"[*] 成功! Token 已保存至: {file_path}")
                if target_successes and success_count >= target_successes:
                    print(f"[*] 已达到目标成功数量 {success_count}/{target_successes}，停止运行。")
                    return 0
            else:
                print("[-] 本次注册失败。")

        except Exception as e:
            print(f"[Error] 发生未捕获异常: {e}")

        if args.once:
            return 0 if success_count >= 1 else 1

        wait_time = random.randint(sleep_min, sleep_max)
        print(f"[*] 休息 {wait_time} 秒...")
        time.sleep(wait_time)


if __name__ == "__main__":
    raise SystemExit(main())
