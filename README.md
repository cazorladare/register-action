# github-register-action

整理后的目录结构：

```text
.
├─ .github/
│  └─ workflows/
│     └─ register.yml
├─ scripts/
│  ├─ task_runner_cfmail.py
│  ├─ task_runner_tempmail.py
│  ├─ task_runner_yydsmail.py
│  └─ daily_artifact_bundle.py
├─ .gitignore
├─ requirements.txt
└─ README.md
```

## 作用

- `scripts/task_runner_cfmail.py`：CFMail 注册脚本
- `scripts/task_runner_tempmail.py`：TempMail.lol 注册脚本
- `scripts/task_runner_yydsmail.py`：YYDS Mail 注册脚本
- `scripts/daily_artifact_bundle.py`：按 UTC+8 日期汇总 artifacts 打包
- `.github/workflows/register.yml`：GitHub Actions 定时/手动任务

## 输出目录

- `codex/`：注册成功后的 json
- `daily_bundle/`：每日汇总 zip

## 本地使用

先安装依赖：

```bash
pip install -r requirements.txt
```

### CFMail

单次成功 1 个后停止：

```bash
python scripts/task_runner_cfmail.py --once
```

成功 20 个后停止：

```bash
python scripts/task_runner_cfmail.py --target-successes 20
```

### YYDS Mail

单次成功 1 个后停止：

```bash
python scripts/task_runner_yydsmail.py --once
```

成功 20 个后停止：

```bash
python scripts/task_runner_yydsmail.py --target-successes 20
```

### TempMail.lol

单次成功 1 个后停止：

```bash
python scripts/task_runner_tempmail.py --once
```

成功 20 个后停止：

```bash
python scripts/task_runner_tempmail.py --target-successes 20
```

### 手动打包某天数据

需要可读取 Actions artifacts 的 GitHub Token：

```bash
set GITHUB_TOKEN=你的token
python scripts/daily_artifact_bundle.py --repo owner/repo --local-date 2026-03-27
```

## GitHub Actions 使用

- `47 */2 * * *`：每 2 小时注册一次
- `0 4 * * *`：每天 UTC 04:00（UTC+8 12:00）发送前一天整天汇总包

手动运行输入：

- `mode=register`
- `provider=cfmail|yydsmail|tempmail`
- `count=目标成功数量`
- `mode=daily_summary`
- `summary_date=YYYY-MM-DD`

## 需要配置的 GitHub Secrets

- `CFMAIL_USER_EMAIL`
- `CFMAIL_USER_PASSWORD`
- `CFMAIL_CUSTOM_AUTH`（可选）
- `CFMAIL_BASE`（可选）
- `CFMAIL_DOMAINS`（可选）
- `YYDSMAIL_API_KEY`（使用 yydsmail 时必填）
- `YYDSMAIL_API_BASE`（可选，默认 `https://maliapi.215.im`）
- `TEMPMAIL_LOL_BASE`（可选，默认 `https://api.tempmail.lol/v2`）
- `REGISTER_PROVIDER`（可选，定时任务默认 provider）
- `TG_BOT_TOKEN`
- `TG_CHAT_ID`

## 停止逻辑

注册任务现在按“成功数量”停止：

- 失败不计数
- 只有成功保存 token 才计入目标数量
