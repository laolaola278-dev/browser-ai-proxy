# Duck 网关（duck.apk 提取版）· 使用说明

把 `D:\xjx\网盘下载\duck.apk` 解包后，还原出里面的「代理」——一个 **OpenAI 兼容 → duck.ai** 的
本地网关，并做成 PC 上可直接运行的 Python 版本。原始 APK 未改动。

> **连接速查见 [CONNECT.md](CONNECT.md)**：网关当前已在 `http://127.0.0.1:7749` 运行，
> 含 curl / OpenAI SDK / the agent harness provider / 局域网 / 排障的复制即用配置。

> **更新（2026-11-05 22:40）**：电脑端握手**可以完全脱离浏览器实现**。
> 新增 `pure` 传输（requests + Node/vm DOM mock），已验证 8/8 次全新挑战 + 对话成功，
> 现为默认模式。详见 [protocol.md](protocol.md) 第 4 节。
>
> **更新（2026-11-06 10:45）**：桌面启动脚本已写到
> [C:\Users\admin\Desktop\启动Duck网关.cmd](C:/Users/admin/Desktop/启动Duck网关.cmd)。
> 修复了 agent 客户端「长 system + 大量 MCP 工具 → 用户问题被截断 → 回复空/答非所问」
> 的 bug，并新增 `--tools off|emulated`；连接与排障见 [CONNECT.md](CONNECT.md)。
>
> **更新（2026-11-06 11:30）**：修复中文回复乱码。requests 读取 duck.ai SSE 时默认按
> ISO-8859-1 解码 UTF-8，导致中文变成 `æ\x88\x91...`；现在所有上游 SSE 读取都强制
> `r.encoding = "utf-8"`。桌面 `.cmd` 也改为 ASCII-only，避免 CMD 代码页显示异常。

## 目录内容

| 文件 | 用途 |
|---|---|
| [duck2api_proxy.py](duck2api_proxy.py) | **可运行的独立代理**（还原后的网关实现，默认 `pure` 传输） |
| [pure_handshake.py](pure_handshake.py) | **无浏览器握手**：requests 取挑战头 → Node/vm 求值 → 拼 token → 发聊天 |
| [solve_challenge.js](solve_challenge.js) | Node 求解器：校准过的 DOM mock，逐字还原真 Chrome 的探针结果 |
| [get_token.py](get_token.py) | Chrome/Playwright 版挑战求解（可选 / 兜底 / 诊断） |
| [challenge.js](challenge.js) | 从 APK `b.h.j` **逐字提取**的 WebView 挑战脚本 |
| [models-extracted.json](models-extracted.json) | 提取出的兜底模型目录 |
| [config-extracted.json](config-extracted.json) | 提取出的默认配置 |
| [protocol.md](protocol.md) | 完整逆向报告：端点、请求头、翻译规则、错误映射、握手还原与验证 |
| [AndroidManifest.decoded.xml](AndroidManifest.decoded.xml) | 解码后的 manifest |
| [dex-strings.txt](dex-strings.txt) / [strings.txt](strings.txt) | DEX 字符串表全量 |
| [inventory.txt](inventory.txt) | APK 信息 + 类清单 |
| `extracted\` / `java\` | 原始解包文件 / 130 个反编译 Java 类 |
| `inventory.py` / `decompile.py` / `extract_artifacts.py` | 可复现的提取脚本 |
| `validate-proxy-pure.py` / `validate-proxy-live.py` | 无浏览器 / 浏览器传输的端到端验证脚本 |
| [CONNECT.md](CONNECT.md) / [connect-example.py](connect-example.py) | 连接速查 / 纯 requests 客户端示例（已实测 200） |
| `challenge-raw.js` / `challenge-header.b64` | 抓取的服务端挑战脚本（会过期，取证用） |
| `_probes\` | 探针、标定、比对脚本与日志（取证用） |

## 快速开始

依赖：Python 3.10+、`requests`；`pure` 模式还需要 **Node.js**（本机已自动找到
Playwright 自带的 `node.exe`，也可用 `--node` 指定）。`hybrid`/`browser` 模式另需
`playwright` + Chrome/Edge。

```powershell
cd E:\hds(js)\look\duck-apk

