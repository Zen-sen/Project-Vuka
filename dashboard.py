import os
import sys
import time
import json
import subprocess
from pathlib import Path
import psutil
from datetime import datetime
from typing import List, Dict, Any, Optional
from database_manager import get_db
from unified_logger import get_logger

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.text import Text
from rich.align import Align
from rich.prompt import Prompt

logger = get_logger("Dashboard")
console = Console()
CONFIG_PATH = Path("config_v4.6.json")


def load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


class CommandCenter:
    def __init__(self):
        self.db = get_db()
        self.symbols = ["EURUSD", "GBPUSD"]
        self.strategies = ["INGWE", "SILVER_BULLET"]
        self._supervisor_proc: Optional[subprocess.Popen] = None

    def _ensure_supervisor(self):
        for proc in psutil.process_iter(['cmdline']):
            try:
                cmdline = proc.info.get('cmdline')
                if cmdline and 'supervisor.py' in cmdline:
                    return
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        logger.info("Supervisor not running — starting in background...")
        self._supervisor_proc = subprocess.Popen(
            [sys.executable, "supervisor.py"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(2)
        logger.info("Supervisor started.")

    def get_system_metrics(self):
        return {
            "cpu": psutil.cpu_percent(),
            "ram": psutil.virtual_memory().percent,
            "time": datetime.now().strftime("%H:%M:%S")
        }

    def _match_cmdline(self, tag: str, cmdline) -> bool:
        symbol, strategy = tag.split('_', 1)
        return symbol in cmdline and strategy in cmdline

    def is_bot_running(self, tag: str) -> bool:
        for proc in psutil.process_iter(['cmdline']):
            try:
                cmdline = proc.info.get('cmdline')
                if cmdline and 'ingwe.py' in cmdline:
                    if self._match_cmdline(tag, cmdline):
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def get_bot_mode(self, tag: str) -> str:
        for proc in psutil.process_iter(['cmdline']):
            try:
                cmdline = proc.info.get('cmdline')
                if cmdline and 'ingwe.py' in cmdline:
                    if self._match_cmdline(tag, cmdline):
                        if '--backtest' in cmdline:
                            return "BACKTEST"
                        if '--check' in cmdline or '--test' in cmdline:
                            return "CHECK"
                        return "LIVE"
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return "-"

    def get_last_activity(self, tag: str) -> Optional[str]:
        symbol, strategy = tag.split('_', 1)
        conn = self.db._get_connection()
        cursor = conn.execute(
            "SELECT MAX(timestamp) FROM system_logs WHERE component = ?",
            (tag,)
        )
        row = cursor.fetchone()
        if row and row[0]:
            elapsed = datetime.now() - datetime.fromisoformat(row[0])
            secs = int(elapsed.total_seconds())
            if secs < 60:
                return f"{secs}s ago"
            elif secs < 3600:
                return f"{secs // 60}m ago"
            else:
                return f"{secs // 3600}h {secs % 3600 // 60}m ago"
        return "-"

    def get_bot_statuses(self) -> List[Dict[str, Any]]:
        status_list = []
        for symbol in self.symbols:
            for strategy in self.strategies:
                tag = f"{symbol}_{strategy}"
                running = self.is_bot_running(tag)
                trades = self.db.get_trades(symbol=symbol, strategy=strategy, limit=1)
                pnl = 0.0
                if trades:
                    pnl = trades[0].get('effective_rr', 0)
                status_list.append({
                    "tag": tag,
                    "symbol": symbol,
                    "strategy": strategy,
                    "pnl": pnl,
                    "mode": self.get_bot_mode(tag),
                    "last_seen": self.get_last_activity(tag),
                    "status": "RUNNING" if running else "STOPPED"
                })
        return status_list

    def get_recent_logs(self):
        conn = self.db._get_connection()
        cursor = conn.execute("SELECT timestamp, level, component, message FROM system_logs ORDER BY timestamp DESC LIMIT 12")
        return [dict(row) for row in cursor.fetchall()]

    def make_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=5),
            Layout(name="top", size=10),
            Layout(name="main", size=18),
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
        
        title = Text("\nPROJECT VUKA", style="bold white on blue", justify="center")
        slogan = Text("\n\"The leopard does not miss because it does not rush.\"", style="italic dim white on blue", justify="center")
        
        metrics_line = Text(f"\n{metrics['time']} | CPU: {metrics['cpu']}% | RAM: {metrics['ram']}%", 
                             style="bold white on blue", justify="center")
        
        full_header = title + slogan + metrics_line
        layout["header"].update(Panel(Align.center(full_header), style="blue"))

    def update_bots(self, layout: Layout):
        table = Table(title="Bot Instances", expand=True, box=None)
        table.add_column("Bot Tag", style="cyan", no_wrap=True)
        table.add_column("Strategy", style="magenta")
        table.add_column("Mode", justify="center", style="yellow")
        table.add_column("P&L/RR", justify="right", style="green")
        table.add_column("Last Seen", justify="center")
        table.add_column("Status", justify="center")

        for bot in self.get_bot_statuses():
            status_style = "bold green" if bot["status"] == "RUNNING" else "bold red"
            mode_style = "green" if bot["mode"] == "LIVE" else ("yellow" if bot["mode"] == "BACKTEST" else "dim")
            ls = bot["last_seen"]
            if ls == "-":
                ls_style = "dim"
            elif ls.endswith("s ago"):
                secs = int(ls.split("s")[0])
                ls_style = "green" if secs < 30 else "yellow" if secs < 120 else "red"
            elif ls.endswith("m ago"):
                ls_style = "yellow"
            else:
                ls_style = "red"
            table.add_row(
                bot["tag"],
                bot["strategy"],
                Text(bot["mode"], style=mode_style),
                f"{bot['pnl']:.2f}",
                Text(ls, style=ls_style),
                Text(bot["status"], style=status_style)
            )
        
        layout["bots"].update(Panel(table, title="Fleet Status", border_style="cyan"))



    def update_config(self, layout: Layout):
        cfg = load_config()
        text = Text()

        veto = cfg.get("veto_gate", {})
        text.append("Veto Gate\n", style="bold underline")
        text.append(f"  Enabled:   {veto.get('enabled', 'N/A')}\n")
        text.append(f"  Mode:      {veto.get('mode', 'N/A')}\n")
        text.append(f"  Threshold: {veto.get('threshold', 'N/A')}\n")
        text.append(f"  Safety:    {veto.get('safety_mode', 'N/A')}\n\n")

        tick_engine = cfg.get("tick_engine", {})
        text.append("Tick Engine\n", style="bold underline")
        text.append(f"  Heartbeat: {tick_engine.get('heartbeat_seconds', 180)}s\n")
        text.append(f"  Mode:      {'Event-driven + fallback' if tick_engine.get('heartbeat_enabled', True) else 'Pure event'}\n\n")

        health = cfg.get("health_monitor", {})
        text.append("Health Monitor\n", style="bold underline")
        text.append(f"  Window:    {health.get('window_size', 'N/A')}\n")
        text.append(f"  Interval:  {health.get('anomaly_check_interval', 'N/A')}s\n\n")

        sm = cfg.get("state_manager", {})
        text.append("State Manager\n", style="bold underline")
        text.append(f"  Backups:   {sm.get('max_backups', 'N/A')}\n")
        text.append(f"  Atomic:    {sm.get('atomic_write_enabled', 'N/A')}")

        layout["config"].update(Panel(text, title="System Config", border_style="yellow"))

    def update_logs(self, layout: Layout):
        log_layout = Layout()
        bot_statuses = self.get_bot_statuses()

        log_layout.split_row(
            *[Layout(name=bot["tag"], ratio=1) for bot in bot_statuses]
        )

        for bot in bot_statuses:
            tag = bot["tag"]
            symbol = bot["symbol"]
            strategy = bot["strategy"]

            conn = self.db._get_connection()
            cursor = conn.execute("""
                SELECT timestamp, level, message 
                FROM system_logs 
                WHERE (symbol = ? AND strategy = ?) 
                ORDER BY timestamp DESC LIMIT 15
            """, (symbol, strategy))
            bot_logs = [dict(row) for row in cursor.fetchall()]

            log_text = Text()
            for log in bot_logs:
                level_style = {
                    "INFO": "white", "WARN": "yellow",
                    "ERROR": "red", "TRADE": "green",
                    "GUARD": "magenta"
                }.get(log['level'], "white")
                log_text.append(f"[{log['timestamp'][-8:]}] ", style="dim")
                log_text.append(f"{log['level'][:4]} ", style=level_style)
                log_text.append(f"{log['message']}\n")

            log_layout[tag].update(
                Panel(log_text, title=f"Logs: {tag}", border_style="blue")
            )

        layout["logs_container"].update(log_layout)

    def update_footer(self, layout: Layout):
        footer_text = Text(" [S]tart Bot | [K]ill Bot | [R]estart Server | [X] Stop All | [Q]uit", 
                           style="bold white on black", justify="center")
        layout["footer"].update(Panel(Align.center(footer_text), border_style="dim"))

    def select_bot(self, action_name: str) -> Optional[str]:
        """Displays a numbered list of bots and returns the selected tag."""
        bots = self.get_bot_statuses()
        if not bots:
            console.print("[red]No bots configured.[/red]")
            return None
        
        console.print(f"\n[bold cyan]Select a bot to {action_name}:[/bold cyan]")
        for i, bot in enumerate(bots, 1):
            status_color = "green" if bot["status"] == "RUNNING" else "red"
            console.print(f" {i}. [bold]{bot['tag']}[/bold] ([{status_color}]{bot['status']}[/{status_color}])")
        
        try:
            choice = Prompt.ask("\nChoice", choices=[str(i) for i in range(1, len(bots) + 1)])
            return bots[int(choice) - 1]["tag"]
        except Exception:
            return None

    def run(self):
        self._ensure_supervisor()
        layout = self.make_layout()
        
        with Live(layout, refresh_per_second=1, screen=True) as live:
            while True:
                self.update_header(layout)
                self.update_bots(layout)
                self.update_config(layout)
                self.update_logs(layout)
                self.update_footer(layout)
                time.sleep(1) 
                break 
        
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self.print_static_view()
            
            choice = Prompt.ask("\nAction", choices=["S", "K", "R", "X", "Q"], default="Q")
            
            if choice == 'Q':
                if self._supervisor_proc:
                    self._supervisor_proc.terminate()
                break
            elif choice == 'S':
                tag = self.select_bot("Start")
                if tag:
                    self.db.push_command("START", target=tag)
                    logger.info(f"Command pushed: START {tag}")
            elif choice == 'K':
                tag = self.select_bot("Kill")
                if tag:
                    self.db.push_command("STOP", target=tag)
                    logger.info(f"Command pushed: STOP {tag}")
            elif choice == 'R':
                self.db.push_command("RESTART_SERVER")
                logger.info("Command pushed: RESTART_SERVER")
            elif choice == 'X':
                self.db.push_command("STOP_ALL")
                logger.info("Command pushed: STOP_ALL")


    def print_static_view(self):
        layout = self.make_layout()
        self.update_header(layout)
        self.update_bots(layout)
        self.update_config(layout)
        self.update_logs(layout)
        self.update_footer(layout)
        console.print(layout)

if __name__ == "__main__":
    cmd_center = CommandCenter()
    cmd_center.run()
