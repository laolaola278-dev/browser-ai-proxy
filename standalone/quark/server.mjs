#!/usr/bin/env node
// Quark Qwen -> OpenAI-compatible proxy. DIRECT HTTP replay of /api/v2/chat.
// No browser / CDP / login needed for normal use: a captured WG ticket is reused.
// Quark occasionally rejects with 403 EX015 (signature error); we retry with fresh IDs.
import http from "node:http";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const NL = String.fromCharCode(10);
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.QW_PORT || 8965);
const TICKET_FILE = process.env.QW_TICKET || path.join(HERE, "ticket.json");
const CFG = JSON.parse(fs.readFileSync(TICKET_FILE, "utf8"));
const UT = process.env.QW_UT || CFG.ut || new URL(CFG.url).searchParams.get("ut");
const BASE = "https://chat-quark2.qianwen.com/api/v2/chat?ut=" + encodeURIComponent(UT) + "&bi=999&fr=win&pf=5001&pr=ucpro&ve=7.4.7.1036&biz_id=ai_chat_v2&fe_version=1.0.0";
const TIMEOUT_MS = Number(process.env.QW_TIMEOUT || 30000);

const MODELS = [
  { id: "Qwen", name: "Qwen-快速（默认）" },
  { id: "tongyi-qwen38-max-release-model", name: "Qwen3.8-Max" },
  { id: "deepseek-v4-pro-model", name: "DeepSeek V4 Pro" },
  { id: "minimax-2.5-model", name: "Minimax 2.5" },
  { id: "kimi-k3-model", name: "Kimi K3（多模态）" },
  { id: "glm-5.2-model", name: "GLM 5.2" }
];
const ALIASES = { "qwen": "Qwen", "qwen-fast": "Qwen", "qwen3.8-max": "tongyi-qwen38-max-release-model", "deepseek-v4-pro": "deepseek-v4-pro-model", "minimax-2.5": "minimax-2.5-model", "kimi-k3": "kimi-k3-model", "glm-5.2": "glm-5.2-model" };
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const rid = () => crypto.randomBytes(16).toString("base64url").slice(0, 22);

function mapModel(m) { if (!m) return "Qwen"; if (MODELS.some(x => x.id === m)) return m; return ALIASES[String(m).toLowerCase()] || "Qwen"; }
function buildPrompt(messages, prompt) {
  if (typeof prompt === "string" && prompt) return prompt;
  if (typeof messages === "string") return messages;
  const parts = [];
  for (const m of (messages || [])) { const role = m.role || "user"; const c = typeof m.content === "string" ? m.content : JSON.stringify(m.content); parts.push(role + ": " + c); }
  return parts.join(NL + NL);
}

function parseAnswer(sse) {
  let a = "", usage = null, frames = 0;
  for (const p of sse.split(/\n\n/)) {
    const line = p.split(/\n/).find(l => l.indexOf("data:") === 0);
    if (!line) continue;
    frames++;
    let j = null; try { j = JSON.parse(line.slice(5).trim()); } catch (e) { continue; }
    for (const m of ((j && j.data && j.data.messages) || [])) {
      if (m.mime_type === "multi_load/iframe" && typeof m.content === "string" && m.content) a = m.content;
    }
    const ex = (j && j.data && j.data.extra_info) || {};
    if (ex.chat_odps && ex.chat_odps.total_usage) usage = ex.chat_odps.total_usage;
  }
  return { answer: a, frames, usage };
}

