# github-register-action

用于通过 GitHub Actions 定时或手动执行注册任务，并按 UTC+8 日期汇总当日产物后打包发送到 Telegram。

当前支持 3 种邮箱提供商：

- `cfmail`
- `yydsmail`
- `tempmail`

---

## 功能概览

- 每 2 小时执行一次注册任务
- 支持 GitHub Actions 手动触发
- 支持按邮箱提供商切换不同 runner
- 注册任务按“成功数量”停止，不按尝试次数停止
- 每天 UTC+8 12:00 自动打包前一天整天产物
- 可将每日汇总 zip 发送到 Telegram

---

## 目录结构

```text
.
├─ .github/
│  └─ workflows/
│     └─ register.yml
├─ scripts/
│  ├─ daily_artifact_bundle.py
│  ├─ task_runner_cfmail.py
│  ├─ task_runner_tempmail.py
│  └─ task_runner_yydsmail.py
├─ .gitignore
├─ README.md
└─ requirements.txt
```

---

## 文件说明

- `.github/workflows/register.yml`
  - GitHub Actions 工作流
  - 包含定时注册、手动注册、每日汇总与 Telegram 推送

- `scripts/task_runner_cfmail.py`
  - 使用 CFMail 的注册脚本

- `scripts/task_runner_yydsmail.py`
  - 使用 YYDS Mail 的注册脚本

- `scripts/task_runner_tempmail.py`
  - 使用 TempMail.lol 的注册脚本

- `scripts/daily_artifact_bundle.py`
  - 按 UTC+8 日期汇总 Actions artifacts 中的 JSON，并生成 zip

---

## GitHub Actions 行为

### 1. 定时注册

工作流中的注册计划：

```cron
47 */2 * * *
```

含义：**每 2 小时运行一次**。

说明：
- 会先随机延迟 0~15 分钟再启动
- 定时任务默认邮箱来源：
  1. 手动输入的 `provider`（如果是手动触发）
  2. `REGISTER_PROVIDER` Secret
  3. 默认回退为 `cfmail`
- 定时注册任务没有手动输入 `count`，因此当前 workflow 会按 **30 个成功账号** 作为每轮目标

### 2. 每日汇总

工作流中的打包计划：

```cron
0 4 * * *
```

含义：
- UTC 时间 04:00 执行
- 对应 **UTC+8 的 12:00**
- 打包 **前一天 UTC+8 整天** 的产物

如果已配置 Telegram 相关 Secrets，则会自动发送 zip；未配置则跳过发送。

---

## 需要配置的 GitHub Secrets

路径：

**Settings → Secrets and variables → Actions → Secrets**

### 通用 Secrets

| Secret | 是否必填 | 说明 |
|---|---|---|
| `REGISTER_PROVIDER` | 否 | 定时任务默认邮箱提供商，可填 `cfmail` / `yydsmail` / `tempmail` |
| `TG_BOT_TOKEN` | 否 | Telegram Bot Token；仅每日汇总推送需要 |
| `TG_CHAT_ID` | 否 | Telegram Chat ID；仅每日汇总推送需要 |

### CFMail

| Secret | 是否必填 | 说明 |
|---|---|---|
| `CFMAIL_USER_EMAIL` | 是（使用 cfmail 时） | CFMail 后台账号 |
| `CFMAIL_USER_PASSWORD` | 是（使用 cfmail 时） | CFMail 后台密码 |
| `CFMAIL_CUSTOM_AUTH` | 否 | CFMail 自定义访问认证 |
| `CFMAIL_BASE` | 否 | CFMail API 基地址 |
| `CFMAIL_DOMAINS` | 否 | 邮箱域名列表；支持 JSON 数组 / 逗号分隔 / 换行分隔 |

> 若 `CFMAIL_BASE` / `CFMAIL_DOMAINS` 不填，则脚本会使用内置默认值。

### YYDS Mail

| Secret | 是否必填 | 说明 |
|---|---|---|
| `YYDSMAIL_API_KEY` | 是（使用 yydsmail 时） | YYDS Mail API Key |
| `YYDSMAIL_API_BASE` | 否 | 默认 `https://maliapi.215.im` |

### TempMail.lol

| Secret | 是否必填 | 说明 |
|---|---|---|
| `TEMPMAIL_LOL_BASE` | 否 | 默认 `https://api.tempmail.lol/v2` |

---

## 如何切换邮箱提供商

### 方式 1：GitHub Actions 手动运行时切换

进入：

**Actions → Register and Daily Summary → Run workflow**

在表单里选择：

- `mode=register`
- `provider=cfmail|yydsmail|tempmail`
- `count=目标成功数量`

### 方式 2：设置定时任务默认邮箱

新增或修改 Secret：

```text
REGISTER_PROVIDER=cfmail
```

也可以改成：

```text
REGISTER_PROVIDER=yydsmail
REGISTER_PROVIDER=tempmail
```

### 方式 3：本地直接运行对应脚本

- `scripts/task_runner_cfmail.py`
- `scripts/task_runner_yydsmail.py`
- `scripts/task_runner_tempmail.py`

---

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

> 本地运行时，脚本同样通过环境变量读取对应配置。

---

## 手动执行工作流

### 手动注册

运行参数：

- `mode=register`
- `provider=cfmail|yydsmail|tempmail`
- `count=目标成功数量`

### 手动生成某天汇总包

运行参数：

- `mode=daily_summary`
- `summary_date=YYYY-MM-DD`

如果 `summary_date` 留空，则默认取前一天（按 UTC+8 计算）。

---

## 本地手动打包某天数据

需要一个可读取 Actions artifacts 的 GitHub Token：

```bash
set GITHUB_TOKEN=你的token
python scripts/daily_artifact_bundle.py --repo owner/repo --local-date 2026-03-27
```

---

## 输出目录

- `codex/`
  - 注册成功后保存的 token JSON

- `daily_bundle/`
  - 每日汇总生成的 zip 文件

GitHub Actions 中，注册产物还会作为 artifact 上传，名称格式类似：

```text
tokens-<run_number>-<run_attempt>
```

---

## 停止逻辑

注册任务现在按“成功数量”停止：

- 失败不计数
- 只有成功保存 token 才计入目标数量
- 例如设置 `count=20`，必须成功拿到 20 个账号才会结束

补充说明：
- 当前注册 job 设有 `timeout-minutes: 90`
- 如果 90 分钟内还没达到目标成功数，GitHub Actions 仍会因超时而结束该轮任务

---

## 备注

- Telegram Secrets 未配置时，每日汇总流程不会报错，只会跳过发送
- `tempmail` 与 `yydsmail` 现在已使用各自独立的邮箱轮询逻辑
- 手动触发注册时，`provider` 的优先级高于 `REGISTER_PROVIDER`
