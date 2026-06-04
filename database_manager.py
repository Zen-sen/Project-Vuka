"""
Database Manager for Project Vuka
Consolidates all trading data (trades, sessions, SL moves) into single SQLite database
Provides atomic writes, multi-instance locking, and efficient querying

Replaces:
- trades_*.json
- sessions_*.json  
- sl_moves_*.json
- loss_tracking (currently in sessions JSON)
"""

import sqlite3
import json
import threading
import time
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from contextlib import contextmanager


logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Thread-safe SQLite wrapper for Project Vuka trading data.
    Handles atomic writes, multi-instance locking, and schema management.
    """
    
    DB_FILE = "vuka_trading.db"
    LOCK_TIMEOUT_SECONDS = 30
    LOCK_RETRY_BACKOFF_MS = [50, 100, 200, 500]  # Exponential backoff
    
    def __init__(self, db_path: str = DB_FILE, enable_wal: bool = True):
        """
        Initialize database manager.
        
        Args:
            db_path: Path to SQLite database file
            enable_wal: Enable Write-Ahead Logging for better concurrency
        """
        self.db_path = Path(db_path)
        self.local = threading.local()
        self.enable_wal = enable_wal
        
        # Create/initialize database
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self.local, 'connection'):
            conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            
            # Enable WAL for better concurrency
            if self.enable_wal:
                conn.execute("PRAGMA journal_mode=WAL")
            
            # Optimize for faster writes
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=10000")
            
            self.local.connection = conn
        
        return self.local.connection
    
    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Trades table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                time DATETIME NOT NULL,
                direction TEXT NOT NULL,
                entry_req REAL,
                entry_fill REAL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                lot_size REAL NOT NULL,
                effective_rr REAL,
                retcode INTEGER,
                comment TEXT,
                session TEXT,
                market_mode TEXT,
                slippage REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(symbol, strategy, time, direction)
            )
        """)
        
        # Sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                session_name TEXT NOT NULL,
                traded BOOLEAN DEFAULT FALSE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, symbol, strategy, session_name)
            )
        """)
        
        # Loss tracking table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS loss_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                consecutive_losses INTEGER DEFAULT 0,
                last_counted_ticket INTEGER DEFAULT 0,
                last_update DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, symbol, strategy)
            )
        """)
        
        # Add last_counted_ticket column for existing databases (safe if already exists)
        try:
            cursor.execute("ALTER TABLE loss_tracking ADD COLUMN last_counted_ticket INTEGER DEFAULT 0")
        except:
            pass
        
        # SL movements table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sl_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                time DATETIME NOT NULL,
                ticket INTEGER,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                entry REAL,
                old_sl REAL,
                new_sl REAL,
                movement REAL,
                label TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Instance locks table (for multi-instance synchronization)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS instance_locks (
                instance_tag TEXT PRIMARY KEY,
                locked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL
            )
        """)
        
        # System logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                level TEXT NOT NULL,
                component TEXT NOT NULL,
                message TEXT NOT NULL,
                symbol TEXT,
                strategy TEXT,
                trace_id TEXT,
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Command queue table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS command_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command TEXT NOT NULL,
                target TEXT,
                params TEXT,
                status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Indices for common queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol_time ON trades(symbol, time DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_date_symbol ON sessions(date, symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_losses_date_symbol ON loss_tracking(date, symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sl_time ON sl_movements(time DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON system_logs(timestamp DESC)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_component ON system_logs(component)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_trace ON system_logs(trace_id)")
        
        conn.commit()
        logger.info(f"Database initialized: {self.db_path}")
    
    @contextmanager
    def _transaction(self):
        """Context manager for atomic transactions."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Transaction failed: {e}")
            raise
    
    def acquire_lock(self, instance_tag: str, timeout_seconds: int = LOCK_TIMEOUT_SECONDS) -> bool:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
        
        for attempt, backoff_ms in enumerate(self.LOCK_RETRY_BACKOFF_MS):
            try:
                with self._transaction() as conn:
                    cur = conn.execute(
                        "SELECT 1 FROM instance_locks WHERE instance_tag = ? AND expires_at > ?",
                        (instance_tag, datetime.now(timezone.utc))
                    )
                    if cur.fetchone():
                        return False
                    conn.execute("""
                        INSERT OR REPLACE INTO instance_locks 
                        (instance_tag, locked_at, expires_at)
                        VALUES (?, ?, ?)
                    """, (instance_tag, datetime.now(timezone.utc), expires_at))
                    return True
            except sqlite3.OperationalError:
                if attempt < len(self.LOCK_RETRY_BACKOFF_MS) - 1:
                    time.sleep(backoff_ms / 1000.0)
                else:
                    return False
        return False
    
    def release_lock(self, instance_tag: str):
        """Release lock for instance."""
        try:
            with self._transaction() as conn:
                conn.execute("DELETE FROM instance_locks WHERE instance_tag = ?", (instance_tag,))
        except Exception as e:
            logger.error(f"Error releasing lock for {instance_tag}: {e}")
    
    def insert_trade(self, trade_dict: Dict[str, Any]) -> int:
        """
        Insert or update trade record.
        
        Args:
            trade_dict: Trade data (must include: symbol, strategy, time, direction, sl, tp, lot_size)
            
        Returns:
            Row ID of inserted/updated trade
        """
        try:
            with self._transaction() as conn:
                cursor = conn.cursor()
                
                # Extract required fields
                symbol = trade_dict.get("symbol", "UNKNOWN")
                strategy = trade_dict.get("strategy", "UNKNOWN")
                
                cursor.execute("""
                    INSERT INTO trades (
                        symbol, strategy, time, direction,
                        entry_req, entry_fill, sl, tp, lot_size,
                        effective_rr, retcode, comment, session, market_mode, slippage
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol,
                    strategy,
                    trade_dict.get("time"),
                    trade_dict.get("direction"),
                    trade_dict.get("entry_req"),
                    trade_dict.get("entry_fill"),
                    trade_dict.get("sl"),
                    trade_dict.get("tp"),
                    trade_dict.get("lot_size"),
                    trade_dict.get("effective_rr"),
                    trade_dict.get("retcode"),
                    trade_dict.get("comment"),
                    trade_dict.get("session"),
                    trade_dict.get("market_mode"),
                    trade_dict.get("slippage")
                ))
                
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            logger.debug(f"Trade already exists (duplicate): {trade_dict.get('time')}")
            return -1
        except Exception as e:
            logger.error(f"Error inserting trade: {e}")
            raise
    
    def insert_session(self, date: str, symbol: str, strategy: str, session_name: str, traded: bool = False):
        """Insert session record."""
        try:
            with self._transaction() as conn:
                conn.execute("""
                    INSERT OR IGNORE INTO sessions
                    (date, symbol, strategy, session_name, traded)
                    VALUES (?, ?, ?, ?, ?)
                """, (date, symbol, strategy, session_name, traded))
        except Exception as e:
            logger.error(f"Error inserting session: {e}")
            raise
    
    def mark_session_traded(self, date: str, symbol: str, strategy: str, session_name: str):
        """Mark session as traded."""
        try:
            with self._transaction() as conn:
                conn.execute("""
                    UPDATE sessions SET traded = TRUE
                    WHERE date = ? AND symbol = ? AND strategy = ? AND session_name = ?
                """, (date, symbol, strategy, session_name))
        except Exception as e:
            logger.error(f"Error updating session: {e}")
            raise
    
    def get_session_status(self, date: str, symbol: str, strategy: str, session_name: str) -> bool:
        """Get whether a session has been traded."""
        try:
            conn = self._get_connection()
            cursor = conn.execute("""
                SELECT traded FROM sessions
                WHERE date = ? AND symbol = ? AND strategy = ? AND session_name = ?
            """, (date, symbol, strategy, session_name))
            row = cursor.fetchone()
            return row["traded"] if row else False
        except Exception as e:
            logger.error(f"Error getting session status: {e}")
            return False
    
    def update_loss_tracking(self, date: str, symbol: str, strategy: str, consecutive_losses: int, last_counted_ticket: int = 0):
        """Update consecutive loss counter and last counted deal ticket."""
        try:
            with self._transaction() as conn:
                conn.execute("""
                    INSERT INTO loss_tracking
                    (date, symbol, strategy, consecutive_losses, last_counted_ticket, last_update)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, symbol, strategy) DO UPDATE SET
                        consecutive_losses = ?,
                        last_counted_ticket = ?,
                        last_update = CURRENT_TIMESTAMP
                """, (
                    date, symbol, strategy, consecutive_losses, last_counted_ticket,
                    datetime.now(timezone.utc),
                    consecutive_losses, last_counted_ticket
                ))
        except Exception as e:
            logger.error(f"Error updating loss tracking: {e}")
            raise
    
    def get_loss_tracking(self, date: str, symbol: str, strategy: str) -> tuple:
        """Get (consecutive_losses, last_counted_ticket) for today."""
        try:
            conn = self._get_connection()
            cursor = conn.execute("""
                SELECT consecutive_losses, last_counted_ticket FROM loss_tracking
                WHERE date = ? AND symbol = ? AND strategy = ?
            """, (date, symbol, strategy))
            row = cursor.fetchone()
            if row:
                return (row["consecutive_losses"], row["last_counted_ticket"])
            return (0, 0)
        except Exception as e:
            logger.error(f"Error getting loss tracking: {e}")
            return (0, 0)
    
    def insert_sl_movement(self, sl_move_dict: Dict[str, Any]) -> int:
        """Insert SL movement record."""
        try:
            with self._transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO sl_movements
                    (time, ticket, symbol, strategy, entry, old_sl, new_sl, movement, label)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sl_move_dict.get("time"),
                    sl_move_dict.get("ticket"),
                    sl_move_dict.get("symbol"),
                    sl_move_dict.get("strategy"),
                    sl_move_dict.get("entry"),
                    sl_move_dict.get("old_sl"),
                    sl_move_dict.get("new_sl"),
                    sl_move_dict.get("movement"),
                    sl_move_dict.get("label")
                ))
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"Error inserting SL movement: {e}")
            raise
    
    def get_trades(self, symbol: str = None, strategy: str = None, days: int = 1, limit: int = 100) -> List[Dict]:
        """Query trades with optional filtering."""
        try:
            conn = self._get_connection()
            
            where_clauses = []
            params = []
            
            if symbol:
                where_clauses.append("symbol = ?")
                params.append(symbol)
            
            if strategy:
                where_clauses.append("strategy = ?")
                params.append(strategy)
            
            if days > 0:
                where_clauses.append("time >= datetime('now', '-' || ? || ' days')")
                params.append(days)
            
            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
            
            query = f"""
                SELECT * FROM trades
                WHERE {where_sql}
                ORDER BY time DESC
                LIMIT ?
            """
            params.append(limit)
            
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error querying trades: {e}")
            return []
    
    def get_daily_trades_count(self, date: str, symbol: str = None, strategy: str = None) -> int:
        """Get count of trades for a specific day."""
        try:
            conn = self._get_connection()
            
            where_clauses = ["DATE(time) = ?"]
            params = [date]
            
            if symbol:
                where_clauses.append("symbol = ?")
                params.append(symbol)
            
            if strategy:
                where_clauses.append("strategy = ?")
                params.append(strategy)
            
            where_sql = " AND ".join(where_clauses)
            
            cursor = conn.execute(f"SELECT COUNT(*) as count FROM trades WHERE {where_sql}", params)
            result = cursor.fetchone()
            return result["count"] if result else 0
        except Exception as e:
            logger.error(f"Error getting trade count: {e}")
            return 0
    
    def log_event(self, level: str, component: str, message: str, 
                     symbol: str = None, strategy: str = None, 
                     trace_id: str = None, metadata: Dict = None):
        """
        Log a system event to the database.
        
        Args:
            level: Log level (INFO, WARN, ERROR, TRADE, GUARD)
            component: Component name (e.g., "Ingwe", "Kronos", "Supervisor")
            message: The log message
            symbol: Trading symbol (optional)
            strategy: Trading strategy (optional)
            trace_id: Correlation ID for tracing requests across components (optional)
            metadata: Additional structured data as a dictionary (optional)
        """
        try:
            with self._transaction() as conn:
                conn.execute("""
                    INSERT INTO system_logs 
                    (level, component, message, symbol, strategy, trace_id, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    level,
                    component,
                    message,
                    symbol,
                    strategy,
                    trace_id,
                    json.dumps(metadata) if metadata else None
                ))
        except Exception as e:
            # Fallback to standard python logging if DB logging fails
            logger.error(f"Critical failure writing to system_logs: {e}")
    
    def push_command(self, command: str, target: str = None, params: Dict = None):
        """Push a command to the queue for the Supervisor."""
        try:
            with self._transaction() as conn:
                conn.execute("""
                    INSERT INTO command_queue (command, target, params)
                    VALUES (?, ?, ?)
                """, (command, target, json.dumps(params) if params else None))
        except Exception as e:
            logger.error(f"Error pushing command: {e}")

    def pop_commands(self) -> List[Dict]:
        try:
            with self._transaction() as conn:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    "SELECT * FROM command_queue WHERE status = 'pending'"
                )
                commands = [dict(row) for row in cursor.fetchall()]
                if commands:
                    ids = tuple(c["id"] for c in commands)
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(
                        f"UPDATE command_queue SET status = 'processed' WHERE id IN ({placeholders})",
                        ids
                    )
                return commands
        except Exception as e:
            logger.error(f"Error popping commands: {e}")
            return []

    def close(self):
        """Close database connection."""
        if hasattr(self.local, 'connection'):
            self.local.connection.close()
            delattr(self.local, 'connection')


# Singleton instance
_db_instance = None


def get_db(db_path: str = DatabaseManager.DB_FILE) -> DatabaseManager:
    """Get or create singleton database instance."""
    global _db_instance
    if _db_instance is None:
        _db_instance = DatabaseManager(db_path)
    return _db_instance
