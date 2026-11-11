# DeepSeekWebProxy

把 **chat.deepseek.com 网页版**封装成 **OpenAI 兼容 API** 的本地反向代理。

纯 Python（标准库 + `requests`），不需要浏览器自动化，也不需要官方 API Key。

> 🚨 **账号封禁实测警告（2026-11-10）**
>
> 使用本代理调用 `chat.deepseek.com` 网页端私有接口**会触发上游风控**。实测账号被
> **直接封禁 9 天**，再次触发后续处罚可能更长，直至永久停用。内置的
> `upstream_pacing`（并发 2 / 最小间隔 800 ms / 重试 2）**不能保证免封**。
>
> ⚠️ **请勿使用主力账号**，也不要把它挂进长期在线的自动化流程。
> 详情与降风险建议见 [ACCOUNT-BAN-RISK.md](<ACCOUNT-BAN-RISK.md>)。

> ⚠️ 本项目是对网页端私有接口的逆向实现，仅供个人学习与研究使用。请遵守
> DeepSeek 的服务条款。账号 `userToken` 属于个人凭据，**绝对不要提交到仓库**
> （已由 `.gitignore` 强制排除）。

## 功能

- **OpenAI 兼容**：`POST /v1/chat/completions`（流式 SSE / 非流式）、`GET /v1/models`
- **PoW**：内置 `DeepSeekHashV1`（SHA3-256 sponge，23 轮）纯 Python 实现；
  可选 `_pow_node.js` + Node 加速（`DEEPSEEK_POW_NODE`）
- **模型映射**：`deepseek-chat → default`；`deepseek-reasoner / deepseek-r1 /
  deepseek-thinking → expert`
- **思考链**：`reasoning_content` 独立输出，`include_thinking` 开关
- **工具调用**：OpenAI `tools` / `tool_choice` → DSML
  `<｜tool▁calls▁begin｜>` 增量解析，支持跨 chunk 拆包与 `finish_reason: tool_calls`
- **附件**：`image_url` / `file`（data URL、裸 base64、http(s)）→
  `upload_file` → `fetch_files` 轮询 → `ref_file_ids`
- **回合状态机**：`INCOMPLETE → /chat/continue`（最多 20 次）、
  空 `FINISHED → /chat/resume_stream`、非 SSE 的 `biz_code 22` 整包解包
- **会话失效自愈**：自动新开 chat session + 全量历史重发一次
- **令牌自愈**：`40003 INVALID_TOKEN` 时自动重扫 Chromium Local Storage、
  验活并热替换，无需重启
- **上游限速**：并发闸门 + 最小请求间隔 + 指数退避重试
- **finish_reason 映射**：`CONTENT_FILTER → content_filter`，
  `INCOMPLETE / TIMEOUT / CONTEXT_LENGTH_EXCEEDED → length`
- 内置简易网页聊天页：`http://127.0.0.1:18088/`

## 快速开始

### 1. 准备环境

```bash
python -m pip install -r requirements.txt
```

要求 Python 3.9+。Windows 看门狗脚本另外需要 `psutil`（已在 requirements 中按平台声明）。

### 2. 取得 userToken

在浏览器登录 <https://chat.deepseek.com>，打开 DevTools → Application →
Local Storage，复制 `userToken` 的值（一长串字符串）。

三选一提供令牌：

```bash
# 方式 A：环境变量（推荐）
export DEEPSEEK_TOKEN='你的userToken'          # Linux/macOS
set DEEPSEEK_TOKEN=你的userToken               # Windows CMD
$env:DEEPSEEK_TOKEN='你的userToken'            # PowerShell

# 方式 B：写到同目录的 deepseek_token.txt（已被 .gitignore 排除）
echo 你的userToken > deepseek_token.txt

# 方式 C：服务启动后热更新
curl -X POST http://127.0.0.1:18088/_set_token -H 'Content-Type: application/json' -d '{"token":"你的userToken"}'
```

### 3. 启动

```bash
python start_proxy.py --port 18088 --host 127.0.0.1
# 或等价地直接运行主程序
python MODIFIED_FILE.py --port 18088 --host 127.0.0.1
```

```bash
# 自检（不需要网络 / 令牌）
python MODIFIED_FILE.py --selftest
```