async function chatOnce(prompt, model) {
  const id = rid();
  const headers = {
    "Content-Type": "application/json",
    "accept": "application/json, text/event-stream, text/plain, */*",
    "prod_id": "tongyi", "Referer": "https://www.qianwen.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 QuarkPC/7.4.7.1036",
    "x-wpk-reqid": id, "x-chat-id": id,
    "x-chat-biz": JSON.stringify({ chatId: id, agentId: "", enableWebp: "", runtimeEnabled: true, debugEnabled: true }),
    "x-wpk-bid": "qianwen-quark", "x-wpk-rel": "4.14.0", "x-wpk-traceid": crypto.randomBytes(16).toString("hex")
  };
  const body = {
    req_id: id, parent_req_id: "0",
    messages: [{ mime_type: "text/plain", content: prompt, meta_data: {}, status: "complete" }],
    scene: "chat", sub_scene: "", scene_param: "first_turn",
    session_id: crypto.randomUUID(), biz_id: "ai_chat_v2", topic_id: rid(),
    model: model, from: "homepage", protocol_version: "v2",
    messages_merge: false, chat_client: "h5", deep_search: "0", bucket: {}, biz_data: "{}"
  };
  const r = await fetch(BASE, { method: "POST", headers, body: JSON.stringify(body), signal: AbortSignal.timeout(TIMEOUT_MS) });
  const t = await r.text();
  if (r.status !== 200) throw new Error("QUARK_REJECT " + r.status + ": " + t.slice(0, 120));
  const out = parseAnswer(t);
  return { ...out, reqId: id };
}
async function chat(prompt, model) {
  let last = null;
  for (let i = 0; i < 5; i++) {
    try { return await chatOnce(prompt, model); }
    catch (e) { last = e; if (/QUARK_REJECT|timeout|aborted/i.test(e.message) && i < 4) { await new Promise(r => setTimeout(r, 250)); continue; } throw e; }
  }
  throw last;
}

function readBody(req) {
  return new Promise((resolve, reject) => { let b = ""; req.on("data", c => { b += c; if (b.length > 1000000) reject(new Error("body too large")); }); req.on("end", () => resolve(b)); req.on("error", reject); });
}
function json(res, code, obj) { res.writeHead(code, { "Content-Type": "application/json; charset=utf-8" }); res.end(JSON.stringify(obj)); }
function fail(res, code, msg) { if (res.headersSent) { res.end(); return; } json(res, code, { error: { message: msg, type: "quark_qwen_proxy_error", code } }); }

const server = http.createServer(async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }
  const url = (req.url || "").split("?")[0];
  try {
    if (req.method === "GET" && url === "/health") return json(res, 200, { ok: true, port: PORT, models: MODELS.length, browserRequired: false, ticketRefreshRequired: false, utSource: TICKET_FILE });
    if (req.method === "GET" && (url === "/v1/models" || url === "/models")) return json(res, 200, { object: "list", data: MODELS.map(m => ({ id: m.id, object: "model", owned_by: "qwen", name: m.name })) });
    if (req.method === "POST" && (url === "/v1/chat/completions" || url === "/chat/completions")) {
      const raw = await readBody(req);
      let body = {}; try { body = JSON.parse(raw || "{}"); } catch (e) { return fail(res, 400, "invalid JSON body"); }
      const prompt = buildPrompt(body.messages, body.prompt);
      if (!prompt) return fail(res, 400, "messages or prompt is required");
      const model = mapModel(body.model);
      const out = await chat(prompt, model);
      const id = "chatcmpl-" + Date.now().toString(36);
      const created = Math.floor(Date.now() / 1000);
      const usage = out.usage || { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
      if (body.stream) {
        res.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" });
        const base = { id, object: "chat.completion.chunk", created, model };
        res.write("data: " + JSON.stringify(Object.assign({}, base, { choices: [{ index: 0, delta: { role: "assistant", content: out.answer }, finish_reason: null }] })) + NL + NL);
        res.write("data: " + JSON.stringify(Object.assign({}, base, { choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })) + NL + NL);
        res.write("data: [DONE]" + NL + NL); return res.end();
      }
      return json(res, 200, { id, object: "chat.completion", created, model, choices: [{ index: 0, message: { role: "assistant", content: out.answer }, finish_reason: "stop" }], usage, quark: { reqId: out.reqId, frames: out.frames } });
    }
    return fail(res, 404, "not found: " + url);
  } catch (e) { log("request failed:", e.message); return fail(res, 502, e.message); }
});

server.listen(PORT, "127.0.0.1", () => { log("Quark Qwen proxy ready at http://127.0.0.1:" + PORT + " (direct HTTP, no browser)"); log("GET /health | GET /v1/models | POST /v1/chat/completions"); });
process.on("SIGINT", () => { log("stopping"); server.close(() => process.exit(0)); });
