# -*- coding: utf-8 -*-
"""
pure_handshake.py — duck.ai X-Vqd-Hash-1 handshake on a PC, with NO browser.

Pipeline (all plain HTTP + a JS engine):
  1. GET  https://duck.ai/duckchat/v1/status   (header x-vqd-accept: 1)
     -> response header x-vqd-hash-1 = base64(obfuscated JS)
  2. Run solve_challenge.js in Node (Playwright's bundled node.exe, or `node`)
     with a calibrated DOM mock -> the challenge result object `res`
  3. Reproduce the APK's b.h.j post-processing in Python:
       client_hashes = [base64(SHA256(x)) for x in res.client_hashes]
       meta += {origin, stack:'no-stack', duration:'1'}
       token = base64(JSON.stringify(res))       <- this is X-Vqd-Hash-1
  4. POST /duckchat/v1/chat with that token using plain requests.

Verified: token output matches a real Chrome WebView byte-for-byte on the
challenge result, and chat returns HTTP 200. No Chrome, no Playwright runtime,
no GUI. Node is used only as a JS interpreter (a few ms, no network).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib3

import requests

urllib3.disable_warnings()

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
SOLVER_JS = os.path.join(HERE, "solve_challenge.js")
DEFAULT_NODE = os.path.join(
    os.path.dirname(os.path.dirname(sys.executable)),
    "Lib", "site-packages", "playwright", "driver", "node.exe")


def find_node(explicit=None):
    if explicit and os.path.isfile(explicit):
        return explicit
    if os.path.isfile(DEFAULT_NODE):
        return DEFAULT_NODE
    p = shutil.which("node")
    if p:
        return p
    raise RuntimeError("Node.js not found (needed as JS engine). "
                       "Pass --node <path> or install playwright.")


def build_token(res, ua):
    """Port of the APK's b.h.j: SHA-256 client_hashes, extend meta, btoa(JSON)."""
    out = dict(res)
    out["client_hashes"] = [
        base64.b64encode(hashlib.sha256(x.encode("utf-8")).digest()).decode("ascii")
        for x in res["client_hashes"]]
    meta = dict(res["meta"])
    meta.update({"origin": "https://duck.ai", "stack": "no-stack", "duration": "1"})
    out["meta"] = meta
    js = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(js.encode("utf-8")).decode("ascii")


