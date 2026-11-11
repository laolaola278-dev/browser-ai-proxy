#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
duck2api_proxy.py — standalone reconstruction of the "代理" (gateway) inside
D:/xjx/网盘下载/duck.apk (package dev.duck2api, "Duck 网关" v1.0.4).

The APK is an Android foreground service that listens on 127.0.0.1:7749 and
exposes an OpenAI-compatible API backed by duck.ai. This script reproduces it
on a PC without Android:

  GET  / , /health , /v1        -> {"status":"ok","service":"duck2api"}
  GET  /v1/models               -> {"object":"list","data":[...]}
  POST /v1/chat/completions     -> chat.completion / chat.completion.chunk

Upstream (reverse-engineered from classes.dex, R8-obfuscated):
  GET  {base}/duckchat/v1/models
  POST {base}/duckchat/v1/chat   (text/event-stream)
  headers: X-Vqd-Hash-1, Origin, Referer, User-Agent, Accept-Language
  body: {"model","messages","canUseTools":false,"reasoningEffort":...,
         "canUseApproxLocation":false,"canUseWebSearch":false,
         "canUploadFiles":false,"canShowGreeting":false}

The X-Vqd-Hash-1 challenge token is solved with the EXACT JavaScript extracted
verbatim from the APK (challenge.js, class b.h.j) inside a real Chrome page.
The upstream chat must be issued from that SAME browser page: the token is
bound to the browser session (cookies / TLS / client hints). This is why the
APK uses a WebView — a plain Python HTTP client with a copied token gets
418 ERR_CHALLENGE. Two transports are provided:
  --transport browser  (default, faithful, solves + chats in Chrome)
  --transport direct   (plain requests; requires --token from a matching client)

Self-check without network:  python duck2api_proxy.py --selftest
Run:                         python duck2api_proxy.py --port 7749
Token only:                  python get_token.py --out token.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
CHALLENGE_JS = os.path.join(HERE, "challenge.js")
MODELS_JSON = os.path.join(HERE, "models-extracted.json")

# ---------------------------------------------------------------------------
# Verbatim from the APK (see protocol.md for the dex -> class mapping)
# ---------------------------------------------------------------------------
SHIM = r"""
window.__duckResult = null;
window.DuckBridge = {
  post: function(token, userAgent) {
    window.__duckResult = {ok: true, token: token, userAgent: userAgent};
  },
  fail: function(message) {
    window.__duckResult = {ok: false, error: String(message)};
  }
};
"""

CHAT_JS = r"""
async (a) => {
  const resp = await fetch('/duckchat/v1/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Accept': 'text/event-stream',
      'Origin': location.origin,
      'Referer': location.origin + '/',
      'Accept-Language': 'en-US,en;q=0.9',
      'X-Vqd-Hash-1': a.token
    },
    body: JSON.stringify(a.body),
    credentials: 'include',
    cache: 'no-store'
  });
  const status = resp.status;
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  const lines = [];
  const emit = async (line) => {
    lines.push(line);
    if (status === 200) { await window.__duckChunk(line + '\n'); }
  };
  while (true) {
    const r = await reader.read();
    if (r.done) break;
    buf += dec.decode(r.value, {stream: true});
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      await emit(buf.slice(0, i).replace(/\r$/, ''));
      buf = buf.slice(i + 1);
    }
  }
  buf += dec.decode();
  if (buf) { await emit(buf); }
  return {status: status, text: lines.join('\n')};
}
"""

MODELS_JS = r"""
async () => {
  const r = await fetch('/duckchat/v1/models', {
    headers: {'Accept': 'application/json'}, credentials: 'include'
  });
  return {status: r.status, text: await r.text()};
}
"""

ERR_INPUT = {"ERR_INPUT_LIMIT", "ERR_SELECTION_INPUT_LIMIT",
             "ERR_OUTPUT_LIMIT", "ERR_MAX_TOKENS"}
ERR_MODEL = {"ERR_MODEL_RESTRICTED", "ERR_MODEL_UNAVAILABLE",
             "ERR_MODEL_DEPRECATED", "ERR_MODEL_REMOVED"}
ERR_TEXT = {
    "ERR_INPUT_LIMIT": "单条消息过长（超出 duck.ai 上限），请缩短后重试",
    "ERR_OUTPUT_LIMIT": "回复长度超出 duck.ai 上限",
    "ERR_MAX_TOKENS": "ERR_MAX_TOKENS",
    "ERR_SELECTION_INPUT_LIMIT": "选中的文本过长",
    "ERR_USER_LIMIT": "duck.ai 账号用量已达上限",
    "ERR_SERVICE_LIMIT": "duck.ai 服务用量已达上限",
    "ERR_RATE_LIMIT": "duck.ai 请求过于频繁，请稍后重试",
    "ERR_CONVERSATION_LIMIT": "本对话已达最大长度，请新开对话",
    "ERR_ACCOUNT_LIMIT": "duck.ai 账号用量已达上限",
    "ERR_MODEL_UNAVAILABLE": "该模型当前不可用",
    "ERR_MODEL_REMOVED": "该模型已下线",
    "ERR_MODEL_RESTRICTED": "该模型需要付费订阅，当前不可用",
    "ERR_MODEL_DEPRECATED": "该模型已下线",
    "ERR_REFUSAL": "duck.ai 拒绝了本轮请求",
    "ERR_CHALLENGE": "duck.ai 挑战凭证失效（ERR_CHALLENGE）",
    "ERR_COUNTRY_NOT_SUPPORTED": "duck.ai 当前网络地区不支持，请切换海外代理/VPN 节点后重试",
}


class DuckError(Exception):
    def __init__(self, etype: str, message: str, status: int = 0):
        super().__init__(message)
        self.etype = etype or "upstream_error"
        self.message = message or "upstream error"
        self.status = status


