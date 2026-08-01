import numpy as np
import pandas as pd

from vuka.market_structure.ict import (
    _find_swing_points,
    calculate_adx_wilder,
    calculate_atr,
    check_displacement_validity,
    detect_breaker_blocks,
    detect_fvg,
    detect_immediate_fvg,
    detect_liquidity_sweep,
    detect_m15_bos,
    detect_unicorn_zone,
)


def make_candle(open_, high, low, close):
    return pd.Series({"open": open_, "high": high, "low": low, "close": close})


class TestSwingPoints:
    def test_no_swings_monotonic(self):
        df = pd.DataFrame({"high": [1, 2, 3, 4, 5], "low": [0.9, 1.9, 2.9, 3.9, 4.9]})
        assert _find_swing_points(df) == ([], [])

    def test_swing_high(self):
        df = pd.DataFrame({"high": [1.0, 2.0, 3.0, 2.0, 1.0], "low": [0.5, 1.5, 2.5, 1.5, 0.5]})
        highs, lows = _find_swing_points(df)
        assert highs == [(2, 3.0)]
        assert lows == []

    def test_swing_low(self):
        df = pd.DataFrame({"high": [2.0, 1.5, 1.0, 1.5, 2.0], "low": [1.5, 1.0, 0.5, 1.0, 1.5]})
        highs, lows = _find_swing_points(df)
        assert lows == [(2, 0.5)]
        assert highs == []

    def test_insufficient_data(self):
        df = pd.DataFrame({"high": [1.0, 2.0], "low": [0.9, 1.9]})
        assert _find_swing_points(df) == ([], [])
        assert _find_swing_points(None) == ([], [])


class TestLiquiditySweep:
    def test_sweep_high(self, ohlcv):
        df = ohlcv.copy()
        df.loc[df.index[-1], "high"] = df["high"].max() * 1.02
        result, level = detect_liquidity_sweep(df)
        assert result == "SWEEP_HIGH"

    def test_sweep_low(self, ohlcv):
        df = ohlcv.copy()
        df.loc[df.index[-1], "low"] = df["low"].min() * 0.98
        result, level = detect_liquidity_sweep(df)
        assert result == "SWEEP_LOW"

    def test_no_sweep(self, ohlcv):
        """Copied candle data that doesn't break recent highs/lows."""
        tail = ohlcv.tail(5).copy()
        tail.iloc[-1, tail.columns.get_loc("high")] = tail["high"].iloc[:-1].max()
        tail.iloc[-1, tail.columns.get_loc("low")] = tail["low"].iloc[:-1].min() + 0.0001
        result, level = detect_liquidity_sweep(tail)
        assert result is None

    def test_sweep_breaks_structural_swing_high(self):
        df = pd.DataFrame({
            "high": [1.0, 2.0, 3.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0, 3.5],
            "low": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4],
            "close": [0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 3.0],
        })
        result, level = detect_liquidity_sweep(df)
        assert result == "SWEEP_HIGH"
        assert level == 3.0

    def test_sweep_breaks_structural_swing_low(self):
        df = pd.DataFrame({
            "high": [1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.4],
            "low": [2.0, 1.8, 1.5, 1.2, 1.0, 1.1, 1.2, 1.3, 1.4, 0.4],
            "close": [1.5, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 0.5],
        })
        result, level = detect_liquidity_sweep(df)
        assert result == "SWEEP_LOW"
        assert level == 1.0

    def test_no_sweep_when_swing_not_broken(self):
        df = pd.DataFrame({
            "high": [1.0, 2.0, 3.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0, 2.9],
            "low": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4],
            "close": [0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 2.5],
        })
        result, level = detect_liquidity_sweep(df)
        assert result is None

    def test_lookback_hides_old_swing(self):
        """A swing older than the lookback window is not considered structural."""
        df = pd.DataFrame({
            "high": [1.0, 2.0, 3.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0, 3.5],
            "low": [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4],
            "close": [0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 3.0],
        })
        assert detect_liquidity_sweep(df, lookback=20)[0] == "SWEEP_HIGH"
        assert detect_liquidity_sweep(df, lookback=3)[0] is None

    def test_insufficient_data(self):
        df = pd.DataFrame({"high": [1.0], "low": [0.9], "close": [0.95]})
        assert detect_liquidity_sweep(df) == (None, None)