# 1) 离线自检（不需要网络/浏览器）
python duck2api_proxy.py --selftest

# 2) 启动网关（默认 127.0.0.1:7749，无浏览器 pure 握手）
python duck2api_proxy.py

# 3) 另开一个终端测试
curl http://127.0.0.1:7749/health
curl http://127.0.0.1:7749/v1/models
curl http://127.0.0.1:7749/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"gpt-6-luna\",\"messages\":[{\"role\":\"user\",\"content\":\"Say PONG\"}]}"
```

单独验证纯握手（不启网关）：`python pure_handshake.py --rounds 5 --delay 8`

流式：请求体加 `"stream": true`，返回 `text/event-stream` 的 `chat.completion.chunk`，以 `[DONE]` 结束。

接任意 OpenAI SDK / 客户端：

```
base_url = http://127.0.0.1:7749/v1
api_key  = 任意值（默认不鉴权；若启动时设了 --key，则填该值）
model    = gpt-6-luna / gpt-5.4-mini / ...（见 /v1/models）
```

## 四种传输（按推荐顺序）

| `--transport` | 握手方式 | 聊天流量 | 需要浏览器 | 说明 |
|---|---|---|---|---|
| **`pure`（默认）** | `requests` 取 `x-vqd-hash-1` + Node/vm 求值 | `requests` | **否** | 最快最轻；新探针出现时自动回退一次性 Chrome |
| `hybrid` | 优先 pure，失败则一次性 Chrome | `requests` | 仅回退时 | 对挑战轮换最稳，浏览器不在热路径 |
| `browser` | Chrome 页面内 `DuckBridge` | 页面内 `fetch` | 是 | 对 APK WebView 行为最忠实；页面内 `/status` 易被 429，已加 pure 兜底 |
| `direct` | 不求解，用 `--token` / `--token-file` | `requests` | 否 | 适用于已有凭证（如真机导出） |

```powershell
python duck2api_proxy.py --transport hybrid     # Chrome 兜底
python duck2api_proxy.py --transport browser    # 全页面内
python get_token.py --out token.json
python duck2api_proxy.py --transport direct --token-file token.json
```

## 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--host` / `--port` | `127.0.0.1` / `7749` | 监听地址；`--host 0.0.0.0` 开放局域网 |
| `--key` | 空 | 网关 API Key（`Authorization: Bearer <key>`）；空=不鉴权 |
| `--upstream` | `https://duck.ai` | 上游基址 |
| `--default-model` | `gpt-6-luna` | 未指定/模型不可用时的兜底 |
| `--reasoning` | `none` | `none` / `low` / `medium` |
| `--transport` | `pure` | `pure` / `hybrid` / `browser` / `direct` |
| `--tools` | `emulated` | `off` = 忽略客户端工具列表（纯文本最稳）；`emulated` = 提示词模拟工具调用。当前桌面脚本已设为 `emulated` |
| `--node` | 自动探测 | Node 可执行文件（`pure` 求解器用） |
| `--browser` | `chrome` | Playwright 通道：`chrome` / `msedge` / `chromium` |
| `--show-browser` | — | 显示浏览器窗口（默认 headless） |
| `--proxy` | 直连 | 出站代理 URL，如 `http://127.0.0.1:7890` |
| `--token` / `--token-file` | — | `direct` 传输用的 `X-Vqd-Hash-1` |
| `--token-ttl` | `1800` | 凭证/模型目录缓存秒数（与 APK 一致） |
| `--mock` / `--selftest` | — | 离线假上游 / 一键自检 |

环境变量：`DUCK_HOST`、`DUCK_PORT`、`DUCK_KEY`、`DUCK_UPSTREAM`、`DUCK_TRANSPORT`、
`DUCK_NODE`、`DUCK_BROWSER`、`DUCK_PROXY`、`DUCK_TOKEN`。

## 电脑端握手是怎么做到的

APK 的握手 = 在 WebView 里加载 duck.ai → 取 `/duckchat/v1/status` 响应头
`x-vqd-hash-1`（base64 混淆 JS）→ 在真实 DOM 中求值 → 对 `client_hashes` 做 SHA-256 →
`btoa(JSON.stringify(res))` 得到 `X-Vqd-Hash-1`。

