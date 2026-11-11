#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek Web -> OpenAI-compatible proxy.

Reverse-engineered from chat.deepseek.com static assets:
  POST /api/v0/chat_session/create
  POST /api/v0/chat/create_pow_challenge
  POST /api/v0/chat/completion
  GET  /api/v0/client/settings?did=DEVICE_ID&scope=model

Local endpoints:
  GET  /                         -> built-in DeepSeek Web chat page
  GET  /health
  GET  /v1/models
  POST /v1/chat/completions
  POST /_set_token
  GET  /deepseek/health
  GET  /deepseek/v1/models
  POST /deepseek/v1/chat/completions
"""
import argparse
import base64
import glob
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MASK = 0xffffffff
RC = [
  0x00000000,0x00000001, 0x00000000,0x00008082, 0x80000000,0x0000808A,
  0x80000000,0x80008000, 0x00000000,0x0000808B, 0x00000000,0x80000001,
  0x80000000,0x80008081, 0x80000000,0x00008009, 0x00000000,0x0000008A,
  0x00000000,0x00000088, 0x00000000,0x80008009, 0x00000000,0x8000000A,
  0x00000000,0x8000808B, 0x80000000,0x0000008B, 0x80000000,0x00008089,
  0x80000000,0x00008003, 0x80000000,0x00008002, 0x80000000,0x00000080,
  0x00000000,0x0000800A, 0x80000000,0x8000000A, 0x80000000,0x80008081,
  0x80000000,0x00008080, 0x00000000,0x80000001, 0x80000000,0x80008008,
]
V = [10,7,11,17,18,3,5,16,8,21,24,4,15,23,19,13,12,2,20,14,22,9,6,1]
W = [1,3,6,10,15,21,28,36,45,55,2,14,27,41,56,8,25,43,62,18,39,61,20,44]


def js_shl(x, k):
    return (x << (k & 31)) & MASK


def js_shru(x, k):
    return (x & MASK) >> (k & 31)


def js_not(x):
    return (~x) & MASK


class Sponge:
    def __init__(self, capacity=256):
        self.rate = 200 - capacity // 4
        self.outlen = capacity // 8
        self.state = [0] * 50
        self.queue = bytearray(self.rate)
        self.offset = 0

    def _absorb_block(self, q, state):
        for r in range(0, len(q), 8):
            n = r // 4
            w0 = (q[r+7] << 24) | (q[r+6] << 16) | (q[r+5] << 8) | q[r+4]
            w1 = (q[r+3] << 24) | (q[r+2] << 16) | (q[r+1] << 8) | q[r]
            state[n] ^= w0
            state[n+1] ^= w1
            state[n] &= MASK
            state[n+1] &= MASK

    def _theta(self, A, C, D, Wv):
        for t in range(5):
            C[2*t] = A[2*t] ^ A[2*(t+5)] ^ A[2*(t+10)] ^ A[2*(t+15)] ^ A[2*(t+20)]
            C[2*t+1] = A[2*t+1] ^ A[2*(t+5)+1] ^ A[2*(t+10)+1] ^ A[2*(t+15)+1] ^ A[2*(t+20)+1]
            C[2*t] &= MASK
            C[2*t+1] &= MASK
        for t in range(5):
            src = (t+1) % 5
            o = C[2*src]
            f = C[2*src+1]
            Wv[0] = (js_shl(o, 1) | js_shru(f, 31)) & MASK
            Wv[1] = (js_shl(f, 1) | js_shru(o, 31)) & MASK
            prev = (t+4) % 5
            D[2*t] = C[2*prev] ^ Wv[0]
            D[2*t+1] = C[2*prev+1] ^ Wv[1]
            for r in range(0, 25, 5):
                idx = 2*(r+t)
                A[idx] ^= D[2*t]
                A[idx+1] ^= D[2*t+1]
                A[idx] &= MASK
                A[idx+1] &= MASK

    def _rhopi(self, A, C, Wv):
        Wv[0] = A[2*1]
        Wv[1] = A[2*1+1]
        for i in range(24):
            t = V[i]
            a = W[i]
            C[0] = A[2*t]
            C[1] = A[2*t+1]
            o = Wv[0]
            f = Wv[1]
            u = 32 - a
            s = 0 if a < 32 else 1
            Wv[s] = (js_shl(o, a) | js_shru(f, u)) & MASK
            Wv[(s+1) % 2] = (js_shl(f, a) | js_shru(o, u)) & MASK
            A[2*t] = Wv[0]
            A[2*t+1] = Wv[1]
            Wv[0] = C[0]
            Wv[1] = C[1]

    def _chi(self, A, C):
        for y in range(0, 25, 5):
            for x in range(5):
                C[2*x] = A[2*(y+x)]
                C[2*x+1] = A[2*(y+x)+1]
            for x in range(5):
                i = 2*(y+x)
                o = 2*((x+1) % 5)
                f = 2*((x+2) % 5)
                A[i] ^= js_not(C[o]) & C[f]
                A[i+1] ^= js_not(C[o+1]) & C[f+1]
                A[i] &= MASK
                A[i+1] &= MASK

    def _iota(self, A, i):
        A[0] ^= RC[2*i]
        A[1] ^= RC[2*i+1]
        A[0] &= MASK
        A[1] &= MASK

    def _permute(self, A):
        C = [0] * 10
        D = [0] * 10
        Wv = [0] * 2
        for i in range(1, 24):
            self._theta(A, C, D, Wv)
            self._rhopi(A, C, Wv)
            self._chi(A, C)
            self._iota(A, i)

    def absorb(self, data):
        for b in data:
            self.queue[self.offset] = b
            self.offset += 1
            if self.offset >= self.rate:
                self._absorb_block(self.queue, self.state)
                self._permute(self.state)
                self.offset = 0
        return self

    def copy(self):
        o = Sponge()
        o.state = self.state[:]
        o.queue = self.queue[:]
        o.offset = self.offset
        return o

    def squeeze(self, padding=6):
        q = self.queue[:]
        q[self.offset] |= padding
        q[self.rate-1] |= 0x80
        st = self.state[:]
        self._absorb_block(q, st)
        out = bytearray(self.outlen)
        for t in range(0, self.outlen, self.rate):
            self._permute(st)
            for r in range(0, min(self.rate, self.outlen-t), 8):
                n = r // 4
                w1 = st[n+1]
                w0 = st[n]
                out[t+r] = w1 & 255
                out[t+r+1] = (w1 >> 8) & 255
                out[t+r+2] = (w1 >> 16) & 255
                out[t+r+3] = (w1 >> 24) & 255
                out[t+r+4] = w0 & 255
                out[t+r+5] = (w0 >> 8) & 255
                out[t+r+6] = (w0 >> 16) & 255
                out[t+r+7] = (w0 >> 24) & 255
        return bytes(out)


def deepseek_hash(data):
    return Sponge().absorb(data).squeeze(6).hex()


def solve(algorithm, challenge, salt, expire_at, difficulty, max_extra=0):
    if algorithm != "DeepSeekHashV1":
        raise ValueError("Unsupported algorithm: " + str(algorithm))
    prefix = f"{salt}_{expire_at}_"
    base = Sponge().absorb(prefix.encode("utf-8"))
    for i in range(difficulty + max_extra):
        digest = base.copy().absorb(str(i).encode("utf-8")).squeeze(6).hex()
        if digest == challenge:
            return i
    return None


def node_available():
    return os.path.isfile(NODE_EXE) and os.path.isfile(POW_JS)


def hidden_console_kwargs():
    """Windows 上隐藏 PoW 子进程的控制台窗口，避免每次请求闪一下黑框。"""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    }


def solve_node(algorithm, challenge, salt, expire_at, difficulty, max_extra=0):
    if algorithm != "DeepSeekHashV1":
        raise ValueError("Unsupported algorithm: " + str(algorithm))
    prefix = f"{salt}_{expire_at}_"
    count = int(difficulty) + int(max_extra)
    proc = subprocess.run(
        [NODE_EXE, POW_JS, "solve", prefix, str(challenge), "0", str(count)],
        capture_output=True,
        text=True,
        timeout=600,
        **hidden_console_kwargs(),
    )
    for line in proc.stdout.splitlines():
        if line.startswith("ANSWER="):
            value = line[7:].strip()
            if value != "none":
                return int(value)
    raise RuntimeError("Node PoW solver returned no answer: " + proc.stdout.strip()[:200] + " " + proc.stderr.strip()[:200])


COMPLETION_PATH = "/api/v0/chat/completion"
CONTINUE_PATH = "/api/v0/chat/continue"
RESUME_PATH = "/api/v0/chat/resume_stream"
UPLOAD_PATH = "/api/v0/file/upload_file"
STATIC_MODEL_IDS = [
    "deepseek-chat",
    "default",
    "deepseek",
    "deepseek-web",
    "deepseek-v3",
    "deepseek-v3.2",
    "deepseek-v4",
    "deepseek-v4.1",
    "deepseek-v4.1-flash",
    "deepseek-r1",
    "deepseek-reasoner",
    "deepseek-thinking",
    "deepseek-coder",
    "vision",
]

INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DeepSeek 网页版代理</title>
<style>
:root { color-scheme: dark; --bg:#0b0f19; --panel:#111827; --line:#253044; --text:#e5e7eb; --muted:#9ca3af; --accent:#4d7cfe; --user:#1f2a44; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; background:radial-gradient(circle at 20% 0%, #16223b 0%, var(--bg) 46%); color:var(--text); font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif; }
.wrap { max-width:920px; margin:0 auto; padding:28px 18px 40px; }
header { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:18px; }
h1 { font-size:20px; margin:0; letter-spacing:.3px; }
.badge { font-size:12px; color:#bbf7d0; background:#064e3b; border:1px solid #047857; padding:6px 10px; border-radius:999px; }
.panel { background:rgba(17,24,39,.94); border:1px solid var(--line); border-radius:16px; box-shadow:0 20px 60px rgba(0,0,0,.35); overflow:hidden; }
.bar { display:flex; gap:10px; padding:12px; border-bottom:1px solid var(--line); background:rgba(15,23,42,.7); }
select, button, textarea { font:inherit; color:var(--text); background:#0f172a; border:1px solid var(--line); border-radius:10px; }
select { padding:8px 10px; min-width:190px; }
button { padding:8px 14px; cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:white; font-weight:600; }
button:disabled { opacity:.55; cursor:default; }
#messages { height:58vh; min-height:360px; overflow:auto; padding:20px; display:flex; flex-direction:column; gap:14px; }
.msg { max-width:82%; white-space:pre-wrap; line-height:1.65; padding:12px 14px; border-radius:14px; border:1px solid var(--line); }
.msg.user { align-self:flex-end; background:var(--user); border-bottom-right-radius:4px; }
.msg.assistant { align-self:flex-start; background:#111827; border-bottom-left-radius:4px; }
.msg .reasoning { color:#a5b4fc; font-size:13px; border-left:2px solid #6366f1; padding-left:10px; margin-bottom:8px; white-space:pre-wrap; }
.empty { margin:auto; color:var(--muted); text-align:center; line-height:1.8; }
.composer { display:flex; gap:10px; padding:14px; border-top:1px solid var(--line); }
textarea { flex:1; resize:none; height:52px; padding:12px; outline:none; }
textarea:focus, select:focus { border-color:var(--accent); }
footer { color:var(--muted); font-size:12px; margin-top:10px; display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; }
code { color:#93c5fd; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>DeepSeek 网页版代理</h1>
    <span class="badge" id="status">连接中</span>
  </header>
  <div class="panel">
    <div class="bar">
      <select id="model" aria-label="模型"></select>
      <button id="new" type="button">新建会话</button>
      <span id="backend" style="margin-left:auto;color:#9ca3af;font-size:12px;align-self:center"></span>
    </div>
    <div id="messages"><div class="empty" id="empty">输入消息，直接走 chat.deepseek.com 网页版会话<br>接口：<code>/v1/chat/completions</code></div></div>
    <form class="composer" id="form">
      <textarea id="input" placeholder="给 DeepSeek 发消息，Enter 发送，Shift+Enter 换行"></textarea>
      <button class="primary" id="send" type="submit">发送</button>
    </form>
  </div>
  <footer><span>本地网页 + OpenAI 兼容接口</span><span id="conversation"></span></footer>
</div>
<script>
const $ = (id) => document.getElementById(id);
const API = location.pathname.startsWith('/deepseek') ? '/deepseek' : '';
const statusEl = $('status');
const modelEl = $('model');
const messagesEl = $('messages');
const inputEl = $('input');
const sendEl = $('send');
const convEl = $('conversation');
let conversationId = localStorage.getItem('ds_web_proxy_conversation');
function newConversation() {
  conversationId = 'web-' + ((window.crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()));
  localStorage.setItem('ds_web_proxy_conversation', conversationId);
  convEl.textContent = '会话 ' + conversationId;
  messagesEl.innerHTML = '<div class="empty" id="empty">新会话已就绪<br>接口：<code>/v1/chat/completions</code></div>';
}
if (!conversationId) newConversation(); else convEl.textContent = '会话 ' + conversationId;
$('new').onclick = newConversation;
function addOption(value) {
  if (!value || [...modelEl.options].some(o => o.value === value)) return;
  const opt = document.createElement('option');
  opt.value = value;
  opt.textContent = value;
  modelEl.appendChild(opt);
}
function setStatus(text, ok) {
  statusEl.textContent = text;
  statusEl.style.background = ok ? '#064e3b' : '#7f1d1d';
  statusEl.style.borderColor = ok ? '#047857' : '#b91c1c';
  statusEl.style.color = ok ? '#bbf7d0' : '#fecaca';
}
async function loadHealth() {
  try {
    const r = await fetch(API + '/health');
    const j = await r.json();
    $('backend').textContent = j.base || '';
    setStatus(j.service === 'deepseek-web-openai-proxy' ? 'DeepSeek Web 已连接' : '服务状态异常', j.service === 'deepseek-web-openai-proxy' && j.token);
  } catch (e) {
    setStatus('连接失败', false);
  }
}
async function loadModels() {
  ['deepseek-chat','default'].forEach(addOption);
  try {
    const r = await fetch(API + '/v1/models');
    const j = await r.json();
    (j.data || []).forEach(m => addOption(m.id));
    if (![...modelEl.options].some(o => o.value === 'deepseek-chat')) modelEl.value = modelEl.options[0].value;
    else modelEl.value = 'deepseek-chat';
  } catch (e) {
    modelEl.value = 'deepseek-chat';
  }
}
function append(role, text) {
  const empty = $('empty');
  if (empty) empty.remove();
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.textContent = text;
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}
function assistantBubble() {
  const empty = $('empty');
  if (empty) empty.remove();
  const div = document.createElement('div');
  div.className = 'msg assistant';
  const reasoning = document.createElement('div');
  reasoning.className = 'reasoning';
  reasoning.style.display = 'none';
  const content = document.createElement('div');
  div.appendChild(reasoning);
  div.appendChild(content);
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return { reasoning, content };
}
async function send() {
  const text = inputEl.value.trim();
  if (!text || sendEl.disabled) return;
  append('user', text);
  inputEl.value = '';
  const out = assistantBubble();
  sendEl.disabled = true;
  setStatus('生成中', true);
  let reasoningText = '';
  let contentText = '';
  try {
    const r = await fetch(API + '/v1/chat/completions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'x-conversation-id': conversationId },
      body: JSON.stringify({ model: modelEl.value || 'deepseek-chat', messages: [{ role: 'user', content: text }], stream: true })
    });
    if (!r.ok || !r.body) {
      const err = await r.text();
      out.content.textContent = err;
      setStatus('请求失败', false);
      return;
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      buffer += decoder.decode(part.value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6).trim();
        if (data === '[DONE]') continue;
        let obj;
        try { obj = JSON.parse(data); } catch (e) { continue; }
        if (obj.error) { out.content.textContent = JSON.stringify(obj.error); continue; }
        const delta = (obj.choices && obj.choices[0] && obj.choices[0].delta) || {};
        if (delta.reasoning_content) {
          reasoningText += delta.reasoning_content;
          out.reasoning.style.display = 'block';
          out.reasoning.textContent = reasoningText;
        }
        if (delta.content) {
          contentText += delta.content;
          out.content.textContent = contentText;
        }
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }
    }
    setStatus('DeepSeek Web 已连接', true);
  } catch (e) {
    out.content.textContent = String(e);
    setStatus('连接失败', false);
  } finally {
    sendEl.disabled = false;
  }
}
$('form').onsubmit = (e) => { e.preventDefault(); send(); };
inputEl.onkeydown = (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
};
loadHealth();
loadModels();
</script>
</body>
</html>
"""