class TestFVG:
    def test_bullish_fvg(self):
        """detect_fvg checks triple (i-2, i-1, i) starting from i=2.
        With 5 candles [p0, c1, c2, c3, p3]: i=2 checks (0,1,2), i=3 checks (1,2,3).
        We need i=3 so c2 extends c1's range by 1.5x and c3 gaps above c1's high."""
        p0 = make_candle(1.0990, 1.1000, 1.0985, 1.0995)
        c1 = make_candle(1.1000, 1.1010, 1.0990, 1.1005)  # range=0.0020
        c2 = make_candle(1.1015, 1.1080, 1.1005, 1.1070)  # range=0.0075 > 0.0030 ✓, body=0.0055/0.0075=0.73 ✓
        c3 = make_candle(1.1020, 1.1040, 1.1015, 1.1030)  # low=1.1015 > c1.high=1.1010 ✓ gap
        p4 = make_candle(1.1025, 1.1045, 1.1020, 1.1040)
        df = pd.DataFrame([p0, c1, c2, c3, p4])
        fvgs = detect_fvg(df)
        assert len(fvgs) >= 1, f"No FVGs found, got {fvgs}"
        assert fvgs[0][0] == "BULLISH_FVG"

    def test_bearish_fvg(self):
        p0 = make_candle(1.1060, 1.1070, 1.1055, 1.1065)
        c1 = make_candle(1.1040, 1.1060, 1.1030, 1.1050)  # range=0.0030
        c2 = make_candle(1.1050, 1.1075, 1.1000, 1.1005)  # range=0.0075>0.0045 ✓, body=0.0045/0.0075=0.6 ✓
        c3 = make_candle(1.1010, 1.1020, 1.0990, 1.1000)  # high=1.1020 < c1.low=1.1030 ✓
        p4 = make_candle(1.1015, 1.1035, 1.1005, 1.1020)
        df = pd.DataFrame([p0, c1, c2, c3, p4])
        fvgs = detect_fvg(df)
        assert len(fvgs) >= 1, f"No FVGs found, got {fvgs}"
        assert fvgs[0][0] == "BEARISH_FVG"

    def test_no_fvg_tight_range(self):
        candles = [
            make_candle(1.1000, 1.1010, 1.0990, 1.1005),
            make_candle(1.1005, 1.1010, 1.0995, 1.1008),
            make_candle(1.1003, 1.1008, 1.0993, 1.1005),
        ]
        df = pd.DataFrame(candles)
        fvgs = detect_fvg(df)
        assert len(fvgs) == 0

    def test_immediate_fvg_delegates_to_detect_fvg(self):
        """v6.2: detect_immediate_fvg must be exactly detect_fvg(max_age=3)."""
        np.random.seed(7)
        n = 100
        close = 1.1000 + np.cumsum(np.random.randn(n) * 0.0005)
        df = pd.DataFrame({
            "open": close,
            "high": close + 0.001,
            "low": close - 0.001,
            "close": close,
        })
        assert detect_immediate_fvg(df) == detect_fvg(df, max_age=3)


class TestBOS:
    def test_bullish_bos(self, ohlcv):
        df = ohlcv.copy()
        df.loc[df.index[-1], "close"] = df["high"].max() * 1.02
        result = detect_m15_bos(df)
        assert result == "BULLISH_BOS"

    def test_bearish_bos(self, ohlcv):
        df = ohlcv.copy()
        df.loc[df.index[-1], "close"] = df["low"].min() * 0.98
        result = detect_m15_bos(df)
        assert result == "BEARISH_BOS"

    def test_insufficient_data(self):
        df = pd.DataFrame({"high": [1.1], "low": [1.09], "close": [1.095]})
        result = detect_m15_bos(df)
        assert result is None


class TestBreakerBlocks:
    def test_breaker_detected(self, ohlcv):
        breakers = detect_breaker_blocks(ohlcv, lookback=200)
        assert isinstance(breakers, list)

    def test_breaker_types(self, ohlcv):
        breakers = detect_breaker_blocks(ohlcv, lookback=200)
        for b in breakers:
            assert b[0] in ("BULLISH_BREAKER", "BEARISH_BREAKER")


class TestUnicornZone:
    def test_unicorn_detected(self):
        fvgs = [("BULLISH_FVG", 1.1000, 1.1020, 5, None, 1.1010)]
        breakers = [("BULLISH_BREAKER", 1.1005, 1.1015, 6)]
        unicorns = detect_unicorn_zone(fvgs, breakers)
        assert len(unicorns) >= 1
        assert unicorns[0][0] == "BULLISH_UNICORN"

    def test_no_unicorn_no_overlap(self):
        fvgs = [("BULLISH_FVG", 1.1000, 1.1020, 5, None, 1.1010)]
        breakers = [("BEARISH_BREAKER", 1.1030, 1.1040, 6)]
        unicorns = detect_unicorn_zone(fvgs, breakers)
        assert len(unicorns) == 0


class TestATR:
    def test_atr_calculated(self, ohlcv):
        atr = calculate_atr(ohlcv)
        assert atr is not None
        assert atr > 0
        assert isinstance(atr, float)

    def test_atr_insufficient_data(self):
        small = pd.DataFrame({
            "high": [1.1, 1.2],
            "low": [1.09, 1.19],
            "close": [1.095, 1.195],
        })
        atr = calculate_atr(small)
        assert atr is None


class TestADX:
    def test_adx_return_values(self, ohlcv):
        adx, pdi, mdi = calculate_adx_wilder(ohlcv)
        assert adx is not None
        assert pdi is not None
        assert mdi is not None
        assert 0 <= adx <= 100

    def test_adx_insufficient_data(self):
        small = pd.DataFrame({
            "high": [1.1, 1.2],
            "low": [1.09, 1.19],
            "close": [1.095, 1.195],
        })
        result = calculate_adx_wilder(small)
        assert result == (None, None, None)


class TestDisplacement:
    def test_valid_displacement_true(self):
        c1 = make_candle(1.1000, 1.1020, 1.0990, 1.1005)
        c2 = make_candle(1.1015, 1.1060, 1.1010, 1.1050)
        assert bool(check_displacement_validity(c1, c2)) is True

    def test_valid_displacement_false(self):
        c1 = make_candle(1.1000, 1.1010, 1.0990, 1.1005)
        c2 = make_candle(1.1005, 1.1030, 1.0995, 1.1020)
        result = check_displacement_validity(c1, c2)
        assert bool(result) is False
