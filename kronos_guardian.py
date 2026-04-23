"""
Kronos Veto Gate - Ingwe Integration Middleware
Filters Ingwe's BUY/SELL signals through the Kronos Oracle
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

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

CONCEPT_TRACKER = None
try:
    from skills.concept_tracker import ConceptTracker
    CONCEPT_TRACKER = ConceptTracker()
except ImportError:
    pass


class KronosVetoGate:
    """
    Veto Gate Middleware for Ingwe
    Intercepts signals and validates against Kronos Oracle
    """

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:8000/v1/predict",
        threshold: float = 0.50,
        enabled: bool = True,
        mode: str = "advisory"
    ):
        """
        Args:
            endpoint: Kronos API URL
            threshold: Minimum confidence to allow trade
            enabled: Enable/disable the veto gate
            mode: "advisory" (log only) or "enforced" (block on veto)
        """
        self.endpoint = endpoint
        self.threshold = threshold
        self.enabled = enabled
        self.mode = mode
        self.request_timeout = 2.5

    def _log_veto_decision(
        self,
        signal: str,
        symbol: str,
        kronos_agree: bool,
        confidence: float,
        decision: str,
        reason: str
    ):
        """Log veto decision in JSON format"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "signal": signal,
            "symbol": symbol,
            "kronos_agree": kronos_agree,
            "confidence": confidence,
            "threshold": self.threshold,
            "decision": decision,
            "mode": self.mode,
            "reason": reason
        }
        logger.info(json.dumps(entry))

    def _prepare_ohlcv_payload(self, df: pd.DataFrame) -> dict:
        """Prepare OHLCV data for Kronos API"""
        df = df.tail(512).copy()

        required_cols = ['open', 'high', 'low', 'close', 'volume']
        
        for col in required_cols:
            if col not in df.columns:
                df[col] = 1000.0
        
        data = {}
        for idx, row in df.iterrows():
            row_dict = {col: float(row[col]) for col in required_cols}
            data[str(idx)] = row_dict

        return data

    def _extract_concepts_from_context(self, context: dict) -> List[str]:
        """Extract ICT concepts from trade context"""
        concepts = []
        
        sweep = context.get("sweep", "")
        fvg_type = context.get("fvg_type", "")
        fvg_position = context.get("fvg_position", "")
        setup_type = context.get("setup_type", "")
        bos_aligned = context.get("bos_aligned", False)
        htf_bias_ok = context.get("htf_bias_ok", False)
        level_sweep = context.get("level_sweep", False)
        
        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG":
            concepts.extend(["liquidity_sweep", "fvg_after_sweep", "bullish_break"])
        elif sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG":
            concepts.extend(["liquidity_sweep", "fvg_after_sweep", "bearish_break"])
        
        if fvg_position == "50%":
            concepts.append("fvg_50Midpoint")
        elif fvg_position == "full":
            concepts.append("fvg_full")
        
        if bos_aligned:
            concepts.append("bos_alignment")
        
        if htf_bias_ok:
            concepts.append("htf_bias")
        
        if level_sweep:
            concepts.append("level_sweep")
        
        if setup_type == "CONTINUATION":
            concepts.append("continuation")
        elif setup_type == "SB_FVG":
            concepts.append("silver_bullet")
        elif setup_type == "UNICON_REVERSAL":
            concepts.append("unicorn_reversal")
        
        return list(set(concepts)) if concepts else ["unknown_setup"]
    
    def record_concepts_used(
        self,
        trade_id: str,
        context: dict,
        kronos_decision: str
    ):
        """Record concepts used for a trade for later attribution"""
        if not CONCEPT_TRACKER:
            return
        
        direction = context.get("direction", "UNKNOWN")
        setup_type = context.get("setup_type", "UNKNOWN")
        concepts = self._extract_concepts_from_context(context)
        
        CONCEPT_TRACKER.record_trade(
            trade_id=trade_id,
            direction=direction,
            concepts_used=concepts,
            kronos_decision=kronos_decision,
            setup_type=setup_type
        )
    
    def get_performance_context(self, concepts: List[str]) -> str:
        """Get performance-aware context for concepts used in a trade"""
        if not CONCEPT_TRACKER:
            return ""
        
        return CONCEPT_TRACKER.get_weighted_context({"concepts_used": concepts})

    def validate(
        self,
        context: dict,
        df: pd.DataFrame,
        symbol: str = "EURUSD"
    ) -> tuple[bool, str]:
        """
        Validate Ingwe signal against Kronos Oracle

        Args:
            context: Structured setup context with keys:
                - direction: "BUY" or "SELL"
                - setup_type: e.g., "SB_FVG", "UNICON_REVERSAL", "CONTINUATION"
                - sweep: "SWEEP_HIGH" or "SWEEP_LOW"
                - fvg_type: "BULLISH_FVG" or "BEARISH_FVG"
                - fvg_position: "50%", "full", "below_50", "above_50"
                - bos_aligned: bool
                - htf_bias_ok: bool
                - confluence_score: int
                - session: str
                - atr: float
                - spread_ok: bool
                - trend: "BULLISH" or "BEARISH"
                - level_sweep: bool
            df: OHLCV DataFrame (last 512 candles)
            symbol: Trading symbol

        Returns:
            (allowed: bool, reason: str)
        """
        if not self.enabled:
            return True, "Veto Gate Disabled"

        direction = context.get("direction", "BUY")
        if direction not in ("BUY", "SELL"):
            return True, f"Ignored non-trade signal: {direction}"

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
                }
            }

            response = requests.post(
                "http://127.0.0.1:8000/v1/predict-structured",
                json=payload,
                timeout=self.request_timeout
            )

            if response.status_code != 200:
                raise Exception(f"API returned {response.status_code}")

            result = response.json()
            kronos_agree = result.get("agree", True)
            confidence = result.get("confidence", 0.5)
            reason = result.get("reason", "Unknown")

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
                reason=reason
            )

            if self.mode == "advisory":
                return True, f"[{decision}] {reason}"
            else:
                return kronos_agree, reason

        except requests.exceptions.Timeout:
            logger.warning("Kronos API timeout - defaulting to ALLOW")
            self._log_veto_decision(
                signal=direction,
                symbol=symbol,
                kronos_agree=True,
                confidence=0.5,
                decision="ALLOW_TIMEOUT",
                reason="API Timeout - Default Allow"
            )
            return True, "API Timeout - Default Allow"

        except requests.exceptions.ConnectionError:
            logger.error("Kronos API unreachable - defaulting to ALLOW")
            self._log_veto_decision(
                signal=direction,
                symbol=symbol,
                kronos_agree=True,
                confidence=0.5,
                decision="ALLOW_OFFLINE",
                reason="API Offline - Default Allow"
            )
            return True, "API Offline - Default Allow"

        except Exception as e:
            logger.error(f"Kronos validation error: {str(e)} - defaulting to ALLOW")
            self._log_veto_decision(
                signal=direction,
                symbol=symbol,
                kronos_agree=True,
                confidence=0.5,
                decision="ALLOW_ERROR",
                reason=f"Error: {str(e)}"
            )
            return True, f"API Error - Default Allow: {str(e)}"

    def is_enabled(self) -> bool:
        return self.enabled

    def set_mode(self, mode: str):
        """Switch between advisory and enforced mode"""
        if mode not in ("advisory", "enforced"):
            raise ValueError(f"Invalid mode: {mode}. Use 'advisory' or 'enforced'.")
        self.mode = mode
        logger.info(f"Veto Gate mode switched to: {mode}")

    def enable(self):
        self.enabled = True
        logger.info("Veto Gate enabled")

    def disable(self):
        self.enabled = False
        logger.info("Veto Gate disabled")


def create_veto_gate(config: Optional[dict] = None) -> KronosVetoGate:
    """Factory function to create Veto Gate from config"""
    if config is None:
        return KronosVetoGate()

    return KronosVetoGate(
        endpoint=config.get("endpoint", "http://127.0.0.1:8000/v1/predict"),
        threshold=config.get("threshold", 0.75),
        enabled=config.get("enabled", True),
        mode=config.get("mode", "advisory")
    )


if __name__ == "__main__":
    gate = KronosVetoGate()
    print(f"Veto Gate initialized: enabled={gate.enabled}, mode={gate.mode}")
    print(f"Endpoint: {gate.endpoint}")
    print(f"Threshold: {gate.threshold}")