BASE = os.environ.get("DEEPSEEK_BASE", "https://chat.deepseek.com").rstrip("/")
NODE_EXE = os.environ.get(
    "DEEPSEEK_POW_NODE",
    r"C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe",
)
POW_JS = os.environ.get("DEEPSEEK_POW_JS") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_pow_node.js"
)
TOKEN_FILE = os.environ.get("DEEPSEEK_TOKEN_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "deepseek_token.txt"
)
DEVICE_ID_FALLBACK = uuid.uuid4().hex
SESSION_LOCK = threading.Lock()
SESSION_MAP = {}
OPTIONAL_HEADER_ENV = [
    ("x-ds-challenge-response", "DEEPSEEK_CHALLENGE_RESPONSE"),
    ("x-ds-sse-heartbeat-timeout-secs", "DEEPSEEK_SSE_HEARTBEAT_TIMEOUT_SECS"),
    ("x-settings-token", "DEEPSEEK_SETTINGS_TOKEN"),
    ("x-debug-latest-reminder", "DEEPSEEK_DEBUG_LATEST_REMINDER"),
    ("x-hif-leim", "DEEPSEEK_HIF_LEIM"),
    ("x-hif-dliq", "DEEPSEEK_HIF_DLIQ"),
]


def truthy(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def get_bool(name, default="0"):
    return truthy(os.environ.get(name, default), truthy(default))


def explicit_flag(payload, key):
    for group in ("extra_body", "deepseek_options"):
        opts = payload.get(group)
        if isinstance(opts, dict) and key in opts:
            return truthy(opts.get(key), False)
    if key in payload:
        return truthy(payload.get(key), False)
    return None


def body_flag(payload, key, env_name):
    explicit = explicit_flag(payload, key)
    if explicit is not None:
        return explicit
    return get_bool(env_name, "0")


def thinking_for_payload(payload):
    model = str(payload.get("model") or "")
    reasoning_effort = str(payload.get("reasoning_effort") or "").strip().lower()
    if reasoning_effort not in ("", "none", "off", "0", "false"):
        return True
    explicit = explicit_flag(payload, "thinking_enabled")
    if explicit is not None:
        return explicit
    if model in ("deepseek-reasoner", "deepseek-r1", "deepseek-thinking"):
        return True
    return get_bool("DEEPSEEK_THINKING", "0")


def search_for_payload(payload):
    model = str(payload.get("model") or "")
    explicit = explicit_flag(payload, "search_enabled")
    if explicit is not None:
        return explicit
    if model in ("deepseek-search", "deepseek-web-search"):
        return True
    return get_bool("DEEPSEEK_SEARCH", "0")


def get_int(name, default=0):
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


def get_base():
    return os.environ.get("DEEPSEEK_BASE", BASE).rstrip("/")


def get_token():
    env = os.environ.get("DEEPSEEK_TOKEN", "").strip()
    if env:
        return env
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def set_token(token):
    token = str(token).strip()
    if not token:
        return False
    directory = os.path.dirname(TOKEN_FILE) or "."
    os.makedirs(directory, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    # The runner pins DEEPSEEK_TOKEN into the environment at startup; without
    # this, /_set_token and the browser refresh only change the file while the
    # live process keeps using the stale token (hot swap must cover both).
    os.environ["DEEPSEEK_TOKEN"] = token
    with SESSION_LOCK:
        SESSION_MAP.clear()
    return True


def get_cookie():
    return os.environ.get("DEEPSEEK_COOKIE", "").strip()


def get_device_id():
    return os.environ.get("DEEPSEEK_DEVICE_ID", "").strip() or DEVICE_ID_FALLBACK


def get_device_model():
    return os.environ.get("DEEPSEEK_DEVICE_MODEL", "").strip()


def get_model_type():
    return os.environ.get("DEEPSEEK_MODEL_TYPE", "default").strip() or "default"


def get_client_version():
    return os.environ.get("DEEPSEEK_CLIENT_VERSION", "2.7.1").strip() or "2.7.1"


def get_client_platform():
    return os.environ.get("DEEPSEEK_CLIENT_PLATFORM", "web").strip() or "web"


def get_client_locale():
    return os.environ.get("DEEPSEEK_CLIENT_LOCALE", "zh_CN").strip() or "zh_CN"


def get_bundle_id():
    return os.environ.get("DEEPSEEK_BUNDLE_ID", "com.deepseek.chat").strip() or "com.deepseek.chat"


def get_tz_offset():
    raw = os.environ.get("DEEPSEEK_TIMEZONE_OFFSET", "")
    if raw:
        try:
            return int(raw)
        except Exception:
            pass
    return -time.timezone


def optional_headers():
    out = {}
    for header, env_name in OPTIONAL_HEADER_ENV:
        value = os.environ.get(env_name, "").strip()
        if value:
            out[header] = value
    return out


# ---------------------------------------------------------------------------
# Upstream pacing: bounded concurrency + spacing + exponential backoff.
# The node PoW path finishes in milliseconds, so without a gate a burst of
# chat requests hits chat.deepseek.com almost simultaneously (the web client
# never does that: its own PoW takes 2-5s and inherently throttles it).
# ---------------------------------------------------------------------------

class SignInExpired(RuntimeError):
    """The web account's token was refused; a fresh browser sign-in is needed."""


class StaleSession(RuntimeError):
    """The chat session was deleted or expired; a fresh chat must be opened."""


RETRY_STATUS = {429, 500, 502, 503, 504}
SIGNIN_CODES = {40003}
TOKEN_STATUS = {"state": "unknown", "refreshed_at": 0, "source": "file"}
TOKEN_LOCK = threading.Lock()
TOKEN_LAST_TRY = 0.0
TOKEN_COOLDOWN = float(os.environ.get("DEEPSEEK_TOKEN_REFRESH_COOLDOWN", "60"))


class UpstreamGate:
    def __init__(self, max_concurrency, min_interval, max_retries, base_delay, max_delay, jitter):
        self.max_concurrency = max(1, int(max_concurrency))
        self.sem = threading.BoundedSemaphore(self.max_concurrency)
        self.spacing_lock = threading.Lock()
        self.min_interval = max(0.0, float(min_interval))
        self.last_start = 0.0
        self.max_retries = max(0, int(max_retries))
        self.base_delay = max(0.05, float(base_delay))
        self.max_delay = max(self.base_delay, float(max_delay))
        self.jitter = min(1.0, max(0.0, float(jitter)))

    def acquire(self):
        self.sem.acquire()
        with self.spacing_lock:
            wait = self.min_interval - (time.time() - self.last_start)
            if wait > 0:
                time.sleep(wait)
            self.last_start = time.time()

    def release(self):
        self.sem.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_exc):
        self.release()
        return False

    def backoff(self, attempt):
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        return delay * (1.0 + random.uniform(-self.jitter, self.jitter))


_UPSTREAM_GATE = None
_GATE_INIT_LOCK = threading.Lock()


def upstream_gate():
    global _UPSTREAM_GATE
    if _UPSTREAM_GATE is None:
        with _GATE_INIT_LOCK:
            if _UPSTREAM_GATE is None:
                _UPSTREAM_GATE = UpstreamGate(
                    get_int("DEEPSEEK_MAX_CONCURRENCY", 2),
                    get_int("DEEPSEEK_MIN_INTERVAL_MS", 800) / 1000.0,
                    get_int("DEEPSEEK_MAX_RETRIES", 2),
                    get_int("DEEPSEEK_BACKOFF_BASE_MS", 600) / 1000.0,
                    get_int("DEEPSEEK_BACKOFF_MAX_MS", 8000) / 1000.0,
                    float(os.environ.get("DEEPSEEK_BACKOFF_JITTER", "0.25")),
                )
    return _UPSTREAM_GATE


def upstream_request(method, url, **kwargs):
    """One upstream call, spaced/queued, retried on 429/5xx/transport errors."""
    gate = upstream_gate()
    attempts = gate.max_retries + 1
    last_error = None
    for attempt in range(attempts):
        response = None
        try:
            with gate:
                response = requests.request(method, url, **kwargs)
            retryable = response.status_code in RETRY_STATUS
        except requests.RequestException as exc:
            last_error = exc
            retryable = True
        if not retryable:
            if response is None:
                raise last_error
            return response
        if attempt + 1 >= attempts:
            if response is not None:
                return response
            raise last_error
        if response is not None:
            response.close()
        time.sleep(gate.backoff(attempt))
    if last_error is not None:
        raise last_error
    return response


# ---------------------------------------------------------------------------
# Envelope + sign-in detection, borrowed from the plugin's api.mjs envelope()
# (it checks the top-level code as well as data.biz_code).
# ---------------------------------------------------------------------------

def zero_code(value):
    return value is None or value == 0 or value == "0"


def web_headers(token, accept="application/json, text/plain, */*"):
    return {
        "Authorization": "Bearer " + str(token or ""),
        "Content-Type": "application/json",
        "Accept": accept,
        "User-Agent": os.environ.get(
            "DEEPSEEK_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
        ),
        "Referer": os.environ.get("DEEPSEEK_REFERER", get_base() + "/"),
        "Origin": os.environ.get("DEEPSEEK_ORIGIN", get_base()),
        "x-client-bundle-id": get_bundle_id(),
        "x-client-platform": get_client_platform(),
        "x-client-version": get_client_version(),
        "x-client-locale": get_client_locale(),
        "x-client-timezone-offset": str(get_tz_offset()),
    }


def check_envelope(js, what, allow=None):
    if not isinstance(js, dict):
        raise RuntimeError("DeepSeek " + what + ": unexpected answer")
    allowed = set()
    for item in (allow or []):
        allowed.add(item)
        allowed.add(str(item))
    code = js.get("code")
    msg = str(js.get("msg") or "")
    data = js.get("data")
    biz_code = data.get("biz_code") if isinstance(data, dict) else None
    biz_msg = str(data.get("biz_msg") or "") if isinstance(data, dict) else ""
    upper = (msg + " " + biz_msg).upper()
    if (code in SIGNIN_CODES or str(code) == "40003"
            or "INVALID_TOKEN" in upper or "AUTHORIZATION FAILED" in upper):
        raise SignInExpired(
            "DeepSeek " + what + ": sign-in refused (" + (msg or biz_msg or "invalid token") + ")"
        )

    def passable(value):
        return zero_code(value) or value in allowed or (value is not None and str(value) in allowed)

    if not passable(code) or not passable(biz_code):
        bad = code if not passable(code) else biz_code
        raise RuntimeError(
            "DeepSeek " + what + " refused (code=" + str(bad) + "): " + (biz_msg or msg or "no reason given")
        )
    if isinstance(data, dict):
        return data.get("biz_data") or js.get("biz_data")
    return js.get("biz_data")


# ---------------------------------------------------------------------------
# Browser token recovery: scan Chromium Local Storage leveldb, validate each
# candidate against /api/v0/users/current (the plugin's whoAmI), hot-swap the
# live one into the token file AND the process env. Browser files are copied to
# a temp dir first: Chrome holds them open with an exclusive lock.
# ---------------------------------------------------------------------------

TOKEN_KEY_ASCII = b"userToken"
TOKEN_KEY_UTF16 = "userToken".encode("utf-16-le")
TOKEN_RUN_RE = re.compile(rb"[A-Za-z0-9+/]{40,}={0,2}")
TOKEN_RECORD_RE = re.compile(rb'\{"value":"([A-Za-z0-9_\-+/=]{40,140})","__version":"[0-9]+"\}')
TOKEN_LENGTH = 64


def browser_local_storage_dirs():
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    names = [
        ("Google", "Chrome", "User Data"),
        ("Microsoft", "Edge", "User Data"),
        ("BraveSoftware", "Brave-Browser", "User Data"),
        ("Vivaldi", "User Data"),
        ("Quark", "User Data"),
        ("Doubao", "User Data"),
        ("Chromium", "User Data"),
    ]
    roots = [os.path.join(local, *parts) for parts in names]
    if roaming:
        roots.append(os.path.join(roaming, "Code"))
    out = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, _files in os.walk(root):
            if dirpath.count(os.sep) - root.count(os.sep) > 6:
                dirnames[:] = []
                continue
            if os.path.basename(dirpath).lower() == "leveldb" and "Local Storage" in dirpath:
                out.append(dirpath)
    return out


def _offer_token(found, token, mtime):
    token = (token or "").strip()
    if len(token) < 40 or len(token) > 140:
        return
    if not re.fullmatch(r"[A-Za-z0-9+/=]{40,140}", token):
        return
    fp = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    if fp not in found or found[fp][1] < mtime:
        found[fp] = (token, mtime)


def browser_token_candidates():
    temp_dir = tempfile.mkdtemp(prefix="dswp_tok_")
    try:
        cutoff = time.time() - 30 * 86400
        files = []
        for d in browser_local_storage_dirs():
            for path in glob.glob(os.path.join(d, "*")):
                try:
                    mtime = os.path.getmtime(path)
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if not os.path.isfile(path) or mtime < cutoff or size > 40_000_000:
                    continue
                files.append((path, mtime))
        files.sort(key=lambda item: -item[1])
        files = files[:120]
        found = {}
        for path, mtime in files:
            dst = os.path.join(
                temp_dir,
                hashlib.sha256(path.encode("utf-8", "replace")).hexdigest()[:16]
                + "_" + os.path.basename(path),
            )
            try:
                shutil.copy2(path, dst)
                with open(dst, "rb") as fh:
                    blob = fh.read()
            except Exception:
                continue
            for needle in (TOKEN_KEY_ASCII, TOKEN_KEY_UTF16):
                at = 0
                while True:
                    at = blob.find(needle, at)
                    if at < 0:
                        break
                    window = blob[at + len(needle): at + len(needle) + 4096]
                    flat = window.replace(b"\x00", b"") if needle == TOKEN_KEY_UTF16 else window
                    record = TOKEN_RECORD_RE.search(flat)
                    if record:
                        _offer_token(found, record.group(1).decode("ascii", "ignore"), mtime)
                    run = TOKEN_RUN_RE.search(flat)
                    if run:
                        value = run.group(0).decode("ascii", "ignore")
                        _offer_token(found, value[:TOKEN_LENGTH], mtime)
                        _offer_token(found, value, mtime)
                    at += len(needle)
        return [v[0] for v in sorted(found.values(), key=lambda v: -v[1])][:50]
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def token_is_live(token, timeout=10):
    try:
        r = requests.get(
            get_base() + "/api/v0/users/current",
            headers=web_headers(token),
            timeout=timeout,
        )
    except Exception:
        return False
    if r.status_code in (401, 403):
        return False
    try:
        js = r.json()
    except Exception:
        return False
    data = js.get("data")
    biz_code = data.get("biz_code") if isinstance(data, dict) else None
    return r.status_code == 200 and zero_code(js.get("code")) and zero_code(biz_code)


def refresh_token_from_browser(force=False):
    """401/40003 -> rescan browser -> validate -> hot-swap. Once per cooldown."""
    global TOKEN_LAST_TRY
    now = time.time()
    with TOKEN_LOCK:
        if not force and now - TOKEN_LAST_TRY < TOKEN_COOLDOWN:
            return None
        TOKEN_LAST_TRY = now
    try:
        candidates = browser_token_candidates()
    except Exception:
        candidates = []
    current = get_token()
    for token in candidates:
        if token == current:
            continue
        if token_is_live(token):
            with TOKEN_LOCK:
                set_token(token)
                TOKEN_STATUS["state"] = "live"
                TOKEN_STATUS["refreshed_at"] = int(time.time())
                TOKEN_STATUS["source"] = "browser"
            return token
    with TOKEN_LOCK:
        TOKEN_STATUS["state"] = "expired"
    return None


def pow_response_header(challenge, answer, target_path=COMPLETION_PATH):
    obj = {
        "algorithm": challenge.get("algorithm"),
        "challenge": challenge.get("challenge"),
        "salt": challenge.get("salt"),
        "answer": answer,
        "signature": challenge.get("signature", ""),
        "target_path": target_path,
    }
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def camel(key):
    return re.sub(r"_([A-Za-z0-9])", lambda m: m.group(1).upper(), str(key))


def is_num(part):
    return re.match(r"^-?\d+$", str(part)) is not None


def key_for(part, index):
    s = str(part)
    if s == "message_id" and index == 0:
        return "id"
    if is_num(s):
        return s
    return camel(s)


def transfer(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            s = str(k)
            ck = camel(s)
            tv = transfer(v)
            out[ck] = tv
            if ck != s:
                out[s] = tv
        return out
    if isinstance(obj, list):
        return [transfer(x) for x in obj]
    return obj


def descend(container, key, next_is_num):
    if isinstance(container, list):
        if not is_num(key):
            return {}
        i = int(key)
        if i < 0:
            i = len(container) + i
        if i < 0:
            i = 0
        while len(container) <= i:
            container.append(None)
        if container[i] is None:
            container[i] = [] if next_is_num else {}
        return container[i]
    if isinstance(container, dict):
        if key not in container or container[key] is None:
            container[key] = [] if next_is_num else {}
        return container[key]
    return {}


def write_path(root, path, op, value):
    parts = [p for p in str(path or "").split("/") if p]
    tv = transfer(value)
    if not parts:
        if op == "SET" and isinstance(value, dict):
            root.clear()
            root.update(tv)
        return
    cur = root
    for idx, part in enumerate(parts[:-1]):
        key = key_for(part, idx)
        cur = descend(cur, key, is_num(parts[idx+1]))
    last = parts[-1]
    key = key_for(last, len(parts)-1)
    if isinstance(cur, list):
        if not is_num(key):
            return
        i = int(key)
        if i < 0:
            i = len(cur) + i
        if i < 0:
            i = 0
        while len(cur) <= i:
            cur.append(None)
        if op == "APPEND":
            old = cur[i]
            if isinstance(tv, list) and isinstance(old, list):
                old.extend(tv)
            elif isinstance(tv, str) and isinstance(old, str):
                cur[i] = old + tv
            else:
                cur[i] = tv
        else:
            cur[i] = tv
        return
    if isinstance(cur, dict):
        if op == "APPEND":
            old = cur.get(key)
            if isinstance(tv, list) and isinstance(old, list):
                old.extend(tv)
            elif isinstance(tv, str) and isinstance(old, str):
                cur[key] = old + tv
            else:
                cur[key] = tv
        else:
            cur[key] = tv


class DeltaParser:
    def __init__(self):
        self.op = "SET"
        self.path = ""

    def iter_ops(self, event, nested=False):
        if not isinstance(event, dict):
            return
        op = event.get("o") or self.op
        path = event.get("p") or self.path
        if not nested:
            self.op = op
            self.path = path
        if op != "BATCH":
            yield (path, op, event.get("v"))
            return
        sub = DeltaParser()
        for child in event.get("v") or []:
            for child_path, child_op, child_value in sub.iter_ops(child):
                if child_path:
                    joined = (str(path).rstrip("/") + "/" + str(child_path).lstrip("/")) if path else child_path
                else:
                    joined = path
                yield (joined, child_op, child_value)


def content_to_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content")
                if isinstance(t, str):
                    out.append(t)
        return "".join(out)
    if isinstance(value, dict):
        t = value.get("text") or value.get("content")
        return t if isinstance(t, str) else ""
    if value is None:
        return ""
    return str(value)


CONTROL_BLOCK_RE = re.compile(
    r"<｜DSML｜｜[^>]{0,500}>"
    r"|</｜DSML｜｜[^>]{0,500}>"
    r"|<\|DSML\|[^>]{0,500}\|>"
    r"|<DSML[^>]{0,500}>"
    r"|</DSML[^>]{0,500}>"
    r"|<[^<>]{0,200}(?:DSML|tool_call|tool_calls|invoke|list_agents)[^<>]{0,200}>",
    re.IGNORECASE | re.DOTALL,
)
STATUS_NOISE_RE = re.compile(
    r"已完成已完成[\s\u00a0]*[，,]?[\s\u00a0]*用时[\s\u00a0：:]*\d+[\s\u00a0]*(?:秒|s|ms|毫秒)",
    re.IGNORECASE,
)
BARE_STATUS_RE = re.compile(
    r"已完成已完成[\s\u00a0]*[，,]?[\s\u00a0]*(?:用时[\s\u00a0：:]*\d+[\s\u00a0]*(?:秒|s|ms|毫秒))?",
    re.IGNORECASE,
)
STANDALONE_STATUS_RE = re.compile(r"(?:^|\n)\s*已完成已完成\s*(?=\n|$)", re.IGNORECASE)
STATUS_PREFIX_RE = re.compile(
    r"已完成已完成[\s\u00a0]*[，,]?[\s\u00a0]*"
    r"(?:用(?:时[\s\u00a0：:]*\d*[\s\u00a0]*(?:s|m|ms|秒|毫秒)?)?)?$",
    re.IGNORECASE,
)
TOOL_PROTOCOL_HINTS = ("DSML", "list_agents", "invoke name=", "tool_choice", "tool_calls")
CONTROL_MARKERS = (
    "<｜DSML｜｜",
    "</｜DSML｜｜",
    "<|DSML|",
    "<DSML",
    "</DSML",
    "已完成已完成，用时",
    "已完成已完成",
)


def sanitize_control_text(value):
    text = str(value or "")
    previous = None
    while previous != text:
        previous = text
        text = CONTROL_BLOCK_RE.sub("", text)
        text = BARE_STATUS_RE.sub("", text)
        text = STATUS_NOISE_RE.sub("", text)
        text = STANDALONE_STATUS_RE.sub("", text)
    return text


def strip_control_blocks(text):
    previous = None
    while previous != text:
        previous = text
        text = CONTROL_BLOCK_RE.sub("", text)
    return text


def sanitize_prompt_text(value, role="user", strict=True):
    text = sanitize_control_text(value)
    if role == "system" and strict:
        kept = []
        for line in text.splitlines():
            lowered = line.lower()
            if any(hint.lower() in lowered for hint in TOOL_PROTOCOL_HINTS):
                continue
            kept.append(line)
        text = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class ControlTextFilter:
    STATUS_MARKER = "已完成已完成"

    def __init__(self, strip_dsml=True):
        self.buffer = ""
        self.strip_dsml = strip_dsml

    def _hold_index(self, text):
        hold = len(text)
        lt = text.rfind("<")
        if lt >= 0 and ">" not in text[lt:]:
            hold = min(hold, lt)
        marker = self.STATUS_MARKER
        for i in range(max(0, len(text) - len(marker)), len(text)):
            tail = text[i:]
            if marker.startswith(tail):
                hold = min(hold, i)
                break
        status = text.rfind(marker)
        if status >= 0 and STATUS_PREFIX_RE.fullmatch(text[status:]):
            hold = min(hold, status)
        return hold

    def _process(self, final):
        text = strip_control_blocks(self.buffer) if self.strip_dsml else self.buffer
        while True:
            match = STATUS_NOISE_RE.search(text)
            if match:
                text = text[:match.start()] + text[match.end():]
                continue
            if not final:
                break
            match = STANDALONE_STATUS_RE.search(text)
            if match:
                text = text[:match.start()] + text[match.end():]
                continue
            break
        if final:
            text = BARE_STATUS_RE.sub("", text)
            text = STANDALONE_STATUS_RE.sub("", text)
            self.buffer = ""
            return text
        status = text.rfind(self.STATUS_MARKER)
        if status >= 0 and not STATUS_PREFIX_RE.fullmatch(text[status:]):
            text = BARE_STATUS_RE.sub("", text)
        hold = self._hold_index(text)
        self.buffer = text[hold:]
        return text[:hold]

    def feed(self, value):
        if not value:
            return ""
        self.buffer += str(value)
        return self._process(False)

    def flush(self):
        return self._process(True)

class DeltaState:
    MAIN_TYPES = {"RESPONSE", "TEMPLATE_RESPONSE"}
    THINK_TYPES = {"THINK"}

    def __init__(self):
        self.parser = DeltaParser()
        self.state = {"response": {}}
        self.last_content = ""
        self.last_reasoning = ""

    def _fragments(self):
        response = self.state.get("response")
        if isinstance(response, dict):
            frags = response.get("fragments")
            if isinstance(frags, list):
                return frags
        return []

    def _normalize_response(self):
        response = self.state.get("response")
        if not isinstance(response, dict):
            return
        mid = None
        for key in ("messageId", "id", "message_id"):
            if key in response and response[key] is not None:
                mid = response[key]
                break
        if mid is not None:
            response.setdefault("messageId", mid)
            response.setdefault("id", mid)
            response.setdefault("message_id", mid)

    def visible_text(self, kind):
        types = self.THINK_TYPES if kind == "reasoning" else self.MAIN_TYPES
        out = []
        for frag in self._fragments():
            if not isinstance(frag, dict):
                continue
            typ = str(frag.get("type") or frag.get("fragmentType") or "").upper()
            if typ in types:
                out.append(content_to_text(frag.get("content")))
        return "".join(out)

    def parent_message_id(self):
        response = self.state.get("response")
        if isinstance(response, dict):
            for key in ("messageId", "id", "message_id"):
                if key in response and response[key] is not None:
                    return response[key]
        return None

    def response_status(self):
        response = self.state.get("response")
        if isinstance(response, dict):
            for key in ("status", "responseStatus", "response_status"):
                if key in response and response[key] is not None:
                    return response[key]
        return None

    def _new_text(self):
        out = []
        for kind, channel, attr in (
            ("content", "content", "last_content"),
            ("reasoning", "reasoning", "last_reasoning"),
        ):
            cur = self.visible_text(kind)
            last = getattr(self, attr)
            if cur == last:
                continue
            if cur.startswith(last) and len(cur) > len(last):
                delta = cur[len(last):]
            else:
                delta = cur
            if delta:
                out.append((channel, delta))
            setattr(self, attr, cur)
        return out

    def apply(self, event):
        for path, op, value in self.parser.iter_ops(event):
            write_path(self.state, path, op, value)
        self._normalize_response()
        return self._new_text()


def iter_sse(lines):
    event = None
    data = []
    for raw in lines:
        if isinstance(raw, bytes):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
        else:
            line = str(raw).rstrip("\r\n")
        if line.startswith(":"):
            continue
        if line == "":
            if event is not None or data:
                yield (event or "delta", "\n".join(data))
            event = None
            data = []
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if event is not None or data:
        yield (event or "delta", "\n".join(data))


def deepseek_events(response, include_thinking=True, state=None):
    if state is None:
        state = DeltaState()
    # a shared DeltaState across continue/resume must not inherit the previous
    # response's patch address, or a whole-tree SET is written to the wrong path
    state.parser.op = "SET"
    state.parser.path = ""
    meta = {"parent_message_id": None, "response_message_id": None}
    for event, data in iter_sse(response.iter_lines()):
        if not data:
            continue
        try:
            obj = json.loads(data)
        except Exception:
            continue
        if event == "ready":
            rid = obj.get("response_message_id") or obj.get("responseMessageId")
            if rid is not None:
                meta["response_message_id"] = rid
            yield ("meta", dict(meta))
            continue
        if event == "delta":
            for channel, text in state.apply(obj):
                if channel == "reasoning" and not include_thinking:
                    continue
                if text:
                    yield ("text", channel, text)
            continue
        if event in ("toast", "hint"):
            if str(obj.get("type") or "").lower() in ("warning", "error"):
                content = obj.get("content") or ""
                if content:
                    yield ("error", content)
            continue
        if event in ("finish", "close"):
            parent = meta.get("response_message_id") or state.parent_message_id()
            if parent is not None:
                meta["parent_message_id"] = parent
            meta["status"] = state.response_status()
            yield ("done", dict(meta))
            return
    parent = meta.get("response_message_id") or state.parent_message_id()
    if parent is not None:
        meta["parent_message_id"] = parent
    meta["status"] = state.response_status()
    yield ("done", dict(meta))


def deepseek_turn(client, session_id, parent_message_id, prompt, model_type,
                  ref_file_ids=None, first_response=None, include_thinking=True, state=None):
    """Drive one turn to completion, as the page does (plugin turn.mjs parity):
    an INCOMPLETE reply continues via /chat/continue, an empty FINISHED reply is
    re-read via /chat/resume_stream (once), and a non-SSE reply (biz_code 22)
    is unpacked whole. A shared DeltaState keeps fragments accumulating across
    the extra calls, so only new text is yielded."""
    ref_file_ids = list(ref_file_ids or [])
    if state is None:
        state = DeltaState()
    endpoint = COMPLETION_PATH
    message_id = None
    continues = 0
    max_continues = get_int("DEEPSEEK_MAX_CONTINUES", 20)
    any_text = False
    any_reasoning = False
    meta = {"parent_message_id": parent_message_id, "response_message_id": None, "status": None}
    response = first_response
    while True:
        if response is None:
            if endpoint == COMPLETION_PATH:
                response = client.request_turn(
                    COMPLETION_PATH, session_id=session_id, parent_message_id=parent_message_id,
                    prompt=prompt, model_type=model_type, ref_file_ids=ref_file_ids,
                )
            else:
                response = client.request_turn(endpoint, session_id=session_id, message_id=message_id)
        ctype = (response.headers.get("content-type") or "").lower()
        if "text/event-stream" not in ctype:
            raw = response.text
            response.close()
            response = None
            try:
                js = json.loads(raw)
            except Exception:
                raise RuntimeError("DeepSeek completion: non-SSE reply could not be parsed")
            biz = check_envelope(js, "completion", allow=[22])
            resp = biz.get("response") if isinstance(biz, dict) else None
            if resp is None and isinstance(biz, dict) and (biz.get("fragments") or biz.get("message_id")):
                resp = biz
            if not isinstance(resp, dict):
                raise RuntimeError("DeepSeek completion: expected a stream")
            for channel, text in state.apply({"v": {"response": resp}}):
                if channel == "reasoning":
                    if not include_thinking:
                        continue
                    any_reasoning = True
                else:
                    any_text = True
                if text:
                    yield ("text", channel, text)
            mid = state.parent_message_id()
            if mid is not None:
                meta["response_message_id"] = mid
                meta["parent_message_id"] = mid
            meta["status"] = state.response_status() or "FINISHED"
            yield ("done", dict(meta))
            return
        for ev in deepseek_events(response, include_thinking, state):
            kind = ev[0]
            if kind == "text":
                if ev[1] == "reasoning":
                    any_reasoning = True
                else:
                    any_text = True
                yield ev
            elif kind == "meta":
                for key, value in ev[1].items():
                    if value is not None:
                        meta[key] = value
                yield ("meta", dict(meta))
            elif kind == "error":
                yield ev
            elif kind == "done":
                for key, value in ev[1].items():
                    if value is not None:
                        meta[key] = value
                break
        response.close()
        response = None
        status = str(meta.get("status") or "").upper()
        mid = meta.get("response_message_id") or meta.get("parent_message_id")
        if status == "INCOMPLETE" and mid is not None and continues < max_continues:
            continues += 1
            endpoint = CONTINUE_PATH
            message_id = mid
            continue
        if status == "FINISHED" and not any_text and not any_reasoning and mid is not None and continues == 0:
            continues += 1
            endpoint = RESUME_PATH
            message_id = mid
            continue
        meta["parent_message_id"] = mid if mid is not None else meta.get("parent_message_id")
        yield ("done", dict(meta))
        return


def sse_chunk(completion_id, created, model, delta, finish_reason=None):
    obj = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }
    return "data: " + json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n\n"


def static_models():
    return {
        "object": "list",
        "data": [
            {"id": mid, "object": "model", "created": 0, "owned_by": "deepseek"}
            for mid in STATIC_MODEL_IDS
        ],
    }


def maybe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def find_model_configs(node):
    node = maybe_json(node)
    if isinstance(node, list):
        if node and all(isinstance(x, dict) and (x.get("model_type") or x.get("modelType")) for x in node):
            return node
        for item in node:
            if isinstance(item, dict):
                key = str(item.get("key") or item.get("name") or "")
                if key in ("model_configs", "modelConfigs"):
                    value = maybe_json(item.get("value") or item.get("val"))
                    if isinstance(value, list):
                        return value
            found = find_model_configs(item)
            if found:
                return found
        return []
    if isinstance(node, dict):
        for key in ("model_configs", "modelConfigs"):
            if key in node:
                value = maybe_json(node[key])
                if isinstance(value, list):
                    return value
                found = find_model_configs(value)
                if found:
                    return found
        for key in ("settings", "biz_data", "data", "bizData"):
            if key in node:
                found = find_model_configs(node[key])
                if found:
                    return found
        for value in node.values():
            found = find_model_configs(value)
            if found:
                return found
    return []


def biz_data(js):
    if not isinstance(js, dict):
        return {}
    data = js.get("data") or {}
    if isinstance(data, dict):
        return data.get("biz_data") or js.get("biz_data") or data
    return js.get("biz_data") or {}


# The page's two wire models, as the plugin maps them: chat -> default,
# reasoner -> expert. The other local aliases are cosmetic switches over the
# same wire pair; reasoner-ish ones pick expert.
EXPERT_MODEL_IDS = {"deepseek-reasoner", "deepseek-r1", "deepseek-thinking"}


def wire_model_for(model):
    name = str(model or "").strip()
    if name in EXPERT_MODEL_IDS:
        return "expert"
    if name in ("default", "expert"):
        return name
    return get_model_type()


def resolve_model_type(model):
    model = str(model or get_model_type())
    if get_bool("DEEPSEEK_STRICT_MODEL", "0"):
        return model
    if model in STATIC_MODEL_IDS or model in EXPERT_MODEL_IDS or model in (
        "deepseek-chat", "deepseek", "deepseek-web", "deepseek-v3", "deepseek-v3.2",
        "deepseek-v4", "deepseek-v4.1", "deepseek-v4.1-flash", "deepseek-coder", "vision",
    ):
        return wire_model_for(model)
    return model


def thinking_for(model):
    if str(model) in EXPERT_MODEL_IDS:
        return True
    return get_bool("DEEPSEEK_THINKING", "0")


def finish_reason_for(status):
    """Map the page's reply status to an OpenAI finish_reason (plugin parity)."""
    s = str(status or "").upper()
    if s == "CONTENT_FILTER":
        return "content_filter"
    if s in ("CONTEXT_LENGTH_EXCEEDED", "INCOMPLETE", "TIMEOUT"):
        return "length"
    return "stop"


# ---------------------------------------------------------------------------
# Attachments (plugin attachments.mjs parity): OpenAI image_url / file parts
# -> upload_file (multipart + its own PoW) -> fetch_files poll -> ref_file_ids.
# ---------------------------------------------------------------------------

PARSE_POLL_MS = 400
PARSE_TIMEOUT_MS = 15000
PARSE_FAILED = {
    "FAILED", "CONTENT_FILTER", "CONTENT_TOO_LONG", "CANCELLED", "CONTENT_EMPTY",
    "_CUSTOM_SYSTEM_ERROR_FAIL",
}
EXT_BY_MIME = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
    "application/pdf": "pdf", "text/plain": "txt", "text/markdown": "md", "text/csv": "csv",
}


