"""
trading_governor.py — P0-FULL Filter System for Project Vuka
Implements session filters, direction filters, and circuit breakers
based on the INGWE strategy analytical report (2026-07-12).

P0-A: Block London Open session
P0-B: Default SELL only (HTF ALIGNED exception)
P0-C: Session whitelist (Asian + London Close)
P0-FULL: Combined + circuit breakers
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unified_logger import get_logger

BASE_DIR = Path(__file__).parent.parent
CONFIG_PATH = BASE_DIR / "config_v4.6.json"
STATE_PATH = BASE_DIR / "data" / "governor_state.json"

logger = get_logger("TradingGovernor")


class TradingGovernor:
    """
    P0-FULL filter gate for the INGWE strategy.

    Filters (in order of evaluation):
      1. Session whitelist  — only trade configured sessions
      2. Session blocklist  — never trade these sessions (overrides whitelist)
      3. Direction filter   — SELL only, with HTF ALIGNED exception for BUY
      4. Daily loss circuit — hard stop when daily P&L <= limit
      5. Weekly trade cap   — max trades per week

    All configurable via config_v4.6.json → "trading_governor" section.
    """

    def __init__(self, config: dict = None):
        cfg = config or self._load_config()

        self.enabled = cfg.get("enabled", True)

        # P0-A / P0-C: Session filters
        self.blocked_sessions = cfg.get("blocked_sessions", ["London Open"])
        self.allowed_sessions = cfg.get("allowed_sessions", ["Asian", "London Close"])

        # P0-B: Direction filter
        self.allowed_directions = cfg.get("allowed_directions", ["SELL"])
        self.htf_aligned_exception = cfg.get("htf_aligned_exception", True)

        # Circuit breakers
        self.daily_loss_limit = cfg.get("daily_loss_limit", 50.0)
        self.weekly_trade_cap = cfg.get("weekly_trade_cap", 10)

        self.log_rejections = cfg.get("log_rejections", True)

        # Runtime state (persisted)
        self._state = self._load_state()
        self._week_start = self._state.get("week_start")
        self._weekly_trades = self._state.get("weekly_trades", 0)

    # ── Config / State I/O ────────────────────────────────────────

    @staticmethod
    def _load_config() -> dict:
        try:
            if CONFIG_PATH.exists():
                with open(CONFIG_PATH) as f:
                    root = json.load(f)
                return root.get("trading_governor", {})
        except Exception:
            pass
        return {}

    def _load_state(self) -> dict:
        try:
            if STATE_PATH.exists():
                with open(STATE_PATH) as f:
                    return json.load(f)
        except Exception:
            pass
        return {"week_start": None, "weekly_trades": 0}

    def _save_state(self):
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "week_start": self._week_start,
            "weekly_trades": self._weekly_trades,
        }
        with open(STATE_PATH, "w") as f:
            json.dump(payload, f, indent=2)

    # ── Weekly counter management ──────────────────────────────────

    def _check_week_rollover(self):
        if self._week_start is None:
            self._week_start = datetime.now(timezone.utc).isoformat()
            self._weekly_trades = 0
            self._save_state()
            return

        stored = datetime.fromisoformat(self._week_start)
        now = datetime.now(timezone.utc)
        if now - stored >= timedelta(days=7):
            self._week_start = now.isoformat()
            self._weekly_trades = 0
            self._save_state()

    # ── Filter: Session ────────────────────────────────────────────

    def check_session(self, session: str, signal_id: str = "") -> tuple:
        """
        Returns (allowed: bool, reason: str).
        P0-A blocks specific sessions. P0-C whitelists sessions.
        """
        if not self.enabled:
            return True, "GOVERNOR_DISABLED"

        # Blocklist check (P0-A)
        if session in self.blocked_sessions:
            if self.log_rejections:
                logger.log("GUARD", f"[P0-A] Blocked session '{session}' for {signal_id}")
            return False, "P0_SESSION_BLOCKED"

        # Whitelist check (P0-C) — if a whitelist is configured
        if self.allowed_sessions and session not in self.allowed_sessions:
            if self.log_rejections:
                logger.log("GUARD", f"[P0-C] Session '{session}' not whitelisted for {signal_id}")
            return False, "P0_SESSION_NOT_WHITELISTED"

        return True, "SESSION_OK"

    # ── Filter: Direction ──────────────────────────────────────────

    def check_direction(self, direction: str, htf_bias: str = None, signal_id: str = "") -> tuple:
        """
        Returns (allowed: bool, reason: str).
        P0-B blocks BUY by default, allows BUY when HTF is ALIGNED BULLISH.
        """
        if not self.enabled:
            return True, "GOVERNOR_DISABLED"

        if direction in self.allowed_directions:
            return True, "DIRECTION_OK"

        # HTF ALIGNED exception: allow BUY when HTF strongly bullish
        if self.htf_aligned_exception and direction == "BUY":
            if htf_bias == "BULLISH":
                return True, "DIRECTION_HTF_EXCEPTION"
            if self.log_rejections:
                logger.log("GUARD", f"[P0-B] BUY blocked (HTF={htf_bias}) for {signal_id}")

        if self.log_rejections:
            logger.log("GUARD", f"[P0-B] Direction '{direction}' blocked for {signal_id}")
        return False, "P0_DIRECTION_BLOCKED"

    # ── Circuit Breakers ───────────────────────────────────────────

    def check_circuit_breakers(self, daily_pnl: float, signal_id: str = "") -> tuple:
        """
        Returns (allowed: bool, reason: str).
        Checks daily loss limit and weekly trade cap.
        """
        if not self.enabled:
            return True, "GOVERNOR_DISABLED"

        # Daily loss circuit
        if daily_pnl <= -self.daily_loss_limit:
            if self.log_rejections:
                logger.log("GUARD", f"[P0-DAILY] Daily loss limit reached ({daily_pnl:.2f}) for {signal_id}")
            return False, "CIRCUIT_DAILY_LOSS"

        # Weekly trade cap
        self._check_week_rollover()
        if self._weekly_trades >= self.weekly_trade_cap:
            if self.log_rejections:
                logger.log("GUARD", f"[P0-WEEKLY] Trade cap reached ({self._weekly_trades}/{self.weekly_trade_cap}) for {signal_id}")
            return False, "CIRCUIT_WEEKLY_CAP"

        return True, "CIRCUITS_OK"

    # ── Unified gate ───────────────────────────────────────────────

    def can_trade(self, session: str, direction: str, daily_pnl: float,
                  htf_bias: str = None, signal_id: str = "") -> tuple:
        """
        Full P0-FULL gate. Checks all filters in order.
        Short-circuit: returns (False, reason) on first rejection.
        """
        allowed, reason = self.check_session(session, signal_id)
        if not allowed:
            return False, reason

        allowed, reason = self.check_direction(direction, htf_bias, signal_id)
        if not allowed:
            return False, reason

        allowed, reason = self.check_circuit_breakers(daily_pnl, signal_id)
        if not allowed:
            return False, reason

        return True, "ALLOW"

    def record_trade(self):
        """Call after a successful trade to update counters."""
        self._weekly_trades += 1
        self._save_state()

    # ── Report ─────────────────────────────────────────────────────

    def report(self) -> str:
        lines = [
            "=== TRADING GOVERNOR ===",
            f"Enabled: {self.enabled}",
            f"Blocked sessions: {self.blocked_sessions}",
            f"Allowed sessions: {self.allowed_sessions}",
            f"Allowed directions: {self.allowed_directions}",
            f"HTF exception: {self.htf_aligned_exception}",
            f"Daily loss limit: -${self.daily_loss_limit:.0f}",
            f"Weekly cap: {self.weekly_trade_cap} trades",
            f"Weekly trades so far: {self._weekly_trades}",
        ]
        return "\n".join(lines)
