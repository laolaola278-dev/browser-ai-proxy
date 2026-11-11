#!/usr/bin/env node
// QQ Browser Yuanbao (QBot) -> OpenAI-compatible local proxy.
// Direct HTTP replay of InitSession + qbot/qa SSE. Verified 2026-11-11.
// A fresh random device fingerprint per start avoids the guest rate-limit / login wall.
import http from "node:http";
import crypto from "node:crypto";

const NL = String.fromCharCode(10);
const PORT = Number(process.env.YB_PORT || 8966);
const TIMEOUT_MS = Number(process.env.YB_TIMEOUT || 90000);
const QUA = process.env.YB_QUA || "PR=PC&CO=WBK&QV=3&PL=WIN&PB=GE&PPVN=22.3.7007.400&COVC=0413800&CHID=10026236&RL=2560*1440&MO=30DJSE0S00&VE=GA&OS=10.0.19045&SYSARCH=Intel&RT=64";
const QIMEI_HDR = "q-qimei36";
const sleep = ms => new Promise(r => setTimeout(r, ms));
let GUID = "", QIMEI = "", USERINFO = "", COOKIE = "";
function rotateDevice() {
  GUID = process.env.YB_GUID || crypto.randomBytes(16).toString("hex");
  QIMEI = process.env.YB_QIMEI || crypto.randomBytes(16).toString("hex");
  USERINFO = Buffer.from(JSON.stringify({ guid: GUID, qua: QUA, qimei36: QIMEI, qbid: "" })).toString("base64");
  COOKIE = "QIMEI36=" + QIMEI + "; qb_qua=" + QUA + "; Q-H5-GUID=" + GUID + "; platform=WIN; Q-H5-USERTYPE=4; Q-H5-TOKENTYPE=7; Q-H5-TOKEN-TYPE=7; Q-H5-AUTH-TYPE=1";
  console.log(new Date().toISOString().slice(11, 19), "device guid=" + GUID.slice(0, 8) + "...");
}
rotateDevice();
const ORIGIN = "chrome-extension://emcjdpbjojjmkecgjbbhnpaegpfeboch";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 QQBrowser/22.3.7007.400";
const INIT_URL = "https://qbh5.qq.com/h5/v2/mtt_qb_qa_coordinator_proxy/InitSession";
const QA_URL = "https://qbqaproxy.imtt.qq.com/cgi-bin/gpt/qbot/qa";

const UI_MODELS = [
  { id: "smart", value: "", name: "智能（自动匹配）", official: true },
  { id: "deepseek", value: "deepseek", name: "DeepSeek（深度思考）", official: true },
  { id: "hunyuan", value: "hunyuan", name: "Hy3（全能模型·新升级）", official: true }
];
const INTERNAL = ["deepseek3.2","glm5","kimi2.5","qwen3","minimax2.5","deepseek3.1","hy3.0","hy3.0lowthinking","hy3.0hthinking","deepseekv4"];
const MODELS = UI_MODELS.concat(INTERNAL.map(x => ({ id: x, value: x, name: x + "（内部/实验）", official: false })));

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

function mapModel(m) {
  if (!m || m === "smart" || m === "auto" || m === "Qwen" || m === "qbot") return "";
  const hit = MODELS.find(x => x.id === m);
  return hit ? hit.value : String(m);
}
function buildPrompt(messages, prompt) {
  if (typeof prompt === "string" && prompt) return prompt;
  if (typeof messages === "string") return messages;
  const parts = [];
  for (const m of (messages || [])) {
    const role = m.role || "user";
    const content = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
    parts.push(role + ": " + content);
  }
  return parts.join(NL + NL);
}

async function initSession() {
  const headers = {
    "Content-Type": "application/json",
    [QIMEI_HDR]: QIMEI,
    "q-qua2": QUA, "q-guid": GUID, "q-openid": "", "q-appid": "",
    "q-qbaccess-token": "", "q-account-type": "4", "q-qbid": "", "q-token": "",
    "q-ua2": QUA, Origin: ORIGIN, "User-Agent": UA, Cookie: COOKIE
  };
  const r = await fetch(INIT_URL, {
    method: "POST", headers,
    body: JSON.stringify({ user_base_info: { guid: GUID, qimei36: QIMEI, qua2: QUA }, intent: "" }),
    signal: AbortSignal.timeout(20000)
  });
  const t = await r.text();
  if (r.headers.get("trpc-func-ret") && r.headers.get("trpc-func-ret") !== "0") throw new Error("GUEST_BLOCKED");
  let j = {}; try { j = JSON.parse(t); } catch (e) {}
  if (!j.session_id) throw new Error("InitSession no session_id: " + t.slice(0, 200));
  return j.session_id;
}