def decode_data_url(text):
    comma = text.find(",")
    if comma < 0:
        return None
    meta = text[5:comma]
    mime = (meta.split(";")[0].strip() or "application/octet-stream")
    try:
        if re.search(r"base64", meta, re.IGNORECASE):
            data = base64.b64decode(text[comma + 1:])
        else:
            data = text[comma + 1:].encode("utf-8")
    except Exception:
        return None
    return data, mime


def filename_for(attachment):
    if attachment.get("filename"):
        return attachment["filename"]
    ext = EXT_BY_MIME.get(attachment.get("mime") or "")
    if attachment.get("kind") == "image":
        return "image." + (ext or "png")
    return ("document." + ext) if ext else "document.bin"


def attachment_from_part(part):
    if not isinstance(part, dict):
        return None
    typ = part.get("type")
    if typ == "image_url":
        raw = part.get("image_url")
        url = raw if isinstance(raw, str) else (raw.get("url") if isinstance(raw, dict) else None)
        if not isinstance(url, str) or not url:
            return None
        if url.lower().startswith("data:"):
            decoded = decode_data_url(url)
            if not decoded:
                return None
            return {"kind": "image", "data": decoded[0], "mime": decoded[1], "filename": None}
        if not re.match(r"^https?://", url, re.IGNORECASE):
            return None
        try:
            rr = requests.get(url, timeout=20)
            if rr.status_code != 200:
                return None
            mime = (rr.headers.get("content-type") or "image/png").split(";")[0].strip() or "image/png"
            return {"kind": "image", "data": rr.content, "mime": mime, "filename": None}
        except Exception:
            return None
    if typ == "file":
        f = part.get("file") if isinstance(part.get("file"), dict) else {}
        filename = f.get("filename") if isinstance(f.get("filename"), str) and f.get("filename") else None
        raw = f.get("file_data") if isinstance(f.get("file_data"), str) else (
            f.get("data") if isinstance(f.get("data"), str) else None)
        if not raw:
            return None
        if raw.lower().startswith("data:"):
            decoded = decode_data_url(raw)
            if not decoded:
                return None
            mime = decoded[1]
            kind = "image" if mime.startswith("image/") else "file"
            return {"kind": kind, "data": decoded[0], "mime": mime, "filename": filename}
        mime = f.get("content_type") if isinstance(f.get("content_type"), str) and f.get("content_type") else "application/octet-stream"
        try:
            data = base64.b64decode(raw)
        except Exception:
            return None
        kind = "image" if mime.startswith("image/") else "file"
        return {"kind": kind, "data": data, "mime": mime, "filename": filename}
    return None


