#!/usr/bin/env python3
"""Cross-platform one-command launcher for DeepSeekWebProxy.

Examples:
    python start_proxy.py
    python start_proxy.py --port 18088 --host 127.0.0.1
    DEEPSEEK_TOKEN=xxxxxxxx python start_proxy.py
    python start_proxy.py --selftest

Token resolution order:
    1. DEEPSEEK_TOKEN environment variable
    2. deepseek_token.txt next to this script (git-ignored)
"""
import argparse
import os
import runpy
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "MODIFIED_FILE.py"


def read_optional(filename, default=""):
    path = ROOT / filename
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return default


def main():
    parser = argparse.ArgumentParser(description="DeepSeek Web -> OpenAI proxy launcher")
    parser.add_argument("--host", default=os.environ.get("DEEPSEEK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DEEPSEEK_PORT", "18088")))
    parser.add_argument("--selftest", action="store_true", help="run built-in self tests and exit")
    args = parser.parse_args()

    token = os.environ.get("DEEPSEEK_TOKEN", "").strip() or read_optional("deepseek_token.txt")
    if not token and not args.selftest:
        print(
            "ERROR: no DeepSeek userToken found.\n"
            "  - set DEEPSEEK_TOKEN=... , or\n"
            "  - write the token into ./deepseek_token.txt (git-ignored), or\n"
            "  - POST /_set_token after the server is up.\n"
            "Get it by signing in to https://chat.deepseek.com and reading\n"
            "localStorage['userToken'] in the browser devtools.",
            file=sys.stderr,
        )
        return 2

    if token:
        os.environ["DEEPSEEK_TOKEN"] = token
    os.environ.setdefault(
        "DEEPSEEK_DEVICE_ID", read_optional("deepseek_device_id.txt", uuid.uuid4().hex)
    )
    os.environ.setdefault("DEEPSEEK_BASE", "https://chat.deepseek.com")
    os.environ.setdefault("DEEPSEEK_CLIENT_VERSION", "2.7.1")
    os.environ.setdefault("DEEPSEEK_TIMEZONE_OFFSET", "28800")
    os.environ["DEEPSEEK_HOST"] = args.host
    os.environ["DEEPSEEK_PORT"] = str(args.port)
    os.environ.setdefault("PYTHONWARNINGS", "ignore")

    sys.argv = [str(MAIN), "--port", str(args.port), "--host", args.host]
    if args.selftest:
        sys.argv.append("--selftest")
    runpy.run_path(str(MAIN), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
