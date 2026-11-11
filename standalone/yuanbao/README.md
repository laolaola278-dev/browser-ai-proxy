# QQ 浏览器「元宝助手」本地代理（桌面启动版）

把 QQ 浏览器内置的元宝助手（QBot / 腾讯元宝）包装成本机 OpenAI 兼容接口。
**不需要打开 QQ 浏览器、不需要 CDP、不需要登录** —— 直接复刻 InitSession + qbot/qa 两段请求。

## 启动

双击桌面 `yuanbao-proxy.cmd`，保持窗口开着，服务在 `127.0.0.1:8966`（与夸克代理 8965 并存，不冲突）。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 状态（含当前设备指纹前缀） |
| GET | `/v1/models` | 3 个官方模型 + 10 个内部/实验模型 |
| POST | `/v1/chat/completions` | OpenAI 兼容，支持 `stream:true`（一次性 chunk） |

```
curl http://127.0.0.1:8966/v1/models
curl -X POST http://127.0.0.1:8966/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"smart\",\"messages\":[{\"role\":\"user\",\"content\":\"只回复 PONG\"}]}"
```

实测：13 个 ID 逐个请求全部 200 且有回答；但无效 model 值也 200，且 SSE 无模型回显、各值自报身份一致，因此**只有 `smart` / `deepseek` / `hunyuan` 三个官方值确定真实生效**，内部 ID 不保证切换（详见 `qq-re\REPORT.md` 5.1.1）。

模型：`smart`（空串，智能自动匹配）、`deepseek`、`hunyuan`（Hy3），以及内部 ID `deepseek3.2` / `glm5` / `kimi2.5` / `qwen3` / `minimax2.5` / `deepseek3.1` / `hy3.0` / `hy3.0lowthinking` / `hy3.0hthinking` / `deepseekv4`（实验性）。未知 model 值原样传给 `ExtendParams.model`。

## 原理与关键点

1. `POST https://qbh5.qq.com/h5/v2/mtt_qb_qa_coordinator_proxy/InitSession` → `{"session_id":"..."}`
2. `POST https://qbqaproxy.imtt.qq.com/cgi-bin/gpt/qbot/qa` → `200 text/event-stream`
   - 头：`q-qimei36`（qimei36）、`q-qua2`/`q-guid`/`q-ua2`、`userinfo`（base64 的 guid/qua/qimei36/qbid）、`Origin`、游客 Cookie
   - 体：`{"SessionID":"...","Intent":"","ExtendParams":{"coordinator_user":"true","pluginVersion":"0.22.1","env":"","model":"<模型值>"},"Question":"..."}`
   - SSE：`SESSION_START` → `MESSAGE {"Text":"P"}` → `MESSAGE {"Text":"ONG"}` → `CLOSE` → `COMPLETED` → `CLOSE data:[DONE]`
3. 模型列表由 `mtt_config_store/GetConfig`（`appid:1023`）的 `config[0].openModels` 下发。

**游客态有频率限制**：同一设备指纹（guid / qimei36）短时间多次请求会返回 `trpc-func-ret:6 / 抱歉，需要登录来为您提供这项服务`（Content-Length 0）。因此代理在**每次启动时生成随机设备指纹**，并在遇到该错误或超时时**自动轮换设备重试**（最多 4 次）。若你用 `YB_GUID`/`YB_QIMEI` 固定了指纹并被限流，去掉这两个环境变量即可恢复自动轮换。

## 环境变量（可选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `YB_PORT` | 8966 | 监听端口 |
| `YB_TIMEOUT` | 90000 | 单次请求超时上限（qa 实际 30s） |
| `YB_GUID` / `YB_QIMEI` | 每次启动随机 | 设备指纹（32 hex） |
| `YB_QUA` | 本机抓包值 | QUA2 设备串 |

## 限制

- 无状态代理：每次请求新建 SessionID，完整 `messages` 拼成一段 prompt（不复用元宝会话）。
- `stream:true` 是等完整回答后一次性发 chunk，不是逐 token（元宝的 SSE 是逐段 delta，可后续改造成真流式）。
- 响应无 token usage，返回 0；静态分析未发现 quota/credit 接口，登录态额度未实证。
- 只监听 127.0.0.1。

## 停止

启动窗口按 `Ctrl+C` 即可；不残留浏览器进程。

## 证据

见 `E:\hds(js)\look\qq-re\REPORT.md`、`qq-re\dynamic\capture\`、`qq-re\static\findings.md`。
