import sys
import time
import json
import queue
import msvcrt
import threading
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any
import psutil
from database_manager import get_db
from unified_logger import get_logger

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.align import Align
from rich import box

logger = get_logger("Dashboard")
CONFIG_PATH = Path("config_v4.6.json")
_OUT = Console(force_terminal=True, legacy_windows=True)
_NO_BOX = box.ASCII
DETACHED = 0x00000008

_CACHE: Dict[str, Any] = {}
_CACHE_TTL: Dict[str, float] = {}

def _cached(key: str, ttl: float, fn):
    now = time.monotonic()
    if key not in _CACHE or now - _CACHE_TTL.get(key, 0) > ttl:
        _CACHE[key] = fn()
        _CACHE_TTL[key] = now
    return _CACHE[key]


def load_config():
    return _cached("config", 30.0, lambda: (
        json.load(open(CONFIG_PATH)) if CONFIG_PATH.exists() else {}
    ))


def check_kronos_health() -> str:
    def _check():
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2) as r:
                return "\u2713" if r.status == 200 else "\u2717"
        except Exception:
            return "\u2717"
    return _cached("kronos_health", 5.0, _check)


class CommandCenter:
    def __init__(self):
        self.db = get_db()
        self.symbols = ["EURUSD", "GBPUSD"]
        self.strategies = ["INGWE", "SILVER_BULLET"]
        self._action_q: queue.Queue = queue.Queue()
        self._status_msg = ""
        self._bot_tags = [f"{s}_{g}" for s in self.symbols for g in self.strategies]
        self._trade_files = {
            tag: Path(f"trades_{tag.replace('_', '_')}.json")
            for tag in self._bot_tags
        }

    def _ensure_supervisor(self):
        for proc in psutil.process_iter(['cmdline']):
            try:
                if 'supervisor.py' in ' '.join(proc.info.get('cmdline') or []):
                    return
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        logger.info("Supervisor not running -- starting in background...")
        subprocess.Popen(
            [sys.executable, "supervisor.py"],
            creationflags=DETACHED,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(2)

    def _scan_python_procs(self):
        def _scan():
            result = []
            for p in psutil.process_iter(['pid', 'cmdline']):
                try:
                    c = ' '.join(p.info.get('cmdline') or [])
                    if p.info['cmdline'] and p.info['cmdline'][0].lower().endswith('python.exe'):
                        result.append(c)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return result
        return _cached("procs", 3.0, _scan)

    def get_system_metrics(self):
        return _cached("metrics", 2.0, lambda: {
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
            "time": datetime.now().strftime("%H:%M:%S")
        })

    def get_bot_statuses(self) -> List[Dict[str, Any]]:
        def _build():
            procs = self._scan_python_procs()
            status_list = []
            for tag in self._bot_tags:
                symbol, strategy = tag.split('_', 1)
                running = any('ingwe.py' in c and symbol in c and strategy in c for c in procs)
                mode = "-"
                for c in procs:
                    if 'ingwe.py' in c and symbol in c and strategy in c:
                        if '--backtest' in c:
                            mode = "BACKTEST"
                        elif '--check' in c or '--test' in c:
                            mode = "CHECK"
                        else:
                            mode = "LIVE"
                        break
                status_list.append({
                    "tag": tag, "symbol": symbol, "strategy": strategy,
                    "pnl": self._get_bot_pnl(tag),
                    "mode": mode,
                    "last_seen": self._get_bot_activity(tag),
                    "status": "RUNNING" if running else "STOPPED"
                })
            return status_list
        return _cached("bot_statuses", 3.0, _build)

    def _get_bot_pnl(self, tag: str) -> float:
        file_tag = tag.replace('_', '_')
        paths = [
            Path(f"trades_{file_tag}.json"),
            Path(f"trades_{tag}.json"),
        ]
        for p in paths:
            if p.exists():
                try:
                    trades = json.loads(p.read_text())
                    vals = [float(t.get("pnl_usd") or 0) for t in trades if t.get("pnl_usd") is not None]
                    return round(sum(vals), 2) if vals else 0.0
                except Exception:
                    return 0.0
        return 0.0

    def _get_bot_activity(self, tag: str) -> str:
        def _query():
            conn = self.db._get_connection()
            cursor = conn.execute(
                "SELECT MAX(timestamp) FROM system_logs WHERE component = ?", (tag,)
            )
            row = cursor.fetchone()
            if row and row[0]:
                try:
                    elapsed = datetime.now() - datetime.fromisoformat(row[0])
                    secs = int(elapsed.total_seconds())
                    if secs < 60:
                        return f"{secs}s ago"
                    if secs < 3600:
                        return f"{secs // 60}m ago"
                    return f"{secs // 3600}h {secs % 3600 // 60}m ago"
                except Exception:
                    return "-"
            return "-"
        return _cached(f"activity_{tag}", 5.0, _query)

    def _get_bot_logs(self, tag: str):
        def _query():
            symbol, strategy = tag.split('_', 1)
            conn = self.db._get_connection()
            cursor = conn.execute(
                "SELECT timestamp, level, message FROM system_logs "
                "WHERE (symbol = ? AND strategy = ?) ORDER BY timestamp DESC LIMIT 8",
                (symbol, strategy)
            )
            return [dict(row) for row in cursor.fetchall()]
        return _cached(f"logs_{tag}", 2.0, _query)

    def _get_recent_logs(self):
        def _query():
            conn = self.db._get_connection()
            cursor = conn.execute(
                "SELECT timestamp, level, component, message FROM system_logs "
                "ORDER BY timestamp DESC LIMIT 8"
            )
            return [dict(row) for row in cursor.fetchall()]
        return _cached("recent_logs", 2.0, _query)

    # ── Layout ────────────────────────────────────────────────────────────────

    def make_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=4),
            Layout(name="top", size=8),
            Layout(name="main", size=12),
            Layout(name="footer", size=3),
        )
        layout["top"].split_row(
            Layout(name="bots", ratio=2),
            Layout(name="config", ratio=1),
        )
        layout["main"].split_row(
            Layout(name="logs_container", ratio=1),
        )
        return layout

    def update_header(self, layout: Layout):
        metrics = self.get_system_metrics()
        kronos = check_kronos_health()
        header = Text(justify="center")
        header.append("PROJECT VUKA\n", style="bold white on blue")
        header.append("\"The leopard does not miss because it does not rush.\"\n",
                       style="italic dim white on blue")
        header.append(
            f"{metrics['time']}  |  CPU {metrics['cpu']}%  |  RAM {metrics['ram']}%  |  "
            f"Kronos {kronos}",
            style="bold white on blue"
        )
        layout["header"].update(Panel(Align.center(header), style="blue", box=_NO_BOX))

    def update_bots(self, layout: Layout):
        table = Table(expand=True, box=None, show_header=True, padding=(0, 1))
        table.add_column("Bot",      style="cyan",    no_wrap=True)
        table.add_column("Mode",     justify="center", style="yellow")
        table.add_column("P&L $",    justify="right",  style="green")
        table.add_column("Last",     justify="center")
        table.add_column("Status",   justify="center")

        for bot in self.get_bot_statuses():
            status_style = "bold green" if bot["status"] == "RUNNING" else "bold red"
            mode_style = "green" if bot["mode"] == "LIVE" else ("yellow" if bot["mode"] == "BACKTEST" else "dim")
            ls = bot["last_seen"]
            if ls == "-":
                ls_style = "dim"
            elif ls.endswith("s ago"):
                ls_style = "green" if int(ls.split("s")[0]) < 30 else "yellow"
            elif ls.endswith("m ago"):
                ls_style = "yellow"
            else:
                ls_style = "red"
            pnl_val = bot["pnl"]
            pnl_str = f"${pnl_val:+.2f}" if pnl_val != 0 else "$0.00"
            pnl_style = "green" if pnl_val > 0 else ("red" if pnl_val < 0 else "dim")
            table.add_row(
                bot["tag"],
                Text(bot["mode"], style=mode_style),
                Text(pnl_str, style=pnl_style),
                Text(ls, style=ls_style),
                Text(bot["status"], style=status_style)
            )
        layout["bots"].update(Panel(table, title="Fleet Status", border_style="cyan", box=_NO_BOX))

    def update_config(self, layout: Layout):
        cfg = load_config()
        text = Text()
        veto = cfg.get("veto_gate", {})
        text.append("Veto Gate\n", style="bold underline")
        text.append(f"  Mode:      {veto.get('mode', 'N/A')}\n")
        text.append(f"  Safety:    {veto.get('safety_mode', 'N/A')}\n")
        text.append(f"  Threshold: {veto.get('threshold', 'N/A')}\n\n")
        sm = cfg.get("state_manager", {})
        text.append("State Manager\n", style="bold underline")
        text.append(f"  Atomic:    {sm.get('atomic_write_enabled', 'N/A')}\n")
        text.append(f"  Backups:   {sm.get('max_backups', 'N/A')}\n")
        kronos = check_kronos_health()
        text.append(f"\nKronos: {kronos}", style="bold white on green" if kronos == "\u2713" else "bold white on red")
        layout["config"].update(Panel(text, title="Config", border_style="yellow", box=_NO_BOX))

    def update_logs(self, layout: Layout):
        log_layout = Layout()
        log_layout.split_row(
            *[Layout(name=tag, ratio=1) for tag in self._bot_tags]
        )
        for tag in self._bot_tags:
            bot_logs = self._get_bot_logs(tag)
            log_text = Text(overflow="fold")
            for log in bot_logs:
                level_style = {
                    "INFO": "white", "WARN": "yellow",
                    "ERROR": "red",  "TRADE": "green", "GUARD": "magenta"
                }.get(log['level'], "white")
                log_text.append(f"[{log['timestamp'][-8:]}] ", style="dim")
                log_text.append(f"{log['level'][:4]} ", style=level_style)
                log_text.append(f"{log['message'][:60]}\n")
            log_layout[tag].update(
                Panel(log_text, title=tag, border_style="blue", box=_NO_BOX)
            )
        layout["logs_container"].update(log_layout)

    def update_footer(self, layout: Layout):
        text = Text(justify="center")
        text.append("[S]tart  [K]ill  [R]estart  [X] Stop All  [Q]uit\n", style="bold white")
        text.append(
            f"Bots: 1={self._bot_tags[0]}  2={self._bot_tags[1]}  "
            f"3={self._bot_tags[2]}  4={self._bot_tags[3]}",
            style="dim"
        )
        if self._status_msg:
            text.append(f"\n{self._status_msg}", style="italic yellow")
        layout["footer"].update(Panel(Align.center(text), border_style="dim", box=_NO_BOX))

    # ── Input ─────────────────────────────────────────────────────────────────

    def _input_thread(self):
        pending_action = None
        while True:
            try:
                if msvcrt.kbhit():
                    ch = msvcrt.getch().decode('ascii', errors='ignore').upper()
                    if ch in ('R', 'X', 'Q'):
                        self._action_q.put(ch)
                    elif ch == 'S':
                        pending_action = 'START'
                        self._action_q.put('_STATUS_START')
                    elif ch == 'K':
                        pending_action = 'STOP'
                        self._action_q.put('_STATUS_KILL')
                    elif ch in ('1', '2', '3', '4') and pending_action:
                        tag = self._bot_tags[int(ch) - 1]
                        self._action_q.put((pending_action, tag))
                        pending_action = None
                    elif ch:
                        pending_action = None
                time.sleep(0.05)
            except Exception:
                break

    def _kill_all(self):
        targets = {"ingwe.py", "kronos_server.py", "supervisor.py", "dashboard.py"}
        for p in psutil.process_iter(['pid', 'cmdline']):
            try:
                if any(t in ' '.join(p.info.get('cmdline') or []) for t in targets):
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _handle_action(self, action):
        if action == 'Q':
            self._kill_all()
            return True
        elif action == '_STATUS_START':
            self._status_msg = "Press 1-4 to select bot to START"
        elif action == '_STATUS_KILL':
            self._status_msg = "Press 1-4 to select bot to KILL"
        elif isinstance(action, tuple):
            cmd, tag = action
            if cmd == 'START':
                self.db.push_command("START", target=tag)
                self._status_msg = f"START sent to {tag}"
            elif cmd == 'STOP':
                self.db.push_command("STOP", target=tag)
                self._status_msg = f"STOP sent to {tag}"
        elif action == 'R':
            self.db.push_command("RESTART_SERVER")
            self._status_msg = "RESTART_SERVER sent"
        elif action == 'X':
            self.db.push_command("STOP_ALL")
            self._status_msg = "STOP_ALL sent"
        return False

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        self._ensure_supervisor()
        layout = self.make_layout()
        threading.Thread(target=self._input_thread, daemon=True).start()

        with Live(layout, console=_OUT, refresh_per_second=1, screen=True, transient=False) as live:
            while True:
                self.update_header(layout)
                self.update_bots(layout)
                self.update_config(layout)
                self.update_logs(layout)
                self.update_footer(layout)
                live.update(layout)

                try:
                    action = self._action_q.get_nowait()
                    if self._handle_action(action):
                        break
                except queue.Empty:
                    pass

                time.sleep(1.0)


if __name__ == "__main__":
    cmd_center = CommandCenter()
    cmd_center.run()