def extract_attachments(messages, from_index=0):
    out = []
    items = messages or []
    index = max(0, from_index)
    while index < len(items) and isinstance(items[index], dict) and items[index].get("role") in ("assistant", "tool"):
        index += 1
    for message in items[index:]:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") == "text":
                continue
            try:
                attachment = attachment_from_part(part)
                if attachment:
                    out.append(attachment)
            except Exception:
                continue
    return out


def prepare_files(client, attachments):
    ref_file_ids = []
    notes = []
    if not attachments:
        return ref_file_ids, notes
    uploaded = []
    for attachment in attachments:
        name = attachment.get("filename") or filename_for(attachment)
        try:
            uploaded.append((client.upload_file(attachment), name))
        except Exception as exc:
            notes.append("[Note: attachment " + name + " could not be delivered to DeepSeek: " + str(exc) + "]")
    for file_id, name in uploaded:
        try:
            ok = client.wait_parsed(file_id)
        except Exception:
            ok = True
        if ok:
            ref_file_ids.append(file_id)
        else:
            notes.append("[Note: attachment " + name + " was not readable by DeepSeek and was left out.]")
    return ref_file_ids, notes


class DSClient:
    def __init__(self, model_type=None, thinking=None, search=None):
        self.base = get_base()
        self.token = get_token()
        self.cookie = get_cookie()
        self.device_id = get_device_id()
        self.device_model = get_device_model()
        self.model_type = str(model_type or get_model_type())
        self.thinking = get_bool("DEEPSEEK_THINKING", "0") if thinking is None else bool(thinking)
        self.search = get_bool("DEEPSEEK_SEARCH", "0") if search is None else bool(search)
        self.shumei = os.environ.get("DEEPSEEK_SHUMEI_DEVICE_ID", "").strip()
        self.pow_max_extra = get_int("DEEPSEEK_POW_MAX_EXTRA", 0)
        self.source = os.environ.get("DEEPSEEK_SOURCE", "").strip() or None

    def headers(self, accept="application/json, text/plain, */*"):
        h = {
            "Authorization": "Bearer " + self.token,
            "Content-Type": "application/json",
            "Accept": accept,
            "User-Agent": os.environ.get(
                "DEEPSEEK_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
            ),
            "Referer": os.environ.get("DEEPSEEK_REFERER", get_base() + "/"),
            "Origin": os.environ.get("DEEPSEEK_ORIGIN", get_base()),
            "x-client-bundle-id": get_bundle_id(),
            "x-client-platform": get_client_platform(),
            "x-client-version": get_client_version(),
            "x-client-locale": get_client_locale(),
            "x-client-timezone-offset": str(get_tz_offset()),
            "x-device-id": self.device_id,
            "x-device-model": self.device_model,
            "x-model-type": self.model_type,
            "x-thinking-enabled": "1" if self.thinking else "0",
        }
        if self.cookie:
            h["Cookie"] = self.cookie
        h.update(optional_headers())
        return h

    def create_session(self):
        r = upstream_request(
            "POST",
            self.base + "/api/v0/chat_session/create",
            headers=self.headers(),
            json={},
            timeout=30,
        )
        if r.status_code in (401, 403):
            raise SignInExpired(
                "DeepSeek chat_session/create: sign-in refused (HTTP " + str(r.status_code) + ")"
            )
        r.raise_for_status()
        js = r.json()
        check_envelope(js, "chat_session/create")
        biz = biz_data(js)
        chat_session = biz.get("chat_session") or biz.get("chatSession") or {}
        sid = chat_session.get("id") or chat_session.get("chat_session_id") or biz.get("id")
        if not sid:
            raise RuntimeError("chat_session id missing in response")
        return str(sid)

    def models(self):
        if not self.token:
            return static_models()
        ids = list(STATIC_MODEL_IDS)
        try:
            r = requests.get(
                self.base + "/api/v0/client/settings",
                headers=self.headers(),
                params={"did": self.device_id, "scope": "model"},
                timeout=20,
            )
            r.raise_for_status()
            configs = find_model_configs(r.json())
            for cfg in configs:
                if not isinstance(cfg, dict):
                    continue
                if not (cfg.get("enabled") and cfg.get("switchable")):
                    continue
                mid = cfg.get("model_type") or cfg.get("modelType")
                if mid and mid not in ids:
                    ids.append(str(mid))
        except Exception:
            pass
        if not ids:
            ids = list(STATIC_MODEL_IDS)
        return {
            "object": "list",
            "data": [
                {"id": mid, "object": "model", "created": 0, "owned_by": "deepseek"}
                for mid in ids
            ],
        }

    def create_pow_challenge(self, target_path=COMPLETION_PATH):
        r = upstream_request(
            "POST",
            self.base + "/api/v0/chat/create_pow_challenge",
            headers=self.headers(),
            json={"target_path": target_path},
            timeout=30,
        )
        if r.status_code in (401, 403):
            raise SignInExpired(
                "DeepSeek create_pow_challenge: sign-in refused (HTTP " + str(r.status_code) + ")"
            )
        r.raise_for_status()
        js = r.json()
        check_envelope(js, "create_pow_challenge")
        biz = biz_data(js)
        challenge = biz.get("challenge") or biz
        if not isinstance(challenge, dict):
            raise RuntimeError("challenge object missing")
        for field in ("algorithm", "challenge", "salt", "difficulty", "signature"):
            if field not in challenge:
                raise RuntimeError("challenge field missing: " + field)
        ch = dict(challenge)
        expire_at = None
        for key in ("expireAt", "expire_at", "expireAfter", "expire_after"):
            if key in ch and ch[key] is not None:
                expire_at = ch[key]
                break
        ch["expireAt"] = 0 if expire_at is None else expire_at
        return ch

    def solve_pow(self, challenge, target_path=COMPLETION_PATH):
        difficulty = int(challenge["difficulty"])
        if node_available():
            answer = solve_node(
                challenge["algorithm"],
                challenge["challenge"],
                challenge["salt"],
                challenge["expireAt"],
                difficulty,
                self.pow_max_extra,
            )
        else:
            answer = solve(
                challenge["algorithm"],
                challenge["challenge"],
                challenge["salt"],
                challenge["expireAt"],
                difficulty,
                self.pow_max_extra,
            )
        if answer is None:
            raise RuntimeError("No solution found for DeepSeekHashV1")
        return answer, pow_response_header(challenge, answer, target_path)

    def request_turn(self, endpoint=COMPLETION_PATH, session_id=None, parent_message_id=None,
                     prompt=None, model_type=None, ref_file_ids=None, message_id=None):
        """One upstream turn request. /chat/completion carries a proof of work;
        /chat/continue and /chat/resume_stream do not (the page proves nothing
        for them — a challenge naming them answers INVALID_TARGET_PATH)."""
        headers = self.headers(accept="text/event-stream")
        if endpoint == COMPLETION_PATH:
            challenge = self.create_pow_challenge(COMPLETION_PATH)
            _answer, pow_header = self.solve_pow(challenge, COMPLETION_PATH)
            headers["X-DS-PoW-Response"] = pow_header
            body = {
                "chat_session_id": session_id,
                "parent_message_id": parent_message_id,
                "model_type": model_type,
                "prompt": prompt,
                "ref_file_ids": list(ref_file_ids or []),
                "thinking_enabled": bool(self.thinking),
                "search_enabled": bool(self.search),
                "source": self.source,
                "action": None,
                "preempt": False,
            }
            if self.shumei:
                body["shumei_device_id"] = self.shumei
        elif endpoint == CONTINUE_PATH:
            body = {
                "chat_session_id": session_id,
                "message_id": message_id,
                "fallback_to_resume": True,
            }
        elif endpoint == RESUME_PATH:
            body = {"chat_session_id": session_id, "message_id": message_id}
        else:
            raise RuntimeError("unknown endpoint " + str(endpoint))
        r = upstream_request(
            "POST",
            self.base + endpoint,
            headers=headers,
            json=body,
            stream=True,
            timeout=(10, 600),
        )
        if r.status_code == 403 and endpoint == COMPLETION_PATH:
            replay = r.headers.get("x-ds-challenge-response")
            if replay:
                headers["x-ds-challenge-response"] = replay
                r.close()
                r = upstream_request(
                    "POST",
                    self.base + endpoint,
                    headers=headers,
                    json=body,
                    stream=True,
                    timeout=(10, 600),
                )
        if r.status_code in (401, 403):
            raise SignInExpired(
                "DeepSeek " + str(endpoint) + ": sign-in refused (HTTP " + str(r.status_code) + ")"
            )
        if r.status_code >= 400:
            try:
                preview = next(r.iter_content(chunk_size=1024), b"").decode("utf-8", "replace")[:800]
            except Exception:
                preview = ""
            code = r.status_code
            r.close()
            low = preview.lower()
            if code in (400, 404, 409, 410) and ("session" in low or "chat_session" in low or "not found" in low):
                raise StaleSession("DeepSeek " + str(endpoint) + " HTTP " + str(code) + ": " + preview)
            raise RuntimeError("HTTP " + str(code) + ": " + preview)
        r.encoding = "utf-8"
        return r

    def completion(self, session_id, parent_message_id, prompt, model_type, ref_file_ids=None):
        return self.request_turn(
            COMPLETION_PATH, session_id=session_id, parent_message_id=parent_message_id,
            prompt=prompt, model_type=model_type, ref_file_ids=ref_file_ids,
        )

    # --- attachments: upload -> poll parse -> ref_file_ids (plugin parity) ---

    def upload_file(self, attachment):
        challenge = self.create_pow_challenge(UPLOAD_PATH)
        _answer, pow_header = self.solve_pow(challenge, UPLOAD_PATH)
        headers = self.headers(accept="*/*")
        headers["X-DS-PoW-Response"] = pow_header
        headers["x-thinking-enabled"] = "0"
        headers["x-model-type"] = "default"
        headers["x-file-size"] = str(len(attachment.get("data") or b""))
        headers.pop("Content-Type", None)  # the multipart boundary comes from the body
        name = attachment.get("filename") or filename_for(attachment)
        mime = attachment.get("mime") or "application/octet-stream"
        files = {"file": (name, bytes(attachment.get("data") or b""), mime)}
        r = upstream_request(
            "POST", self.base + UPLOAD_PATH, headers=headers, files=files, timeout=(10, 120),
        )
        if r.status_code in (401, 403):
            raise SignInExpired("DeepSeek file upload: sign-in refused (HTTP " + str(r.status_code) + ")")
        r.raise_for_status()
        js = r.json()
        check_envelope(js, "file upload")
        biz = biz_data(js)
        fid = biz.get("id") if isinstance(biz, dict) else None
        if not fid and isinstance(biz, dict):
            fobj = biz.get("file")
            if isinstance(fobj, dict):
                fid = fobj.get("id")
        if not fid:
            raise RuntimeError("DeepSeek file upload: no file id returned")
        return fid

    def file_status(self, file_id):
        r = requests.get(
            self.base + "/api/v0/file/fetch_files",
            headers=self.headers(),
            params={"file_ids": str(file_id)},
            timeout=20,
        )
        if r.status_code in (401, 403):
            raise SignInExpired("DeepSeek file status: sign-in refused (HTTP " + str(r.status_code) + ")")
        r.raise_for_status()
        js = r.json()
        check_envelope(js, "file status")
        biz = biz_data(js)
        files = biz.get("files") if isinstance(biz, dict) else None
        if not isinstance(files, list):
            return None
        hit = None
        for f in files:
            if isinstance(f, dict) and f.get("id") == file_id:
                hit = f
                break
        if hit is None and files:
            hit = files[0]
        status = hit.get("status") if isinstance(hit, dict) else None
        return str(status).upper() if isinstance(status, str) else None

    def wait_parsed(self, file_id):
        deadline = time.time() + PARSE_TIMEOUT_MS / 1000.0
        while True:
            try:
                status = self.file_status(file_id)
            except Exception:
                status = None
            if status == "SUCCESS":
                return True
            if status and status in PARSE_FAILED:
                return False
            if time.time() >= deadline:
                return True
            time.sleep(PARSE_POLL_MS / 1000.0)


