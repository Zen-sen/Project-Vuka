import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple, List
from enum import Enum

import pandas as pd
import requests

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
VETO_LOG_FILE = LOG_DIR / "kronos_veto.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(VETO_LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


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
                logger.info(f"Circuit breaker: OPEN → HALF_OPEN (recovery test)")
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
            logger.info(f"Circuit breaker: HALF_OPEN → CLOSED (recovered)")
    
    def _on_failure(self):
        """Increment failure count, possibly open circuit"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitBreakerState.HALF_OPEN:
            logger.warning(f"Circuit breaker: HALF_OPEN → OPEN (recovery failed)")
            self.state = CircuitBreakerState.OPEN
        
        elif self.failure_count >= self.failure_threshold:
            old_state = self.state
            self.state = CircuitBreakerState.OPEN
            logger.warning(f"Circuit breaker: {old_state.value} → OPEN (threshold reached: {self.failure_count})")
    
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
        safety_mode: str = "VETO_SAFE"  # NEW: "VETO_SAFE" | "ALLOW_SAFE"
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
        """
        self.endpoint = endpoint
        self.threshold = threshold
        self.enabled = enabled
        self.mode = mode
        self.safety_mode = safety_mode
        self.request_timeout = 2.5
        
        # Circuit breaker
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=30,
            half_open_max_calls=1
        )
    
    def _log_veto_decision(
        self,
        signal: str,
        symbol: str,
        kronos_agree: bool,
        confidence: float,
        decision: str,
        reason: str,
        circuit_state: str = "CLOSED"
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
            "reason": reason
        }
        logger.info(json.dumps(entry))
    
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

            success, result = self.circuit_breaker.call(
                requests.post,
                self.endpoint,
                json=payload,
                timeout=self.request_timeout
            )

            if not success:
                raise Exception(f"Circuit breaker blocked: {result}")

            response = result
            if response.status_code != 200:
                raise Exception(f"API returned {response.status_code}")

            result_data = response.json()
            kronos_agree = result_data.get("agree", True)
            confidence = result_data.get("confidence", 0.5)
            reason = result_data.get("reason", "Unknown")

            if confidence < self.threshold:
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
                circuit_state=circuit_state
            )

            if self.mode == "advisory":
                return True, f"[{decision}] {reason}"
            else:
                return kronos_agree, reason

        except requests.exceptions.Timeout:
            # Timeout -> Circuit breaker fault
            circuit_state = self.circuit_breaker.get_state()
            
            if self.safety_mode == "VETO_SAFE":
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
        safety_mode=config.get("safety_mode", "VETO_SAFE")
    )


if __name__ == "__main__":
    gate = KronosVetoGate(safety_mode="VETO_SAFE")
    print(f"✅ Veto Gate initialized:")
    print(f"   - Enabled: {gate.enabled}")
    print(f"   - Mode: {gate.mode}")
    print(f"   - Safety Mode: {gate.safety_mode}")
    print(f"   - Circuit Breaker: {gate.get_circuit_breaker_state()}")