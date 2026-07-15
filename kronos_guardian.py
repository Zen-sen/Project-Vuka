import json
import logging
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List
from enum import Enum

import pandas as pd
import requests
from notifier import send as send_notification

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
VETO_LOG_FILE = LOG_DIR / "kronos_veto.log"
KRONOS_DECISIONS_FILE = DATA_DIR / "kronos_decisions.json"

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(VETO_LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class HeartbeatMonitor:
    """
    Periodically checks the Kronos API health endpoint.
    Sends notifications on state transitions (up->down, down->up).
    Runs as a daemon thread so it never blocks shutdown.
    """
    def __init__(self, endpoint: str, interval_seconds: int = 60):
        self.health_url = endpoint.replace("/v1/predict-ict", "/health")
        self.interval = interval_seconds
        self._last_known_healthy = None
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        time.sleep(5)
        while not self._stop_event.is_set():
            try:
                resp = requests.get(self.health_url, timeout=5)
                is_healthy = resp.status_code == 200
            except Exception:
                is_healthy = False

            if is_healthy:
                if self._last_known_healthy is False:
                    send_notification("KRONOS HEARTBEAT", "API is back online.", level="INFO")
                    logger.info("Heartbeat: Kronos API recovered.")
                elif self._last_known_healthy is None:
                    logger.info("Heartbeat: Kronos API healthy.")
                self._last_known_healthy = True
            else:
                if self._last_known_healthy is not False:
                    send_notification("KRONOS HEARTBEAT", "API unreachable!", level="ERROR")
                    logger.warning("Heartbeat: Kronos API unreachable.")
                self._last_known_healthy = False

            self._stop_event.wait(self.interval)

    def stop(self):
        self._stop_event.set()


class CircuitBreakerState(Enum):
    """Circuit Breaker States"""
    CLOSED = "CLOSED"          # Normal operation
    OPEN = "OPEN"              # Failing, block requests
    HALF_OPEN = "HALF_OPEN"    # Testing recovery


class CircuitBreaker:
    """
    Prevents cascading failures when Kronos is unavailable.
    
    State Machine:
    CLOSED --[failures > threshold]--> OPEN
    OPEN --[timeout reached]--> HALF_OPEN
    HALF_OPEN --[success]--> CLOSED
    HALF_OPEN --[failure]--> OPEN
    """
    
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: int = 30,
        half_open_max_calls: int = 1
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        
        self.failure_count = 0
        self.state = CircuitBreakerState.CLOSED
        self.last_failure_time = None
        self.half_open_calls = 0
    
    def call(self, func, *args, **kwargs) -> Tuple[bool, any]:
        """
        Execute func with circuit breaker protection.
        
        Returns:
            (success: bool, result: any)
        """
        if self.state == CircuitBreakerState.OPEN:
            # Check if recovery timeout has passed
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitBreakerState.HALF_OPEN
                self.half_open_calls = 0
                logger.info(f"Circuit breaker: OPEN -> HALF_OPEN (recovery test)")
            else:
                return False, None  # Still open, reject
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return True, result
        
        except Exception as e:
            self._on_failure()
            return False, str(e)
    
    def _on_success(self):
        """Reset on successful call"""
        self.failure_count = 0
        old_state = self.state
        self.state = CircuitBreakerState.CLOSED
        
        if old_state == CircuitBreakerState.HALF_OPEN:
            logger.info(f"Circuit breaker: HALF_OPEN -> CLOSED (recovered)")
    
    def _on_failure(self):
        """Increment failure count, possibly open circuit"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.warning(f"Circuit breaker: HALF_OPEN -> OPEN (recovery failed)")
            self.state = CircuitBreakerState.OPEN
            send_notification("KRONOS CIRCUIT", "HALF_OPEN -> OPEN (recovery failed)", level="ERROR")
        
        elif self.failure_count >= self.failure_threshold:
            old_state = self.state
            self.state = CircuitBreakerState.OPEN
            msg = f"Circuit breaker: {old_state.value} -> OPEN (threshold reached: {self.failure_count})"
            logger.warning(msg)
            send_notification("KRONOS CIRCUIT", msg, level="ERROR")
    
    def get_state(self) -> str:
        return self.state.value


class KronosVetoGate:
    """
    Enhanced Kronos Validation Gate with Circuit Breaker & VETO_SAFE Mode
    """
    
    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:8000/v1/predict-ict",
        threshold: float = 0.40,
        enabled: bool = True,
        mode: str = "enforced",
        safety_mode: str = "VETO_SAFE",  # NEW: "VETO_SAFE" | "ALLOW_SAFE"
        heartbeat_interval: int = 0  # 0 = disabled, otherwise seconds between health checks
    ):
        """
        Args:
            endpoint: Kronos API endpoint
            threshold: Minimum confidence to allow trade
            enabled: Enable/disable veto gate
            mode: "advisory" (log only) or "enforced" (block on veto)
            safety_mode: Error handling behavior
                "VETO_SAFE": Block trades on any error (conservative, misses trades)
                "ALLOW_SAFE": Allow trades on errors (aggressive, v4.5 behavior)
            heartbeat_interval: Seconds between health checks (0 = disabled)
        """
        self.endpoint = endpoint
        self.threshold = threshold
        self.enabled = enabled
        self.mode = mode
        self.safety_mode = safety_mode
        self.request_timeout = 2.5
        
        # Last decision (consumed by enrichment pipeline)
        self.last_decision: Optional[dict] = None
        
        # Circuit breaker
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=30,
            half_open_max_calls=1
        )

        # Heartbeat health monitor
        self._heartbeat = None
        if heartbeat_interval > 0:
            self._heartbeat = HeartbeatMonitor(endpoint, heartbeat_interval)
    
    def _log_veto_decision(
        self,
        signal: str,
        symbol: str,
        kronos_agree: bool,
        confidence: float,
        decision: str,
        reason: str,
        circuit_state: str = "CLOSED",
        api_latency_ms: float = 0.0
    ):
        """Log veto decision in JSON format"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal": signal,
            "symbol": symbol,
            "kronos_agree": kronos_agree,
            "confidence": round(confidence, 2),
            "threshold": self.threshold,
            "decision": decision,
            "mode": self.mode,
            "safety_mode": self.safety_mode,
            "circuit_breaker_state": circuit_state,
            "api_latency_ms": round(api_latency_ms, 1),
            "reason": reason
        }
        logger.info(json.dumps(entry))
        self.last_decision = entry
        self._persist_decision(entry)

    def _persist_decision(self, entry: dict):
        """Append decision to structured JSON array for enrichment pipeline."""
        try:
            decisions = []
            if KRONOS_DECISIONS_FILE.exists():
                with open(KRONOS_DECISIONS_FILE) as f:
                    decisions = json.load(f)
            decisions.append(entry)
            with open(KRONOS_DECISIONS_FILE, "w") as f:
                json.dump(decisions, f, indent=2)
        except Exception:
            pass
    
    def _prepare_ohlcv_payload(self, df: pd.DataFrame) -> dict:
        """
        Prepare OHLCV data for Kronos API (v4.6: correct format)
        """
        df = df.tail(512).copy()

        required_cols = ['open', 'high', 'low', 'close', 'volume']
        
        for col in required_cols:
            if col not in df.columns:
                df[col] = 1000.0
        
        # v4.6: Return dict with 'candles' key containing list of records
        candles = []
        for idx, row in df.iterrows():
            candles.append({col: float(row[col]) for col in required_cols})
        
        return {"candles": candles}
    
    def _fallback_confidence(self, context: dict) -> float:
        """
        Calculate fallback confidence based on Ingwe confluence score.
        Used when Kronos is unavailable.
        
        Maps Ingwe score (0-120) to confidence (0-1)
        """
        confluence_score = context.get("confluence_score", 0)
        
        # Scale: 0-60 = 0.2-0.4, 60-90 = 0.4-0.7, 90-120 = 0.7-0.95
        if confluence_score < 60:
            confidence = 0.2 + (confluence_score / 60) * 0.2
        elif confluence_score < 90:
            confidence = 0.4 + ((confluence_score - 60) / 30) * 0.3
        else:
            confidence = 0.7 + ((confluence_score - 90) / 30) * 0.25
        
        return min(0.95, max(0.1, confidence))
    
    def validate(
        self,
        context: dict,
        df: pd.DataFrame,
        symbol: str = "EURUSD"
    ) -> Tuple[bool, str]:
        """
        Validate Ingwe signal against Kronos Oracle with circuit breaker.
        
        Returns:
            (allowed: bool, reason: str)
        """
        if not self.enabled:
            return True, "Veto Gate Disabled"
        
        direction = context.get("direction", "BUY")
        if direction not in ("BUY", "SELL"):
            return True, f"Ignored non-trade signal: {direction}"

        # Phase 4b: Pattern-based veto from concept_tracker (before API call)
        try:
            from skills.concept_tracker import should_auto_veto
            session = context.get("session", "unknown")
            setup_type = context.get("setup_type", "UNKNOWN")
            if session and setup_type:
                veto, reason = should_auto_veto(
                    symbol, session, setup_type, direction,
                    min_samples=15, veto_threshold=0.40
                )
                if veto:
                    self._log_veto_decision(
                        signal=direction,
                        symbol=symbol,
                        kronos_agree=False,
                        confidence=0.0,
                        decision="VETO_PATTERN",
                        reason=f"Pattern veto: {reason}",
                        circuit_state="CLOSED"
                    )
                    return False, f"Pattern veto: {reason}"
                if "ABOVE 65%" in reason:
                    # Boost: pattern has strong history, reduce API dependency
                    self._log_veto_decision(
                        signal=direction,
                        symbol=symbol,
                        kronos_agree=True,
                        confidence=0.85,
                        decision="ALLOW_HISTORICAL",
                        reason=f"Historical approval: {reason}",
                        circuit_state="CLOSED"
                    )
                    return True, f"Historical approval: {reason}"
        except ImportError:
            pass
        except Exception:
            pass

        circuit_state = self.circuit_breaker.get_state()
        
        # Check circuit breaker state
        if circuit_state == "OPEN":
            # Circuit is open, use fallback
            fallback_confidence = self._fallback_confidence(context)
            
            if fallback_confidence < self.threshold:
                self._log_veto_decision(
                    signal=direction,
                    symbol=symbol,
                    kronos_agree=False,
                    confidence=fallback_confidence,
                    decision="VETO_CIRCUIT_OPEN",
                    reason=f"Kronos offline (circuit OPEN), fallback confidence {fallback_confidence:.0%} < threshold",
                    circuit_state=circuit_state
                )
                return False, "Kronos offline - blocking trade (circuit breaker)"
            else:
                if self.mode == "advisory":
                    decision = "ALLOW_FALLBACK"
                else:
                    decision = "ALLOW_FALLBACK"
                
                self._log_veto_decision(
                    signal=direction,
                    symbol=symbol,
                    kronos_agree=True,
                    confidence=fallback_confidence,
                    decision=decision,
                    reason=f"Kronos offline, using Ingwe confluence ({fallback_confidence:.0%})",
                    circuit_state=circuit_state
                )
                return True, f"Kronos offline - allowing based on Ingwe score ({fallback_confidence:.0%})"
        
        # Normal path: Try to call Kronos
        try:
            ohlcv_data = self._prepare_ohlcv_payload(df)

            payload = {
                "ohlcv": ohlcv_data,
                "context": {
                    "direction": direction,
                    "setup_type": context.get("setup_type", "UNKNOWN"),
                    "sweep": context.get("sweep", "UNKNOWN"),
                    "fvg_type": context.get("fvg_type", "UNKNOWN"),
                    "fvg_position": context.get("fvg_position", "unknown"),
                    "bos_aligned": context.get("bos_aligned", False),
                    "htf_bias_ok": context.get("htf_bias_ok", False),
                    "confluence_score": context.get("confluence_score", 0),
                    "session": context.get("session", "UNKNOWN"),
                    "atr": float(context.get("atr", 0)),
                    "spread_ok": context.get("spread_ok", True),
                    "trend": context.get("trend", "UNKNOWN"),
                    "level_sweep": context.get("level_sweep", False)
                },
                "ict_context": {
                    "sweep": context.get("sweep", ""),
                    "fvg_type": context.get("fvg_type", ""),
                    "trend": context.get("trend", "UNKNOWN"),
                    "session": context.get("session", ""),
                    "bos_aligned": context.get("bos_aligned", False),
                    "htf_bias_ok": context.get("htf_bias_ok", False),
                    "setup_type": context.get("setup_type", ""),
                    "fvg_position": context.get("fvg_position", ""),
                    "confluence_score": context.get("confluence_score", 0)
                }
            }

            api_start = time.time()
            success, result = self.circuit_breaker.call(
                requests.post,
                self.endpoint,
                json=payload,
                timeout=self.request_timeout
            )
            api_latency_ms = (time.time() - api_start) * 1000

            if not success:
                raise Exception(f"Circuit breaker blocked: {result}")

            response = result
            if response.status_code != 200:
                raise Exception(f"API returned {response.status_code}")

            result_data = response.json()
            kronos_agree = result_data.get("agree", True)
            confidence = result_data.get("confidence", 0.5)
            reason = result_data.get("reason", "Unknown")

            # Per-direction threshold: BUY uses buy_threshold if provided
            effective_threshold = self.threshold
            if direction == "BUY":
                buy_threshold = context.get("buy_threshold", None)
                if buy_threshold is not None:
                    effective_threshold = buy_threshold

            if confidence < effective_threshold:
                kronos_agree = False
                reason = f"Low Confidence ({confidence:.0%})"

            if self.mode == "advisory":
                decision = "ALLOW" if kronos_agree else "VETO_ADVISORY"
            else:
                decision = "ALLOW" if kronos_agree else "VETO_BLOCKED"

            self._log_veto_decision(
                signal=direction,
                symbol=symbol,
                kronos_agree=kronos_agree,
                confidence=confidence,
                decision=decision,
                reason=reason,
                circuit_state=circuit_state,
                api_latency_ms=api_latency_ms
            )

            if self.mode == "advisory":
                return True, f"[{decision}] {reason}"
            else:
                return kronos_agree, reason

        except requests.exceptions.Timeout:
            # Timeout -> Circuit breaker fault
            circuit_state = self.circuit_breaker.get_state()
            
            if self.safety_mode == "VETO_SAFE":
                send_notification("KRONOS ALERT", "API Timeout detected. Trading may be blocked (VETO_SAFE).", level="WARN")
                self._log_veto_decision(
                    signal=direction,
                    symbol=symbol,
                    kronos_agree=False,
                    confidence=0.0,
                    decision="VETO_TIMEOUT",
                    reason="Kronos API timeout (VETO_SAFE mode)",
                    circuit_state=circuit_state
                )
                return False, "Kronos timeout - blocking trade (VETO_SAFE)"
            else:
                self._log_veto_decision(
                    signal=direction,
                    symbol=symbol,
                    kronos_agree=True,
                    confidence=0.5,
                    decision="ALLOW_TIMEOUT",
                    reason="Kronos API timeout (ALLOW_SAFE mode)",
                    circuit_state=circuit_state
                )
                return True, "Kronos timeout - allowing trade (ALLOW_SAFE)"

        except requests.exceptions.ConnectionError:
            # Connection error -> Circuit breaker fault
            circuit_state = self.circuit_breaker.get_state()
            
            if self.safety_mode == "VETO_SAFE":
                send_notification("KRONOS ALERT", "API Unreachable. Trading may be blocked (VETO_SAFE).", level="WARN")
                self._log_veto_decision(
                    signal=direction,
                    symbol=symbol,
                    kronos_agree=False,
                    confidence=0.0,
                    decision="VETO_OFFLINE",
                    reason="Kronos API unreachable (VETO_SAFE mode)",
                    circuit_state=circuit_state
                )
                return False, "Kronos offline - blocking trade (VETO_SAFE)"
            else:
                self._log_veto_decision(
                    signal=direction,
                    symbol=symbol,
                    kronos_agree=True,
                    confidence=0.5,
                    decision="ALLOW_OFFLINE",
                    reason="Kronos API unreachable (ALLOW_SAFE mode)",
                    circuit_state=circuit_state
                )
                return True, "Kronos offline - allowing trade (ALLOW_SAFE)"

        except Exception as e:
            # Other error
            circuit_state = self.circuit_breaker.get_state()
            
            if self.safety_mode == "VETO_SAFE":
                self._log_veto_decision(
                    signal=direction,
                    symbol=symbol,
                    kronos_agree=False,
                    confidence=0.0,
                    decision="VETO_ERROR",
                    reason=f"Error: {str(e)} (VETO_SAFE mode)",
                    circuit_state=circuit_state
                )
                return False, f"Validation error - blocking trade (VETO_SAFE): {str(e)}"
            else:
                self._log_veto_decision(
                    signal=direction,
                    symbol=symbol,
                    kronos_agree=True,
                    confidence=0.5,
                    decision="ALLOW_ERROR",
                    reason=f"Error: {str(e)} (ALLOW_SAFE mode)",
                    circuit_state=circuit_state
                )
                return True, f"Validation error - allowing trade (ALLOW_SAFE): {str(e)}"

    def is_enabled(self) -> bool:
        return self.enabled

    def set_mode(self, mode: str):
        """Switch between advisory and enforced mode"""
        if mode not in ("advisory", "enforced"):
            raise ValueError(f"Invalid mode: {mode}. Use 'advisory' or 'enforced'.")
        self.mode = mode
        logger.info(f"Veto Gate mode switched to: {mode}")

    def set_safety_mode(self, safety_mode: str):
        """Switch between VETO_SAFE and ALLOW_SAFE"""
        if safety_mode not in ("VETO_SAFE", "ALLOW_SAFE"):
            raise ValueError(f"Invalid safety mode: {safety_mode}. Use 'VETO_SAFE' or 'ALLOW_SAFE'.")
        self.safety_mode = safety_mode
        logger.info(f"Safety mode switched to: {safety_mode}")

    def enable(self):
        self.enabled = True
        logger.info("Veto Gate enabled")

    def disable(self):
        self.enabled = False
        logger.info("Veto Gate disabled")

    def get_circuit_breaker_state(self) -> str:
        """Return current circuit breaker state"""
        return self.circuit_breaker.get_state()


def create_veto_gate(config: Optional[dict] = None) -> KronosVetoGate:
    """Factory function to create Veto Gate from config"""
    if config is None:
        return KronosVetoGate(safety_mode="VETO_SAFE")

    return KronosVetoGate(
        endpoint=config.get("endpoint", "http://127.0.0.1:8000/v1/predict-ict"),
        threshold=config.get("threshold", 0.75),
        enabled=config.get("enabled", True),
        mode=config.get("mode", "enforced"),
        safety_mode=config.get("safety_mode", "VETO_SAFE"),
        heartbeat_interval=config.get("heartbeat_interval", 0)
    )


if __name__ == "__main__":
    gate = KronosVetoGate(safety_mode="VETO_SAFE")
    print(f"[OK] Veto Gate initialized:")
    print(f"   - Enabled: {gate.enabled}")
    print(f"   - Mode: {gate.mode}")
    print(f"   - Safety Mode: {gate.safety_mode}")
    print(f"   - Circuit Breaker: {gate.get_circuit_breaker_state()}")