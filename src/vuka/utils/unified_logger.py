"""
Unified Logger for Project Vuka
Centralizes all system events into the SQLite database for correlation and monitoring.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from vuka.data.database_manager import get_db

class UnifiedLogger:
    def __init__(self, component: str):
        self.component = component
        self.db = get_db()
        # Each instance of a bot gets a session_id to track its current run
        self.session_id = str(uuid.uuid4())[:8]

    def log(self, level: str, message: str, 
            symbol: Optional[str] = None, 
            strategy: Optional[str] = None, 
            trace_id: Optional[str] = None, 
            metadata: Optional[Dict[str, Any]] = None):
        """
        Log an event to the unified system log.
        """
        # If no trace_id is provided, we don't create one here to avoid 
        # polluting the logs. Trace IDs should be created at the start 
        # of a specific request/setup flow.
        
        self.db.log_event(
            level=level.upper(),
            component=self.component,
            message=message,
            symbol=symbol,
            strategy=strategy,
            trace_id=trace_id,
            metadata=metadata
        )

    def info(self, message: str, **kwargs):
        self.log("INFO", message, **kwargs)

    def warn(self, message: str, **kwargs):
        self.log("WARN", message, **kwargs)

    def warning(self, message: str, **kwargs):
        self.log("WARN", message, **kwargs)

    def error(self, message: str, **kwargs):
        self.log("ERROR", message, **kwargs)

    def trade(self, message: str, **kwargs):
        self.log("TRADE", message, **kwargs)

    def guard(self, message: str, **kwargs):
        self.log("GUARD", message, **kwargs)

    def create_trace(self) -> str:
        """Generate a unique ID to track a specific trade setup flow."""
        return str(uuid.uuid4())[:8]

# Helper to create loggers for different components
def get_logger(component: str) -> UnifiedLogger:
    return UnifiedLogger(component)