纯代码版把「真实 DOM」换成 Node `vm` 上下文里校准过的 DOM mock：

1. `requests` 直连 `GET /duckchat/v1/status`（带 `x-vqd-accept: 1`）拿到挑战脚本；
2. [solve_challenge.js](solve_challenge.js) 在 Node `vm` 里执行该脚本：
   字符串表轮换、`server_hashes`、`challenge_id`、`timestamp`、`meta.debug` 全部由脚本自身算出；
   DOM 探针（`#jsa` iframe/CSP、`innerHTML` 序列化、`querySelectorAll` 计数、
   `instanceof HTMLDivElement`、`Object.prototype.toString`、`window.Object` 等）由 mock 按
   **真 Chrome 实测值**校准；
3. Python 复刻 `b.h.j` 后处理：SHA-256 `client_hashes`、补 `meta.origin/stack/duration`、
   `base64(json)` → token；
4. 用 `requests` 带该 token `POST /duckchat/v1/chat`（SSE 流式读回）。

**关键更正**：凭证**并不**与浏览器会话绑定。早前「独立 requests 会 418」是因为那批凭证由
**带自动化痕迹的 headless Chromium** 解出、被服务端标记；用真实 Chrome（加
`--disable-blink-features=AutomationControlled`）解出的 token，纯 `requests` 直接 200，
连 `sec-ch-ua` 都不需要。

标定结果：抓取的 17 个不同代挑战脚本，Node 求解输出与真 Chrome **逐字段完全一致**；
`pure_handshake.py --rounds 8 --delay 8` → **8/8** 全新挑战 + 对话 200。

> 注意：duck.ai 会轮换 DOM 探针族；[solve_challenge.js](solve_challenge.js) 内置已观测到的
> 4 类 `innerHTML` 规范化表和自适应字符串提取，新族出现时 `pure` 会自动回退一次性 Chrome。
> `/duckchat/v1/status` 有频率限制（连续猛打会 429），已加指数退避；正常 30 分钟缓存不会触发。
> 本机系统代理 `127.0.0.1:12000` 对 duck.ai TLS 握手失败，脚本默认直连，需要时 `--proxy`。
> **2026-11-06 追加**：直连出口被 duck.ai 判 `451 ERR_COUNTRY_NOT_SUPPORTED` 时，
> 网关会自动改用系统代理（iKuuuVPNCore `127.0.0.1:12000`）重试并沿用，无参数启动即可；
> 请求体带 UTF-8 BOM 也已兼容。

## 能力边界

- 与 APK 一致：`canUseTools/canUseWebSearch/canUploadFiles/canUseApproxLocation/canShowGreeting` 全为 `false`；
  不支持图片/文件上传、联网搜索。`tools` 是**提示词模拟**（最多 4 次重试 + 解析一行原始 JSON），
  不是原生 function calling。
- `pure` / `hybrid` / `direct` 用多线程 HTTPServer；仅 `browser` 因 Playwright 线程亲和用单线程。
- 主程序现在带轻量守护：服务内部未捕获异常会打印 traceback，并在 3 秒后自动重建监听，最多尝试 5 次。
- 凭证缓存 30 分钟，遇 418 自动重解重试一次；模型目录同样缓存 30 分钟。
- 未内嵌任何密钥/账号；`gatewayKey` 默认空。上游是第三方服务，请遵守其条款，勿用于滥用或对外提供服务。

## 验证记录

| 项目 | 结果 |
|---|---|
| `--selftest`（离线 mock，7 项端点） | **PASS** |
| 17 个挑战脚本 vs 真 Chrome | **17/17 逐字段一致** |
| `pure_handshake.py --rounds 8 --delay 8` | **8/8** 全新挑战 + 对话 200 |
| `pure` 网关端到端 | /health、/v1/models(6)、非流式 `PONG`、流式 `HI`+`[DONE]`、tools `get_time` 全 200 |
| `hybrid` / `browser` 回归 | 均 200（browser 在页面内 429 时由 pure 兜底成功） |

详见 [protocol.md](protocol.md) 第 4 节。

## 回滚

原始 `D:\xjx\网盘下载\duck.apk` 未被修改；删除 `E:\hds(js)\look\duck-apk\` 即完全清理。
