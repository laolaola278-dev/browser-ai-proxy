from pathlib import Path
import os
import runpy
import socket
import sys
import time

BASE = Path(__file__).resolve().parent
MOD = BASE / "MODIFIED_FILE.py"
PORT = int(os.environ.get("DEEPSEEK_PORT", "18088"))
HOST = os.environ.get("DEEPSEEK_HOST", "127.0.0.1")

def read_optional(name, default=""):
    path = BASE / name
    if path.is_file():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return default


# Environment variables win; the local files are optional so a fresh clone
# (which must never contain credentials) can still boot.
token = os.environ.get("DEEPSEEK_TOKEN", "").strip() or read_optional("deepseek_token.txt")
device_id = (os.environ.get("DEEPSEEK_DEVICE_ID", "").strip()
             or read_optional("deepseek_device_id.txt", "DEVICE_ID"))

os.environ["DEEPSEEK_TOKEN"] = token
os.environ["DEEPSEEK_DEVICE_ID"] = device_id
os.environ["DEEPSEEK_BASE"] = "https://chat.deepseek.com"
os.environ["DEEPSEEK_MODEL_TYPE"] = "default"
os.environ["DEEPSEEK_TIMEZONE_OFFSET"] = "28800"
os.environ["DEEPSEEK_CLIENT_VERSION"] = "2.7.1"
os.environ["DEEPSEEK_CLIENT_LOCALE"] = "zh_CN"
os.environ["DEEPSEEK_CLIENT_PLATFORM"] = "web"
os.environ["DEEPSEEK_BUNDLE_ID"] = "com.deepseek.chat"
os.environ["DEEPSEEK_ORIGIN"] = "https://chat.deepseek.com"
os.environ["DEEPSEEK_REFERER"] = "https://chat.deepseek.com/"
os.environ["DEEPSEEK_THINKING"] = os.environ.get("DEEPSEEK_THINKING", "0")
os.environ["DEEPSEEK_SEARCH"] = os.environ.get("DEEPSEEK_SEARCH", "0")
os.environ["DEEPSEEK_ACCESS_LOG"] = "0"
os.environ["DEEPSEEK_HOST"] = HOST
os.environ["DEEPSEEK_PORT"] = str(PORT)
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    try:
        probe.bind((HOST, PORT))
    except OSError as exc:
        print("DEEPSEEK_NEW_PORT_IN_USE " + str(PORT) + " " + str(exc), file=sys.stderr, flush=True)
        raise SystemExit(2)

sys.argv = [str(MOD), "--port", str(PORT), "--host", HOST]
while True:
    try:
        runpy.run_path(str(MOD), run_name="__main__")
        break
    except KeyboardInterrupt:
        break
    except SystemExit:
        break
    except Exception as exc:
        print("DEEPSEEK_NEW_PROXY_RESTART " + repr(exc), file=sys.stderr, flush=True)
        time.sleep(1)
