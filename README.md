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

## 分组网关（每个代理独立分组）

统一代理按 `groups` 配置管理，模型 id 一律 `<组名>/<模型>`，互不混淆：

| 分组 | 类型 | 上游 | 模型 |
|---|---|---|---|
| `quark` | 内置 | 夸克 quarkchat `/api/v2/chat` | 6 |
| `yuanbao` | 内置 | QQ 浏览器元宝 `qbot/qa` | 13 |
| `deepseek-web` | 外部 OpenAI 上游 | `http://127.0.0.1:18088/v1` | 14（动态拉取） |
| `duck` | 外部 OpenAI 上游 | `http://127.0.0.1:7749/v1` | 6（动态拉取） |

- 外部两个网关的桌面启动脚本已收录在 `standalone/launchers/`（快照），`config.json` 里每组的 `startCmd` 指向桌面原路径；面板可一键「启动」。
- 外部组通过 `/v1/models` 动态拉模型、`/health` 做健康检查（15 秒轮询），`key` 为空时走无鉴权。
- DeepSeek 网页版代理当前 `token_status: expired`（上游需要重新登录 chat.deepseek.com），面板会标红提示；这是上游状态，不影响其它分组。

## 前端面板（参考喜鹊 magpie 布局）

浏览器打开 `http://127.0.0.1:8976/` 即可，纯静态单文件 `panel.html`，5 秒自动刷新。

- 侧栏：供应商 / 路由 / 网关 / 观测·用量
- 供应商：分组卡片（名称、地址、模型数、脱敏 key、在线状态、启用开关、启动按钮、token 过期提示）
- 路由：请求模型 → 统一代理 → 各分组上游的拓扑与分组明细
- 网关：本体信息 + 各组健康检查
- 用量：总请求 / Token 每秒(估算) / 成功率 / 平均耗时、按分组柱状图、最近请求表格、热力图
- 数据来自 `/api/status`、`/api/models`、`/api/stats`、`/api/requests`；开关走 `/api/groups/:id/toggle`，启动走 `/api/groups/:id/start`

## GitHub

私有仓库：https://github.com/laolaola278-dev/browser-ai-proxy

`config.json`（含设备 ut）与 `ticket.json` 已被 `.gitignore` 排除，仓库只含 `config.example.json` 模板。

## 上游注意事项（实测 + 两路调研）

- **DeepSeek 网页版代理**（`deepseek-web`，18088）：14 个模型，OpenAI 兼容，含私有 `reasoning_content` / `tool_calls`；上游靠 `userToken` + did + PoW。当前 `health.token_status = expired`（实测 chat 401），且调研记录 2026-11-09 起账号被封 9 天 → **高风险，不要设为默认**（本项目 `defaultProvider` 保持 `quark`）。恢复需重新登录 chat.deepseek.com 或 POST `/_set_token`。
- **Duck2API 网关**（`duck`，7749）：仅 `GET /`、`/health`、`/v1/models`、`POST /v1/chat/completions` 等 5 个端点；模型目录动态（上游 30 分钟缓存，失败回落 `models-extracted.json`），纯文本、不支持图片/联网；上游靠 `X-Vqd-Hash-1` 挑战；451 需海外出口（自动切系统代理 12000）。本地 curl 需 `--noproxy "*"`，Node fetch 直连 127.0.0.1 无需代理。
- **GitHub 仓库澄清**：`laolaola278-dev/DeepSeekWebProxy`（私有）与本地 18088 项目一致；但 `laolaola278-dev/duck2api-pro` 是**另一个更新的项目**（主程序 `duck2api_pro_proxy.py`、默认端口 7750、身份池、`/v1/responses|messages|images/generations`），**不是** 7749 部署的网关。7749 的源码在本机 `E:\hds(js)\look\duck-apk`。接入时不要按 7750 项目给 7749 配端点。

## 面板 v2（对齐喜鹊真实布局）

调研结论：3425 的 magpie 面板是 Wails/WebView2 桌面窗口（前端明文在 `E:\project\喜鹊\magpie-main\internal\gui\assets\`），不是 HTTP 前端站点；配色 `--accent:#4f46e5` / `--bg:#f4f4f6` / 暗色 `#1a1a1e`。本面板据此做了：

- 侧栏分组：模型（供应商/路由/网关）· 观测（用量）· 扩展（插件），底部网关状态灯
- 供应商行：图标 + 名称徽章 + `域名 · N models` + 脱敏 key 药丸 + 在线灯 + 开关 + 展开箭头（健康检查 / 启动脚本 / 启动按钮 / token 过期红标）
- 路由页：SVG 三层拓扑（Agent → 网关 → 各分组节点，带连线）+ 路由策略卡（智能/顺序/权重/最少使用/手动/按意图）
- 用量页：概览 / 请求 双页签、4 列 KPI、按分组柱状图、按模型折叠排行、16 列请求表（Time/Agent/Requested/Provider·account/Sent/Served/Effort/In/Out/Cache/Cost/TTFT/Duration/Speed/Status）、CSV 导出、53 周热力图、耗时色（≤10s 绿 / 10–30s 琥珀 / >30s 红）
- 亮/暗主题切换、`tabular-nums`、12px 卡片 / 999px 药丸 / 8px 分段控件
- 兼容 API 别名：`/api/providers`、`/api/state`、`/api/usage`、`/api/usage/requests`、`/api/requests.csv`（同时保留 `/api/status`、`/api/stats`、`/api/requests`）

## 调研资料（未纳入本仓库）

`E:\hds(js)\look\qq-re\research\gateways.md`（23KB / 两网关 7 节规格）、`magpie-ui.md`（26.6KB / 296 行，含 HTML/CSS 片段）、`evidence\`（29 个文件 / 32 条编号证据）。
