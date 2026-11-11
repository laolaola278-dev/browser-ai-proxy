#!/usr/bin/env node
// Unified Browser-AI proxy: Quark Qwen (quarkchat) + QQ Browser Yuanbao (QBot).
// Single process, no browser / CDP / login needed. One OpenAI-compatible endpoint.
import http from "node:http";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

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
// ---------- grouped gateways, health, stats ----------
const GROUPS = CFG.groups || {};
for (const [k, g] of Object.entries(GROUPS)) { g.enabled = g.enabled !== false; g.live = false; g.modelCount = g.models || 0; g.modelCache = []; }
const STATS = { startedAt: Date.now(), total: 0, ok: 0, fail: 0, sumMs: 0, tokens: 0, byGroup: {}, byModel: {}, buckets: {}, requests: [] };
function estTokens(x) { return Math.max(0, Math.round(String(x == null ? "" : x).length / 4)); }
function record(r) {
  STATS.total++; if (r.ok) STATS.ok++; else STATS.fail++;
  STATS.sumMs += r.ms || 0; STATS.tokens += (r.in || 0) + (r.out || 0);
  STATS.byGroup[r.group] = (STATS.byGroup[r.group] || 0) + 1;
  STATS.byModel[r.model] = (STATS.byModel[r.model] || 0) + 1;
  const b = Math.floor(Date.now() / 60000) * 60000; STATS.buckets[b] = (STATS.buckets[b] || 0) + 1;
  STATS.requests.unshift(r); if (STATS.requests.length > 200) STATS.requests.pop();
}
function upstreamHeaders(g) { const h = { "Content-Type": "application/json" }; if (g.key) h.Authorization = "Bearer " + g.key; return h; }
async function checkGroup(id, g) {
  if (g.type === "builtin") { g.live = true; return; }
  try { const r = await fetch(g.health || (g.baseURL.replace(/\/v1$/, "") + "/health"), { signal: AbortSignal.timeout(2500) }); g.live = r.ok; try { g.healthInfo = await r.json(); } catch (e) {} } catch (e) { g.live = false; }
  try {
    const r = await fetch(g.baseURL.replace(/\/$/, "") + "/models", { headers: upstreamHeaders(g), signal: AbortSignal.timeout(4000) });
    const j = await r.json(); const list = j.data || j.models || [];
    g.modelCache = list.map(m => typeof m === "string" ? m : (m.id || m.name || "")).filter(Boolean);
    g.modelCount = g.modelCache.length;
  } catch (e) {}
}
function checkAll() { for (const [id, g] of Object.entries(GROUPS)) checkGroup(id, g).catch(() => {}); }
checkAll(); setInterval(checkAll, 15000).unref();
function allModels() {
  let out = MODELS.map(m => ({ id: m.id, object: "model", owned_by: m.group, name: m.name, group: m.group }));
  for (const [id, g] of Object.entries(GROUPS)) {
    if (g.type === "builtin" || !g.enabled) continue;
    for (const mid of g.modelCache) out.push({ id: id + "/" + mid, object: "model", owned_by: id, name: mid, group: id });
  }
  return out;
}
function statsPayload() {
  const secs = Math.max(1, (Date.now() - STATS.startedAt) / 1000);
  const heat = []; for (let i = 97; i >= 0; i--) { const b = Math.floor(Date.now() / 60000) * 60000 - i * 60000; heat.push(STATS.buckets[b] || 0); }
  return { port: PORT, uptime: Math.round(secs) + "s", total: STATS.total, ok: STATS.ok, fail: STATS.fail,
    tps: Math.round(STATS.tokens / secs), avgMs: STATS.total ? Math.round(STATS.sumMs / STATS.total) : 0,
    byGroup: STATS.byGroup, byModel: STATS.byModel, heat, groups: Object.keys(GROUPS).length };
}
function parseUpstream(text, contentType) {
  let answer = "", usage = null;
  if ((contentType || "").indexOf("text/event-stream") >= 0 || text.indexOf("data:") === 0) {
    for (const p of text.split(/\n\n/)) {
      const line = p.split(/\n/).find(l => l.indexOf("data:") === 0); if (!line) continue;
      const d = line.slice(5).trim(); if (d === "[DONE]") continue;
      let j = null; try { j = JSON.parse(d); } catch (e) { continue; }
      const c = j.choices && j.choices[0] && (j.choices[0].delta || j.choices[0].message);
      if (c && typeof c.content === "string") answer += c.content;
      if (j.usage) usage = j.usage;
    }
  } else {
    let j = null; try { j = JSON.parse(text); } catch (e) {}
    if (j) { const c = j.choices && j.choices[0] && j.choices[0].message; if (c) answer = c.content || ""; usage = j.usage || null; }
    else answer = text.slice(0, 2000);
  }
  return { answer, usage };
}
async function upstreamChat(g, body, upModel) {
  const url = g.baseURL.replace(/\/$/, "") + "/chat/completions";
  const payload = Object.assign({}, body, { model: upModel, stream: false });
  const r = await fetch(url, { method: "POST", headers: upstreamHeaders(g), body: JSON.stringify(payload), signal: AbortSignal.timeout(60000) });
  const text = await r.text();
  if (!r.ok) throw new Error("UPSTREAM " + r.status + ": " + text.slice(0, 160));
  const out = parseUpstream(text, r.headers.get("content-type"));
  return { answer: out.answer, usage: out.usage, frames: 0, reqId: "" };
}
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
  const hit = MODELS.find(x => x.id === m);
  if (hit) { const g = GROUPS[hit.provider] || {}; return { group: hit.provider, up: hit.up, id: hit.id, builtin: g.type === "builtin", g }; }
  const parts = String(m || "").split("/");
  const gid = parts[0], rest = parts.slice(1).join("/");
  const g = GROUPS[gid];
  if (g && g.enabled) return { group: gid, up: rest, id: String(m), builtin: g.type === "builtin", g };
  const bare = MODELS.find(x => x.id.split("/")[1] === String(m));
  if (bare) { const bg = GROUPS[bare.provider] || {}; return { group: bare.provider, up: bare.up, id: bare.id, builtin: bg.type === "builtin", g: bg }; }
  const d = MODELS.find(x => x.provider === (CFG.defaultProvider || "quark"));
  return { group: d.provider, up: d.up, id: d.id, builtin: true, g: GROUPS[d.provider] || {} };
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
    if (url === "/api/providers") return json(res, 200, { providers: Object.entries(GROUPS).map(([id, g]) => ({ id, name: g.label || id, type: g.type, models: g.modelCount, enabled: g.enabled, live: g.live, key: g.key ? g.key.slice(0, 4) + "..." + g.key.slice(-4) : "", base: g.baseURL || g.endpoint || "", healthInfo: g.healthInfo || null })) });
    if (url === "/api/usage") return json(res, 200, statsPayload());
    if (url === "/api/usage/requests") return json(res, 200, { rows: STATS.requests.slice(0, 50) });
    if (url === "/api/state") return json(res, 200, { groups: GROUPS, stats: statsPayload() });
    if (url === "/api/requests.csv" || url === "/api/usage/requests.csv") {
      const cols = ["time", "agent", "group", "model", "actual", "in", "out", "ms", "speed", "cost", "ok"];
      const csv = [cols.join(",")].concat(STATS.requests.map(r => cols.map(c => JSON.stringify(r[c] == null ? "" : r[c])).join(","))).join("\n");
      res.writeHead(200, { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": "attachment; filename=requests.csv" }); return res.end(csv);
    }
    if (url === "/api/status") return json(res, 200, { groups: GROUPS });
    if (url === "/api/models") return json(res, 200, { object: "list", data: allModels() });
    if (url === "/api/stats") return json(res, 200, statsPayload());
    if (url === "/api/requests") return json(res, 200, { requests: STATS.requests.slice(0, 50) });
    if (url.indexOf("/api/groups/") === 0) {
      const seg = url.split("/"); const gid = decodeURIComponent(seg[3] || ""); const act = seg[4]; const g = GROUPS[gid];
      if (!g) return fail(res, 404, "unknown group " + gid);
      if (act === "toggle") { g.enabled = !g.enabled; return json(res, 200, { group: gid, enabled: g.enabled }); }
      if (act === "start") {
        const L = g.launch;
        if (L && L.script) {
          if (L.port) { try { const rr = await fetch("http://127.0.0.1:" + L.port + "/health", { signal: AbortSignal.timeout(1500) }); if (rr.ok) return json(res, 200, { already: true, group: gid, port: L.port }); } catch (e) {} }
          const cwd = path.join(HERE, L.cwd || ".");
          const env = Object.assign({}, process.env, L.env || {});
          const python = L.python || process.env.BAI_PYTHON || "python";
          try {
            const ch = spawn(python, [path.join(cwd, L.script)].concat(L.args || []), { cwd, env, detached: true, stdio: "ignore", windowsHide: true });
            ch.on("error", e => log("launch error " + gid + ": " + e.message));
            ch.unref();
            return json(res, 200, { started: gid, script: L.script, port: L.port || null, cwd: L.cwd });
          } catch (e) { return fail(res, 500, e.message); }
        }
        if (g.startCmd) {
          try { spawn("cmd.exe", ["/c", "start", "", g.startCmd], { detached: true, stdio: "ignore" }).unref(); return json(res, 200, { started: gid, cmd: g.startCmd }); }
          catch (e) { return fail(res, 500, e.message); }
        }
        return fail(res, 400, "no launch/startCmd for " + gid);
      }
      return fail(res, 404, "unknown action");
    }
    if (url === "/" || url === "/index.html" || url === "/panel.html") {
      try { const f = fs.readFileSync(path.join(HERE, "panel.html")); res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }); return res.end(f); }
      catch (e) { return fail(res, 500, e.message); }
    }
    if (req.method === "GET" && url === "/health") return json(res, 200, { ok: true, port: PORT, providers: ["quark", "yuanbao"], models: MODELS.length, browserRequired: false, ticketRefreshRequired: false });
    if (req.method === "GET" && (url === "/v1/models" || url === "/models")) return json(res, 200, { object: "list", data: MODELS.map(m => ({ id: m.id, object: "model", owned_by: m.group, name: m.name })) });
    if (req.method === "POST" && (url === "/v1/chat/completions" || url === "/chat/completions")) {
      const raw = await readBody(req); let body = {}; try { body = JSON.parse(raw || "{}"); } catch (e) { return fail(res, 400, "invalid JSON body"); }
      const prompt = buildPrompt(body.messages, body.prompt); if (!prompt) return fail(res, 400, "messages or prompt is required");
      const m = resolve(body.model);
      const t0 = Date.now(); let out;
      try {
        if (m.builtin) out = m.group === "quark" ? await quarkChat(prompt, m.up) : await yuanbaoChat(prompt, m.up);
        else out = await upstreamChat(m.g, body, m.up || m.id.split("/").slice(1).join("/"));
      } catch (e) { record({ time: new Date().toISOString().slice(11, 19), agent: (body.user || req.headers["x-agent"] || "unknown"), group: m.group, provider: m.group, model: m.id, actual: m.up || m.id, in: estTokens(prompt), out: 0, ms: Date.now() - t0, ok: false, stream: !!body.stream, cost: 0, ttft: 0, speed: 0 }); throw e; }
      const _ms = Date.now() - t0, _in = estTokens(prompt), _out = estTokens(out.answer);
      record({ time: new Date().toISOString().slice(11, 19), agent: (body.user || req.headers["x-agent"] || "unknown"), group: m.group, provider: m.group, model: m.id, actual: m.up || m.id, in: _in, out: _out, ms: _ms, ok: true, stream: !!body.stream, cost: 0, ttft: 0, speed: Math.round(_out / Math.max(1, _ms / 1000)) });
      const id = "chatcmpl-" + Date.now().toString(36); const created = Math.floor(Date.now() / 1000);
      const usage = out.usage || { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
      if (body.stream) {
        res.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" });
        const base = { id, object: "chat.completion.chunk", created, model: m.id, provider: m.group };
        res.write("data: " + JSON.stringify(Object.assign({}, base, { choices: [{ index: 0, delta: { role: "assistant", content: out.answer }, finish_reason: null }] })) + NL + NL);
        res.write("data: " + JSON.stringify(Object.assign({}, base, { choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })) + NL + NL);
        res.write("data: [DONE]" + NL + NL); return res.end();
      }
      return json(res, 200, { id, object: "chat.completion", created, model: m.id, provider: m.group, choices: [{ index: 0, message: { role: "assistant", content: out.answer }, finish_reason: "stop" }], usage });
    }
    return fail(res, 404, "not found: " + url);
  } catch (e) { log("request failed:", e.message); return fail(res, 502, e.message); }
});
server.listen(PORT, "127.0.0.1", () => { log("Unified Browser-AI proxy ready at http://127.0.0.1:" + PORT); log("providers: quark (6 models) + yuanbao (13 models) | GET /v1/models | POST /v1/chat/completions"); });
process.on("SIGINT", () => { log("stopping"); server.close(() => process.exit(0)); });
