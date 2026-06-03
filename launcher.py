"""
Project Vuka -- Python Launcher
Replaces start_all.bat as the single entry point.
Works correctly regardless of how it's invoked (interactive terminal,
double-click, Task Scheduler, subprocess call).
"""
import sys
import time
import socket
import subprocess
import psutil
import urllib.request
import urllib.error
from pathlib import Path

VUKA_DIR   = Path(__file__).parent.resolve()
PYTHON     = r"C:\Users\classic\AppData\Local\Python\pythoncore-3.14-64\python.exe"
BOOT_DELAY = 2       # seconds between bot instance starts
KRONOS_URL = "http://127.0.0.1:8000/health"
KRONOS_MAX_WAIT = 60  # seconds to wait for Kronos to be ready (model load can take 30-45s)

DETACHED = 0x00000008          # Windows: DETACHED_PROCESS
NO_WIN   = subprocess.CREATE_NO_WINDOW
BREAKAWAY = 0x01000000        # Windows: CREATE_BREAKAWAY_FROM_JOB

def log(msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def kill_stale():
    """Kill any existing Vuka processes before starting fresh."""
    targets = {"ingwe.py", "kronos_server.py", "supervisor.py", "dashboard.py"}
    killed = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if any(t in cmdline for t in targets):
                proc.kill()
                killed.append(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if killed:
        log(f"Killed {len(killed)} stale process(es): {killed}")
        time.sleep(3)

def launch(label: str, script: str, *args, visible: bool = False) -> subprocess.Popen:
    """Launch a Python script as a detached process."""
    cmd = [PYTHON, script, *args]
    if visible:
        proc = subprocess.Popen(
            cmd,
            cwd=str(VUKA_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            cwd=str(VUKA_DIR),
            creationflags=DETACHED | NO_WIN,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    log(f"  Started {label} -- PID {proc.pid}")
    return proc

def wait_for_kronos(timeout: int = KRONOS_MAX_WAIT) -> bool:
    """Poll Kronos health endpoint until ready or timeout."""
    log(f"Waiting for Kronos (max {timeout}s)...")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        try:
            s.connect(('127.0.0.1', 8000))
            s.close()
            with urllib.request.urlopen(KRONOS_URL, timeout=10) as r:
                if r.status == 200:
                    log("  Kronos ready [OK]")
                    return True
        except Exception:
            pass
        time.sleep(1)
    log("  !! Kronos did not respond within timeout -- bots will start in VETO_SAFE mode")
    return False

def kill_port_8000():
    """Free port 8000 if something is still bound to it."""
    for conn in psutil.net_connections(kind="tcp"):
        if conn.laddr.port == 8000 and conn.status == "LISTEN":
            try:
                psutil.Process(conn.pid).kill()
                log(f"  Freed port 8000 (killed PID {conn.pid})")
                time.sleep(1)
            except Exception:
                pass

def main():
    print()
    print("=" * 50)
    print("   PROJECT VUKA -- ONE-CLICK LAUNCHER")
    print("=" * 50)
    print()

    log("[1/5] Stopping stale processes...")
    kill_stale()
    kill_port_8000()

    log("[2/5] Starting Kronos AI Server (visible)...")
    launch("Kronos Server", "kronos_server.py", visible=True)

    log("[3/5] Waiting for Kronos health check...")
    wait_for_kronos(timeout=KRONOS_MAX_WAIT)

    log("[4/5] Starting Supervisor...")
    launch("Supervisor", "supervisor.py", visible=False)
    time.sleep(3)

    log("[5/5] Starting Dashboard...")
    launch("Dashboard", "dashboard.py", visible=True)

    print()
    print("=" * 50)
    print("   ALL SYSTEMS STARTED")
    print("   Run:  python launcher.py stop    -- kill everything")
    print("         python launcher.py status  -- check what's running")
    print("=" * 50)
    print()

def stop():
    targets = {"ingwe.py", "kronos_server.py", "supervisor.py", "dashboard.py"}
    killed = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
            if any(t in cmdline for t in targets):
                proc.kill()
                killed.append(f"{proc.info['pid']}")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    print(f"Stopped {len(killed)} process(es): {', '.join(killed) if killed else 'none running'}")

def status():
    targets = {"ingwe.py", "kronos_server.py", "supervisor.py", "dashboard.py"}
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            label = next((t for t in targets if t in " ".join(cmdline)), None)
            if label:
                args = " ".join(str(a) for a in cmdline[2:4])
                found.append((label, proc.info["pid"], args))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    print("\n====== VUKA STATUS ======\n")
    for label, pid, args in sorted(found):
        print(f"  PID {pid:>7}  {label:25s}  {args}")

    missing = {t for t in targets if not any(f[0] == t for f in found)}
    if missing:
        print()
        for m in missing:
            print(f"  !! MISSING: {m}")

    print()
    try:
        with urllib.request.urlopen(KRONOS_URL, timeout=2) as r:
            print(f"  Kronos health: {r.read().decode()}")
    except Exception:
        print("  Kronos health: NOT RESPONDING")
    print()

if __name__ == "__main__":
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "start"
    if cmd == "stop":
        stop()
    elif cmd == "status":
        status()
    elif cmd == "restart":
        stop()
        time.sleep(3)
        main()
    else:
        main()
