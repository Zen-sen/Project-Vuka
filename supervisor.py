#!/usr/bin/env python3
"""
supervisor.py - Project Vuka Always-On Watchdog
Keeps bot instances running 24/7 with crash recovery.
"""

import subprocess
import time
import os
import sys
import signal
import logging
import psutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from unified_logger import get_logger
from database_manager import get_db
from notifier import send as send_notification

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

# Setup logging
LOG_DIR.mkdir(exist_ok=True)


class BotInstance:
    def __init__(self, symbol: str, strategy: str):
        self.symbol = symbol
        self.strategy = strategy
        self.process: Optional[subprocess.Popen] = None
        self.crash_count = 0
        self.last_crash_time: Optional[datetime] = None
        
    @property
    def name(self) -> str:
        return f"{self.symbol}_{self.strategy}"
    
    @property
    def is_running(self) -> bool:
        if self.process is None:
            return False
        return self.process.poll() is None
    
    def start(self) -> bool:
        if self.is_running:
            logger.info(f"{self.name} is already running (PID: {self.process.pid})")
            return True
        
        logger.info(f"Starting {self.name}...")
        
        try:
            log_file = LOG_DIR / f"{self.name.lower().replace(' ', '_')}.log"
            self.process = subprocess.Popen(
                [sys.executable, "ingwe.py", self.symbol, self.strategy],
                cwd=str(PROJECT_DIR),
                stdout=open(log_file, "a"),
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )
            logger.info(f"{self.name} started (PID: {self.process.pid})")
            self.crash_count = 0
            return True
        except Exception as e:
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
                except:
                    pass
            self.process = None

    
    def restart(self):
        self.stop()
        self.crash_count += 1
        self.last_crash_time = datetime.now()
        
        msg = f"{self.name} crashed #{self.crash_count} -- restarting in {RESTART_DELAY}s"
        logger.warning(msg)
        
        if self.crash_count >= MAX_CRASHES_BEFORE_ALERT:
            alert = f"{self.name} crashed {self.crash_count} times! Manual intervention may be needed."
            logger.warning(alert)
            send_notification("SUPERVISOR ALERT", alert, level="ERROR")
        
        send_notification("BOT CRASH", msg, level="WARN")
        logger.info(f"Restarting {self.name} in {RESTART_DELAY}s (crash #{self.crash_count})...")
        time.sleep(RESTART_DELAY)
        self.start()


class ManagedProcess:
    def __init__(self, name: str, script: str, args: list = None):
        self.name = name
        self.script = script
        self.args = args or []
        self.process: Optional[subprocess.Popen] = None
        self.crash_count = 0
        self.last_crash_time: Optional[datetime] = None

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
            self.process = subprocess.Popen(
                [sys.executable, self.script, *self.args],
                cwd=str(PROJECT_DIR),
                stdout=open(log_file, "a"),
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            )
            logger.info(f"{self.name} started (PID: {self.process.pid})")
            self.crash_count = 0
            return True
        except Exception as e:
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
                except:
                    pass
            self.process = None

    def restart(self):
        self.stop()
        self.crash_count += 1
        self.last_crash_time = datetime.now()
        if self.crash_count >= MAX_CRASHES_BEFORE_ALERT:
            logger.warning(f"{self.name} crashed {self.crash_count} times! Manual intervention may be needed.")
        logger.info(f"Restarting {self.name} in {RESTART_DELAY}s (crash #{self.crash_count})...")
        time.sleep(RESTART_DELAY)
        self.start()


class Supervisor:
    def __init__(self):
        self.bots: Dict[str, BotInstance] = {}
        self.running = False
        self.kronos_ok = False
        
        for symbol, strategy in BOT_SCRIPTS:
            bot = BotInstance(symbol, strategy)
            self.bots[bot.name] = bot
    
    @staticmethod
    def kill_stale_processes():
        """Kill any orphaned Vuka python processes before starting fresh."""
        killed = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline')
                if not cmdline:
                    continue
                cmd = ' '.join(cmdline)
                if 'python' in proc.info.get('name', '').lower():
                    if any(tag in cmd for tag in ['ingwe.py', 'dashboard.py']):
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
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect(('127.0.0.1', 8000))
            s.close()
            return True
        except Exception:
            return False

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

    
    def run(self):
        self.running = True
        logger.info("Supervisor loop started. Press Ctrl+C to stop.")
        
        db = get_db()
        
        while self.running:
            try:
                # 1. Process Command Queue
                commands = db.pop_commands()
                for cmd_data in commands:
                    cmd = cmd_data['command']
                    target = cmd_data['target']
                    
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
                        # Restarting the server requires a process kill/start
                        # We'll implement this via a subprocess call to the server
                        logger.info("Restarting Kronos Server...")
                        subprocess.Popen([sys.executable, "kronos_server.py"])
                
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
