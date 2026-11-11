# DeepSeekWebProxy 补丁记录 — 2026-11-10

基线：\`MODIFIED_FILE.py\`（1970 行 / 74712 字节）
备份：\`MODIFIED_FILE.py.bak-toolcall-token-gate-20261110\`
结果：2713 行 / 105053 字节（+909 / −166）
对照：https://github.com/volcanicll/magpie-deepseek-web-auth （master）
验证：selftest ALL_OK；流式工具调用集成测试 ALL_OK；重启后 live 实测通过。

## 一、工具调用层（整体借鉴插件）

插件源码：\`src/toolcall.mjs\` + \`src/stream.mjs\` + \`src/openai.mjs\`。

| 你的原实现 | 插件做法 | 本次改动 |
|---|---|---|
| \`FUNCTION_CALL_START/END\` 自定义中文协议；只在整段回复聚合后解析一次 | 标准 \`<tool_call>{"name":...,"arguments":{}}</tool_call>\`，JSON 一行一工具、可并行、禁止代码围栏、禁止 DSML | \`tools_to_prompt()\` 采用插件协议原文；\`build_tool_instructions()\` 保留为别名 |
| 只认一种标记；DSML 被 \`ControlTextFilter\` 直接删掉 | 双格式：\`<tool_call>\` + 网页原生 DSML \`invoke/parameter\`，宽松匹配全角/竖线变体 | \`parse_tool_call()\`、\`parse_dsml_calls()\`、\`dsml_tag_name()\` |
| 聚合后才解析；工具轮次流式期间一个字都不吐 | \`PatchReader.emitText\` 增量解析，跨 delta 的半截标记 hold 住、65K runaway 保护 | 新增 \`ToolCallScanner\`（feed/flush），\`_collect_events\` 增加 \`tool_calls\` / \`folded\` 事件，\`_stream_completion\` 边解析边发 tool_calls chunk |
| 解析失败的标记剥掉后仍可能混进正文 | 解析失败的块折进 reasoning，永不进正文 | \`folded\` 事件 → 仅当 \`include_thinking\` 时以 \`reasoning_content\` 流出 |
| 历史里工具结果写成 \`Tool: \`，assistant 的 tool_calls 被跳过 | \`messagesToPrompt\` 把 assistant 的 tool_calls 回填成 \`<tool_call>\`，tool 结果写成 \`Tool result (name): …\` | \`build_prompt()\` / \`build_fc_continuation()\` + \`tool_name_map()\` + \`render_tool_call()\` |
| \`ControlTextFilter\` 无差别删 DSML 标签 | DSML 块在有 tools 时是调用，不能删 | \`ControlTextFilter(strip_dsml=...)\`，工具轮次关闭 DSML 剥离 |

兼容：旧的 \`FUNCTION_CALL_START/END\` 仍能被 \`ToolCallScanner\` 解析，旧客户端不会突然失效。

## 二、令牌过期：从「重启」改成「自动重扫 + 验活 + 热替换」

- \`check_envelope()\`：学插件 \`api.mjs\` 的 \`envelope()\`，**同时判顶层 \`code\` 与 \`data.biz_code\`**。原来的 \`data:null\` + \`code:40003 INVALID_TOKEN\` 被误报成 \`chat_session id missing\`（误导性 502），现在抛 \`SignInExpired\`。
- \`browser_token_candidates()\`：扫描 Chrome/Edge/Brave/Vivaldi/Quark/Doubao/Chromium + VS Code 的 Local Storage leveldb；先复制文件到临时目录（绕开 Chrome 独占锁），兼容 ASCII/UTF-16 的 \`userToken\`、\`{"value":…,"__version":"0"}\` 记录与 64 位 base64，按 mtime 去重排序。
- \`token_is_live()\`：用插件同款 \`GET /api/v0/users/current\` 按业务码验活（最便宜、不需要 PoW）。
- \`refresh_token_from_browser()\`：\`TOKEN_LOCK\` + 60s 冷却，防止并发请求把扫描打成风暴；找到活令牌就 \`set_token()\` 写文件 + 写 \`os.environ["DEEPSEEK_TOKEN"]\` + 清空 \`SESSION_MAP\`。
- \`_handle_completion()\`：\`SignInExpired\` → 自动重扫 → 成功则整轮重试一次，失败返回清晰的 401（带上游原因）。
- 顺带修 \`set_token()\` 的 env 遮蔽 bug（runner 启动时把 \`DEEPSEEK_TOKEN\` 钉进环境，原来 \`/_set_token\` 只改文件不生效）。
- \`/health\` 新增 \`token_status\` / \`token_refreshed_at\` / \`token_source\`（缓存状态，不额外打上游）。

## 三、并发限速 / 队列 / 退避

- \`UpstreamGate\`：\`BoundedSemaphore\` 限并发 + 两次上游请求最小间隔（默认 800ms），补齐 node PoW 毫秒级完成所缺的天然节流。
- \`upstream_request()\`：429/500/502/503/504 与传输异常按 \`base * 2^n\` + ±25% jitter 指数退避重试（默认 2 次重试）。
- 三个关键上游调用（\`create_session\` / \`create_pow_challenge\` / \`completion\` 含 403 replay）全部走 gate；\`models()\` 与 \`whoAmI\` 保持直连（可选/验活，不占队列）。

可调环境变量：
\`DEEPSEEK_MAX_CONCURRENCY=2\`、\`DEEPSEEK_MIN_INTERVAL_MS=800\`、\`DEEPSEEK_MAX_RETRIES=2\`、
\`DEEPSEEK_BACKOFF_BASE_MS=600\`、\`DEEPSEEK_BACKOFF_MAX_MS=8000\`、\`DEEPSEEK_BACKOFF_JITTER=0.25\`、
\`DEEPSEEK_TOKEN_REFRESH_COOLDOWN=60\`。

## 四、验证结果

1. \`python MODIFIED_FILE.py --selftest\` → \`SELFTEST ALL_OK\`（新增 tool_call_layer / token_envelope / upstream_pacing 断言）。
2. 进程内流式集成测试：跨 SSE delta 的 \`<tool_call>\` + DSML invoke + 畸形块 → \`CONTENT='你好  完成  正文'\`、2 个 tool_calls、畸形块进 \`FOLDED\`，普通轮次回归通过。
3. 重启后 live：
   - POST 无 tools → \`401 {"type":"authentication_error","upstream":"…sign-in refused (INVALID_TOKEN)"}\`（2.0s，含一次浏览器重扫）；不再是误导性的 502 \`chat_session id missing\`。
   - POST 带 tools → 同样 401（0.3s，冷却生效），工具路径无崩溃。
   - \`/health\` → \`token_status:"expired"\`、\`upstream_pacing:{max_concurrency:2,min_interval_ms:800,max_retries:2}\`。
4. \`set_token\` 热替换（文件 + env + \`get_token\`）单测通过；gate 三次 acquire 实测 0.302s ≥ 0.30s；浏览器扫描找到 3 个候选。

## 五、仍未移植的插件能力（建议按此顺序补）

1. **回合状态机**（差距最大）：插件 \`turn.mjs\` 对 \`INCOMPLETE\` 走 \`/chat/continue\`、空 \`FINISHED\` 走 \`/chat/resume_stream\`、非 SSE 的 biz_code 22 整包解包。你目前截断就吐「（空响应）」。建议把 \`_collect_events\` 之后加一层状态机，最多 20 次续写。
2. **\`deepseek-reasoner\` 走 \`expert\` wire**：插件把 chat→\`default\`、reasoner→\`expert\` 直接映射；你的 \`resolve_model_type\` 把 14 个模型全折回 \`default\`，只开 \`x-thinking-enabled\`。建议至少给 reasoner 单独映射 \`expert\`。
3. **附件**：插件 \`attachments.mjs\` 的 \`upload_file\` multipart + \`fetch_files\` 轮询 + \`ref_file_ids\`，并处理「附件轮次服务端强制关思考」；你的 \`ref_file_ids\` 恒为 \`[]\`。
4. **会话失效自愈**：插件聊天被删/过期自动开新 chat 带全量标注历史重试一次；你的 \`SESSION_MAP\` 只在内存里，无 stale-session 重试。
5. **\`finishReason(status)\`**：按 \`CONTENT_FILTER\` / \`CONTEXT_LENGTH_EXCEEDED\` / \`TIMEOUT\` 映射 \`content_filter\` / \`length\`，你目前一律 \`stop\`。

## 六、重要提醒

本机 Chrome/Edge 里目前**没有任何活的 chat.deepseek.com 令牌**（6 个候选全部 \`INVALID_TOKEN\`）。
自动重扫 + 热替换的代码路径已经验证可用，但要真正恢复出 200，仍需先在浏览器登录
https://chat.deepseek.com （或 \`POST /_set_token\` 粘贴新的 userToken）—— 之后**无需重启**，
代理会自动验活并热替换。

---

# 第二批补丁 — 2026-11-10（继续修改）

基线仍为 \`MODIFIED_FILE.py.bak-toolcall-token-gate-20261110\`（1970 行）。
累计结果：3202 行 / 123846 字节（+1441 / −209）。\`--selftest\` 全部通过。

## ① 回合状态机（差距最大，已补）

新增 \`deepseek_turn()\`（对照插件 \`turn.mjs\`），并改造 \`DSClient.request_turn()\`：

| 场景 | 插件 | 本次实现 |
|---|---|---|
| \`status=INCOMPLETE\` | 发 \`/chat/continue\`（\`{chat_session_id,message_id,fallback_to_resume:true}\`），最多 20 次 | \`deepseek_turn\` 循环，\`DEEPSEEK_MAX_CONTINUES\` 默认 20 |
| \`status=FINISHED\` 但正文/思考全空 | 发 \`/chat/resume_stream\`（\`{chat_session_id,message_id}\`）重读一次 | 同左，仅一次 |
| 非 SSE 应答（\`biz_code 22\` 整包） | \`json(..., {allow:[22]})\` 后取 \`biz.response\` | \`check_envelope(..., allow=[22])\` + \`DeltaState\` 整包注入 |
| 两个续写端点不带 PoW | 只有 \`/chat/completion\` 要 PoW | \`request_turn\` 按 endpoint 决定是否 \`x-ds-pow-response\` |

关键细节：**续写必须共享同一个 \`DeltaState\`**，否则第二段要么重复要么丢失。为此
\`deepseek_events()\` 增加 \`state\` 参数，并在每次进入时重置 \`parser.path/op\`
（不重置会因上一条 \`response/status\` 的补丁地址残留，把整包 SET 写到错误路径——测试时实测到并修掉）。
\`_handle_completion\` 先取到首个 \`response\`，再交给 \`deepseek_turn\` 续写，避免 SSE 头发出后才发现错误。

## ② deepseek-reasoner → expert wire（已补）

新增 \`wire_model_for()\` / \`EXPERT_MODEL_IDS\`：\`deepseek-reasoner / deepseek-r1 / deepseek-thinking → expert\`，
其余别名 → \`default\`（与插件 chat→default、reasoner→expert 一致）。\`resolve_model_type\` 改用该映射，
\`thinking_for\` 同步复用 \`EXPERT_MODEL_IDS\`。

## ③ 附件（已补）

新增（对照插件 \`attachments.mjs\`）：
- \`decode_data_url()\` / \`filename_for()\` / \`attachment_from_part()\`：支持 \`image_url\`（data URL 或 http(s) 下载）
  与 \`file\`（\`file_data\`/\`data\`，data URL 或裸 base64）。
- \`extract_attachments(messages, from_index)\`：构造轮只取本轮新增消息上的附件。
- \`DSClient.upload_file()\`：multipart 到 \`/api/v0/file/upload_file\`，带它自己的 PoW +
  \`x-thinking-enabled:0\` / \`x-model-type:default\` / \`x-file-size\`。
- \`DSClient.file_status()\` / \`wait_parsed()\`：轮询 \`/api/v0/file/fetch_files\`（400ms × 15s）。
- \`prepare_files()\`：上传→等待→\`ref_file_ids\`；失败/不可读的附件转成提示语拼进 prompt。
- \`_handle_completion\`：有 \`ref_file_ids\` 时强制 \`model_type=default\` 且 \`thinking=False\`（网页端规则）。

## ④ 会话失效自愈（已补）

新增 \`StaleSession\` 异常：\`request_turn\` 对 4xx 且响应体含 \`session/chat_session/not found\` 时抛出。
\`_handle_completion\` 在**流式开始前**捕获，若本轮是延续轮（已有 session）则清空映射 → 自动新开 chat →
用全量标注历史重发一次（\`continuation=False\`）；失败才回 502。与插件「自动开新聊天携带全量历史重试一次」一致。

## ⑤ finishReason(status)（已补）

新增 \`finish_reason_for(status)\`：\`CONTENT_FILTER→content_filter\`，
\`CONTEXT_LENGTH_EXCEEDED/INCOMPLETE/TIMEOUT→length\`，其余 \`stop\`。\`_aggregate_completion\` 与
\`_stream_completion\` 的收尾块都改用该映射（工具调用仍为 \`tool_calls\`）。

## 验证

1. \`--selftest\`：新增 \`turn_state_machine / finish_reason_map / wire_model_map / attachment_parse\`，
   含 INCOMPLETE→continue、空 FINISHED→resume_stream、biz_code 22 三条完整断言 → \`ALL_OK\`。
2. 独立集成测试：A 续写文本 \`第一段第二段\`（无重复）、B resume \`补读\`、C 整包 \`整包回答\`、
   D 映射、E wire、F 附件解析、G \`StaleSession\` 正/误判 → 全通过。
3. \`prepare_files\`：上传失败转提示、解析失败剔除 → 通过。
4. 重启后 live 冒烟：普通 / reasoner 流式 / 带图片 三种请求均返回干净的 \`401 authentication_error\`
   （令牌已死），无崩溃、无 500；\`/health\` 正常。

## 仍受限于令牌

五项能力均已按插件对齐并通过离线/单元/集成测试；唯一没法在本机跑出 200 的原因仍是没有活的
chat.deepseek.com 令牌（Chrome/Edge 里 6 个候选全 \`INVALID_TOKEN\`）。登录后无需重启，代理会自动验活热替换。
