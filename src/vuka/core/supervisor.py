#!/usr/bin/env python3
"""
supervisor.py - Project Vuka Always-On Watchdog
Keeps bot instances running 24/7 with crash recovery.
"""

import json
import subprocess
import time
import os
import sys
import signal
import threading
import psutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from vuka.utils.unified_logger import get_logger
from vuka.utils.notifier import send as send_notification

# Configuration
PROJECT_DIR = Path(__file__).parent.absolute()
LOG_DIR = PROJECT_DIR / "logs"

# Initialize Unified Logger for Supervisor
logger = get_logger("Supervisor")

BOT_SCRIPTS = [
    ("EURUSD", "INGWE"),
    ("GBPUSD", "INGWE"),
    ("EURUSD", "SILVER_BULLET"),
    ("GBPUSD", "SILVER_BULLET"),
]
RESTART_DELAY = 60  # seconds to wait before restarting crashed bot
HEALTH_CHECK_INTERVAL = 30  # seconds between health checks
MAX_CRASHES_BEFORE_ALERT = 5
# Cooldown between crash notifications per bot, so a bot that crash-loops
# cannot flood Telegram/email (the DB-backed command queue may be dead too).
CRASH_NOTIFY_COOLDOWN_SEC = 300
# File-based command queue fallback used when the SQLite queue is unavailable.
FILE_COMMAND_QUEUE = PROJECT_DIR / "supervisor_commands.json"

# Setup logging
LOG_DIR.mkdir(exist_ok=True)


class _ManagedProcess:
    """Shared subprocess lifecycle used by both bot instances and generic jobs.

    Both BotInstance and ManagedProcess share the same start/stop/restart
    mechanics; subclasses only differ in how the command line is built.
    """

    def __init__(self, name: str, script: str, args: Optional[List[str]] = None,
                 module_mode: bool = False):
        self.name = name
        self.script = script
        self.args = args or []
        self.module_mode = module_mode  # True -> `python -m script`
        self.process: Optional[subprocess.Popen] = None
        self.crash_count = 0
        self.last_crash_time: Optional[datetime] = None
        self._log_handle = None
        self._restarting = False

    def _build_cmd(self) -> List[str]:
        cmd = [sys.executable]
        if self.module_mode:
            cmd.append("-m")
        cmd.append(self.script)
        cmd.extend(self.args)
        return cmd

    @property
    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None

    def start(self) -> bool:
        if self.is_running:
            logger.info(f"{self.name} is already running (PID: {self.process.pid})")
            return True

        logger.info(f"Starting {self.name} ({self.script})...")

        try:
            log_file = LOG_DIR / f"{self.name.lower().replace(' ', '_')}.log"
            # Open once per spawn and close the parent's handle right after
            # Popen: the child inherits the fd, so nothing leaks here.
            self._log_handle = open(log_file, "a")
            self.process = subprocess.Popen(
                self._build_cmd(),
                cwd=str(PROJECT_DIR),
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )
            self._log_handle.close()
            self._log_handle = None
            logger.info(f"{self.name} started (PID: {self.process.pid})")
            self.crash_count = 0
            return True
        except Exception as e:
            if self._log_handle is not None:
                try:
                    self._log_handle.close()
                except Exception:
                    pass
                self._log_handle = None
            logger.error(f"Failed to start {self.name}: {e}")
            return False

    def stop(self):
        if self.process and self.is_running:
            logger.info(f"Stopping {self.name} (PID: {self.process.pid})...")
            try:
                if os.name == 'nt':
                    self.process.terminate()
                else:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=10)
            except Exception as e:
                logger.warning(f"Error stopping {self.name}: {e}")
                try:
                    if os.name == 'nt':
                        self.process.kill()
                    else:
                        os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except Exception:
                    pass
            self.process = None

    def _notify_due(self) -> bool:
        """Throttle crash notifications so a crash-looping bot cannot flood
        Telegram/email: inform once on the first crash, then only escalate on
        every MAX_CRASHES_BEFORE_ALERT-th crash (crash 5, 10, 15, ...)."""
        if self.crash_count == 1:
            return True
        if (self.crash_count >= MAX_CRASHES_BEFORE_ALERT
                and self.crash_count % MAX_CRASHES_BEFORE_ALERT == 0):
            return True
        return False

    def restart(self):
        """Restart this process without blocking the supervisor loop.

        The crash-cooldown sleep and relaunch run on a daemon thread so a
        crashing bot cannot stall health checks or command processing for the
        other instances (RESTART_DELAY is 60s). A re-entry guard prevents the
        health loop from stacking duplicate restarts.
        """
        if self._restarting:
            logger.info(f"{self.name} already restarting -- skipping")
            return
        self._restarting = True
        threading.Thread(
            target=self._restart_worker,
            name=f"restart-{self.name}",
            daemon=True,
        ).start()

    def _restart_worker(self):
        try:
            self.stop()
            self.crash_count += 1
            self.last_crash_time = datetime.now()

            msg = f"{self.name} crashed #{self.crash_count} -- restarting in {RESTART_DELAY}s"
            logger.warning(msg)

            if self._notify_due():
                if self.crash_count >= MAX_CRASHES_BEFORE_ALERT:
                    alert = f"{self.name} crashed {self.crash_count} times! Manual intervention may be needed."
                    logger.warning(alert)
                    send_notification("SUPERVISOR ALERT", alert, level="ERROR")
                else:
                    send_notification("BOT CRASH", msg, level="WARN")
            logger.info(f"Restarting {self.name} in {RESTART_DELAY}s (crash #{self.crash_count})...")
            time.sleep(RESTART_DELAY)
            self.start()
        finally:
            self._restarting = False