Windows 用户也可双击 `start_deepseek_18088.cmd` 后台启动（需先把脚本里的
Python 路径改成自己的），或运行 `python _desktop_start.py` 拉起看门狗。

## API

| 路径 | 说明 |
|---|---|
| `GET /` | 内置网页聊天页 |
| `GET /health`，`GET /deepseek/health` | 健康检查（含 `token_status`、限流参数） |
| `GET /v1/models`，`GET /deepseek/v1/models` | 模型目录 |
| `POST /v1/chat/completions`，`POST /deepseek/v1/chat/completions` | 聊天补全 |
| `POST /_set_token` | 热替换 token |

Base URL：`http://127.0.0.1:18088/v1`（任意 API Key 均可，服务端不校验）。

```bash
curl http://127.0.0.1:18088/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer x' \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"你好"}],"stream":false}'
```

工具调用、图片/文件附件均按 OpenAI 格式传入即可：

```json
{
  "model": "deepseek-chat",
  "messages": [{"role": "user", "content": [
    {"type": "text", "text": "这张图里是什么？"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
  ]}],
  "tools": [{"type": "function", "function": {"name": "get_time", "parameters": {"type": "object", "properties": {}}}}]
}
```

## 环境变量

完整清单见 [`.env.example`](<.env.example>)。常用项：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_TOKEN` | — | 网页版 userToken |
| `DEEPSEEK_DEVICE_ID` | 随机 | `did` 设备标识 |
| `DEEPSEEK_BASE` | `https://chat.deepseek.com` | 上游地址 |
| `DEEPSEEK_HOST` / `DEEPSEEK_PORT` | `127.0.0.1` / `8080` | 本地监听 |
| `DEEPSEEK_POW_NODE` / `DEEPSEEK_POW_JS` | — | 可选的 Node PoW 加速器 |
| `DEEPSEEK_MAX_CONCURRENCY` | `2` | 上游并发上限 |
| `DEEPSEEK_MIN_INTERVAL_MS` | `800` | 上游请求最小间隔 |
| `DEEPSEEK_MAX_RETRIES` | `2` | 退避重试次数 |
| `DEEPSEEK_MAX_CONTINUES` | `20` | `/chat/continue` 最大次数 |
| `DEEPSEEK_INCLUDE_THINKING` | `0` | 默认是否输出思考链 |
| `DEEPSEEK_TOKEN_REFRESH_COOLDOWN` | `60` | 令牌重扫冷却（秒） |

## 目录说明

| 文件 | 作用 |
|---|---|
| `MODIFIED_FILE.py` | 代理主程序（单文件，含内置网页、PoW、状态机、工具调用、附件） |
| `start_proxy.py` | 跨平台启动器（自动读取 env / token 文件） |
| `_pow_node.js` | 可选 Node PoW 加速器 |
| `_desktop_start.py` / `_watch_deepseek_18088.py` / `_run_live_proxy_new.py` | Windows 后台看门狗（可选） |
| `start_deepseek_18088.cmd` | Windows 启动脚本（需按本机修改 Python 路径） |
| `PATCH_NOTES-20261110.md` | 与参考实现逐项对比的改动与验证记录 |
| `ACCOUNT-BAN-RISK.md` | 账号风控/封禁实测记录与降风险建议 |

## 安全须知

- **封号风险（实测）**：使用本代理会触发上游风控，已发生账号被封禁 9 天的案例，
  后续可能更长。完整说明、触发原因与降风险建议见
  [ACCOUNT-BAN-RISK.md](<ACCOUNT-BAN-RISK.md>)。已被封禁时请立即停止一切请求、
  等待解封，不要反复换 token 试探。
- `deepseek_token.txt` / `deepseek_device_id.txt` 已被 `.gitignore` 排除，
  本仓库根目录使用白名单式忽略规则，防止误提交抓包与凭据文件。
- 令牌失效时（`INVALID_TOKEN`）服务会返回清晰的 `401 authentication_error`，
  并尝试从本机 Chromium 浏览器存储中自动验活、热替换有效令牌。
- 内置的 PoW、请求头、模型 ID 均可能随上游改版而失效，属预期情况。

## 致谢与参考

协议细节与状态机设计参考了
[`volcanicll/magpie-deepseek-web-auth`](<https://github.com/volcanicll/magpie-deepseek-web-auth>)。
本仓库的实现差异、修复项与验证记录见 `PATCH_NOTES-20261110.md`。
