import json
import os
import shutil
from pathlib import Path
from datetime import datetime, timezone
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages session persistence with atomic writes and backup rotation.
    
    Features:
    - Atomic writes (temp file + move) prevent corruption
    - Backup rotation keeps last N versions
    - Corruption detection with automatic recovery
    - JSON schema validation
    """
    
    def __init__(self, session_file: str = "sessions_today.json", max_backups: int = 10):
        self.session_file = Path(session_file)
        self.backup_dir = self.session_file.parent / "state_backups"
        self.backup_dir.mkdir(exist_ok=True)
        self.max_backups = max_backups
        self.lock_file = self.session_file.with_suffix(".lock")
    
    def _atomic_write(self, data: dict) -> bool:
        """
        Write data atomically using temp file + move pattern.
        Prevents partial writes if process crashes mid-write.
        """
        temp_file = self.session_file.with_suffix(".tmp")
        
        try:
            # Step 1: Write to temp file
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            
            # Step 2: Atomic move (replaces old file)
            if self.session_file.exists():
                # Keep old as .bak for recovery
                backup_bak = self.session_file.with_suffix(".bak")
                if backup_bak.exists():
                    backup_bak.unlink()
                shutil.move(str(self.session_file), str(backup_bak))
            
            shutil.move(str(temp_file), str(self.session_file))
            
            logger.info(f"Atomic write successful: {self.session_file}")
            return True
        
        except Exception as e:
            logger.error(f"Atomic write failed: {str(e)}")
            if temp_file.exists():
                temp_file.unlink()
            return False
    
    def _rotate_backups(self):
        """Keep only max_backups versioned backups, delete oldest."""
        try:
            backups = sorted(self.backup_dir.glob("session_*.json"))
            
            if len(backups) > self.max_backups:
                for old_backup in backups[:-self.max_backups]:
                    old_backup.unlink()
                    logger.info(f"Deleted old backup: {old_backup.name}")
        
        except Exception as e:
            logger.warning(f"Backup rotation failed: {str(e)}")
    
    def save_session(self, trades: list, metadata: dict) -> bool:
        """
        Save session state with atomic write and backup creation.
        
        Args:
            trades: List of executed trades
            metadata: Session metadata (PnL, timestamps, etc.)
        
        Returns:
            True if save successful, False otherwise
        """
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trades": trades,
            "metadata": metadata,
            "version": "v4.6",
            "backup_count": len(list(self.backup_dir.glob("session_*.json")))
        }
        
        # Step 1: Create timestamped backup before overwriting
        try:
            if self.session_file.exists():
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_file = self.backup_dir / f"session_{timestamp}.json"
                shutil.copy2(self.session_file, backup_file)
                logger.debug(f"Backup created: {backup_file.name}")
        except Exception as e:
            logger.warning(f"Backup creation failed: {str(e)}")
        
        # Step 2: Atomic write
        success = self._atomic_write(data)
        
        if success:
            self._rotate_backups()
            logger.info(f"Session saved: {len(trades)} trades")
        
        return success
    
    def load_session(self) -> Optional[dict]:
        """
        Load session state with corruption detection.
        
        Returns:
            Session dict if valid, None if corrupted or missing
            Automatically attempts recovery from backups on corruption
        """
        if not self.session_file.exists():
            logger.info("No session file found")
            return None
        
        try:
            with open(self.session_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Validate schema
            self._validate_schema(data)
            
            logger.info(f"Session loaded: {len(data.get('trades', []))} trades")
            return data
        
        except json.JSONDecodeError as e:
            logger.error(f"Session file corrupted (JSON): {str(e)}")
            return self._restore_from_backup()
        
        except ValueError as e:
            logger.error(f"Session file invalid: {str(e)}")
            return self._restore_from_backup()
        
        except Exception as e:
            logger.error(f"Session load failed: {str(e)}")
            return None
    
    def _validate_schema(self, data: dict):
        """Validate session data structure."""
        required = ["timestamp", "trades", "metadata", "version"]
        
        if not isinstance(data, dict):
            raise ValueError("Session must be a dict")
        
        for field in required:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")
        
        if not isinstance(data["trades"], list):
            raise ValueError("Trades must be a list")
        
        # Validate each trade has required fields
        for i, trade in enumerate(data["trades"]):
            if not isinstance(trade, dict):
                raise ValueError(f"Trade {i} is not a dict")
            trade_fields = ["time", "direction", "entry"]
            for field in trade_fields:
                if field not in trade:
                    raise ValueError(f"Trade {i} missing field: {field}")
    
    def _restore_from_backup(self) -> Optional[dict]:
        """Attempt to restore from most recent valid backup."""
        backups = sorted(self.backup_dir.glob("session_*.json"), reverse=True)
        
        logger.info(f"Attempting recovery from {len(backups)} backups...")
        
        for backup in backups:
            try:
                with open(backup, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                self._validate_schema(data)
                
                logger.info(f"Restored from backup: {backup.name}")
                
                # Restore to main session file
                shutil.copy2(backup, self.session_file)
                
                return data
            
            except Exception as e:
                logger.warning(f"Backup {backup.name} unusable: {str(e)}")
                continue
        
        logger.error("No valid backups available")
        return None
    
    def get_backup_count(self) -> int:
        """Return number of backup files."""
        return len(list(self.backup_dir.glob("session_*.json")))
    
    def clear_backups(self):
        """Remove all backups (use with caution)."""
        try:
            for backup in self.backup_dir.glob("session_*.json"):
                backup.unlink()
            logger.info("Backups cleared")
        except Exception as e:
            logger.error(f"Backup cleanup failed: {str(e)}")


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)
    
    mgr = StateManager()
    
    # Test save
    test_trades = [
        {"time": "2026-05-15 10:00", "direction": "BUY", "entry": 1.0, "sl": 0.99, "tp": 1.03}
    ]
    test_meta = {"daily_pnl": 50, "instance": "EURUSD_INGWE"}
    
    mgr.save_session(test_trades, test_meta)
    
    # Test load
    loaded = mgr.load_session()
    print(f"[OK] Loaded: {len(loaded['trades'])} trades")
    print(f"[OK] Backups: {mgr.get_backup_count()}")