def friendly_upstream_error(exc: Exception) -> DuckError:
    text = str(exc or "")
    if "ERR_COUNTRY_NOT_SUPPORTED" in text or "status 451" in text or "451," in text:
        return DuckError("upstream_error",
                         "duck.ai 当前网络地区不支持（HTTP 451 ERR_COUNTRY_NOT_SUPPORTED）。"
                         "请把电脑代理切到可用海外节点；当前 iKuuu 本地代理对 duck.ai TLS 失败。",
                         451)
    if "no x-vqd-hash-1 header" in text:
        return DuckError("upstream_error",
                         "无法获取 duck.ai 挑战凭证（X-Vqd-Hash-1）。通常是地区限制、代理断开或 duck.ai 限流。",
                         502)
    return DuckError("upstream_error", text[:300] or type(exc).__name__, 502)


def classify_error(etype: str, http_status: int) -> str:
    """Port of e.f.i() + e.f.g() in the APK."""
    t = (etype or "").upper()
    if t == "ERR_CHALLENGE":
        return "upstream_error"
    if t in ERR_INPUT:
        return "invalid_request_error"
    if t in ERR_MODEL:
        return "model_not_available"
    if "RATE_LIMIT" in t or "LIMIT" in t or http_status == 429:
        return "rate_limit_exceeded"
    if http_status == 400:
        return "invalid_request_error"
    if http_status in (401, 403):
        return "invalid_api_key"
    if http_status == 404:
        return "not_found"
    return "upstream_error"


def status_for(etype: str) -> int:
    """Port of d.b.h() in the APK."""
    return {"invalid_request_error": 400, "context_length_exceeded": 400,
            "model_not_available": 400, "invalid_api_key": 401,
            "rate_limit_exceeded": 429}.get(etype, 502)


def error_message(etype: str, raw: str, http_status: int) -> str:
    t = (etype or "").upper()
    if t in ERR_TEXT:
        return ERR_TEXT[t]
    if raw:
        return raw[:400]
    return "duck.ai 返回 HTTP %s" % http_status


