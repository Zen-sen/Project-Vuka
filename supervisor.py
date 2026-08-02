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
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# Configuration
PROJECT_DIR = Path(__file__).parent.absolute()
LOG_DIR = PROJECT_DIR / "logs"
BOT_SCRIPTS = [
    ("EURUSD", "INGWE"),
    ("GBPUSD", "INGWE"),
]
RESTART_DELAY = 60  # seconds to wait before restarting crashed bot
HEALTH_CHECK_INTERVAL = 30  # seconds between health checks
MAX_CRASHES_BEFORE_ALERT = 5

# Setup logging
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "supervisor.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


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
        
        if self.crash_count >= MAX_CRASHES_BEFORE_ALERT:
            logger.warning(f"{self.name} crashed {self.crash_count} times! Manual intervention may be needed.")
        
        logger.info(f"Restarting {self.name} in {RESTART_DELAY}s (crash #{self.crash_count})...")
        time.sleep(RESTART_DELAY)
        self.start()


class Supervisor:
    def __init__(self):
        self.bots: Dict[str, BotInstance] = {}
        self.running = False
        
        for symbol, strategy in BOT_SCRIPTS:
            bot = BotInstance(symbol, strategy)
            self.bots[bot.name] = bot
    
    def start_all(self):
        logger.info("=" * 60)
        logger.info("PROJECT VUKA SUPERVISOR STARTING")
        logger.info(f"Project directory: {PROJECT_DIR}")
        logger.info(f"Monitoring {len(self.bots)} bot instances")
        logger.info("=" * 60)
        
        for name, bot in self.bots.items():
            bot.start()
            time.sleep(2)  # Stagger startup
    
    def stop_all(self):
        logger.info("Stopping all bot instances...")
        for name, bot in self.bots.items():
            bot.stop()
        logger.info("All bots stopped.")
    
    def check_health(self):
        crashed = []
        for name, bot in self.bots.items():
            if not bot.is_running:
                crashed.append(bot)
                logger.warning(f"{name} is not running!")
        
        return crashed
    
    def run(self):
        self.running = True
        logger.info("Supervisor loop started. Press Ctrl+C to stop.")
        
        while self.running:
            try:
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


def signal_handler(signum, frame):
    logger.info("Shutdown signal received.")
    sys.exit(0)


def main():
    logger.info("Starting Project Vuka Supervisor...")
    
    # Handle signals
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)
    
    supervisor = Supervisor()
    
    try:
        supervisor.start_all()
        supervisor.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        supervisor.stop_all()
        raise


if __name__ == "__main__":
    main()