def last_user_text(messages):
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user":
            return content_to_text(message.get("content", ""))
    return ""


def first_user_text(messages):
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user":
            return content_to_text(message.get("content", ""))
    return ""


def build_fc_continuation(messages, tool_instructions):
    """续接轮：把函数返回结果拼成一条新消息，并保留调用格式说明。"""
    cut = -1
    for i, m in enumerate(messages):
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls"):
            cut = i
    tail = messages[cut + 1:] if cut >= 0 else messages[-4:]
    names = tool_name_map(messages)
    lines = []
    for m in tail:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("tool", "user"):
            continue
        text = sanitize_prompt_text(content_to_text(m.get("content", "")), "tool" if role == "tool" else "user")
        if not text:
            continue
        if role == "tool":
            name = str(m.get("name") or "").strip() or names.get(str(m.get("tool_call_id") or ""), "")
            lines.append("Tool result" + (" (" + name + ")" if name else "") + ": " + text)
        else:
            lines.append("User: " + text)
    if not lines:
        lines.append("User: " + sanitize_prompt_text(last_user_text(messages), "user"))
    lines.append("System: 以上是函数返回的结果。请据此用自然语言回答；若仍需调用其它函数，仍按上述格式输出调用。")
    if tool_instructions:
        lines.insert(0, "System: " + tool_instructions)
    return "\n\n".join(lines)