class BotInstance(_ManagedProcess):
    """A trading bot launched as ``python -m vuka.core.bot <symbol> <strategy>``."""

    def __init__(self, symbol: str, strategy: str):
        super().__init__(
            name=f"{symbol}_{strategy}",
            script="vuka.core.bot",
            args=[symbol, strategy],
            module_mode=True,
        )


class ManagedProcess(_ManagedProcess):
    """A generic managed subprocess launched as ``python <script> <args>``."""

    def __init__(self, name: str, script: str, args: Optional[List[str]] = None):
        super().__init__(name=name, script=script, args=args, module_mode=False)


class Supervisor:
    def __init__(self):
        self.bots: Dict[str, BotInstance] = {}
        self.running = False
        self.kronos_ok = False
        self._db = None

        for symbol, strategy in BOT_SCRIPTS:
            bot = BotInstance(symbol, strategy)
            self.bots[bot.name] = bot

    @staticmethod
    def kill_stale_processes():
        """Kill any orphaned Vuka python processes before starting fresh."""
        current_pid = os.getpid()
        killed = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['pid'] == current_pid:
                    continue
                cmdline = proc.info.get('cmdline')
                if not cmdline:
                    continue
                cmd = ' '.join(cmdline)
                if 'python' in proc.info.get('name', '').lower():
                    if any(tag in cmd for tag in ['ingwe.py', 'dashboard.py', 'vuka.core.bot']):
                        proc.kill()
                        killed.append((proc.info['pid'], cmd.split('\\')[-1].split('/')[-1]))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        if killed:
            logger.info(f"Killed {len(killed)} stale process(es):")
            for pid, name in killed:
                logger.info(f"  PID {pid} - {name}")
        else:
            logger.info("No stale processes found.")

    def start_all(self):
        logger.info("=" * 60)
        logger.info("PROJECT VUKA SUPERVISOR STARTING")
        logger.info(f"Project directory: {PROJECT_DIR}")
        logger.info(f"Monitoring {len(self.bots)} bot instances")
        logger.info("=" * 60)

        # Clean slate: kill any orphaned Vuka processes
        self.kill_stale_processes()
        time.sleep(1)

        # Check if Kronos is already running (external process)
        self.kronos_ok = self._check_kronos()
        if self.kronos_ok:
            logger.info("Kronos already running on port 8000 -- skipping managed launch")
        else:
            logger.warning("Kronos not detected on port 8000 -- bots will start in VETO_SAFE mode")

        for name, bot in self.bots.items():
            bot.start()
            time.sleep(2)  # Stagger startup

        send_notification("SUPERVISOR", f"Started -- monitoring {len(self.bots)} bots")

    @staticmethod
    def _check_kronos() -> bool:
        """Check if Kronos is alive via port 8000."""
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.settimeout(2)
            s.connect(('127.0.0.1', 8000))
            return True
        except Exception:
            return False
        finally:
            s.close()

    def stop_all(self):
        logger.info("Stopping all bot instances...")
        for name, bot in self.bots.items():
            bot.stop()
        logger.info("All bots stopped.")

    def check_health(self):
        crashed = []
        kronos_alive = self._check_kronos()
        if not kronos_alive:
            logger.warning("Kronos not responding on port 8000 -- bots will use VETO_SAFE mode")
        for name, bot in self.bots.items():
            if not bot.is_running:
                crashed.append(bot)
                logger.warning(f"{name} is not running!")

        return crashed

    def _pop_commands(self) -> List[Dict]:
        """Pop commands from the DB queue, falling back to a JSON file if the
        SQLite database is unavailable or broken (P0: never crash the watchdog)."""
        if self._db is not None:
            try:
                return self._db.pop_commands()
            except Exception as e:
                logger.error(f"DB command queue unavailable ({e}); using file fallback")
                self._db = None

        # File-based fallback queue (dashboard/supervisor can drop a JSON array).
        try:
            if FILE_COMMAND_QUEUE.exists():
                data = json.loads(FILE_COMMAND_QUEUE.read_text(encoding="utf-8"))
                FILE_COMMAND_QUEUE.unlink(missing_ok=True)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.error(f"Error reading file command queue: {e}")
        return []

    def run(self):
        self.running = True
        logger.info("Supervisor loop started. Press Ctrl+C to stop.")

        # P0: a dead DB must not take the whole watchdog down with it.
        try:
            from vuka.data.database_manager import get_db
            self._db = get_db()
            logger.info("Connected to command queue database.")
        except Exception as e:
            self._db = None
            logger.error(f"Database unavailable ({e}); supervisor running in "
                         "file-queue + health-check mode")

        while self.running:
            try:
                # 1. Process Command Queue
                commands = self._pop_commands()
                for cmd_data in commands:
                    cmd = cmd_data.get('command')
                    target = cmd_data.get('target')

                    logger.info(f"Executing command: {cmd} on {target}")

                    if cmd == "START":
                        if target in self.bots:
                            self.bots[target].start()
                        else:
                            logger.error(f"Unknown bot target: {target}")
                    elif cmd == "STOP":
                        if target in self.bots:
                            self.bots[target].stop()
                        else:
                            logger.error(f"Unknown bot target: {target}")
                    elif cmd == "STOP_ALL":
                        self.stop_all()
                    elif cmd == "RESTART_SERVER":
                        logger.info("Restarting Kronos Server...")
                        subprocess.Popen([sys.executable, "-m", "vuka.ai.kronos_server"])

                # 2. Health Check
                crashed = self.check_health()

                for bot in crashed:
                    bot.restart()

                time.sleep(HEALTH_CHECK_INTERVAL)

            except KeyboardInterrupt:
                logger.info("Keyboard interrupt received.")
                self.running = False
            except Exception as e:
                logger.error(f"Supervisor error: {e}")
                time.sleep(5)

        self.stop_all()
        logger.info("Supervisor stopped.")


_supervisor_ref = None


def signal_handler(signum, frame):
    logger.info("Shutdown signal received.")
    if _supervisor_ref is not None:
        _supervisor_ref.stop_all()
    sys.exit(0)


def main():
    global _supervisor_ref
    logger.info("Starting Project Vuka Supervisor...")

    supervisor = Supervisor()
    _supervisor_ref = supervisor

    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)

    try:
        supervisor.start_all()
        supervisor.run()
    finally:
        supervisor.stop_all()


if __name__ == "__main__":
    main()
