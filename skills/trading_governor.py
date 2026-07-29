"""
trading_governor.py — P0-FULL Filter System for Project Vuka
Implements session filters, direction filters, and circuit breakers
based on the INGWE strategy analytical report (2026-07-12).

Phase 3 changes:
- P0-A: Dynamic session filtering via concept_tracker (data-driven veto)
- P0-B: Direction filter uses per-pattern win rates
- P0-C: Session whitelist
- P0-FULL: Combined + circuit breakers
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from vuka.utils.unified_logger import get_logger

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
      3. Direction filter   — data-driven, allows BUY if WR > min_buy_win_rate
      4. Pattern veto       — auto-block if concept_tracker WR < veto_threshold
      5. Daily loss circuit — hard stop when daily P&L <= limit
      6. Weekly trade cap   — max trades per week

    All configurable via config_v4.6.json → "trading_governor" section.
    """

    def __init__(self, config: dict = None):
        cfg = config or self._load_config()

        self.enabled = cfg.get("enabled", True)

        # P0-A / P0-C: Session filters
        self.blocked_sessions = cfg.get("blocked_sessions", [])
        self.allowed_sessions = cfg.get("allowed_sessions", ["Asian", "London Close", "London Open"])

        # P0-B: Direction filter
        self.allowed_directions = cfg.get("allowed_directions", ["SELL", "BUY"])
        self.htf_aligned_exception = cfg.get("htf_aligned_exception", True)

        # Circuit breakers
        self.daily_loss_limit = cfg.get("daily_loss_limit", 50.0)
        self.weekly_trade_cap = cfg.get("weekly_trade_cap", 10)

        self.log_rejections = cfg.get("log_rejections", True)

        # Phase 3: Data-driven config
        dd = cfg.get("data_driven", {})
        self.dd_enabled = dd.get("enabled", True)
        self.dd_min_samples = dd.get("min_samples", 15)
        self.dd_veto_wr = dd.get("veto_win_rate", 0.40)
        self.dd_approve_wr = dd.get("approve_win_rate", 0.60)
        self.dd_min_buy_wr = dd.get("min_buy_win_rate", 0.45)

        # Market Circuit: Phase-based trading rules
        mc = cfg.get("market_circuit", {})
        self.mc_enabled = mc.get("enabled", True)
        self.mc_block_phases = mc.get("block_phases", ["CHOP"])
        self.mc_caution_phases = mc.get("caution_phases", ["SQUEEZE", "CONSOLIDATION"])
        self.mc_prefer_phases = mc.get("prefer_phases", ["EXPANSION_BULLISH", "EXPANSION_BEARISH"])

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

    # ── Phase 3a/3b: Data-driven pattern veto ──────────────────────

    def _check_pattern_veto(self, symbol: str, session: str, setup_type: str,
                            direction: str, signal_id: str = "") -> tuple:
        """
        Check concept_tracker for pattern-based veto.
        Auto-veto if win rate < veto_threshold with sufficient samples.
        """
        if not self.dd_enabled:
            return True, "DATA_DRIVEN_DISABLED"

        try:
            from skills.concept_tracker import get_pattern_win_rate, should_auto_veto
            veto, reason = should_auto_veto(
                symbol, session, setup_type, direction,
                min_samples=self.dd_min_samples,
                veto_threshold=self.dd_veto_wr
            )
            if veto:
                if self.log_rejections:
                    logger.log("GUARD", f"[P0-PATTERN] {reason} for {signal_id}")
                return False, f"P0_PATTERN_VETO:{reason}"
            return True, f"PATTERN_OK:{reason}"
        except ImportError:
            return True, "CONCEPT_TRACKER_UNAVAILABLE"
        except Exception as e:
            if self.log_rejections:
                logger.log("WARN", f"[P0-PATTERN] Error: {e}")
            return True, "PATTERN_VETO_ERROR"

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

    # ── Filter: Direction (Phase 3c: data-driven) ──────────────────

    def check_direction(self, direction: str, htf_bias: str = None,
                        signal_id: str = "", symbol: str = "",
                        session: str = "", setup_type: str = "") -> tuple:
        """
        Returns (allowed: bool, reason: str).
        P0-B uses data-driven per-direction win rates when available.
        """
        if not self.enabled:
            return True, "GOVERNOR_DISABLED"

        # If direction is in the explicit allowed list, approve immediately
        if direction in self.allowed_directions:
            return True, "DIRECTION_OK"

        # Phase 3c: Check per-direction win rate for BUY
        if direction == "BUY" and self.dd_enabled and symbol and session:
            try:
                from skills.concept_tracker import get_pattern_win_rate
                wr, samples = get_pattern_win_rate(
                    symbol, session, setup_type, "BUY",
                    min_samples=self.dd_min_samples
                )
                if samples >= self.dd_min_samples and wr >= self.dd_min_buy_wr:
                    return True, f"DIRECTION_BUY_OK:WR={wr:.0%}({samples}t)"
                if samples >= self.dd_min_samples and wr < self.dd_min_buy_wr:
                    if self.log_rejections:
                        logger.log("GUARD", f"[P0-B] BUY blocked for {signal_id}: WR={wr:.0%}<{self.dd_min_buy_wr:.0%} ({samples}t)")
                    return False, f"P0_DIRECTION_BLOCKED:BUY_WR={wr:.0%}"
            except ImportError:
                pass

        # HTF ALIGNED exception: allow BUY when HTF strongly bullish
        if self.htf_aligned_exception and direction == "BUY":
            if htf_bias == "BULLISH":
                return True, "DIRECTION_HTF_EXCEPTION"
            if self.log_rejections:
                logger.log("GUARD", f"[P0-B] BUY blocked (HTF={htf_bias}) for {signal_id}")

        if self.log_rejections:
            logger.log("GUARD", f"[P0-B] Direction '{direction}' blocked for {signal_id}")
        return False, "P0_DIRECTION_BLOCKED"

    # ── Market Circuit: Phase filter ───────────────────────────────

    def check_market_phase(self, market_phase: str = "UNKNOWN", signal_id: str = "") -> tuple:
        """
        Returns (allowed: bool, reason: str).
        Blocks trades during toxic phases, flags caution for others.
        """
        if not self.enabled or not self.mc_enabled:
            return True, "PHASE_FILTER_DISABLED"

        if market_phase == "UNKNOWN":
            return True, "PHASE_UNKNOWN"

        if market_phase in self.mc_block_phases:
            if self.log_rejections:
                logger.log("GUARD", f"[P0-PHASE] Blocked: {market_phase} for {signal_id}")
            return False, f"P0_PHASE_BLOCKED:{market_phase}"

        if market_phase in self.mc_caution_phases:
            if self.log_rejections:
                logger.log("GUARD", f"[P0-PHASE] Caution: {market_phase} for {signal_id}")
            return True, f"PHASE_CAUTION:{market_phase}"

        if market_phase in self.mc_prefer_phases:
            return True, f"PHASE_PREFERRED:{market_phase}"

        return True, f"PHASE_OK:{market_phase}"

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
                  htf_bias: str = None, signal_id: str = "",
                  symbol: str = "", setup_type: str = "",
                  market_phase: str = "UNKNOWN") -> tuple:
        """
        Full P0-FULL gate. Checks all filters in order.
        Short-circuit: returns (False, reason) on first rejection.
        Phase 3: Includes data-driven pattern veto and direction filtering.
        v5.6: Includes market circuit phase filter.
        """
        allowed, reason = self.check_session(session, signal_id)
        if not allowed:
            return False, reason

        # Market Circuit: Phase gate (run early to skip other checks in bad phases)
        allowed, reason = self.check_market_phase(market_phase, signal_id)
        if not allowed:
            return False, reason

        allowed, reason = self.check_direction(
            direction, htf_bias, signal_id,
            symbol=symbol, session=session, setup_type=setup_type
        )
        if not allowed:
            return False, reason

        # Phase 3b: Pattern-based veto from concept_tracker
        if self.dd_enabled and symbol and setup_type:
            allowed, reason = self._check_pattern_veto(
                symbol, session, setup_type, direction, signal_id
            )
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
            f"Data-driven veto: {self.dd_enabled}",
            f"  min_samples={self.dd_min_samples}, veto_wr<{self.dd_veto_wr}, approve_wr>{self.dd_approve_wr}",
            f"  min_buy_wr={self.dd_min_buy_wr}",
            f"Market circuit: {self.mc_enabled}",
            f"  Block phases: {self.mc_block_phases}",
            f"  Caution phases: {self.mc_caution_phases}",
            f"  Prefer phases: {self.mc_prefer_phases}",
            f"Daily loss limit: -${self.daily_loss_limit:.0f}",
            f"Weekly cap: {self.weekly_trade_cap} trades",
            f"Weekly trades so far: {self._weekly_trades}",
        ]
        return "\n".join(lines)