class PureHandshake:
    def __init__(self, base="https://duck.ai", ua=DEFAULT_UA, node=None,
                 proxy=None, trust_env=False, insecure=False,
                 connect_timeout=20, token_ttl=1800, verbose=False):
        self.base = base.rstrip("/")
        self.ua = ua
        self.node = find_node(node)
        self.proxy = proxy
        self.insecure = insecure
        self.connect_timeout = connect_timeout
        self.token_ttl = token_ttl
        self.verbose = verbose
        self.s = requests.Session()
        self.s.trust_env = trust_env
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}
        self.token = None
        self.token_ts = 0.0
        self.last_error = None

    def _log(self, *a):
        if self.verbose:
            print("[pure]", *a, file=sys.stderr)

    def status_headers(self):
        return {"x-vqd-accept": "1", "User-Agent": self.ua,
                "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                "Origin": self.base, "Referer": self.base + "/"}

    def solve(self, force=False):
        """Perform steps 1-3, return (token, ua)."""
        if (not force and self.token and
                (time.time() - self.token_ts) < self.token_ttl):
            return self.token, self.ua
        raw = None
        r = None
        url = self.base + "/duckchat/v1/status"
        hdr = self.status_headers()
        for attempt in range(4):
            try:
                r = self.s.get(url, headers=hdr, timeout=self.connect_timeout,
                               verify=not self.insecure)
            except Exception as e:
                r = None
                self._log("status transport error:", str(e)[:120])
            if r is not None and r.status_code != 429:
                break
            self._log("status 429/unavailable, backoff", 3 * (2 ** attempt), "s")
            time.sleep(3 * (2 ** attempt))
        # duck.ai geo-blocks some direct egress IPs with 451
        # ERR_COUNTRY_NOT_SUPPORTED. Fall back to the OS-configured proxy
        # (Windows registry / env vars) and keep using it for chat + models.
        need_fallback = (r is None or r.status_code in (403, 429, 451)
                         or r.status_code >= 500
                         or not r.headers.get("x-vqd-hash-1"))
        if need_fallback:
            try:
                s2 = requests.Session()      # trust_env=True -> system proxy
                r2 = s2.get(url, headers=hdr, timeout=self.connect_timeout,
                            verify=not self.insecure)
                if r2.status_code == 200 and r2.headers.get("x-vqd-hash-1"):
                    self.s = s2
                    r = r2
                    self._log("switched to system proxy (direct egress blocked)")
            except Exception as e:
                self._log("system proxy fallback failed:", str(e)[:120])
        if r is not None:
            raw = r.headers.get("x-vqd-hash-1")
        if r is None or r.status_code != 200 or not raw:
            raise RuntimeError("status %s, no x-vqd-hash-1 header" %
                               (r.status_code if r is not None else "no response"))
        js = base64.b64decode(raw).decode("utf-8", "replace")
        self.last_js = js
        fd, path = tempfile.mkstemp(suffix=".js", prefix="duck-challenge-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(js)
            p = subprocess.run([self.node, SOLVER_JS, path, self.ua],
                               capture_output=True, text=True, encoding="utf-8",
                               timeout=30)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        try:
            out = json.loads(p.stdout)
        except Exception:
            raise RuntimeError("solver output unreadable: %r / %r" %
                               (p.stdout[:200], p.stderr[:200]))
        if not out.get("ok"):
            raise RuntimeError("solver failed: " + str(out.get("error"))[:300])
        res = out["res"]
        self.ua = out.get("userAgent") or self.ua
        tok = build_token(res, self.ua)
        self.token, self.token_ts = tok, time.time()
        self._log("solved: challenge=%s tokens=%d client=%s" %
                  (res["meta"]["challenge_id"][:12], len(tok),
                   res["client_hashes"]))
        return tok, self.ua

    def invalidate(self):
        self.token = None
        self.token_ts = 0.0

    def chat_headers(self, tok):
        return {"Content-Type": "application/json", "Accept": "text/event-stream",
                "Origin": self.base, "Referer": self.base + "/",
                "User-Agent": self.ua, "Accept-Language": "en-US,en;q=0.9",
                "X-Vqd-Hash-1": tok}

    def chat(self, body, on_line=None, read_timeout=600):
        """POST /duckchat/v1/chat. Returns (status, raw_text)."""
        tok, _ = self.solve()
        r = self.s.post(self.base + "/duckchat/v1/chat",
                        headers=self.chat_headers(tok),
                        data=json.dumps(body).encode("utf-8"),
                        timeout=(self.connect_timeout, read_timeout),
                        verify=not self.insecure, stream=True)
        # duck.ai SSE is UTF-8 but often omits charset; requests would otherwise
        # decode it as ISO-8859-1 and Chinese text becomes mojibake.
        r.encoding = "utf-8"
        lines = []
        for raw in r.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            lines.append(raw)
            if r.status_code == 200 and on_line:
                on_line(raw + "\n")
        return r.status_code, "\n".join(lines)

    def _get_with_fallback(self, url, headers):
        """GET through the current session; on geo-block/throttle/failure retry
        once via the OS-configured proxy and keep it for later requests."""
        r = None
        try:
            r = self.s.get(url, headers=headers, timeout=self.connect_timeout,
                           verify=not self.insecure)
        except Exception as e:
            self._log("transport error:", str(e)[:120])
        if (r is None or r.status_code in (403, 429, 451) or r.status_code >= 500):
            try:
                s2 = requests.Session()      # trust_env=True -> system proxy
                r2 = s2.get(url, headers=headers, timeout=self.connect_timeout,
                            verify=not self.insecure)
                if r2.status_code == 200:
                    self.s = s2
                    self._log("switched to system proxy (direct egress blocked)")
                    return r2
            except Exception as e:
                self._log("system proxy fallback failed:", str(e)[:120])
        return r

    def models(self):
        r = self._get_with_fallback(self.base + "/duckchat/v1/models",
                                    {"Accept": "application/json",
                                     "User-Agent": self.ua,
                                     "Referer": self.base + "/"})
        if r is None or r.status_code != 200:
            raise RuntimeError("models HTTP %s" %
                               (r.status_code if r is not None else "no response"))
        return r.json()

    def close(self):
        self.s.close()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="PC-only duck.ai handshake (no browser)")
    ap.add_argument("--base", default="https://duck.ai")
    ap.add_argument("--ua", default=DEFAULT_UA)
    ap.add_argument("--node", default=None)
    ap.add_argument("--proxy", default=None)
    ap.add_argument("--trust-env", action="store_true")
    ap.add_argument("--insecure", action="store_true")
    ap.add_argument("--rounds", type=int, default=3,
                    help="fresh handshakes + chats to verify")
    ap.add_argument("--delay", type=float, default=4.0,
                    help="seconds between rounds (status endpoint is rate-limited)")
    ap.add_argument("--save-fail", default=None,
                    help="directory to dump challenge JS when a round fails")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    h = PureHandshake(base=a.base, ua=a.ua, node=a.node, proxy=a.proxy,
                      trust_env=a.trust_env, insecure=a.insecure,
                      verbose=a.verbose)
    print("node:", h.node)
    body = {"model": "gpt-6-luna",
            "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
            "canUseTools": False, "reasoningEffort": "none",
            "canUseApproxLocation": False, "canUseWebSearch": False,
            "canUploadFiles": False, "canShowGreeting": False}
    ok = 0
    for i in range(1, a.rounds + 1):
        try:
            h.solve(force=True)
            status, raw = h.chat(body)
            text = ""
            for line in raw.splitlines():
                if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                    try:
                        obj = json.loads(line[6:])
                        if obj.get("action") == "success":
                            text += obj.get("message") or ""
                    except Exception:
                        pass
            good = status == 200 and "PONG" in text
            print("round %d: chat=%s reply=%r %s" %
                  (i, status, text[:60], "OK" if good else "FAIL"))
            ok += bool(good)
            if not good and a.save_fail:
                os.makedirs(a.save_fail, exist_ok=True)
                fn = os.path.join(a.save_fail, "fail-round%d.js" % i)
                open(fn, "w", encoding="utf-8").write(getattr(h, "last_js", ""))
        except Exception as e:
            print("round %d: ERROR %s %s" % (i, type(e).__name__, str(e)[:200]))
        if i < a.rounds:
            time.sleep(a.delay)
    h.close()
    print("RESULT: %d/%d" % (ok, a.rounds))
    return 0 if ok == a.rounds else 1


if __name__ == "__main__":
    sys.exit(main())
