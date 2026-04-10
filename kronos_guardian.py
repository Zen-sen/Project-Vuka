"""
Kronos Veto Gate - Ingwe Integration Middleware
Filters Ingwe's BUY/SELL signals through the Kronos Oracle
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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


class KronosVetoGate:
    """
    Veto Gate Middleware for Ingwe
    Intercepts signals and validates against Kronos Oracle
    """

    def __init__(
        self,
        endpoint: str = "http://127.0.0.1:8000/v1/predict",
        threshold: float = 0.75,
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
        
        # Convert each column to list, handling numpy types
        data = {}
        for col in required_cols:
            values = df[col].values
            # Convert to native Python list, handling numpy types
            data[col] = [float(v) for v in values]

        return data

    def validate(
        self,
        signal: str,
        df: pd.DataFrame,
        symbol: str = "EURUSD"
    ) -> tuple[bool, str]:
        """
        Validate Ingwe signal against Kronos Oracle

        Args:
            signal: "BUY" or "SELL" from Ingwe
            df: OHLCV DataFrame (last 512 candles)
            symbol: Trading symbol

        Returns:
            (allowed: bool, reason: str)
        """
        if not self.enabled:
            return True, "Veto Gate Disabled"

        if signal not in ("BUY", "SELL"):
            return True, f"Ignored non-trade signal: {signal}"

        try:
            ohlcv_data = self._prepare_ohlcv_payload(df)

            response = requests.post(
                "http://127.0.0.1:8000/v1/predict-ohlcv",
                json=ohlcv_data,
                timeout=self.request_timeout
            )

            if response.status_code != 200:
                raise Exception(f"API returned {response.status_code}")

            result = response.json()
            kronos_agree = result.get("agree", True)
            confidence = result.get("confidence", 0.5)
            reason = result.get("reason", "Unknown")

            # Check threshold
            if confidence < self.threshold:
                kronos_agree = False
                reason = f"Low Confidence ({confidence:.0%})"

            # Determine decision based on mode
            if self.mode == "advisory":
                decision = "ALLOW" if kronos_agree else "VETO_ADVISORY"
            else:  # enforced
                decision = "ALLOW" if kronos_agree else "VETO_BLOCKED"

            # Log the decision
            self._log_veto_decision(
                signal=signal,
                symbol=symbol,
                kronos_agree=kronos_agree,
                confidence=confidence,
                decision=decision,
                reason=reason
            )

            # Return based on mode
            if self.mode == "advisory":
                return True, f"[{decision}] {reason}"
            else:
                return kronos_agree, reason

        except requests.exceptions.Timeout:
            logger.warning("Kronos API timeout - defaulting to ALLOW")
            self._log_veto_decision(
                signal=signal,
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
                signal=signal,
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
                signal=signal,
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