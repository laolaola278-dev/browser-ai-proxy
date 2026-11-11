from pathlib import Path
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
WATCH = ROOT / "_watch_deepseek_18088.py"
HOST = os.environ.get("DEEPSEEK_HOST", "127.0.0.1")
PORT = int(os.environ.get("DEEPSEEK_PORT", "18088"))
LOCK_PORT = int(os.environ.get("DEEPSEEK_WATCH_LOCK_PORT", "18089"))
URL = "http://" + HOST + ":" + str(PORT) + "/deepseek/health"
PYW = os.environ.get("DEEPSEEK_PYTHONW") or str(
    Path(sys.executable).with_name("pythonw.exe")
)

if not Path(PYW).is_file():
    PYW = sys.executable

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def healthy():
    try:
        req = urllib.request.Request(URL, headers={"Connection": "close"})
        with opener.open(req, timeout=2) as r:
            body = r.read().decode("utf-8", "replace")
            return (
                r.status == 200
                and '"service":"deepseek-web-openai-proxy"' in body
            ), body
    except Exception as exc:
        return False, repr(exc)


def listener_records():
    try:
        import psutil
    except Exception as exc:
        print("DESKTOP_PSUTIL_UNAVAILABLE " + repr(exc))
        return []
    records = []
    for conn in psutil.net_connections(kind="inet"):
        if conn.status != psutil.CONN_LISTEN or not conn.laddr or not conn.pid:
            continue
        if conn.laddr.port not in (PORT, LOCK_PORT):
            continue
        try:
            proc = psutil.Process(conn.pid)
            cmdline = " ".join(proc.cmdline())
        except Exception:
            cmdline = ""
        records.append((conn.laddr.port, conn.pid, cmdline))
    return records


def legacy_runtime(cmdline):
    low = str(cmdline).lower()
    marker = ("_run_live_proxy_new.py" in low or "_watch_deepseek_18088.py" in low)
    legacy = "\\deepseek_reverse\\" in low
    active = "\\deepseekwebproxy\\" in low
    return marker and legacy and not active


def stop_legacy_listeners():
    stopped = []
    for port, pid, cmdline in listener_records():
        if not legacy_runtime(cmdline):
            continue
        try:
            import psutil
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
            stopped.append(str(pid))
            print("DESKTOP_STOPPED_LEGACY_PORT=" + str(port) + " PID=" + str(pid))
        except Exception as exc:
            print("DESKTOP_STOP_ERROR PID=" + str(pid) + " " + repr(exc))
    return stopped


def lock_held():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex((HOST, LOCK_PORT)) == 0


def start_watchdog():
    env = os.environ.copy()
    env["DEEPSEEK_HOST"] = HOST
    env["DEEPSEEK_PORT"] = str(PORT)
    env["DEEPSEEK_WATCH_LOCK_PORT"] = str(LOCK_PORT)
    env["DEEPSEEK_PYTHONW"] = PYW
    env["PYTHONWARNINGS"] = "ignore"
    env["NO_PROXY"] = "127.0.0.1,localhost"
    flags = 0x00000008 | 0x00000200 | 0x08000000
    proc = subprocess.Popen(
        [PYW, str(WATCH)],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
    )
    (ROOT / "deepseek_18088_watch.pid").write_text(str(proc.pid), encoding="ascii")
    print("DESKTOP_WATCHDOG_STARTED_PID=" + str(proc.pid))
    return proc.pid


def main():
    print("DESKTOP_ACTIVE_ROOT=" + str(ROOT))
    stopped = stop_legacy_listeners()
    print("DESKTOP_LEGACY_STOPPED=" + (",".join(stopped) if stopped else "none"))
    ok, body = healthy()
    if ok:
        active = any(
            port == PORT and "\\deepseekwebproxy\\" in cmdline.lower()
            for port, _pid, cmdline in listener_records()
        )
        if active:
            print("DEEPSEEK_WEB_PROXY_ALREADY_HEALTHY " + body)
            return 0
        print("DESKTOP_RESTARTING_NON_ACTIVE_RUNTIME")
    if not lock_held():
        start_watchdog()
    else:
        print("DESKTOP_WATCHDOG_LOCK_ACTIVE")
    deadline = time.time() + 30
    while time.time() < deadline:
        ok, body = healthy()
        if ok:
            print("DEEPSEEK_WEB_PROXY_HEALTHY " + body)
            return 0
        time.sleep(0.5)
    print("DEEPSEEK_WEB_PROXY_START_TIMEOUT " + str(body))
    return 1


if __name__ == "__main__":
    sys.exit(main())
