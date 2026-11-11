# 夸克内置 Qwen 本地代理（桌面启动版 · 无票据直连）

把夸克浏览器内的 Qwen（quarkchat）包装成本机 OpenAI 兼容接口。
**无需登录、无需打开夸克浏览器、无需 WG 票据、无需任何刷新步骤** —— 请求 URL 由固定的 `ut` 设备串 + 明文常量构造。

## 启动

双击桌面 `quark-qwen-proxy.cmd`，服务在 `127.0.0.1:8965`（元宝代理 8966 并存）。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 状态（含 `ticketRefreshRequired:false`） |
| GET | `/v1/models` | 6 个网页对话模型 |
| POST | `/v1/chat/completions` | OpenAI 兼容，支持 `stream:true`（一次性 chunk） |

```
curl http://127.0.0.1:8965/v1/models
curl -X POST http://127.0.0.1:8965/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"Qwen\",\"messages\":[{\"role\":\"user\",\"content\":\"只回复 PONG\"}]}"
```

## 原理（2026-11-11 消融实验结论）

- 早期以为必须复用抓包得到的 WG 票据（`kps_wg/sign_wg/ut/kp/mt/xs/vcode`）。逐个参数消融后发现：
  - **必需**：`ut`（设备级 utdId 加密串）+ 明文业务常量 `bi=999/fr=win/pf=5001/pr=ucpro/ve=7.4.7.1036/biz_id=ai_chat_v2/fe_version=1.0.0`。
  - **可省**：`kps_wg` / `kp`（须成对删除或成对保留，单删一个会 403）、`sign_wg` / `mt` / `xs` / `vcode` / `timestamp` / `nonce` / `sign` / `sign_type`。
  - 因此代理直接构造：`https://chat-quark2.qianwen.com/api/v2/chat?ut=<UT>&bi=999&fr=win&pf=5001&pr=ucpro&ve=7.4.7.1036&biz_id=ai_chat_v2&fe_version=1.0.0`，**不再读取任何 Nx* 票据**。
- 每次请求动态生成 `req_id` / `session_id` / `topic_id` 与 `x-chat-*` 头，POST 后解析 SSE 的 `multi_load/iframe` 得到回答。游客态（无 Cookie）即可。

## 关于 `ut`（唯一需要的值）

- 来自夸克私有扩展 API `chrome.publicParamsPrivate.ucParams("ut")` / `chrome.account.getUserInfo().utdId`，是**设备级固定值**（实测相隔 13 小时、跨独立 profile 两次取值完全相同），所以不存在「票据过期刷新」问题。
- 它不能纯离线从零生成（是 native 加密的 utdId），但一次写入 `ticket.json` 的 `ut` 字段后可长期使用。
- **仅当换机器 / 清除夸克设备标识时才需重新取一次**：用带 `--remote-debugging-port` 的夸克打开 quarkchat，在页面控制台执行 `chrome.publicParamsPrivate.ucParams("ut")`，把返回的 `ut` 填回 `ticket.json`（或设环境变量 `QW_UT`）。这是初始配置，不是周期性刷新。

## 已知限制

1. 夸克**概率性**返回 `403 EX015 签名错误`（约 25–50%），代理内置最多 5 次自动重试（每次换 ID），实测连续 8 次（含多模型 / stream）全部 200。
2. 无状态代理：每次新 SessionID，完整 `messages` 拼成一段 prompt；`stream:true` 为一次性 chunk；usage 取自 SSE `total_usage`。
3. 第三方模型有今日次数 / 积分门槛（见 `quark-re\REPORT.md` 8.4）。

## 环境变量（可选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `QW_PORT` | 8965 | 监听端口 |
| `QW_TICKET` | 本目录 `ticket.json` | 配置文件（只需 `ut`） |
| `QW_UT` | — | 直接覆盖 ut |
| `QW_TIMEOUT` | 30000 | 单次请求超时 ms |

## 停止

启动窗口按 `Ctrl+C`；不残留浏览器进程。

## 证据

见 `E:\hds(js)\look\quark-re\REPORT.md` 第 12、13 节与 `quark-re\dynamic\capture\`。
