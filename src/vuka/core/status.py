import psutil

EXPECTED = {"kronos_server", "supervisor", "dashboard"}
KEYWORDS = [
    "vuka.core.bot",
    "vuka.core.supervisor",
    "vuka.core.dashboard",
    "vuka.ai.kronos_server",
]

procs = []

for p in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        name = p.info["name"] or ""
        if "python" not in name.lower():
            continue
        cmd = " ".join(p.info.get("cmdline") or [])
        if not any(kw in cmd for kw in KEYWORDS):
            continue
        parts = cmd.split()
        if "-m" in parts:
            idx = parts.index("-m") + 1
            label = parts[idx] if idx < len(parts) else "?"
            args = " ".join(parts[idx + 1 : idx + 3]) if len(parts) > idx + 1 else ""
        else:
            label = parts[1] if len(parts) > 1 else "?"
            args = " ".join(parts[2:4]) if len(parts) > 2 else ""
        procs.append((label, args, p.info["pid"]))
    except Exception:
        pass

if not procs:
    print("  No Vuka processes running.")
else:
    for label, args, pid in sorted(procs):
        print(f"  PID {pid:>6}  {label:30s}  {args}")

running = {p[0].split(".")[-1] for p in procs}
missing = EXPECTED - running
if missing:
    print()
    for m in sorted(missing):
        print(f"  !! MISSING: {m}")