async function chatOnce(prompt, model) {
  const sid = await initSession();
  const r = await fetch(QA_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json", userinfo: USERINFO, accept: "text/event-stream", Origin: ORIGIN, Cookie: COOKIE, "q-guid": GUID, "q-ua2": QUA },
    body: JSON.stringify({ SessionID: sid, Intent: "", ExtendParams: { coordinator_user: "true", pluginVersion: "0.22.1", env: "", model: model }, Question: prompt }),
    signal: AbortSignal.timeout(Math.min(TIMEOUT_MS, 30000))
  });
  if (!r.ok) throw new Error("qbot/qa HTTP " + r.status + ": " + (await r.text()).slice(0, 200));
  const t = await r.text();
  let answer = "", frames = 0;
  for (const p of t.split(/\n\n/)) {
    if (!p.trim()) continue;
    frames++;
    const ls = p.split(/\n/);
    const ev = (ls.find(l => l.indexOf("event:") === 0) || "").slice(6).trim();
    const dl = ls.find(l => l.indexOf("data:") === 0);
    if (!dl) continue;
    const d = dl.slice(5).trim();
    if (ev === "MESSAGE" || (!ev && d.indexOf("{") === 0)) {
      let j = null; try { j = JSON.parse(d); } catch (e) {}
      if (j && typeof j.Text === "string") answer += j.Text;
    }
  }
  return { answer, frames, sessionId: sid };
}

async function chat(prompt, model) {
  for (let i = 0; i < 4; i++) {
    try { return await chatOnce(prompt, model); }
    catch (e) {
      if ((e.message === "GUEST_BLOCKED" || /no session_id|timeout|aborted/i.test(e.message)) && i < 3) {
        rotateDevice(); await sleep(400); continue;
      }
      throw e;
    }
  }
  throw new Error("all device rotations exhausted");
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let b = "";
    req.on("data", c => { b += c; if (b.length > 1000000) reject(new Error("body too large")); });
    req.on("end", () => resolve(b));
    req.on("error", reject);
  });
}
function json(res, code, obj) { res.writeHead(code, { "Content-Type": "application/json; charset=utf-8" }); res.end(JSON.stringify(obj)); }
function fail(res, code, msg) { if (res.headersSent) { res.end(); return; } json(res, code, { error: { message: msg, type: "yuanbao_proxy_error", code } }); }

const server = http.createServer(async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  if (req.method === "OPTIONS") { res.writeHead(204); res.end(); return; }
  const url = (req.url || "").split("?")[0];
  try {
    if (req.method === "GET" && url === "/health") return json(res, 200, { ok: true, port: PORT, models: MODELS.length, browserRequired: false, deviceGuid: GUID.slice(0, 8) + "..." });
    if (req.method === "GET" && (url === "/v1/models" || url === "/models")) return json(res, 200, { object: "list", data: MODELS.map(m => ({ id: m.id, object: "model", owned_by: m.official ? "yuanbao" : "yuanbao-internal", name: m.name, experimental: !m.official })) });
    if (req.method === "POST" && (url === "/v1/chat/completions" || url === "/chat/completions")) {
      const raw = await readBody(req);
      let body = {}; try { body = JSON.parse(raw || "{}"); } catch (e) { return fail(res, 400, "invalid JSON body"); }
      const prompt = buildPrompt(body.messages, body.prompt);
      if (!prompt) return fail(res, 400, "messages or prompt is required");
      const model = mapModel(body.model);
      const out = await chat(prompt, model);
      const id = "chatcmpl-" + Date.now().toString(36);
      const created = Math.floor(Date.now() / 1000);
      if (body.stream) {
        res.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-cache" });
        const base = { id, object: "chat.completion.chunk", created, model: body.model || "smart" };
        res.write("data: " + JSON.stringify(Object.assign({}, base, { choices: [{ index: 0, delta: { role: "assistant", content: out.answer }, finish_reason: null }] })) + NL + NL);
        res.write("data: " + JSON.stringify(Object.assign({}, base, { choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })) + NL + NL);
        res.write("data: [DONE]" + NL + NL);
        return res.end();
      }
      return json(res, 200, { id, object: "chat.completion", created, model: body.model || "smart", choices: [{ index: 0, message: { role: "assistant", content: out.answer }, finish_reason: "stop" }], usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, yuanbao: { sessionId: out.sessionId, frames: out.frames } });
    }
    return fail(res, 404, "not found: " + url);
  } catch (e) {
    log("request failed:", e.message);
    return fail(res, 502, e.message);
  }
});

server.listen(PORT, "127.0.0.1", () => {
  log("Yuanbao proxy ready at http://127.0.0.1:" + PORT + " (direct HTTP, no browser)");
  log("GET /health | GET /v1/models | POST /v1/chat/completions");
});
process.on("SIGINT", () => { log("stopping"); server.close(() => process.exit(0)); });
