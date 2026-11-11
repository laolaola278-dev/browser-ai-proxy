# Unified Browser-AI Proxy（夸克 Qwen + QQ 浏览器元宝）

一个进程、一个端口、一个 OpenAI 兼容接口，把本次逆向的两个浏览器内置 AI 统合起来：

| Provider | 来源 | 模型数 | 端口（旧独立代理） |
|---|---|---|---|
| `quark` | 夸克浏览器 quarkchat Qwen | 6 | 8965 |
| `yuanbao` | QQ 浏览器元宝助手 QBot | 13 | 8966 |

## 启动

双击桌面 `browser-ai-proxy.cmd`，服务在 `127.0.0.1:8976`。**不需要打开任何浏览器、不需要 CDP、不需要登录、不需要票据刷新**。

## 接口

- `GET /health` — 状态
- `GET /v1/models` — 19 个模型（id 带 `quark/` 或 `yuanbao/` 前缀）
- `POST /v1/chat/completions` — 按 `model` 前缀路由；也接受不带前缀的裸 id（如 `smart`、`Qwen`），`auto` 走默认 provider

```
curl http://127.0.0.1:8976/v1/models
curl -X POST http://127.0.0.1:8976/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"quark/Qwen\",\"messages\":[{\"role\":\"user\",\"content\":\"只回复 PONG\"}]}"
curl -X POST http://127.0.0.1:8976/v1/chat/completions -H "Content-Type: application/json" -d "{\"model\":\"yuanbao/deepseek\",\"messages\":[{\"role\":\"user\",\"content\":\"只回复 PONG\"}]}"
```

响应额外带 `provider` 字段；支持 `stream:true`（一次性 chunk）。

## 配置

- `config.json`：`quark.ut`（夸克设备固定串，唯一必需值）、`yuanbao.qua`（QUA2）、可选 `yuanbao.guid/qimei`（留空则每次启动随机生成并自动轮换）、`defaultProvider`。
- `config.example.json` 是脱敏模板，可直接复制填写。
- 环境变量可覆盖：`BAI_PORT` / `BAI_CONFIG` / `QW_UT` / `YB_GUID` / `YB_QIMEI` / `YB_QUA`。

## 两个上游的实现要点

- **夸克**：URL 由 `ut` + 明文常量构造（消融证明 WG 票据非必需），动态 `req_id/session_id/topic_id` 与 `x-chat-*` 头，解析 SSE `multi_load/iframe`。概率性 403 EX015 由 5 次重试吸收。
- **元宝**：`InitSession` → `qbot/qa` SSE，随机设备指纹 + 遇登录墙/超时自动轮换，解析逐段 `MESSAGE {"Text":...}`。
- 无状态：每次请求独立会话，完整 `messages` 拼成一段 prompt；usage 夸克取自 `total_usage`，元宝无则为 0。

## 旧独立代理

桌面 `quark-qwen-proxy\`(8965) 与 `yuanbao-proxy\`(8966) 仍可用；统一代理**不依赖它们**（单进程内联两套逻辑），可只留一个。

## 证据

见 `E:\hds(js)\look\quark-re\REPORT.md` 与 `E:\hds(js)\look\qq-re\REPORT.md`。

## 仓库结构

- `server.mjs` / `config.example.json` / `.gitignore` / `browser-ai-proxy.cmd`（桌面）—— 统一代理本体
- `standalone/quark`、`standalone/yuanbao` —— 两个独立代理的源码快照（敏感配置已排除）
- 完整逆向报告见 `E:\hds(js)\look\quark-re\REPORT.md` 与 `qq-re\REPORT.md`（未纳入本仓库）
