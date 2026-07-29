"""
market_circuit.py — Market Structure State Machine for Project Vuka
Detects market phases: expansion, consolidation, squeeze, breakout, chop.
Provides a unified circuit state that bots, governor, and Kronos can read.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List

import numpy as np
import pandas as pd

from vuka.market_structure.indicators import (
    calculate_adx_wilder,
    calculate_bollinger_bands,
    calculate_keltner_channels,
    detect_range_ratio,
)
from vuka.utils.unified_logger import get_logger

BASE_DIR = Path(__file__).parent.parent
STATE_PATH = BASE_DIR / "data" / "market_circuit.json"
CONFIG_PATH = BASE_DIR / "config_v4.6.json"
CIRCUIT_LOG = BASE_DIR / "logs" / "market_circuit.log"

logger = get_logger("MarketCircuit")

PHASES = [
    "EXPANSION_BULLISH",
    "EXPANSION_BEARISH",
    "CONSOLIDATION",
    "SQUEEZE",
    "BREAKOUT_BULLISH",
    "BREAKOUT_BEARISH",
    "CHOP",
    "UNKNOWN",
]


class MarketCircuit:
    """
    Market structure state machine.
    Detects and persists the current market phase for all components to consume.
    """

    def __init__(
        self,
        adx_trend_min: float = 25.0,
        squeeze_bb_pct: float = 15.0,
        consolidation_range_ratio: float = 0.35,
        breakout_body_pct: float = 0.60,
        bb_period: int = 20,
        bb_std: float = 2.0,
    ):
        self.adx_trend_min = adx_trend_min
        self.squeeze_bb_pct = squeeze_bb_pct
        self.consolidation_range_ratio = consolidation_range_ratio
        self.breakout_body_pct = breakout_body_pct
        self.bb_period = bb_period
        self.bb_std = bb_std

        self._phase = "UNKNOWN"
        self._confidence = 0
        self._transitions: List[Dict] = []
        self._last_update = None
        self._bb_width = 0.0
        self._kc_width = 0.0
        self._range_ratio = 0.0
        self._adx = 0.0
        self._bos = "NONE"
        self._trend = "NONE"
        self._load()

    # ── Public API ────────────────────────────────────────────

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def confidence(self) -> int:
        return self._confidence

    @property
    def summary(self) -> dict:
        return {
            "phase": self._phase,
            "confidence": self._confidence,
            "adx": round(self._adx, 1),
            "trend": self._trend,
            "bos": self._bos,
            "bb_width": round(self._bb_width, 6),
            "kc_width": round(self._kc_width, 6),
            "range_ratio": round(self._range_ratio, 3),
            "transitions": self._transitions[-5:] if self._transitions else [],
            "last_update": self._last_update,
        }

    def detect(
        self,
        df_m1: pd.DataFrame,
        df_m15: pd.DataFrame,
        df_h1: pd.DataFrame,
        bos: str = "NONE",
    ) -> str:
        """
        Run market circuit detection.
        Returns the detected phase string.
        """
        prev_phase = self._phase

        # Extract numpy arrays from DataFrames
        h1_high = df_h1["high"].values if hasattr(df_h1, "high") else None
        h1_low = df_h1["low"].values if hasattr(df_h1, "low") else None
        h1_close = df_h1["close"].values if hasattr(df_h1, "close") else None

        m15_close = df_m15["close"].values if hasattr(df_m15, "close") else None
        m15_high = df_m15["high"].values if hasattr(df_m15, "high") else None
        m15_low = df_m15["low"].values if hasattr(df_m15, "low") else None

        m1_close = df_m1["close"].values if hasattr(df_m1, "close") else None
        m1_high = df_m1["high"].values if hasattr(df_m1, "high") else None
        m1_low = df_m1["low"].values if hasattr(df_m1, "low") else None

        if h1_close is None or len(h1_close) < 30:
            self._set_phase("UNKNOWN", 0)
            return self._phase

        # 1. ADX on H1
        adx, plus_di, minus_di = None, None, None
        if h1_high is not None and h1_low is not None:
            adx, plus_di, minus_di = calculate_adx_wilder(h1_high, h1_low, h1_close, 14)
        self._adx = adx or 0

        # 2. Trend direction from H1 EMAs
        trend = self._detect_trend(h1_close)
        self._trend = trend

        # 3. Bollinger Bands on H1 close
        bb_upper, bb_mid, bb_lower, bb_width = calculate_bollinger_bands(
            h1_close, self.bb_period, self.bb_std
        )
        self._bb_width = bb_width or 0

        # 4. Keltner Channels on H1
        kc_upper, kc_mid, kc_lower, kc_width = None, None, None, None
        if h1_high is not None and h1_low is not None:
            kc_upper, kc_mid, kc_lower, kc_width = calculate_keltner_channels(
                h1_high, h1_low, h1_close, 20, 14, 1.5
            )
        self._kc_width = kc_width or 0

        # 5. Range ratio (M15 vs H1)
        range_ratio = None
        if m15_high is not None and m15_low is not None and h1_high is not None and h1_low is not None:
            range_ratio = detect_range_ratio(m15_high, m15_low, 15, 60)
        self._range_ratio = range_ratio or 0

        # 6. BB width percentile (how tight relative to recent history)
        bb_squeeze = self._check_bb_squeeze(h1_close)

        self._bos = bos

        # ── Phase Decision Logic ──────────────────────────
        phase = self._classify_phase(
            adx=adx or 0,
            trend=trend,
            bos=bos,
            bb_width=bb_width or 0,
            bb_squeeze=bb_squeeze,
            range_ratio=range_ratio or 0,
            m1_close=m1_close,
            m15_close=m15_close,
            h1_close=h1_close,
        )

        self._set_phase(phase, self._compute_confidence(phase, adx or 0, range_ratio or 0, bb_width or 0))

        # Log transition
        if phase != prev_phase and prev_phase != "UNKNOWN":
            entry = {
                "from": prev_phase,
                "to": phase,
                "time": datetime.now(timezone.utc).isoformat(),
                "adx": round(adx or 0, 1),
                "trend": trend,
                "bos": bos,
                "bb_width": round(bb_width or 0, 6),
            }
            self._transitions.append(entry)
            logger.log(
                "GUARD",
                f"[CIRCUIT] Phase transition: {prev_phase} -> {phase} "
                f"(ADX={adx} Trend={trend} BOS={bos} BB={bb_width or 0:.6f})",
            )

        self._save()
        return phase

    # ── Internal Detection ────────────────────────────────────

    def _detect_trend(self, close: np.ndarray) -> str:
        if len(close) < 60:
            return "NONE"
        ema10 = np.mean(close[-10:])
        ema30 = np.mean(close[-30:])
        ema50 = np.mean(close[-50:])

        bullish = ema10 > ema30 > ema50
        bearish = ema10 < ema30 < ema50

        if bullish:
            return "BULLISH"
        elif bearish:
            return "BEARISH"
        return "NONE"

    def _check_bb_squeeze(self, close: np.ndarray) -> bool:
        if len(close) < self.bb_period * 2:
            return False
        current_width = self._bb_width
        widths = []
        for i in range(len(close) - self.bb_period, len(close)):
            segment = close[:i+1] if len(close[:i+1]) >= self.bb_period else None
            if segment is not None:
                _, _, _, w = calculate_bollinger_bands(segment, self.bb_period, self.bb_std)
                if w:
                    widths.append(w)
        if not widths:
            return False
        pct_of_max = (current_width / max(widths)) * 100 if max(widths) > 0 else 100
        return pct_of_max <= self.squeeze_bb_pct

    def _detect_breakout(
        self, close: np.ndarray, bb_upper: Optional[float], bb_lower: Optional[float]
    ) -> Optional[str]:
        if close is None or len(close) < 3 or bb_upper is None or bb_lower is None:
            return None
        last = close[-1]
        prev = close[-2]
        body = abs(last - prev)
        candle_range = max(close[-3:]) - min(close[-3:]) if len(close) >= 3 else body
        if candle_range == 0:
            return None
        body_dominance = body / candle_range

        if last > bb_upper and body_dominance >= self.breakout_body_pct:
            return "BREAKOUT_BULLISH"
        if last < bb_lower and body_dominance >= self.breakout_body_pct:
            return "BREAKOUT_BEARISH"
        return None

    def _classify_phase(
        self,
        adx: float,
        trend: str,
        bos: str,
        bb_width: float,
        bb_squeeze: bool,
        range_ratio: float,
        m1_close: Optional[np.ndarray],
        m15_close: Optional[np.ndarray],
        h1_close: np.ndarray,
    ) -> str:
        bb_upper, bb_mid, bb_lower, _ = calculate_bollinger_bands(
            h1_close, self.bb_period, self.bb_std
        )

        breakout = self._detect_breakout(h1_close, bb_upper, bb_lower)
        if breakout:
            return breakout

        if bb_squeeze and adx < self.adx_trend_min:
            return "SQUEEZE"

        if range_ratio < self.consolidation_range_ratio and adx < self.adx_trend_min:
            return "CONSOLIDATION"

        if trend == "BULLISH" and adx >= self.adx_trend_min:
            if bos == "BULLISH_BOS":
                return "EXPANSION_BULLISH"
            if adx >= 30:
                return "EXPANSION_BULLISH"
            return "CHOP"

        if trend == "BEARISH" and adx >= self.adx_trend_min:
            if bos == "BEARISH_BOS":
                return "EXPANSION_BEARISH"
            if adx >= 30:
                return "EXPANSION_BEARISH"
            return "CHOP"

        if adx < 20:
            if range_ratio < self.consolidation_range_ratio:
                return "CONSOLIDATION"
            return "CHOP"

        return "CHOP"

    def _compute_confidence(self, phase: str, adx: float, range_ratio: float, bb_width: float) -> int:
        if phase == "UNKNOWN":
            return 0
        base = 50
        if phase in ("EXPANSION_BULLISH", "EXPANSION_BEARISH"):
            base = 60 + min(int(adx * 1.5), 30)
        elif phase in ("BREAKOUT_BULLISH", "BREAKOUT_BEARISH"):
            base = 75 + min(int(adx * 1.0), 20)
        elif phase == "SQUEEZE":
            squeeze_tightness = max(0, min(100, int((1 - bb_width * 1000) * 30)))
            base = 50 + squeeze_tightness
        elif phase == "CONSOLIDATION":
            tightness = max(0, min(100, int((1 - range_ratio) * 50)))
            base = 40 + tightness
        elif phase == "CHOP":
            base = 25
        return min(base, 100)

    def _set_phase(self, phase: str, confidence: int):
        self._phase = phase
        self._confidence = confidence
        self._last_update = datetime.now(timezone.utc).isoformat()

    # ── Persistence ───────────────────────────────────────────

    def _load(self):
        try:
            if STATE_PATH.exists():
                with open(STATE_PATH) as f:
                    data = json.load(f)
                self._phase = data.get("phase", "UNKNOWN")
                self._confidence = data.get("confidence", 0)
                self._transitions = data.get("transitions", [])
                self._last_update = data.get("last_update")
        except Exception:
            pass

    def _save(self):
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "phase": self._phase,
            "confidence": self._confidence,
            "adx": round(self._adx, 1),
            "trend": self._trend,
            "bos": self._bos,
            "bb_width": round(self._bb_width, 6),
            "range_ratio": round(self._range_ratio, 3),
            "transitions": self._transitions[-20:] if self._transitions else [],
            "last_update": self._last_update,
        }
        with open(STATE_PATH, "w") as f:
            json.dump(payload, f, indent=2)


# ── Global Singleton ────────────────────────────────────────

_CIRCUIT: Optional[MarketCircuit] = None


def get_circuit() -> MarketCircuit:
    global _CIRCUIT
    if _CIRCUIT is None:
        cfg = _load_mc_config()
        _CIRCUIT = MarketCircuit(
            adx_trend_min=cfg.get("adx_trend_min", 25.0),
            squeeze_bb_pct=cfg.get("squeeze_bb_pct", 15.0),
            consolidation_range_ratio=cfg.get("consolidation_range_ratio", 0.35),
            bb_period=cfg.get("bb_period", 20),
            bb_std=cfg.get("bb_std", 2.0),
        )
    return _CIRCUIT


def _load_mc_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH) as f:
                root = json.load(f)
            return root.get("market_circuit", {})
    except Exception:
        pass
    return {}


def detect_market_phase(
    df_m1: pd.DataFrame,
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame,
    bos: str = "NONE",
) -> str:
    circuit = get_circuit()
    return circuit.detect(df_m1, df_m15, df_h1, bos)


def get_market_summary() -> dict:
    circuit = get_circuit()
    return circuit.summary


if __name__ == "__main__":
    c = MarketCircuit()
    print("Market Circuit v1.0")
    print(f"Current phase: {c.phase}")
    print(f"Summary: {json.dumps(c.summary, indent=2)}")