def load_fallback_models():
    try:
        with open(MODELS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return [{"id": "gpt-6-luna", "name": "GPT-6 Luna", "provider": "openai",
                 "supportedReasoningEffort": ["none", "low"],
                 "accessTier": ["free", "plus", "pro", "internal"],
                 "entityHasAccess": True}]


class SSEParser:
    """Port of e.f.k(): read duck.ai SSE 'data:' lines."""

    def __init__(self, on_message=None):
        self.buf = ""
        self.messages = []
        self.error = None
        self.done = False
        self.on_message = on_message

    def feed(self, text: str):
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._line(line.strip())

    def _line(self, line: str):
        if not line.startswith("data:"):
            return
        payload = line[5:].strip()
        if payload == "[DONE]":
            self.done = True
            return
        try:
            obj = json.loads(payload)
        except Exception:
            return
        if obj.get("action") == "error":
            self.error = DuckError(obj.get("type", ""),
                                   obj.get("message", ""),
                                   obj.get("status", 0) or 0)
            return
        msg = obj.get("message")
        if msg:
            self.messages.append(msg)
            if self.on_message:
                self.on_message(msg)


# ---------------------------------------------------------------------------
# Upstream transports
# ---------------------------------------------------------------------------
class BrowserUpstream:
    """Faithful transport: challenge + chat inside one real Chrome page."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.chat_lock = threading.Lock()
        self._on_line = None
        self.token = None
        self.ua = DEFAULT_UA
        self.token_ts = 0.0
        self.models_cache = None
        self.models_ts = 0.0
        self.pw = None
        self.browser = None
        self.ctx = None
        self.page = None
        self.challenge_js = ""
        with open(CHALLENGE_JS, "r", encoding="utf-8") as f:
            self.challenge_js = f.read()

    def start(self):
        from playwright.sync_api import sync_playwright
        args = ["--ignore-certificate-errors",
                "--disable-blink-features=AutomationControlled",
                "--exclude-switches=enable-automation"]
        if self.cfg.proxy:
            args.append("--proxy-server=" + self.cfg.proxy)
        else:
            args += ["--proxy-server=direct://", "--proxy-bypass-list=*"]
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
            channel=self.cfg.browser, headless=self.cfg.headless, args=args)
        self.ctx = self.browser.new_context(
            user_agent=self.cfg.ua, ignore_https_errors=True, locale="en-US")
        self.ctx.add_init_script(SHIM)
        self.page = self.ctx.new_page()
        self.page.set_default_timeout(max(self.cfg.read_timeout, 60) * 1000)
        self.page.expose_binding("__duckChunk", self._chunk)
        base = self.cfg.upstream.rstrip("/")
        self.page.goto(base + "/", wait_until="domcontentloaded",
                       timeout=self.cfg.connect_timeout * 1000)
        try:
            self.page.wait_for_selector("#jsa", timeout=15000)
        except Exception:
            pass
        self._solve_with_fallback()
        return self

    def _solve_with_fallback(self):
        """In-page challenge first; if duck.ai throttles the browser's
        /status fetch (429), fall back to the pure requests+Node handshake.
        The resulting token is portable and works in the page too."""
        try:
            self._solve()
        except Exception as e:
            print("[browser] in-page challenge failed (%s), trying pure handshake" %
                  str(e)[:120], file=sys.stderr)
            try:
                tok, ua = solve_token_pure(self.cfg)
                self.token, self.ua, self.token_ts = tok, ua, time.time()
            except Exception as e2:
                print("[browser] pure handshake also failed (%s); will retry "
                      "on first request" % str(e2)[:120], file=sys.stderr)

    def _chunk(self, source, text):
        cb = self._on_line
        if cb:
            cb(text)

    def _solve(self):
        self.page.evaluate("window.__duckResult = null")
        self.page.evaluate(self.challenge_js)
        self.page.wait_for_function("window.__duckResult !== null",
                                    timeout=self.cfg.connect_timeout * 1000)
        res = self.page.evaluate("window.__duckResult")
        if not res or not res.get("ok"):
            raise DuckError("upstream_error",
                            "challenge: " + str(res and res.get("error")))
        self.token = res["token"]
        self.ua = res.get("userAgent") or self.cfg.ua
        self.token_ts = time.time()

    def refresh(self):
        with self.lock:
            # Pure handshake first: the browser's in-page /status fetch is
            # often throttled (429) after repeated challenges.
            try:
                tok, ua = solve_token_pure(self.cfg)
                self.token, self.ua, self.token_ts = tok, ua, time.time()
                return
            except Exception as e:
                print("[browser] pure refresh failed (%s), reloading page" %
                      str(e)[:120], file=sys.stderr)
            base = self.cfg.upstream.rstrip("/")
            self.page.reload(wait_until="domcontentloaded",
                             timeout=self.cfg.connect_timeout * 1000)
            try:
                self.page.wait_for_selector("#jsa", timeout=15000)
            except Exception:
                pass
            self._solve_with_fallback()

    def ensure_token(self):
        if not self.token or (time.time() - self.token_ts) > self.cfg.token_ttl:
            self.refresh()

    def models(self):
        with self.lock:
            if self.models_cache and (time.time() - self.models_ts) < self.cfg.token_ttl:
                return self.models_cache
            try:
                out = self.page.evaluate(MODELS_JS)
                data = json.loads(out["text"]) if out["status"] == 200 else {}
                parsed = parse_models(data)
                if parsed:
                    self.models_cache, self.models_ts = parsed, time.time()
                    return parsed
            except Exception as e:
                print("[!] models fetch:", e, file=sys.stderr)
            return self.models_cache or load_fallback_models()

    def chat(self, body, on_line):
        with self.chat_lock:
            self.ensure_token()
            self._on_line = on_line
            try:
                out = self.page.evaluate(CHAT_JS, {"body": body, "token": self.token})
                return out["status"], out.get("text", "")
            finally:
                self._on_line = None

    def close(self):
        try:
            if self.browser:
                self.browser.close()
            if self.pw:
                self.pw.stop()
        except Exception:
            pass


class DirectUpstream:
    """Plain requests transport. Needs a token supplied via --token/--token-file.

    The token is NOT bound to the client that solved it (verified): a token from
    a non-automation-flagged Chrome works from plain requests. Earlier 418s came
    from tokens solved by a bot-flagged headless Chromium, not from the client.
    """

    def __init__(self, cfg):
        import requests
        self.cfg = cfg
        self.s = requests.Session()
        self.s.trust_env = cfg.trust_env
        if cfg.proxy:
            self.s.proxies = {"http": cfg.proxy, "https": cfg.proxy}
        self.models_cache = None
        self.models_ts = 0.0

    def _headers(self, extra=None):
        h = {"User-Agent": self.cfg.ua,
             "Accept-Language": "en-US,en;q=0.9",
             "Origin": self.cfg.upstream.rstrip("/"),
             "Referer": self.cfg.upstream.rstrip("/") + "/"}
        if extra:
            h.update(extra)
        return h

    def models(self):
        if self.models_cache and (time.time() - self.models_ts) < self.cfg.token_ttl:
            return self.models_cache
        try:
            r = self.s.get(self.cfg.upstream.rstrip("/") + "/duckchat/v1/models",
                           headers=self._headers({"Accept": "application/json"}),
                           timeout=self.cfg.connect_timeout, verify=not self.cfg.insecure)
            if r.status_code == 200:
                parsed = parse_models(r.json())
                if parsed:
                    self.models_cache, self.models_ts = parsed, time.time()
                    return parsed
        except Exception as e:
            print("[!] models fetch:", e, file=sys.stderr)
        return load_fallback_models()

    def chat(self, body, on_line):
        h = self._headers({"Content-Type": "application/json",
                           "Accept": "text/event-stream",
                           "X-Vqd-Hash-1": self.cfg.token})
        r = self.s.post(self.cfg.upstream.rstrip("/") + "/duckchat/v1/chat",
                        headers=h, data=json.dumps(body).encode("utf-8"),
                        timeout=(self.cfg.connect_timeout, self.cfg.read_timeout),
                        verify=not self.cfg.insecure, stream=True)
        # duck.ai SSE is UTF-8 but often omits charset; requests would otherwise
        # decode it as ISO-8859-1 and Chinese text becomes mojibake.
        r.encoding = "utf-8"
        lines = []
        for raw in r.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            lines.append(raw)
            if r.status_code == 200:
                on_line(raw + "\n")
        return r.status_code, "\n".join(lines)

    def refresh(self):
        pass

    def close(self):
        self.s.close()


def solve_token_pure(cfg):
    """Solve the challenge with pure requests + Node(vm), no browser at all."""
    import pure_handshake as ph
    h = ph.PureHandshake(base=cfg.upstream, ua=cfg.ua, node=cfg.node,
                         proxy=cfg.proxy, trust_env=cfg.trust_env,
                         insecure=cfg.insecure,
                         connect_timeout=cfg.connect_timeout,
                         token_ttl=cfg.token_ttl)
    try:
        return h.solve(force=True)
    finally:
        h.close()


def solve_token_chrome(cfg):
    """One-shot Chrome solve used as a fallback when the pure solver cannot
    handle a newly rotated challenge. Creates its own Playwright instance in
    the CALLING thread, so it is safe from any handler thread."""
    from playwright.sync_api import sync_playwright
    args = ["--ignore-certificate-errors",
            "--disable-blink-features=AutomationControlled",
            "--exclude-switches=enable-automation"]
    if cfg.proxy:
        args.append("--proxy-server=" + cfg.proxy)
    else:
        args += ["--proxy-server=direct://", "--proxy-bypass-list=*"]
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=cfg.browser, headless=cfg.headless,
                                    args=args)
        ctx = browser.new_context(user_agent=cfg.ua, ignore_https_errors=True,
                                  locale="en-US")
        ctx.add_init_script(SHIM)
        page = ctx.new_page()
        page.set_default_timeout(cfg.connect_timeout * 1000)
        page.goto(cfg.upstream.rstrip("/") + "/", wait_until="domcontentloaded",
                  timeout=cfg.connect_timeout * 1000)
        try:
            page.wait_for_selector("#jsa", timeout=15000)
        except Exception:
            pass
        with open(CHALLENGE_JS, "r", encoding="utf-8") as f:
            challenge_js = f.read()
        page.evaluate("window.__duckResult = null")
        page.evaluate(challenge_js)
        page.wait_for_function("window.__duckResult !== null",
                               timeout=cfg.connect_timeout * 1000)
        res = page.evaluate("window.__duckResult")
        browser.close()
    if not res.get("ok"):
        raise DuckError("upstream_error", "chrome challenge: " + str(res.get("error")))
    return res["token"], res.get("userAgent") or cfg.ua


def direct_chat(cfg, session, token, ua, body, on_line):
    """POST /duckchat/v1/chat with a plain requests session."""
    base = cfg.upstream.rstrip("/")
    h = {"Content-Type": "application/json", "Accept": "text/event-stream",
         "Origin": base, "Referer": base + "/", "User-Agent": ua,
         "Accept-Language": "en-US,en;q=0.9", "X-Vqd-Hash-1": token}
    r = session.post(base + "/duckchat/v1/chat", headers=h,
                     data=json.dumps(body).encode("utf-8"),
                     timeout=(cfg.connect_timeout, cfg.read_timeout),
                     verify=not cfg.insecure, stream=True)
    # duck.ai SSE is UTF-8 but often omits charset; requests would otherwise
    # decode it as ISO-8859-1 and Chinese text becomes mojibake.
    r.encoding = "utf-8"
    lines = []
    for raw in r.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        lines.append(raw)
        if r.status_code == 200:
            on_line(raw + "\n")
    return r.status_code, "\n".join(lines)


class PureUpstream:
    """Fully browser-free: requests + Node(vm) DOM mock (pure_handshake.py).
    Falls back to a one-shot Chrome solve if a new challenge generation appears.
    """

    def __init__(self, cfg):
        import pure_handshake as ph
        self.cfg = cfg
        self.ph = ph.PureHandshake(
            base=cfg.upstream, ua=cfg.ua, node=cfg.node, proxy=cfg.proxy,
            trust_env=cfg.trust_env, insecure=cfg.insecure,
            connect_timeout=cfg.connect_timeout, token_ttl=cfg.token_ttl)
        self.fallback = None       # (token, ua, ts)
        self.lock = threading.Lock()
        self.models_cache = None
        self.models_ts = 0.0

    def _fallback_token(self):
        with self.lock:
            if self.fallback and (time.time() - self.fallback[2]) < self.cfg.token_ttl:
                return self.fallback[0], self.fallback[1]
            tok, ua = solve_token_chrome(self.cfg)
            self.fallback = (tok, ua, time.time())
            print("[pure] challenge fell back to a one-shot Chrome solve",
                  file=sys.stderr)
            return tok, ua

    def chat(self, body, on_line):
        try:
            return self.ph.chat(body, on_line, read_timeout=self.cfg.read_timeout)
        except Exception as e:
            print("[pure] solver failed (%s), using Chrome fallback" %
                  str(e)[:160], file=sys.stderr)
        try:
            tok, ua = self._fallback_token()
            return direct_chat(self.cfg, self.ph.s, tok, ua, body, on_line)
        except Exception as e2:
            print("[pure] Chrome fallback failed (%s), retrying pure" %
                  str(e2)[:160], file=sys.stderr)
            time.sleep(3)
            self.ph.invalidate()
            return self.ph.chat(body, on_line, read_timeout=self.cfg.read_timeout)

    def refresh(self):
        self.ph.invalidate()
        with self.lock:
            self.fallback = None

    def models(self):
        if self.models_cache and (time.time() - self.models_ts) < self.cfg.token_ttl:
            return self.models_cache
        try:
            parsed = parse_models(self.ph.models())
            if parsed:
                self.models_cache, self.models_ts = parsed, time.time()
                return parsed
        except Exception as e:
            print("[!] models fetch:", str(e)[:160], file=sys.stderr)
        return load_fallback_models()

    def close(self):
        self.ph.close()


class HybridUpstream:
    """Chrome solves the challenge once per TTL; all chat traffic via requests.
    Robust against challenge rotation, no browser in the hot path.
    """

    def __init__(self, cfg):
        import requests
        self.cfg = cfg
        self.s = requests.Session()
        self.s.trust_env = cfg.trust_env
        if cfg.proxy:
            self.s.proxies = {"http": cfg.proxy, "https": cfg.proxy}
        self.token = None
        self.ua = cfg.ua
        self.token_ts = 0.0
        self.lock = threading.Lock()
        self.models_cache = None
        self.models_ts = 0.0

    def _ensure(self, force=False):
        with self.lock:
            if force or not self.token or (time.time() - self.token_ts) > self.cfg.token_ttl:
                # Pure handshake is faster and avoids duck.ai throttling the
                # browser's in-page /status fetch; Chrome is the fallback for a
                # challenge generation the Node mock cannot (yet) handle.
                try:
                    self.token, self.ua = solve_token_pure(self.cfg)
                except Exception as e:
                    print("[hybrid] pure solve failed (%s), using Chrome" %
                          str(e)[:120], file=sys.stderr)
                    self.token, self.ua = solve_token_chrome(self.cfg)
                self.token_ts = time.time()

    def chat(self, body, on_line):
        self._ensure()
        return direct_chat(self.cfg, self.s, self.token, self.ua, body, on_line)

    def refresh(self):
        self._ensure(force=True)

    def models(self):
        if self.models_cache and (time.time() - self.models_ts) < self.cfg.token_ttl:
            return self.models_cache
        try:
            r = self.s.get(self.cfg.upstream.rstrip("/") + "/duckchat/v1/models",
                           headers={"Accept": "application/json", "User-Agent": self.ua,
                                    "Referer": self.cfg.upstream.rstrip("/") + "/"},
                           timeout=self.cfg.connect_timeout, verify=not self.cfg.insecure)
            if r.status_code == 200:
                parsed = parse_models(r.json())
                if parsed:
                    self.models_cache, self.models_ts = parsed, time.time()
                    return parsed
        except Exception as e:
            print("[!] models fetch:", str(e)[:160], file=sys.stderr)
        return load_fallback_models()

    def close(self):
        self.s.close()


class MockUpstream:
    """Offline self-test upstream (no network, no browser)."""

    def __init__(self, cfg):
        self.cfg = cfg

    def models(self):
        return load_fallback_models()

    def chat(self, body, on_line):
        for ch in "PONG":
            on_line("data: " + json.dumps({"action": "success", "message": ch}) + "\n\n")
        on_line("data: [DONE]\n\n")
        return 200, ""

    def refresh(self):
        pass

    def close(self):
        pass


def parse_models(data):
    """Port of e.i.a(): /duckchat/v1/models -> model records."""
    out = []
    items = (data or {}).get("models", []) if isinstance(data, dict) else []
    for m in items:
        mid = (m.get("id") or "").strip()
        if not mid:
            continue
        out.append({
            "id": mid,
            "name": (m.get("name") or "").strip() or mid,
            "provider": (m.get("provider") or "").strip(),
            "supportedReasoningEffort": [x for x in (m.get("supportedReasoningEffort") or []) if x],
            "accessTier": [x for x in (m.get("accessTier") or []) if x],
            "entityHasAccess": bool(m.get("entityHasAccess", True)),
        })
    for m in out:
        m.setdefault("entityHasAccess", True)
    return out


# ---------------------------------------------------------------------------
# OpenAI -> duck.ai request translation (port of d.h / d.b / e.f.c)
# ---------------------------------------------------------------------------
def text_of(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") in ("text", "input_text"):
                parts.append(str(p.get("text", "")))
            elif isinstance(p, str):
                parts.append(p)
            elif p is not None:
                parts.append(str(p))
        return "".join(parts)
    return str(content)


def merge_messages(pairs, role, text):
    if pairs and pairs[-1][0] == role:
        pairs[-1] = (role, pairs[-1][1] + "\n\n" + text)
    else:
        pairs.append((role, text))


def tool_prompt(tools, forced_name, require=False):
    lines = ["You have access to the following functions. They are executed by the "
             "user's own application, not by you.",
             "<tools>"]
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or {}
        name = (fn.get("name") or "").strip()
        if not name:
            continue
        desc = re.sub(r"\s+", " ", (fn.get("description") or ""))[:200]
        lines.append('<tool name="%s">' % name)
        if desc:
            lines.append(desc)
        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        req = set(params.get("required") or [])
        plist = []
        for k, v in props.items():
            typ = (v.get("type") if isinstance(v, dict) else "any") or "any"
            plist.append("%s (%s%s)" % (k, typ, ", required" if k in req else ""))
        if plist:
            lines.append("Params: " + ", ".join(plist)[:300])
        lines.append("</tool>")
    lines.append("</tools>")
    lines.append("When a tool matches the request, reply with EXACTLY one line of raw "
                 'JSON and nothing else: {"name": "<tool name>", "arguments": {...}}')
    lines.append("No prose, no markdown, no code fences. If no tool matches, answer normally.")
    if forced_name:
        lines.append('You must call the tool "%s".' % forced_name)
    elif require:
        lines.append("You must call at least one tool.")
    else:
        lines.append("If no tool clearly matches the request, answer normally in plain text; "
                     "do NOT invent a tool call.")
    return "\n".join(lines)


def parse_tool_calls(text):
    """Port of c.c.k(): find raw-JSON tool calls in the model reply."""
    calls = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or not (line.startswith("{") or '{"name"' in line):
            continue
        start = line.find("{")
        end = line.rfind("}")
        if start < 0 or end <= start:
            continue
        try:
            obj = json.loads(line[start:end + 1])
        except Exception:
            continue
        name = obj.get("name")
        if isinstance(name, str) and name:
            args = obj.get("arguments", {})
            if not isinstance(args, (dict, str)):
                args = json.dumps(args, ensure_ascii=False)
            calls.append({"name": name, "arguments": args})
    return calls


def nudge(messages, attempt, forced_name):
    out = [dict(m) for m in messages]
    note = ("[System: your previous reply did not contain a function call. You MUST "
            'reply with ONLY one line of raw JSON {"name": "<tool name>", '
            '"arguments": {...}} exactly as specified above. ')
    note += "Do not reply in prose. (retry #%d)]" % attempt
    if forced_name:
        note += '\nYou must call the tool "%s".' % forced_name
    else:
        note += "\nYou must call at least one tool."
    for m in reversed(out):
        if m.get("role") == "user":
            m["content"] = note + "\n\n" + str(m.get("content") or "")
            return out
    out.append({"role": "user", "content": note})
    return out


def build_messages(openai_messages, tools_prompt, limit=15000):
    """Port of d.h, with a FIX over the APK/earlier port.

    The old code put a long system preamble + tool prompt IN FRONT of the first
    user message, then truncated the whole string from the left. With agent
    clients (long system prompt + many MCP tools) that cut off the user's real
    question, so upstream only saw system text and replied "What would you like
    to work on?". Here system/tools get their own caps and the user's message
    is always kept intact (tail-preserving truncation).
    """
    preamble = []
    pairs = []
    for m in openai_messages:
        if not isinstance(m, dict):
            continue
        role = (m.get("role") or "").lower()
        text = text_of(m.get("content"))
        if not text.strip():
            continue
        if role in ("system", "developer"):
            preamble.append(text.strip())
        elif role == "assistant":
            merge_messages(pairs, "assistant", text.strip())
        else:                       # user / tool -> upstream "user"
            merge_messages(pairs, "user", text.strip())

    system_max = 6000
    tools_max = 4000
    history_max = max(1500, limit - system_max - tools_max - 500)

    head = "\n\n".join(preamble)
    if len(head) > system_max:
        head = head[:system_max - 30] + "\n…（system 过长已截断）"
    tp = tools_prompt or ""
    if len(tp) > tools_max:
        tp = tp[:tools_max - 30] + "\n…（工具定义过长已截断）"

    kept, total = [], 0
    for role, text in reversed(pairs):
        cost = len(text) + 2
        if kept and total + cost > history_max:
            break
        kept.insert(0, (role, text))
        total += cost
    omitted = len(pairs) - len(kept)

    prefix = head
    if omitted > 0:
        note = "[Note] Earlier turns were omitted due to length limits."
        prefix += ("\n\n" + note) if prefix else (note + "\n\n")
    if tp:
        prefix += ("\n\n" if prefix else "") + tp

    result = []
    if kept:
        first_role, first_text = kept[0]
        if first_role == "user":
            # Never cut the tail: the user's question lives at the END.
            combined = ((prefix + "\n\n") if prefix else "") + first_text
            if len(combined) > limit:
                room = limit - len(first_text) - 60
                if room > 200:
                    prefix = "…（前文过长已截断）\n" + prefix[-room:]
                    combined = prefix + "\n\n" + first_text
                else:
                    combined = _cap_keep_tail(first_text, limit)
            result.append({"role": "user", "content": combined})
            for role, text in kept[1:]:
                result.append({"role": role, "content": _cap_keep_tail(text)})
        else:
            if prefix:
                result.append({"role": "user", "content": prefix.rstrip()})
            for role, text in kept:
                result.append({"role": role, "content": _cap_keep_tail(text)})
    else:
        result.append({"role": "user",
                       "content": _cap_keep_tail(prefix.rstrip() or "Continue.")})

    if not result or result[-1].get("role") != "user":
        result.append({"role": "user", "content": "Continue."})
    return result


def _cap(s, limit=15000):
    if len(s) > limit:
        return s[:limit - 20] + "\n…（内容过长已截断）"
    return s


def _cap_keep_tail(s, limit=8000):
    """Truncate from the HEAD so the tail (the actual question) survives."""
    s = s or ""
    if len(s) > limit:
        return "…（前文过长已截断）\n" + s[-(limit - 20):]
    return s


def pick_model(requested, available, default_model):
    ids = [m["id"] for m in available if m.get("entityHasAccess", True)]
    if not ids:
        ids = [m["id"] for m in available]
    if not ids:
        ids = ["gpt-6-luna"]
    if requested:
        for i in ids:
            if i == requested:
                return i
        for i in ids:
            if i.lower() == requested.lower():
                return i
    if default_model and default_model in ids:
        return default_model
    if "gpt-6-luna" in ids:
        return "gpt-6-luna"
    return ids[0]


def pick_reasoning(requested, available, model_id, default_effort):
    want = (requested or default_effort or "none").strip().lower()
    if want not in ("none", "low", "medium", "high"):
        want = "none"
    if want == "high":
        want = "none"          # APK only supports none/low/medium
    supported = []
    for m in available:
        if m["id"] == model_id:
            supported = [x.lower() for x in (m.get("supportedReasoningEffort") or [])]
            break
    if supported:
        order = {"medium": ["medium", "low", "none"],
                 "low": ["low", "none"], "none": ["none"]}[want]
        for c in order:
            if c in supported:
                return c
        return supported[0]
    return want


def duck_body(model, messages, reasoning):
    """Port of e.f.c(): exact upstream JSON body."""
    return {
        "model": model,
        "messages": messages,
        "canUseTools": False,
        "reasoningEffort": reasoning,
        "canUseApproxLocation": False,
        "canUseWebSearch": False,
        "canUploadFiles": False,
        "canShowGreeting": False,
    }


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class Gateway:
    def __init__(self, cfg):
        self.cfg = cfg
        if cfg.mock:
            self.upstream = MockUpstream(cfg)
        elif cfg.transport == "direct":
            self.upstream = DirectUpstream(cfg)
        elif cfg.transport == "pure":
            self.upstream = PureUpstream(cfg)
        elif cfg.transport == "hybrid":
            self.upstream = HybridUpstream(cfg)
        else:
            self.upstream = BrowserUpstream(cfg).start()

    def close(self):
        self.upstream.close()

    def models(self):
        return self.upstream.models()

    def chat(self, body, on_line):
        """Returns (status, raw_text). Handles one 418/ERR_CHALLENGE retry."""
        for attempt in range(2):
            status, raw = self.upstream.chat(body, on_line)
            if status == 418 or (status != 200 and "ERR_CHALLENGE" in raw):
                if attempt == 0:
                    self.upstream.refresh()
                    continue
            return status, raw
        return status, raw


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "duck2api/1.0.4"
    gateway: Gateway = None

    def log_message(self, fmt, *args):
        print("[*] %s - %s" % (self.address_string(), fmt % args), file=sys.stderr)

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _error(self, code, etype, message):
        self._json(code, {"error": {"message": message, "type": etype, "code": etype}})

    def _sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        self.close_connection = True

    def _sse(self, obj):
        data = ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8")
        self.wfile.write(data)
        self.wfile.flush()

    def _sse_done(self):
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _read_body(self):
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            chunks = []
            while True:
                size_line = self.rfile.readline().strip()
                if not size_line:
                    break
                size = int(size_line.split(b";")[0], 16)
                if size == 0:
                    self.rfile.readline()
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.readline()
            return b"".join(chunks)
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _authorized(self):
        key = self.gateway.cfg.key
        if not key:
            return True
        auth = self.headers.get("Authorization", "")
        got = auth.strip()
        if got.lower().startswith("bearer "):
            got = got[7:].strip()
        return got == key

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._authorized():
            return self._error(401, "invalid_api_key", "Missing or invalid API key.")
        if path in ("/", "/health", "/v1"):
            return self._json(200, {"status": "ok", "service": "duck2api"})
        if path == "/v1/models":
            models = self.gateway.models()
            data = []
            for m in models:
                if not m.get("entityHasAccess", True):
                    continue
                data.append({"id": m["id"], "object": "model", "created": 0,
                             "owned_by": (m.get("provider") or "duck.ai") or "duck.ai"})
            return self._json(200, {"object": "list", "data": data})
        return self._error(404, "not_found", "Unknown endpoint: GET " + path)

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._authorized():
            return self._error(401, "invalid_api_key", "Missing or invalid API key.")
        if path != "/v1/chat/completions":
            return self._error(404, "not_found", "Unknown endpoint: POST " + path)
        try:
            req = json.loads(self._read_body().decode("utf-8-sig"))
        except Exception:
            return self._error(400, "invalid_request_error", "Request body is not valid JSON.")
        if not isinstance(req, dict):
            return self._error(400, "invalid_request_error", "Request body is not valid JSON.")
        msgs = req.get("messages")
        if not isinstance(msgs, list):
            return self._error(400, "invalid_request_error", "'messages' is required.")
        if not msgs:
            return self._error(400, "invalid_request_error", "'messages' must not be empty.")

        cfg = self.gateway.cfg
        stream = bool(req.get("stream"))
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())
        available = self.gateway.models()
        model = pick_model(req.get("model"), available, cfg.default_model)
        effort_raw = req.get("reasoning_effort") or req.get("reasoningEffort")
        reasoning = pick_reasoning(effort_raw, available, model, cfg.reasoning)

        tools = req.get("tools") or []
        if getattr(cfg, "tools_mode", "emulated") == "off":
            tools = []
        choice = req.get("tool_choice")
        forced = None
        require_tool = False
        if choice == "none":
            tools = []
        elif isinstance(choice, dict) and choice.get("type") == "function":
            fn = choice.get("function") or {}
            forced = fn.get("name") or None
            require_tool = True
        elif choice == "required":
            require_tool = True
        elif choice == "auto":
            pass
        if tools and not isinstance(tools, list):
            tools = []
        tp = tool_prompt(tools, forced, require_tool) if tools else ""

        # SSE state
        state = {"started": False, "role_sent": False, "broken": False}

        def chunk(obj):
            if not state["started"]:
                self._sse_headers()
                state["started"] = True
            self._sse(obj)

        def delta(**kw):
            return {"index": 0, "delta": kw, "finish_reason": None}

        def send_chunk(d, finish=None):
            chunk({"id": cid, "object": "chat.completion.chunk",
                   "created": created, "model": model,
                   "choices": [{"index": 0, "delta": d, "finish_reason": finish}]})

        def emit_text(t):
            if not stream:
                return
            if not state["role_sent"]:
                send_chunk({"role": "assistant"})
                state["role_sent"] = True
            send_chunk({"content": t})

        def on_line(line):
            parser.feed(line)

        parser = SSEParser(on_message=emit_text)

        try:
            if tools:
                # Tool mode: buffer the whole reply, retry up to 4 times (d.b.f/i).
                text = ""
                calls = []
                attempt_msgs = msgs
                attempts = 4 if require_tool else 1
                for attempt in range(1, attempts + 1):
                    parser = SSEParser()
                    dm = build_messages(attempt_msgs, tp)
                    body = duck_body(model, dm, reasoning)
                    status, raw = self.gateway.chat(body, parser.feed)
                    self._check_upstream(status, raw, parser)
                    text = "".join(parser.messages)
                    calls = parse_tool_calls(text)
                    if calls:
                        break
                    if attempt < attempts:
                        attempt_msgs = nudge(attempt_msgs, attempt, forced)
                if not calls:
                    # Fall back to plain text (APK: "未触发工具，回落文本")
                    if stream:
                        if not state["role_sent"]:
                            send_chunk({"role": "assistant"})
                        if text:
                            send_chunk({"content": text})
                        send_chunk({}, "stop")
                        self._sse_done()
                    else:
                        self._json(200, self._completion(cid, created, model, text, []))
                    return
                tc = []
                for c in calls:
                    tc.append({"id": "call_" + uuid.uuid4().hex[:22],
                               "type": "function",
                               "function": {"name": c["name"],
                                            "arguments": c["arguments"] if isinstance(c["arguments"], str)
                                            else json.dumps(c["arguments"], ensure_ascii=False)}})
                if stream:
                    send_chunk({"role": "assistant"})
                    for i, c in enumerate(tc):
                        chunk({"id": cid, "object": "chat.completion.chunk",
                               "created": created, "model": model,
                               "choices": [{"index": 0,
                                            "delta": {"tool_calls": [dict(c, index=i)]},
                                            "finish_reason": None}]})
                    send_chunk({}, "tool_calls")
                    self._sse_done()
                else:
                    message = {"role": "assistant",
                               "content": text if text.strip() else None,
                               "tool_calls": tc}
                    self._json(200, {"id": cid, "object": "chat.completion",
                                     "created": created, "model": model,
                                     "choices": [{"index": 0, "message": message,
                                                  "finish_reason": "tool_calls"}],
                                     "usage": {"prompt_tokens": 0,
                                               "completion_tokens": 0,
                                               "total_tokens": 0}})
                return

            # Plain chat (streaming or buffered)
            dm = build_messages(msgs, "")
            body = duck_body(model, dm, reasoning)
            status, raw = self.gateway.chat(body, on_line)
            self._check_upstream(status, raw, parser)
            text = "".join(parser.messages)
            if stream:
                if not state["role_sent"]:
                    send_chunk({"role": "assistant"})
                send_chunk({}, "stop")
                self._sse_done()
            else:
                self._json(200, self._completion(cid, created, model, text, []))
        except DuckError as e:
            self._fail(e, state, stream)
        except (BrokenPipeError, ConnectionResetError):
            state["broken"] = True
        except Exception as e:
            print("[!] handler:", type(e).__name__, str(e)[:200], file=sys.stderr)
            self._fail(friendly_upstream_error(e), state, stream)

    @staticmethod
    def _completion(cid, created, model, text, tool_calls):
        message = {"role": "assistant", "content": text}
        if tool_calls:
            message["content"] = text if text.strip() else None
            message["tool_calls"] = tool_calls
        return {"id": cid, "object": "chat.completion", "created": created,
                "model": model,
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if tool_calls else "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}

    def _check_upstream(self, status, raw, parser):
        if parser.error:
            e = parser.error
            raise DuckError(e.etype, error_message(e.etype, e.message, e.status),
                            e.status or status)
        if status != 200:
            etype, message, st = "", "", status
            try:
                obj = json.loads(raw)
                etype = obj.get("type", "")
                message = obj.get("message", "")
                st = obj.get("status", status) or status
            except Exception:
                message = raw[:300]
            raise DuckError(etype, error_message(etype, message, st), st)

    def _fail(self, e, state, stream):
        if state.get("broken"):
            return
        etype = classify_error(e.etype, e.status)
        if stream and state.get("started"):
            try:
                self._sse({"error": {"message": e.message, "type": etype, "code": etype}})
                self._sse_done()
            except Exception:
                pass
        else:
            try:
                self._error(status_for(etype), etype, e.message)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(description="duck.apk gateway, extracted & reconstructed")
    ap.add_argument("--host", default=os.environ.get("DUCK_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("DUCK_PORT", "7749")))
    ap.add_argument("--key", default=os.environ.get("DUCK_KEY", ""),
                    help="gatewayKey; empty = no auth (APK default)")
    ap.add_argument("--upstream", default=os.environ.get("DUCK_UPSTREAM", "https://duck.ai"))
    ap.add_argument("--default-model", default=os.environ.get("DUCK_DEFAULT_MODEL", "gpt-6-luna"))
    ap.add_argument("--reasoning", default=os.environ.get("DUCK_REASONING", "none"),
                    choices=["none", "low", "medium"])
    ap.add_argument("--tools", dest="tools_mode",
                    default=os.environ.get("DUCK_TOOLS", "emulated"),
                    choices=["emulated", "off"],
                    help="emulated = prompt-based tool calls (default); "
                         "off = ignore client tools entirely, pure text answers "
                         "(recommended for agent clients with many MCP tools)")
    ap.add_argument("--transport", default=os.environ.get("DUCK_TRANSPORT", "pure"),
                    choices=["pure", "hybrid", "browser", "direct"],
                    help="pure = NO browser (requests + Node/vm), hybrid = Chrome solves once, "
                         "browser = all in Chrome page, direct = supplied token")
    ap.add_argument("--node", default=os.environ.get("DUCK_NODE", None),
                    help="path to node.exe for --transport pure (auto-detected by default)")
    ap.add_argument("--browser", default=os.environ.get("DUCK_BROWSER", "chrome"),
                    help="Playwright channel: chrome / msedge / chromium")
    ap.add_argument("--headless", action="store_true", default=True)
    ap.add_argument("--show-browser", dest="headless", action="store_false")
    ap.add_argument("--proxy", default=os.environ.get("DUCK_PROXY", None),
                    help="outbound proxy URL; default = direct (system proxy 12000 breaks duck.ai TLS)")
    ap.add_argument("--trust-env", action="store_true", default=False,
                    help="direct transport: honour system proxy env vars")
    ap.add_argument("--insecure", action="store_true", default=False)
    ap.add_argument("--token", default=os.environ.get("DUCK_TOKEN", ""),
                    help="X-Vqd-Hash-1 for --transport direct")
    ap.add_argument("--token-file", default=None)
    ap.add_argument("--ua", default=DEFAULT_UA)
    ap.add_argument("--connect-timeout", type=int, default=20)
    ap.add_argument("--read-timeout", type=int, default=600)
    ap.add_argument("--token-ttl", type=int, default=1800,
                    help="challenge/model cache seconds (APK: 1800000 ms)")
    ap.add_argument("--mock", action="store_true", help="offline fake upstream")
    ap.add_argument("--selftest", action="store_true", help="run offline self-test and exit")
    return ap


def selftest(cfg):
    import requests
    gw = Gateway(cfg)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Handler.gateway = gw
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = "http://127.0.0.1:%d" % port
    ok = True
    try:
        for path in ("/", "/health", "/v1", "/v1/models"):
            r = requests.get(base + path, timeout=10)
            print("GET", path, r.status_code, r.text[:160])
            ok &= r.status_code == 200
        payload = {"model": "gpt-6-luna", "messages": [{"role": "user", "content": "hi"}]}
        r = requests.post(base + "/v1/chat/completions", json=payload, timeout=20)
        print("POST non-stream", r.status_code, r.text[:220])
        ok &= r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "PONG"
        r = requests.post(base + "/v1/chat/completions",
                          json=dict(payload, stream=True), timeout=20)
        print("POST stream", r.status_code, r.text[:260].replace("\n", " | "))
        parts = []
        for line in r.text.splitlines():
            if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                try:
                    parts.append(json.loads(line[6:])["choices"][0]["delta"].get("content") or "")
                except Exception:
                    pass
        ok &= (r.status_code == 200 and "[DONE]" in r.text
               and "".join(parts) == "PONG")
        r = requests.post(base + "/v1/chat/completions", json={"x": 1}, timeout=10)
        print("POST bad", r.status_code, r.text[:120])
        ok &= r.status_code == 400
        r = requests.get(base + "/nope", timeout=10)
        print("GET bad", r.status_code, r.text[:120])
        ok &= r.status_code == 404
    finally:
        srv.shutdown()
        srv.server_close()
        gw.close()
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    cfg = build_parser().parse_args()
    if cfg.token_file and not cfg.token:
        try:
            with open(cfg.token_file, "r", encoding="utf-8") as f:
                d = json.load(f)
                cfg.token = d.get("token", "")
                if d.get("userAgent"):
                    cfg.ua = d["userAgent"]
        except Exception as e:
            print("[!] token-file:", e, file=sys.stderr)
    if cfg.selftest:
        cfg.mock = True
        return selftest(cfg)

    # Playwright's sync API must be driven from the thread that created it.
    # Only the persistent `browser` transport needs a single-threaded server.
    server_cls = (HTTPServer if (cfg.transport == "browser" and not cfg.mock)
                  else ThreadingHTTPServer)
    last_error = None
    for attempt in range(1, 6):
        gw = None
        srv = None
        try:
            gw = Gateway(cfg)
            Handler.gateway = gw
            srv = server_cls((cfg.host, cfg.port), Handler)
            srv.daemon_threads = True
            print("duck2api listening on http://%s:%d  (transport=%s, upstream=%s, key=%s)" %
                  (cfg.host, cfg.port, "mock" if cfg.mock else cfg.transport,
                   cfg.upstream, "<empty>" if not cfg.key else "<set>"))
            print("  GET  /health   GET /v1/models   POST /v1/chat/completions")
            srv.serve_forever()
            return 0
        except KeyboardInterrupt:
            return 0
        except Exception as e:
            last_error = e
            import traceback
            print("[!] server crashed (attempt %d/5): %s" % (attempt, e), file=sys.stderr)
            traceback.print_exc()
            try:
                if srv is not None:
                    srv.server_close()
            except Exception:
                pass
            try:
                if gw is not None:
                    gw.close()
            except Exception:
                pass
            if attempt < 5:
                print("[*] restarting gateway in 3 seconds ...", file=sys.stderr)
                time.sleep(3)
    if last_error is not None:
        print("[X] gateway gave up after repeated crashes", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