def build_prompt(messages, continuation, tool_instructions=None, tools_active=False):
    if continuation:
        if tools_active:
            return build_fc_continuation(messages, tool_instructions)
        return sanitize_prompt_text(last_user_text(messages), "user")
    parts = []
    names = tool_name_map(messages)
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        calls = message.get("tool_calls") if isinstance(message.get("tool_calls"), list) else []
        text = sanitize_prompt_text(content_to_text(message.get("content", "")), role, strict=not tools_active)
        rendered = ""
        if role == "assistant" and calls:
            blocks = []
            for call in calls:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = fn.get("name") or call.get("name")
                if not name:
                    continue
                args = fn.get("arguments") if "arguments" in fn else call.get("arguments")
                blocks.append(render_tool_call(name, args))
            rendered = "\n".join(blocks)
        if not text and not rendered:
            continue
        if role == "system":
            parts.append("System: " + text)
        elif role == "assistant":
            body = "\n".join(x for x in (text, rendered) if x)
            parts.append("Assistant: " + body)
        elif role == "tool":
            name = str(message.get("name") or "").strip() or names.get(str(message.get("tool_call_id") or ""), "")
            parts.append("Tool result" + (" (" + name + ")" if name else "") + ": " + text)
        else:
            parts.append("User: " + text)
    if tool_instructions:
        parts.insert(0, "System: " + tool_instructions)
    if len(parts) == 1 and parts[0].startswith("User: ") and not tool_instructions:
        return parts[0][6:]
    return "\n\n".join(parts) or last_user_text(messages)


TOOL_CALL_OPEN = "<tool_call>"
TOOL_CALL_SHUT = "</tool_call>"
LEGACY_TOOL_OPEN = "FUNCTION_CALL_START"
LEGACY_TOOL_SHUT = "FUNCTION_CALL_END"
DSML_OPEN_MARK = "<｜｜DSML"
DSML_SHUT_MARK = "</｜｜DSML"
TOOL_RUNAWAY = 65536
DSML_RUNAWAY = 65536
FENCE = chr(96) * 3

DSML_INVOKE_RE = re.compile(
    r"<[｜|\s]*DSML[｜|\s]*invoke\b([^>]*)>([\s\S]*?)<[｜|\s]*/[｜|\s]*DSML[｜|\s]*invoke[^>]*>",
    re.IGNORECASE,
)
DSML_PARAMETER_RE = re.compile(
    r"<[｜|\s]*DSML[｜|\s]*parameter\b([^>]*)>([\s\S]*?)<[｜|\s]*/[｜|\s]*DSML[｜|\s]*parameter[^>]*>",
    re.IGNORECASE,
)
STRAY_DSML_RE = re.compile(r"</?[｜|\s]*DSML[^>]{0,500}>", re.IGNORECASE)


def dsml_tag_name(attrs):
    m = re.search(r'name\s*=\s*"([^"]*)"', str(attrs or ""))
    return m.group(1) if m else None


def parse_tool_call(inner):
    """One <tool_call> block (or legacy FUNCTION_CALL block) -> a call dict."""
    text = str(inner or "").strip()
    if text.startswith(FENCE):
        text = text[len(FENCE):]
        text = re.sub(r"^[a-zA-Z]*\s*", "", text)
        if text.endswith(FENCE):
            text = text[: -len(FENCE)]
        text = text.strip()
    if not text:
        return None
    try:
        value = json.loads(text)
    except Exception:
        m = re.search(r"([A-Za-z_][\w.\-]*)\s*\((.*)\)", text, re.DOTALL)
        if not m:
            return None
        raw = m.group(2).strip()
        try:
            args_obj = json.loads(raw) if raw.startswith("{") else {}
        except Exception:
            return None
        value = {"name": m.group(1), "arguments": args_obj}
    name = str(value.get("name") or "").strip() if isinstance(value, dict) else ""
    if not name:
        return None
    args = value.get("arguments", value.get("parameters")) if isinstance(value, dict) else None
    arg_text = args if isinstance(args, str) else json.dumps(
        args if args is not None else {}, ensure_ascii=False
    )
    return {
        "id": "call_" + uuid.uuid4().hex[:12],
        "name": name,
        "arguments": arg_text,
    }


def parse_dsml_calls(block):
    """The web model's native DSML invoke markup -> tool calls (dual format)."""
    calls = []
    for match in DSML_INVOKE_RE.finditer(str(block or "")):
        name = dsml_tag_name(match.group(1))
        if not name:
            continue
        args = {}
        for parameter in DSML_PARAMETER_RE.finditer(match.group(2)):
            key = dsml_tag_name(parameter.group(1))
            if not key:
                continue
            raw = parameter.group(2)
            try:
                args[key] = json.loads(raw)
            except Exception:
                args[key] = raw
        calls.append({
            "id": "call_" + uuid.uuid4().hex[:12],
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False, separators=(",", ":")),
        })
    return calls


def render_tool_call(name, args):
    raw_args = args if isinstance(args, str) else json.dumps(
        args if args is not None else {}, ensure_ascii=False
    )
    return (
        TOOL_CALL_OPEN + '{"name":' + json.dumps(str(name), ensure_ascii=False)
        + ',"arguments":' + raw_args + "}" + TOOL_CALL_SHUT
    )


def tools_to_prompt(tools):
    """The plugin's tool protocol: exact markup, parallel calls, no DSML."""
    listed = []
    for tool in tools or []:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(fn, dict) and isinstance(fn.get("name"), str):
            listed.append(json.dumps({
                "name": fn.get("name"),
                "description": fn.get("description") if isinstance(fn.get("description"), str) else "",
                "parameters": fn.get("parameters") or {},
            }, ensure_ascii=False, separators=(",", ":")))
    if not listed:
        return ""
    return "\n".join([
        "[Tool calling]",
        "You can call the tools listed at the end of this block. To call one, end your reply with a line of exactly this shape:",
        '<tool_call>{"name":"tool name","arguments":{}}</tool_call>',
        '- "arguments" carries the tool\'s parameters as JSON, following its schema.',
        "You may output several <tool_call> blocks in one reply to call tools in parallel.",
        "Do not wrap a call in code fences, and write nothing after the calls: their results arrive with the next turn.",
        "Use exactly this markup — never the DSML/内部工具 format, never any other tag style.",
        "When no tool fits, answer normally and call nothing.",
        "The tools:",
    ] + listed)


def build_tool_instructions(tools):
    return tools_to_prompt(tools)


class ToolCallScanner:
    """Incremental parser for the plugin's two tool-call markups.

    Blocks split across SSE deltas are held; unparseable markup is folded into
    reasoning (never the reply); DSML blocks with tools on are parsed as calls.
    """
    MARKERS = (TOOL_CALL_OPEN, LEGACY_TOOL_OPEN, DSML_OPEN_MARK, DSML_SHUT_MARK)

    def __init__(self):
        self.hold = ""
        self.block = None
        self.buf = ""
        self.depth = 0
        self.seen = 0

    @staticmethod
    def _tail_hold(text):
        longest = max(len(m) for m in ToolCallScanner.MARKERS) - 1
        for n in range(min(len(text), longest), 0, -1):
            tail = text[-n:]
            if any(m.startswith(tail) for m in ToolCallScanner.MARKERS):
                return n
        return 0

    @staticmethod
    def _visible(text):
        return STRAY_DSML_RE.sub("", text)

    @staticmethod
    def _pack(visible, calls, folded):
        return "".join(visible), calls, "".join(folded)

    def feed(self, text):
        return self._run(str(text or ""), False)

    def flush(self):
        return self._run("", True)

    def _reset_block(self):
        self.buf = ""
        self.block = None
        self.depth = 0
        self.seen = 0

    def _run(self, text, final):
        visible, calls, folded = [], [], []
        buf = (self.hold or "") + text
        self.hold = ""
        while True:
            if self.block in ("tool", "legacy"):
                opener = TOOL_CALL_OPEN if self.block == "tool" else LEGACY_TOOL_OPEN
                closer = TOOL_CALL_SHUT if self.block == "tool" else LEGACY_TOOL_SHUT
                combined = self.buf + buf
                idx = combined.find(closer)
                if idx < 0:
                    if len(combined) > TOOL_RUNAWAY or final:
                        folded.append(opener + combined)
                        self._reset_block()
                        buf = ""
                        if final:
                            return self._pack(visible, calls, folded)
                        continue
                    self.buf = combined
                    return self._pack(visible, calls, folded)
                inner = combined[:idx]
                buf = combined[idx + len(closer):]
                self._reset_block()
                call = parse_tool_call(inner)
                if call:
                    calls.append(call)
                else:
                    folded.append(opener + inner + closer)
                continue
            if self.block == "dsml":
                if not buf:
                    if final:
                        folded.append(self.buf)
                        self._reset_block()
                        return self._pack(visible, calls, folded)
                    return self._pack(visible, calls, folded)
                open_at = buf.find(DSML_OPEN_MARK)
                shut_at = buf.find(DSML_SHUT_MARK)
                spots = [i for i in (open_at, shut_at) if i >= 0]
                if not spots:
                    keep = 0 if final else self._tail_hold(buf)
                    if keep:
                        self.buf += buf[:-keep]
                        self.seen += len(buf) - keep
                        self.hold = buf[-keep:]
                    else:
                        self.buf += buf
                        self.seen += len(buf)
                    if final:
                        folded.append(self.buf)
                        self._reset_block()
                        return self._pack(visible, calls, folded)
                    if self.seen > DSML_RUNAWAY:
                        folded.append(self.buf)
                        self._reset_block()
                    return self._pack(visible, calls, folded)
                at = min(spots)
                if at > 0:
                    self.buf += buf[:at]
                    self.seen += at
                    buf = buf[at:]
                gt = buf.find(">")
                if gt < 0:
                    self.hold = buf
                    if final:
                        folded.append(self.buf + self.hold)
                        self.hold = ""
                        self._reset_block()
                        return self._pack(visible, calls, folded)
                    return self._pack(visible, calls, folded)
                tag = buf[:gt + 1]
                buf = buf[gt + 1:]
                is_shut = tag.startswith(DSML_SHUT_MARK) or tag.startswith("</")
                self.depth += -1 if is_shut else 1
                self.buf += tag
                self.seen += len(tag)
                if self.depth <= 0:
                    raw = self.buf
                    self._reset_block()
                    parsed = parse_dsml_calls(raw)
                    if parsed:
                        calls.extend(parsed)
                    else:
                        folded.append(raw)
                elif self.seen > DSML_RUNAWAY:
                    folded.append(self.buf)
                    self._reset_block()
                continue
            spots = []
            for marker in (TOOL_CALL_OPEN, LEGACY_TOOL_OPEN, DSML_OPEN_MARK):
                pos = buf.find(marker)
                if pos >= 0:
                    spots.append((pos, marker))
            if not spots:
                keep = 0 if final else self._tail_hold(buf)
                if keep:
                    self.hold = buf[-keep:]
                    out = buf[:-keep]
                else:
                    out = buf
                clean = self._visible(out)
                if clean:
                    visible.append(clean)
                return self._pack(visible, calls, folded)
            pos, marker = min(spots, key=lambda item: item[0])
            if pos > 0:
                clean = self._visible(buf[:pos])
                if clean:
                    visible.append(clean)
                buf = buf[pos:]
            if marker == DSML_OPEN_MARK:
                self._reset_block()
                self.block = "dsml"
                continue
            self._reset_block()
            self.block = "tool" if marker == TOOL_CALL_OPEN else "legacy"
            buf = buf[len(marker):]


def extract_tool_calls(text):
    """Last-resort single-shot parse. Returns (calls_or_None, folded_reasoning)."""
    if not isinstance(text, str):
        return None, ""
    scanner = ToolCallScanner()
    _visible, calls, folded = scanner.feed(text)
    _visible2, calls2, folded2 = scanner.flush()
    combined = (calls or []) + (calls2 or [])
    return (combined or None), (folded or "") + (folded2 or "")


def tool_name_map(messages):
    names = {}
    for m in messages or []:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        for call in m.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            cid = call.get("id")
            fn = call.get("function") or {}
            name = fn.get("name") if isinstance(fn, dict) else None
            if cid and name:
                names[str(cid)] = str(name)
    return names


def strip_tool_call_block(text):
    if not isinstance(text, str):
        return text
    for opener, closer in (
        (TOOL_CALL_OPEN, TOOL_CALL_SHUT),
        (LEGACY_TOOL_OPEN, LEGACY_TOOL_SHUT),
    ):
        while True:
            start = text.find(opener)
            if start < 0:
                break
            end = text.find(closer, start + len(opener))
            if end < 0:
                text = text[:start] + text[start + len(opener):]
                break
            text = text[:start] + text[end + len(closer):]
    return text.strip()


