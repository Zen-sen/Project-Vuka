#!/usr/bin/env python3
"""
status.py -- Report which Vuka processes are running.

Reusable as a library: import `scan_processes()` / `missing_processes()`.
Nothing runs at import time -- all output happens inside main(), which is
only reached via `python -m vuka.core.status` / `python status.py`.
"""

EXPECTED = {"kronos_server", "supervisor", "dashboard"}
KEYWORDS = [
    "vuka.core.bot",
    "vuka.core.supervisor",
    "vuka.core.dashboard",
    "vuka.ai.kronos_server",
]


def scan_processes():
    """
    Return [(label, args, pid), ...] for matching Vuka python processes.

    Uses psutil's structured ``cmdline`` list rather than whitespace-splitting
    the joined string, so paths with spaces ("C:\\Program Files\\Python\\...")
    are parsed correctly. Returns [] if psutil is unavailable.
    """
    try:
        import psutil
    except ImportError:
        return []

    procs = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = p.info["name"] or ""
            if "python" not in name.lower():
                continue
            cmdline = p.info.get("cmdline") or []
            cmd = " ".join(cmdline)
            if not any(kw in cmd for kw in KEYWORDS):
                continue
            if "-m" in cmdline:
                idx = cmdline.index("-m") + 1
                label = cmdline[idx] if idx < len(cmdline) else "?"
                args = " ".join(cmdline[idx + 1 : idx + 3])
            else:
                # cmdline[0] is the interpreter, cmdline[1] is the script path.
                label = cmdline[1] if len(cmdline) > 1 else "?"
                args = " ".join(cmdline[2:4])
            procs.append((label, args, p.info["pid"]))
        except Exception:
            continue
    return procs


def missing_processes(procs):
    """Return the expected services that are not currently running."""
    running = {label.split(".")[-1] for label, _, _ in procs}
    return sorted(EXPECTED - running)


def main() -> int:
    try:
        import psutil  # noqa: F401  (verify availability before scanning)
    except ImportError:
        print("  psutil is not installed.")
        print("  Install it with:  pip install psutil")
        return 1

    procs = scan_processes()

    if not procs:
        print("  No Vuka processes running.")
    else:
        for label, args, pid in sorted(procs):
            print(f"  PID {pid:>6}  {label:30s}  {args}")

    missing = missing_processes(procs)
    if missing:
        print()
        for m in missing:
            print(f"  !! MISSING: {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
