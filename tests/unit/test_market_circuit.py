"""Market Circuit phase classifier tests.

Regression coverage for the "stuck in CHOP" bug: the circuit was being fed a
hard-coded ``"NONE"`` BOS from bot.py, so a clean H1 trend with ADX 25-30 could
never classify as EXPANSION_* and the governor hard-blocked every scan.

These tests pin the classifier contract:
  * a genuine BOS on a trending market must flip the phase out of CHOP,
  * a strong directional trend without a BOS must NOT be mislabelled CHOP,
  * weak/range-bound conditions still resolve to CHOP/CONSOLIDATION.
"""
import numpy as np
import pandas as pd
import pytest

from skills.market_circuit import MarketCircuit


def _h1_uptrend(n: int = 200, noise: float = 0.0008) -> pd.DataFrame:
    x = np.linspace(0, 1, n)
    closes = 1.10 + 0.08 * x + np.sin(np.linspace(0, 14, n)) * noise
    return pd.DataFrame(
        {"high": closes + 0.0005, "low": closes - 0.0005, "close": closes}
    )


def _h1_downtrend(n: int = 200, noise: float = 0.0008) -> pd.DataFrame:
    x = np.linspace(0, 1, n)
    closes = 1.20 - 0.08 * x + np.sin(np.linspace(0, 14, n)) * noise
    return pd.DataFrame(
        {"high": closes + 0.0005, "low": closes - 0.0005, "close": closes}
    )


def _h1_chop(n: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    x = np.linspace(0, 24, n)
    closes = 1.10 + 0.001 * np.sin(x) + rng.normal(0, 0.0002, n)
    return pd.DataFrame(
        {"high": closes + 0.0005, "low": closes - 0.0005, "close": closes}
    )


class TestPhaseFlipsOutOfChop:
    def test_bullish_trend_with_bos_is_expansion_not_chop(self):
        circuit = MarketCircuit(persist=False)
        df = _h1_uptrend()
        phase = circuit.detect(df, df, df, bos="BULLISH_BOS")
        assert phase == "EXPANSION_BULLISH"

    def test_bearish_trend_with_bos_is_expansion_not_chop(self):
        circuit = MarketCircuit(persist=False)
        df = _h1_downtrend()
        phase = circuit.detect(df, df, df, bos="BEARISH_BOS")
        assert phase == "EXPANSION_BEARISH"

    def test_strong_trend_without_bos_is_not_chop(self):
        circuit = MarketCircuit(persist=False)
        df = _h1_uptrend()
        phase = circuit.detect(df, df, df, bos="NONE")
        assert phase in ("EXPANSION_BULLISH", "BREAKOUT_BULLISH")

    def test_chop_stays_chop(self):
        circuit = MarketCircuit(persist=False)
        df = _h1_chop()
        phase = circuit.detect(df, df, df, bos="NONE")
        assert phase == "CHOP"