def strip_orphan_markers(text):
    """模型没按格式输出时，别把协议标记漏给用户。"""
    if not isinstance(text, str):
        return text
    for marker in (TOOL_CALL_OPEN, TOOL_CALL_SHUT, LEGACY_TOOL_OPEN, LEGACY_TOOL_SHUT):
        text = text.replace(marker, "")
    text = STRAY_DSML_RE.sub("", text)
    return text.strip()


def conversation_key(headers, payload, messages):
    for name in ("x-conversation-id", "conversation-id", "x-session-id", "x-chat-session-id"):
        value = headers.get(name)
        if value:
            return "header:" + str(value)
    for name in ("conversation_id", "session_id", "user"):
        value = payload.get(name)
        if value:
            return "payload:" + str(value)
    seed = {
        "first_user": first_user_text(messages),
        "model": payload.get("model") or get_model_type(),
        "user": payload.get("user"),
    }
    digest = hashlib.sha256(
        json.dumps(seed, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return "seed:" + digest


class Handler(BaseHTTPRequestHandler):
    server_version = "DeepSeekWebProxy/1.0"

    def log_message(self, fmt, *args):
        if get_bool("DEEPSEEK_ACCESS_LOG", "0"):
            super().log_message(fmt, *args)

    def _send_json(self, status, obj):
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            return raw, json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            return raw, {}

    def _write_sse(self, text):
        try:
            self.wfile.write(text.encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _api_path(self):
        path = self.path.split("?", 1)[0]
        if path == "/deepseek" or path.startswith("/deepseek/"):
            path = path[len("/deepseek"):] or "/"
        if path == "/ds":
            path = "/"
        return path

    def _send_html(self, status, html):
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self._api_path()
        if path in ("/", "/index.html", "/ds"):
            self._send_html(200, INDEX_HTML)
            return
        if path == "/health":
            self._send_json(200, {
                "ok": True,
                "service": "deepseek-web-openai-proxy",
                "base": get_base(),
                "token": bool(get_token()),
                "token_status": TOKEN_STATUS.get("state", "unknown"),
                "token_refreshed_at": TOKEN_STATUS.get("refreshed_at", 0),
                "token_source": TOKEN_STATUS.get("source", "file"),
                "upstream_pacing": {
                    "max_concurrency": upstream_gate().max_concurrency,
                    "min_interval_ms": int(upstream_gate().min_interval * 1000),
                    "max_retries": upstream_gate().max_retries,
                },
                "models": len(STATIC_MODEL_IDS),
                "endpoints": {
                    "web": "/",
                    "prefixed_web": "/deepseek/",
                    "chat": "/deepseek/v1/chat/completions",
                    "models": "/deepseek/v1/models",
                },
            })
            return
        if path in ("/v1/models", "/models"):
            try:
                self._send_json(200, DSClient().models())
            except Exception as exc:
                self._send_json(502, {"error": {"message": str(exc), "type": "upstream_error"}})
            return
        self._send_json(404, {"error": {"message": "Not Found", "type": "invalid_request_error"}})

    def do_POST(self):
        path = self._api_path()
        if path == "/_set_token":
            raw, _ = self._read_json()
            text = raw.decode("utf-8", "replace").strip()
            token = text
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    token = obj.get("token") or obj.get("userToken") or obj.get("user_token") or token
            except Exception:
                pass
            if token.lower().startswith("bearer "):
                token = token[7:].strip()
            if not set_token(token):
                self._send_json(400, {"error": {"message": "empty token", "type": "invalid_request_error"}})
                return
            self._send_json(200, {"ok": True, "token_len": len(token), "token_file": TOKEN_FILE})
            return
        if path in ("/v1/chat/completions", "/chat/completions"):
            self._handle_completion()
            return
        self._send_json(404, {"error": {"message": "Not Found", "type": "invalid_request_error"}})

    def _handle_completion(self):
        _, payload = self._read_json()
        if not isinstance(payload, dict):
            payload = {}
        if not get_token():
            self._send_json(401, {"error": {
                "message": "TOKEN missing; POST /_set_token or set DEEPSEEK_TOKEN",
                "type": "authentication_error",
            }})
            return
        messages = payload.get("messages") or []
        if not isinstance(messages, list) or not messages:
            self._send_json(400, {"error": {"message": "messages required", "type": "invalid_request_error"}})
            return
        raw_tools = payload.get("tools") or []
        tools = [t for t in raw_tools if isinstance(t, dict)]
        tools_active = bool(tools)
        tool_instructions = build_tool_instructions(tools) if tools else ""
        choice = payload.get("tool_choice")
        force = ""
        if isinstance(choice, dict):
            fn = choice.get("function")
            fname = fn.get("name") if isinstance(fn, dict) else None
            if fname:
                force = "本轮必须调用函数 " + str(fname) + "，只输出调用，不要输出任何自然语言。"
        elif str(choice or "").lower() in ("required", "any"):
            force = "本轮必须调用至少一个函数，只输出调用，不要输出任何自然语言。"
        if force:
            tool_instructions = (tool_instructions + "\n" + force).strip()
        if str(choice or "").lower() == "none":
            tools_active = False
            tool_instructions = ""
        key = conversation_key(self.headers, payload, messages)
        openai_model = str(payload.get("model") or get_model_type())
        resolved_model = resolve_model_type(openai_model)
        thinking_enabled = thinking_for_payload(payload)
        search_enabled = search_for_payload(payload)
        include_thinking = truthy(payload.get("include_thinking"), get_bool("DEEPSEEK_INCLUDE_THINKING", "0"))
        first_response = None
        client = None
        ref_file_ids = []
        session = None
        prompt = ""
        body_model_type = resolved_model
        session_id = None
        for attempt in range(2):
            ref_file_ids = []
            client = DSClient(model_type=resolved_model, thinking=thinking_enabled, search=search_enabled)
            try:
                with SESSION_LOCK:
                    session = SESSION_MAP.setdefault(key, {"chat_session_id": None, "parent_message_id": None})
                    session_id = session.get("chat_session_id")
                    parent = session.get("parent_message_id")
                had_session = bool(session_id)
                if not session_id:
                    session_id = client.create_session()
                    with SESSION_LOCK:
                        session["chat_session_id"] = session_id
                        session["parent_message_id"] = None
                    parent = None
                continuation = parent is not None
                if continuation:
                    from_index = len(messages)
                    for i in range(len(messages) - 1, -1, -1):
                        if isinstance(messages[i], dict) and messages[i].get("role") == "user":
                            from_index = i
                            break
                else:
                    from_index = 0
                attachments = extract_attachments(messages, from_index)
                ref_file_ids, file_notes = prepare_files(client, attachments)
                prompt = build_prompt(messages, continuation, tool_instructions, tools_active)
                if file_notes:
                    prompt = (prompt + "\n\n" + "\n".join(file_notes)).strip()
                body_model_type = None if continuation else resolved_model
                if ref_file_ids:
                    body_model_type = "default"
                    client.thinking = False
                first_response = client.request_turn(
                    COMPLETION_PATH, session_id=session_id, parent_message_id=parent,
                    prompt=prompt, model_type=body_model_type, ref_file_ids=ref_file_ids,
                )
                break
            except SignInExpired as exc:
                with TOKEN_LOCK:
                    TOKEN_STATUS["state"] = "expired"
                if attempt == 0 and refresh_token_from_browser():
                    with SESSION_LOCK:
                        SESSION_MAP.pop(key, None)
                    first_response = None
                    continue
                self._send_json(401, {"error": {
                    "message": "DeepSeek web sign-in expired; sign in to chat.deepseek.com in a Chromium browser and retry (or POST /_set_token with a fresh userToken).",
                    "type": "authentication_error",
                    "upstream": str(exc),
                }})
                return
            except StaleSession as exc:
                if attempt == 0 and had_session:
                    with SESSION_LOCK:
                        SESSION_MAP.pop(key, None)
                    first_response = None
                    continue
                self._send_json(502, {"error": {"message": str(exc), "type": "upstream_error"}})
                return
            except Exception as exc:
                self._send_json(502, {"error": {"message": str(exc), "type": "upstream_error"}})
                return
        if first_response is None:
            self._send_json(502, {"error": {"message": "upstream did not respond", "type": "upstream_error"}})
            return
        with TOKEN_LOCK:
            if TOKEN_STATUS.get("state") != "live":
                TOKEN_STATUS["state"] = "live"
        client.include_thinking = include_thinking
        stream = truthy(payload.get("stream", False))
        completion_id = "chatcmpl-" + uuid.uuid4().hex
        created = int(time.time())
        with SESSION_LOCK:
            parent_for_turn = session.get("parent_message_id")
        turn = deepseek_turn(
            client, session_id, parent_for_turn, prompt, body_model_type,
            ref_file_ids=ref_file_ids, first_response=first_response, include_thinking=True,
        )
        if stream:
            self._stream_completion(turn, completion_id, created, openai_model, include_thinking, session, tools_active)
        else:
            self._aggregate_completion(turn, completion_id, created, openai_model, include_thinking, session, tools_active)

    def _collect_events(self, turn, include_thinking, tools_active=False, scanner=None):
        content = []
        reasoning = []
        folded = []
        calls = []
        meta = {}
        error = ""
        filters = {
            "content": ControlTextFilter(strip_dsml=not tools_active),
            "reasoning": ControlTextFilter(),
        }

        def emit_content(raw):
            if scanner is None:
                if raw:
                    content.append(raw)
                    yield ("text", "content", raw)
                return
            if not raw:
                return
            visible, new_calls, new_folded = scanner.feed(raw)
            if visible:
                content.append(visible)
                yield ("text", "content", visible)
            for call in new_calls:
                calls.append(call)
                yield ("tool_calls", call)
            if new_folded:
                folded.append(new_folded)
                yield ("folded", new_folded)

        for event in turn:
            kind = event[0]
            if kind == "text":
                channel, text = event[1], event[2]
                clean = filters.setdefault(channel, ControlTextFilter(strip_dsml=not tools_active)).feed(text)
                if not clean:
                    continue
                if channel == "reasoning":
                    reasoning.append(clean)
                    yield ("text", "reasoning", clean)
                    continue
                for out in emit_content(clean):
                    yield out
            elif kind == "meta":
                for key, value in event[1].items():
                    if value is not None:
                        meta[key] = value
            elif kind == "error":
                error = event[1]
            elif kind == "done":
                for key, value in event[1].items():
                    if value is not None:
                        meta[key] = value
                break
        for channel, text_filter in filters.items():
            tail = text_filter.flush()
            if not tail:
                continue
            if channel == "reasoning":
                reasoning.append(tail)
                yield ("text", "reasoning", tail)
            else:
                for out in emit_content(tail):
                    yield out
        if scanner is not None:
            visible, new_calls, new_folded = scanner.flush()
            if visible:
                content.append(visible)
                yield ("text", "content", visible)
            for call in new_calls:
                calls.append(call)
                yield ("tool_calls", call)
            if new_folded:
                folded.append(new_folded)
                yield ("folded", new_folded)
        yield ("final", {
            "content": "".join(content),
            "reasoning": "".join(reasoning),
            "folded": "".join(folded),
            "calls": calls,
            "meta": meta,
            "status": meta.get("status"),
            "error": error,
        })

    def _stream_completion(self, turn, completion_id, created, model, include_thinking, session, tools_active=False):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self._write_sse(sse_chunk(completion_id, created, model, {"role": "assistant"}))
        final = {}
        call_count = 0
        try:
            scanner = ToolCallScanner() if tools_active else None
            for event in self._collect_events(turn, include_thinking, tools_active, scanner):
                kind = event[0]
                if kind == "text":
                    channel, text = event[1], event[2]
                    if channel == "reasoning":
                        if include_thinking:
                            self._write_sse(sse_chunk(completion_id, created, model, {"reasoning_content": text}))
                    else:
                        self._write_sse(sse_chunk(completion_id, created, model, {"content": text}))
                elif kind == "folded":
                    if include_thinking:
                        self._write_sse(sse_chunk(completion_id, created, model, {"reasoning_content": event[1]}))
                elif kind == "tool_calls":
                    call = event[1]
                    self._write_sse(sse_chunk(completion_id, created, model, {
                        "tool_calls": [{
                            "index": call_count,
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": call["arguments"]},
                        }],
                    }))
                    call_count += 1
                elif kind == "final":
                    final = event[1]
            if tools_active and not call_count:
                extra, _folded = extract_tool_calls(final.get("content", ""))
                if extra:
                    for i, call in enumerate(extra):
                        self._write_sse(sse_chunk(completion_id, created, model, {
                            "tool_calls": [{
                                "index": i,
                                "id": call["id"],
                                "type": "function",
                                "function": {"name": call["name"], "arguments": call["arguments"]},
                            }],
                        }))
                    call_count = len(extra)
            if call_count:
                self._write_sse(sse_chunk(completion_id, created, model, {}, "tool_calls"))
            else:
                content = str(final.get("content", ""))
                if not content.strip():
                    fallback = str(final.get("reasoning", "")).strip() or "（空响应）"
                    self._write_sse(sse_chunk(completion_id, created, model, {"content": fallback}))
                self._write_sse(sse_chunk(completion_id, created, model, {}, finish_reason_for(final.get("status"))))
            self._write_sse("data: [DONE]\n\n")
        except Exception as exc:
            err = {"error": {"message": str(exc), "type": "stream_error"}}
            self._write_sse("data: " + json.dumps(err, ensure_ascii=False, separators=(",", ":")) + "\n\n")
            self._write_sse("data: [DONE]\n\n")
        finally:
            parent = final.get("meta", {}).get("parent_message_id")
            with SESSION_LOCK:
                session["parent_message_id"] = parent
            self.close_connection = True

    def _aggregate_completion(self, turn, completion_id, created, model, include_thinking, session, tools_active=False):
        try:
            final = {}
            calls = []
            scanner = ToolCallScanner() if tools_active else None
            for event in self._collect_events(turn, include_thinking, tools_active, scanner):
                if event[0] == "final":
                    final = event[1]
                elif event[0] == "tool_calls":
                    calls.append(event[1])
            content = str(final.get("content", ""))
            reasoning = str(final.get("reasoning", ""))
            folded = str(final.get("folded", ""))
            if tools_active and not calls:
                extra, more_folded = extract_tool_calls(content)
                if extra:
                    calls = extra
                    folded += more_folded
            if calls:
                content = strip_orphan_markers(strip_tool_call_block(content))
            else:
                content = strip_orphan_markers(content)
            if not content.strip() and reasoning and not calls:
                content = reasoning
            if not content.strip() and not calls:
                content = "（空响应）"
            message = {"role": "assistant", "content": content}
            finish_reason = "stop"
            if calls:
                message["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for c in calls
                ]
                finish_reason = "tool_calls"
            else:
                finish_reason = finish_reason_for(final.get("status"))
            if include_thinking and (reasoning or folded):
                message["reasoning_content"] = reasoning + folded
            obj = {
                "id": completion_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
            if final.get("error"):
                obj["upstream_hint"] = final["error"]
            self._send_json(200, obj)
        except Exception as exc:
            self._send_json(502, {"error": {"message": str(exc), "type": "stream_error"}})
        finally:
            parent = locals().get("final", {}).get("meta", {}).get("parent_message_id")
            with SESSION_LOCK:
                session["parent_message_id"] = parent

def selftest():
    prefix = "salt_123_"
    expected = "508526b4c1f8138ca3221fd99448aa2d00ba5ec37e603a1d2dc389e56279b281"
    sha3 = "4b6fb288eb30b483e90d495ba2136c9e8074337bec58f5e7b4179d388a2d3db9"
    port_digest = deepseek_hash((prefix + "0").encode("utf-8"))
    sha3_digest = hashlib.sha3_256((prefix + "0").encode("utf-8")).hexdigest()
    answer = solve("DeepSeekHashV1", expected, "salt", 123, 10, 0)
    assert port_digest == expected, port_digest
    assert sha3_digest == sha3, sha3_digest
    assert answer == 0, answer
    challenge = {
        "algorithm": "DeepSeekHashV1",
        "challenge": expected,
        "salt": "salt",
        "answer": answer,
        "signature": "sig",
        "target_path": COMPLETION_PATH,
    }
    header = pow_response_header(challenge, answer)
    decoded = json.loads(base64.b64decode(header).decode("utf-8"))
    assert decoded["answer"] == 0 and decoded["target_path"] == COMPLETION_PATH
    chunk = sse_chunk("chatcmpl-test", 0, "default", {"content": "A"})
    assert '"object":"chat.completion.chunk"' in chunk and '"content":"A"' in chunk
    state = DeltaState()
    state.apply({"p": "", "o": "SET", "v": {"response": {"fragments": [{"type": "RESPONSE", "content": "ABC"}]}}})
    state.apply({"p": "response/fragments/0/content", "o": "APPEND", "v": "DE"})
    delta_text = state.visible_text("content")
    assert delta_text == "ABCDE", delta_text
    neg_state = DeltaState()
    neg_state.apply({"p": "", "o": "SET", "v": {"response": {"fragments": [
        {"type": "THINK", "content": "R"},
        {"type": "RESPONSE", "content": "我是"},
    ]}}})
    neg_state.apply({"p": "response/fragments/-1/content", "o": "APPEND", "v": "OK"})
    assert neg_state.visible_text("content") == "我是OK", neg_state.visible_text("content")
    assert neg_state.visible_text("reasoning") == "R", neg_state.visible_text("reasoning")
    dsml_sample = (
        "前<｜DSML｜｜ calls>"
        "<｜DSML｜｜ invoke name=\"list_agents\"></｜DSML｜｜ invoke>"
        "</｜DSML｜｜ calls>已完成已完成，用时 2秒后"
    )
    assert sanitize_control_text(dsml_sample) == "前后", sanitize_control_text(dsml_sample)
    stream_filter = ControlTextFilter()
    streamed = "".join(stream_filter.feed(ch) for ch in dsml_sample) + stream_filter.flush()
    assert streamed == "前后", streamed
    normal_filter = ControlTextFilter()
    normal_streamed = "".join(normal_filter.feed(ch) for ch in "已经完成") + normal_filter.flush()
    assert normal_streamed == "已经完成", normal_streamed
    tool_prompt = build_prompt([
        {"role": "system", "content": "Use <｜DSML｜｜ calls> list_agents"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1"}]},
        {"role": "user", "content": "只回复：OK"},
    ], False)
    assert tool_prompt == "只回复：OK", tool_prompt
    assert [item["id"] for item in static_models()["data"]] == STATIC_MODEL_IDS
    assert thinking_for_payload({"model": "default"}) is False
    assert thinking_for_payload({"model": "deepseek-r1"}) is True
    assert thinking_for_payload({"model": "default", "thinking_enabled": True}) is True
    assert thinking_for_payload({"model": "default", "reasoning_effort": "high"}) is True
    assert thinking_for_payload({"model": "default", "thinking_enabled": False}) is False
    assert thinking_for_payload({"model": "deepseek-r1", "thinking_enabled": False}) is False
    assert search_for_payload({"model": "default"}) is False
    assert search_for_payload({"model": "default", "search_enabled": True}) is True
    assert search_for_payload({"model": "deepseek-search", "search_enabled": False}) is False
    assert search_for_payload({"model": "default", "extra_body": {"search_enabled": True}}) is True
    # --- borrowed from magpie-deepseek-web-auth: dual-format tool calling ---
    tools = [{
        "type": "function",
        "function": {"name": "get_time", "description": "Get the time", "parameters": {"type": "object", "properties": {}}},
    }]
    protocol = tools_to_prompt(tools)
    assert "<tool_call>" in protocol and '"name":"get_time"' in protocol and "DSML" in protocol
    call = parse_tool_call('{"name":"get_time","arguments":{"city":"上海"}}')
    assert call and call["name"] == "get_time" and json.loads(call["arguments"])["city"] == "上海"
    assert parse_tool_call("not json") is None
    dsml = (
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="exec_command">'
        '<｜｜DSML｜｜ parameter name="cmd" string="true">ls -la</｜｜DSML｜｜ parameter>'
        '</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>'
    )
    dsml_calls = parse_dsml_calls(dsml)
    assert len(dsml_calls) == 1
    assert dsml_calls[0]["name"] == "exec_command"
    assert json.loads(dsml_calls[0]["arguments"])["cmd"] == "ls -la"
    scanner = ToolCallScanner()
    v1, c1, f1 = scanner.feed("你好 <tool_call>{\"name\":\"get_time\",\"arg")
    v2, c2, f2 = scanner.feed('uments\":{}}</tool_call> 完成')
    v3, c3, f3 = scanner.flush()
    assert re.sub(r"\s+", " ", (v1 + v2 + v3)).strip() == "你好 完成"
    assert len(c1 + c2 + c3) == 1 and (c1 + c2 + c3)[0]["name"] == "get_time"
    scanner2 = ToolCallScanner()
    v1, c1, f1 = scanner2.feed("<tool_call>not json</tool_call>正文")
    v2, c2, f2 = scanner2.flush()
    assert (v1 + v2) == "正文" and not (c1 + c2) and "not json" in (f1 + f2)
    scanner3 = ToolCallScanner()
    v1, c1, f1 = scanner3.feed(dsml)
    v2, c2, f2 = scanner3.flush()
    assert len(c1 + c2) == 1 and (c1 + c2)[0]["name"] == "exec_command"
    assert (v1 + v2).strip() == ""
    history = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_time", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "10:00"},
        {"role": "user", "content": "几点?"},
    ]
    prompt = build_prompt(history, False, tools_to_prompt(tools), True)
    assert "<tool_call>" in prompt and "Tool result (get_time): 10:00" in prompt
    extra_calls, folded = extract_tool_calls('答 <tool_call>{"name":"get_time","arguments":{}}</tool_call>')
    assert extra_calls and extra_calls[0]["name"] == "get_time" and folded == ""
    # --- envelope / sign-in detection (top-level code, not just biz_code) ---
    try:
        check_envelope({"code": 40003, "msg": "INVALID_TOKEN", "data": None}, "test")
        raise AssertionError("SignInExpired not raised")
    except SignInExpired:
        pass
    assert check_envelope({"code": 0, "data": {"biz_code": 0, "biz_data": {"ok": 1}}}, "test") == {"ok": 1}
    # --- upstream pacing ---
    gate = UpstreamGate(2, 0.01, 2, 0.05, 0.4, 0.0)
    assert gate.max_concurrency == 2 and gate.max_retries == 2 and gate.backoff(0) >= 0.0
    print("SELFTEST tool_call_layer=ok")
    print("SELFTEST token_envelope=ok")
    print("SELFTEST upstream_pacing=ok")
    print("SELFTEST port_digest=" + port_digest)
    print("SELFTEST sha3_256  =" + sha3_digest)
    print("SELFTEST solve0=" + str(answer))
    print("SELFTEST pow_header=ok")
    print("SELFTEST sse_chunk=ok")
    print("SELFTEST delta_state=" + delta_text)
    print("SELFTEST negative_fragment_index=ok")
    print("SELFTEST control_filter=ok")
    print("SELFTEST tool_prompt=ok")
    print("SELFTEST model_catalog=ok")
    # --- turn state machine / finish_reason / wire model / attachments ---
    class _FR:
        def __init__(self, frames, ctype="text/event-stream", text=""):
            self._frames = frames
            self.headers = {"content-type": ctype}
            self.text = text
        def iter_lines(self):
            for ev, obj in self._frames:
                yield "event: " + ev
                yield "data: " + json.dumps(obj, ensure_ascii=False)
                yield ""
        def close(self):
            pass
    class _FC:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = []
        def request_turn(self, endpoint=COMPLETION_PATH, **kw):
            self.calls.append(endpoint)
            return self.responses.pop(0)
    def _collect(turn):
        ev = list(Handler._collect_events(object(), turn, True, False, None))
        return [e[1] for e in ev if e[0] == "final"][0]
    r1 = _FR([("ready", {"response_message_id": 1}),
              ("delta", {"p": "", "o": "SET", "v": {"response": {"fragments": [{"type": "RESPONSE", "content": "A"}]}}}),
              ("delta", {"p": "response/status", "o": "SET", "v": "INCOMPLETE"}), ("close", {})])
    r2 = _FR([("ready", {"response_message_id": 1}),
              ("delta", {"p": "response/fragments/-1/content", "o": "APPEND", "v": "B"}),
              ("delta", {"p": "response/status", "o": "SET", "v": "FINISHED"}), ("close", {})])
    fc = _FC([r1, r2])
    fin = _collect(deepseek_turn(fc, "s", None, "hi", "default"))
    assert fin["content"] == "AB" and fc.calls == [COMPLETION_PATH, CONTINUE_PATH], (fin["content"], fc.calls)
    e1 = _FR([("ready", {"response_message_id": 2}), ("delta", {"p": "response/status", "o": "SET", "v": "FINISHED"}), ("close", {})])
    e2 = _FR([("ready", {"response_message_id": 2}),
              ("delta", {"p": "", "o": "SET", "v": {"response": {"fragments": [{"type": "RESPONSE", "content": "R"}]}}}), ("close", {})])
    fc = _FC([e1, e2])
    fin = _collect(deepseek_turn(fc, "s", None, "hi", "default"))
    assert fin["content"] == "R" and fc.calls == [COMPLETION_PATH, RESUME_PATH], (fin["content"], fc.calls)
    w = _FR([], ctype="application/json", text=json.dumps({"code": 0, "data": {"biz_code": 22, "biz_data": {"response": {"message_id": 3, "status": "FINISHED", "fragments": [{"type": "RESPONSE", "content": "W"}]}}}}))
    fin = _collect(deepseek_turn(_FC([w]), "s", None, "hi", "default"))
    assert fin["content"] == "W"
    assert finish_reason_for("CONTENT_FILTER") == "content_filter"
    assert finish_reason_for("CONTEXT_LENGTH_EXCEEDED") == "length"
    assert finish_reason_for("TIMEOUT") == "length"
    assert resolve_model_type("deepseek-reasoner") == "expert"
    assert resolve_model_type("deepseek-chat") == "default"
    _img = attachment_from_part({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"P").decode()}})
    assert _img["kind"] == "image" and _img["mime"] == "image/png" and _img["data"] == b"P"
    assert filename_for({"kind": "file", "mime": "application/pdf"}) == "document.pdf"
    print("SELFTEST turn_state_machine=ok")
    print("SELFTEST finish_reason_map=ok")
    print("SELFTEST wire_model_map=ok")
    print("SELFTEST attachment_parse=ok")
    print("SELFTEST ALL_OK")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=get_int("DEEPSEEK_PORT", 8080))
    parser.add_argument("--host", default=os.environ.get("DEEPSEEK_HOST", "127.0.0.1"))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print("DEEPSEEK_PROXY_READY http://" + args.host + ":" + str(args.port), flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
