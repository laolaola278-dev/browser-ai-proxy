from pathlib import Path
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "_run_live_proxy_new.py"
_PYW_CANDIDATE = Path(sys.executable).with_name("pythonw.exe")
PYW = os.environ.get("DEEPSEEK_PYTHONW") or str(
    _PYW_CANDIDATE if _PYW_CANDIDATE.is_file() else Path(sys.executable)
)
HOST = os.environ.get("DEEPSEEK_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEEPSEEK_PORT", "18088"))
LOCK_PORT = int(os.environ.get("DEEPSEEK_WATCH_LOCK_PORT", "18089"))
URL = "http://" + HOST + ":" + str(PORT) + "/deepseek/health"

lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    lock.bind((HOST, LOCK_PORT))
    lock.listen(1)
except OSError as exc:
    print("DEEPSEEK_WATCH_ALREADY_RUNNING " + str(exc), file=sys.stderr, flush=True)
    raise SystemExit(0)

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def healthy():
    try:
        req = urllib.request.Request(URL, headers={"Connection": "close"})
        with opener.open(req, timeout=1.5) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status == 200 and '"service":"deepseek-web-openai-proxy"' in body
    except Exception:
        return False


def start_runner():
    env = os.environ.copy()
    env["DEEPSEEK_HOST"] = HOST
    env["DEEPSEEK_PORT"] = str(PORT)
    env["PYTHONWARNINGS"] = "ignore"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    flags = 0x00000008 | 0x00000200 | 0x08000000
    proc = subprocess.Popen(
        [PYW, str(RUNNER)],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )
    (ROOT / "deepseek_18088.pid").write_text(str(proc.pid), encoding="ascii")
    return proc.pid


while True:
    if healthy():
        time.sleep(2)
        continue
    try:
        pid = start_runner()
        print("DEEPSEEK_WATCH_RESTART_PID=" + str(pid), file=sys.stderr, flush=True)
    except Exception as exc:
        print("DEEPSEEK_WATCH_START_ERROR=" + repr(exc), file=sys.stderr, flush=True)
    time.sleep(3)
