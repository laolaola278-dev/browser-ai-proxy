# 项目内置网关源码（四个代理自包含）

本目录收录了统一网关所需的两个 GitHub 仓库源码与一个本地重建网关源码，全部随仓库自带。

| 分组 | 目录 | 来源 | 启动入口 | 端口 |
|---|---|---|---|---|
| `quark` | （内联在 `../server.mjs`） | 本次逆向 | 无需启动 | — |
| `yuanbao` | （内联在 `../server.mjs`） | 本次逆向 | 无需启动 | — |
| `deepseek-web` | `gateways/deepseek-web/` | `laolaola278-dev/DeepSeekWebProxy`（私有仓库，白名单 13 文件） | `python _desktop_start.py` → 看门狗 → `_run_live_proxy_new.py` → `MODIFIED_FILE.py` | 18088 |
| `duck` | `gateways/duck/` | duck.apk 重建的 duck2api 网关 | `python duck2api_proxy.py --host 127.0.0.1 --port 7749 --tools emulated` | 7749 |
| `duckpro`（备用实现） | `gateways/duckpro/` | `laolaola278-dev/duck2api-pro`（白名单 5 文件） | `python duck2api_pro_proxy.py` | 7750 |

## 凭证（一律不随仓库分发）

- `deepseek-web` 需要 `DEEPSEEK_TOKEN`（环境变量优先；`deepseek_token.txt` / `deepseek_device_id.txt` 为可选本地文件，**未纳入仓库**）。上游登录态需自行登录 chat.deepseek.com 获取。
- `duck` 的挑战 token 由运行时 `challenge.js` 在浏览器内计算，不落盘；`config-extracted.json` 的 `gatewayKey` 为空。
- `duckpro` 的身份池配置由使用者自备，不属于本仓库。

## 依赖

- DeepSeekWebProxy：Python 3.9+、`pip install -r requirements.txt`、Node.js（PoW）。
- duck-apk 网关：Python 3.10+、requests、urllib3、Node.js；`--transport browser` 另需 Playwright + Chrome。
- duck2api-pro：见其 `README.md`。

统一代理 `config.json` 里各组的 `launch` 指向这些目录，面板「启动」按钮或 `POST /api/groups/:id/start` 会直接拉起对应 Python 子进程。