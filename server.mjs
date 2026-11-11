#!/usr/bin/env node
// Unified Browser-AI proxy: Quark Qwen (quarkchat) + QQ Browser Yuanbao (QBot).
// Single process, no browser / CDP / login needed. One OpenAI-compatible endpoint.
import http from "node:http";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const NL = String.fromCharCode(10);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const CFG = JSON.parse(fs.readFileSync(process.env.BAI_CONFIG || path.join(HERE, "config.json"), "utf8"));
const PORT = Number(process.env.BAI_PORT || CFG.port || 8976);
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const rid = () => crypto.randomBytes(16).toString("base64url").slice(0, 22);

// ---------- Quark Qwen ----------
const QK_UT = process.env.QW_UT || CFG.quark.ut;
const QK_BASE = "https://chat-quark2.qianwen.com/api/v2/chat?ut=" + encodeURIComponent(QK_UT) + "&bi=999&fr=win&pf=5001&pr=ucpro&ve=7.4.7.1036&biz_id=ai_chat_v2&fe_version=1.0.0";
const QK_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 QuarkPC/7.4.7.1036";
function qkParse(sse) {
  let a = "", usage = null, frames = 0;
  for (const p of sse.split(/\n\n/)) {
    const line = p.split(/\n/).find(l => l.indexOf("data:") === 0); if (!line) continue; frames++;
    let j = null; try { j = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
    for (const m of ((j && j.data && j.data.messages) || [])) if (m.mime_type === "multi_load/iframe" && typeof m.content === "string" && m.content) a = m.content;
    const ex = (j && j.data && j.data.extra_info) || {}; if (ex.chat_odps && ex.chat_odps.total_usage) usage = ex.chat_odps.total_usage;
  }
  return { answer: a, usage, frames };
}
async function qkOnce(prompt, model) {
  const id = rid();
  const headers = {
    "Content-Type": "application/json", "accept": "application/json, text/event-stream, text/plain, */*",
    "prod_id": "tongyi", "Referer": "https://www.qianwen.com/", "User-Agent": QK_UA,
    "x-wpk-reqid": id, "x-chat-id": id,
    "x-chat-biz": JSON.stringify({ chatId: id, agentId: "", enableWebp: "", runtimeEnabled: true, debugEnabled: true }),
    "x-wpk-bid": "qianwen-quark", "x-wpk-rel": "4.14.0", "x-wpk-traceid": crypto.randomBytes(16).toString("hex")
  };
  const body = {
    req_id: id, parent_req_id: "0", messages: [{ mime_type: "text/plain", content: prompt, meta_data: {}, status: "complete" }],
    scene: "chat", sub_scene: "", scene_param: "first_turn", session_id: crypto.randomUUID(),
    biz_id: "ai_chat_v2", topic_id: rid(), model: model, from: "homepage", protocol_version: "v2",
    messages_merge: false, chat_client: "h5", deep_search: "0", bucket: {}, biz_data: "{}"
  };
  const r = await fetch(QK_BASE, { method: "POST", headers, body: JSON.stringify(body), signal: AbortSignal.timeout(30000) });
  const t = await r.text();
  if (r.status !== 200) throw new Error("QUARK_REJECT " + r.status + ": " + t.slice(0, 120));
  return { ...qkParse(t), reqId: id };
}
async function quarkChat(prompt, model) {
  let last = null;
  for (let i = 0; i < 5; i++) { try { return await qkOnce(prompt, model); } catch (e) { last = e; if (/QUARK_REJECT|timeout|aborted/i.test(e.message) && i < 4) { await sleep(250); continue; } throw e; } }
  throw last;
}

// ---------- QQ Browser Yuanbao ----------
const YB_QUA = process.env.YB_QUA || CFG.yuanbao.qua;
const QIMEI_HDR = "q-qimei36";
const YB_INIT = "https://qbh5.qq.com/h5/v2/mtt_qb_qa_coordinator_proxy/InitSession";
const YB_QA = "https://qbqaproxy.imtt.qq.com/cgi-bin/gpt/qbot/qa";
const YB_ORIGIN = "chrome-extension://emcjdpbjojjmkecgjbbhnpaegpfeboch";
const YB_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 QQBrowser/22.3.7007.400";
let YB_GUID = "", YB_QIMEI = "", YB_USERINFO = "", YB_COOKIE = "";
function rotateDevice() {
  YB_GUID = CFG.yuanbao.guid || process.env.YB_GUID || crypto.randomBytes(16).toString("hex");
  YB_QIMEI = CFG.yuanbao.qimei || process.env.YB_QIMEI || crypto.randomBytes(16).toString("hex");
  YB_USERINFO = Buffer.from(JSON.stringify({ guid: YB_GUID, qua: YB_QUA, qimei36: YB_QIMEI, qbid: "" })).toString("base64");
  YB_COOKIE = "QIMEI36=" + YB_QIMEI + "; qb_qua=" + YB_QUA + "; Q-H5-GUID=" + YB_GUID + "; platform=WIN; Q-H5-USERTYPE=4; Q-H5-TOKENTYPE=7; Q-H5-TOKEN-TYPE=7; Q-H5-AUTH-TYPE=1";
  log("yuanbao device " + YB_GUID.slice(0, 8) + "...");
}
rotateDevice();
function ybParse(sse) {
  let a = "", frames = 0;
  for (const p of sse.split(/\n\n/)) {
    const ls = p.split(/\n/); const ev = (ls.find(l => l.indexOf("event:") === 0) || "").slice(6).trim();
    const dl = ls.find(l => l.indexOf("data:") === 0); if (!dl) continue; frames++;
    const d = dl.slice(5).trim();
    if (ev === "MESSAGE" || (!ev && d.indexOf("{") === 0)) { let j = null; try { j = JSON.parse(d); } catch (e) {} if (j && typeof j.Text === "string") a += j.Text; }
  }
  return { answer: a, frames, usage: null };
}
async function ybOnce(prompt, model) {
  const ih = { "Content-Type": "application/json", [QIMEI_HDR]: YB_QIMEI, "q-qua2": YB_QUA, "q-guid": YB_GUID, "q-openid": "", "q-appid": "", "q-qbaccess-token": "", "q-account-type": "4", "q-qbid": "", "q-token": "", "q-ua2": YB_QUA, Origin: YB_ORIGIN, "User-Agent": YB_UA, Cookie: YB_COOKIE };
  const ir = await fetch(YB_INIT, { method: "POST", headers: ih, body: JSON.stringify({ user_base_info: { guid: YB_GUID, qimei36: YB_QIMEI, qua2: YB_QUA }, intent: "" }), signal: AbortSignal.timeout(20000) });
  const it = await ir.text();
  if (ir.headers.get("trpc-func-ret") && ir.headers.get("trpc-func-ret") !== "0") throw new Error("GUEST_BLOCKED");
  let sid = ""; try { sid = JSON.parse(it).session_id || ""; } catch (e) {}
  if (!sid) throw new Error("YB_NO_SESSION " + it.slice(0, 120));
  const qh = { "Content-Type": "application/json", userinfo: YB_USERINFO, accept: "text/event-stream", Origin: YB_ORIGIN, Cookie: YB_COOKIE, "q-guid": YB_GUID, "q-ua2": YB_QUA };
  const qb = { SessionID: sid, Intent: "", ExtendParams: { coordinator_user: "true", pluginVersion: "0.22.1", env: "", model: model }, Question: prompt };
  const qr = await fetch(YB_QA, { method: "POST", headers: qh, body: JSON.stringify(qb), signal: AbortSignal.timeout(Math.min(30000, CFG.timeout || 30000)) });
  const t = await qr.text();
  if (qr.status !== 200) throw new Error("YB_REJECT " + qr.status + ": " + t.slice(0, 120));
  return { ...ybParse(t), reqId: sid };
}
async function yuanbaoChat(prompt, model) {
  let last = null;
  for (let i = 0; i < 4; i++) {
    try { return await ybOnce(prompt, model); }
    catch (e) { last = e; if (/GUEST_BLOCKED|YB_NO_SESSION|timeout|aborted|YB_REJECT/i.test(e.message) && i < 3) { rotateDevice(); await sleep(400); continue; } throw e; }
  }
  throw last;
}

// ---------- model registry & routing ----------
const MODELS = [
  { id: "quark/Qwen", provider: "quark", up: "Qwen", name: "夸克 Qwen-快速（默认）" },
  { id: "quark/tongyi-qwen38-max-release-model", provider: "quark", up: "tongyi-qwen38-max-release-model", name: "夸克 Qwen3.8-Max" },
  { id: "quark/deepseek-v4-pro-model", provider: "quark", up: "deepseek-v4-pro-model", name: "夸克 DeepSeek V4 Pro" },
  { id: "quark/minimax-2.5-model", provider: "quark", up: "minimax-2.5-model", name: "夸克 Minimax 2.5" },
  { id: "quark/kimi-k3-model", provider: "quark", up: "kimi-k3-model", name: "夸克 Kimi K3（多模态）" },
  { id: "quark/glm-5.2-model", provider: "quark", up: "glm-5.2-model", name: "夸克 GLM 5.2" },
  { id: "yuanbao/smart", provider: "yuanbao", up: "", name: "元宝 智能（自动匹配）" },
  { id: "yuanbao/deepseek", provider: "yuanbao", up: "deepseek", name: "元宝 DeepSeek（深度思考）" },
  { id: "yuanbao/hunyuan", provider: "yuanbao", up: "hunyuan", name: "元宝 Hy3（全能·新升级）" },
  { id: "yuanbao/deepseek3.2", provider: "yuanbao", up: "deepseek3.2", name: "元宝 内部 deepseek3.2" },
  { id: "yuanbao/glm5", provider: "yuanbao", up: "glm5", name: "元宝 内部 glm5" },
  { id: "yuanbao/kimi2.5", provider: "yuanbao", up: "kimi2.5", name: "元宝 内部 kimi2.5" },
  { id: "yuanbao/qwen3", provider: "yuanbao", up: "qwen3", name: "元宝 内部 qwen3" },
  { id: "yuanbao/minimax2.5", provider: "yuanbao", up: "minimax2.5", name: "元宝 内部 minimax2.5" },
  { id: "yuanbao/deepseek3.1", provider: "yuanbao", up: "deepseek3.1", name: "元宝 内部 deepseek3.1" },
  { id: "yuanbao/hy3.0", provider: "yuanbao", up: "hy3.0", name: "元宝 内部 hy3.0" },
  { id: "yuanbao/hy3.0lowthinking", provider: "yuanbao", up: "hy3.0lowthinking", name: "元宝 内部 hy3.0lowthinking" },
  { id: "yuanbao/hy3.0hthinking", provider: "yuanbao", up: "hy3.0hthinking", name: "元宝 内部 hy3.0hthinking" },
  { id: "yuanbao/deepseekv4", provider: "yuanbao", up: "deepseekv4", name: "元宝 内部 deepseekv4" }
];
function resolve(m) {
  if (!m || m === "auto") return MODELS.find(x => x.provider === (CFG.defaultProvider || "quark"));
  const hit = MODELS.find(x => x.id === m); if (hit) return hit;
  const bare = MODELS.find(x => x.id.split("/")[1] === m); if (bare) return bare;
  return MODELS.find(x => x.provider === (CFG.defaultProvider || "quark"));
}
function buildPrompt(messages, prompt) {
  if (typeof prompt === "string" && prompt) return prompt;
  if (typeof messages === "string") return messages;
  const parts = [];
  for (const m of (messages || [])) { const role = m.role || "user"; const c = typeof m.content === "string" ? m.content : JSON.stringify(m.content); parts.push(role + ": " + c); }
  return parts.join(NL + NL);
}

// ---------- HTTP ----------
function readBody(req) { return new Promise((resolve, reject) => { let b = ""; req.on("data", c => { b += c; if (b.length > 1000000) reject(new Error("body too large")); }); req.on("end", () => resolve(b)); req.on("error", reject); }); }
function json(res, code, obj) { res.writeHead(code, { "Content-Type": "application/json; charset=utf-8" }); res.end(JSON.stringify(obj)); }
function fail(res, code, msg) { if (res.headersSent) { res.end(); return; } json(res, code, { error: { message: msg, type: "browser_ai_proxy_error", code } }); }
const server = http.createServer(async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }
  const url = (req.url || "").split("?")[0];
  try {
    if (req.method === "GET" && url === "/health") return json(res, 200, { ok: true, port: PORT, providers: ["quark", "yuanbao"], models: MODELS.length, browserRequired: false, ticketRefreshRequired: false });
    if (req.method === "GET" && (url === "/v1/models" || url === "/models")) return json(res, 200, { object: "list", data: MODELS.map(m => ({ id: m.id, object: "model", owned_by: m.provider, name: m.name })) });
    if (req.method === "POST" && (url === "/v1/chat/completions" || url === "/chat/completions")) {
      const raw = await readBody(req); let body = {}; try { body = JSON.parse(raw || "{}"); } catch (e) { return fail(res, 400, "invalid JSON body"); }
      const prompt = buildPrompt(body.messages, body.prompt); if (!prompt) return fail(res, 400, "messages or prompt is required");
      const m = resolve(body.model);
      const out = m.provider === "quark" ? await quarkChat(prompt, m.up) : await yuanbaoChat(prompt, m.up);
      const id = "chatcmpl-" + Date.now().toString(36); const created = Math.floor(Date.now() / 1000);
      const usage = out.usage || { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
      if (body.stream) {
        res.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" });
        const base = { id, object: "chat.completion.chunk", created, model: m.id, provider: m.provider };
        res.write("data: " + JSON.stringify(Object.assign({}, base, { choices: [{ index: 0, delta: { role: "assistant", content: out.answer }, finish_reason: null }] })) + NL + NL);
        res.write("data: " + JSON.stringify(Object.assign({}, base, { choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })) + NL + NL);
        res.write("data: [DONE]" + NL + NL); return res.end();
      }
      return json(res, 200, { id, object: "chat.completion", created, model: m.id, provider: m.provider, choices: [{ index: 0, message: { role: "assistant", content: out.answer }, finish_reason: "stop" }], usage });
    }
    return fail(res, 404, "not found: " + url);
  } catch (e) { log("request failed:", e.message); return fail(res, 502, e.message); }
});
server.listen(PORT, "127.0.0.1", () => { log("Unified Browser-AI proxy ready at http://127.0.0.1:" + PORT); log("providers: quark (6 models) + yuanbao (13 models) | GET /v1/models | POST /v1/chat/completions"); });
process.on("SIGINT", () => { log("stopping"); server.close(() => process.exit(0)); });
