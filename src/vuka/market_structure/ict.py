import numpy as np
import pandas as pd

from .indicators import calculate_adx_wilder as _calculate_adx_wilder


def _find_swing_points(df: pd.DataFrame, lookback: int = 20) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Locate confirmed fractal swing highs/lows in the last `lookback` candles.

    A swing high (low) is a candle whose high (low) exceeds (undercuts) both of
    its immediate neighbours -- a structural pivot, not local noise. Returns
    (swing_highs, swing_lows) as (index, price) pairs, oldest to newest.
    """
    if df is None or len(df) < 3:
        return [], []
    recent = df.tail(lookback).reset_index(drop=True)
    n = len(recent)

    swing_highs: list[tuple[int, float]] = []
    swing_lows:  list[tuple[int, float]] = []

    for i in range(1, n - 1):
        h = recent.iloc[i]["high"]
        lo = recent.iloc[i]["low"]
        if h > recent.iloc[i - 1]["high"] and h > recent.iloc[i + 1]["high"]:
            swing_highs.append((i, h))
        if lo < recent.iloc[i - 1]["low"] and lo < recent.iloc[i + 1]["low"]:
            swing_lows.append((i, lo))

    return swing_highs, swing_lows


def detect_liquidity_sweep(df: pd.DataFrame, lookback: int = 20):
    """
    v6.2: Sweeps of true structural swing points (confirmed fractals), not
    5-candle local noise. A 5-minute local high is meaningless without macro
    context -- only breaks of confirmed swing pivots count as stop hunts.

    Reuses the same swing logic as detect_m15_bos.
    """
    if df is None or len(df) < 3:
        return None, None

    swing_highs, swing_lows = _find_swing_points(df, lookback)
    last = df.iloc[-1]

    if swing_highs and last["high"] > swing_highs[-1][1]:
        return "SWEEP_HIGH", swing_highs[-1][1]
    if swing_lows and last["low"] < swing_lows[-1][1]:
        return "SWEEP_LOW", swing_lows[-1][1]
    return None, None


def check_displacement_validity(c1: pd.Series, c2: pd.Series) -> bool:
    c1_range = c1["high"] - c1["low"]
    c2_range = c2["high"] - c2["low"]
    if c1_range == 0 or c2_range == 0:
        return True
    range_ok = c2_range > (c1_range * 1.5)
    body     = abs(c2["close"] - c2["open"])
    body_ok  = (body / c2_range) >= 0.6
    return range_ok and body_ok


def detect_fvg(df: pd.DataFrame, max_age: int = 0) -> list:
    fvgs  = []
    start = max(2, len(df) - max_age - 1) if max_age > 0 else 2
    for i in range(start, len(df) - 1):
        c1, c2, c3 = df.iloc[i-2], df.iloc[i-1], df.iloc[i]
        if not check_displacement_validity(c1, c2):
            continue
        if c1["high"] < c3["low"]:
            gap    = c3["low"] - c1["high"]
            fvg_50 = c1["high"] + gap * 0.5
            fvgs.append(("BULLISH_FVG", c1["high"], c3["low"], i, c2, fvg_50))
        if c3["high"] < c1["low"]:
            gap    = c1["low"] - c3["high"]
            fvg_50 = c1["low"] - gap * 0.5
            fvgs.append(("BEARISH_FVG", c3["high"], c1["low"], i, c2, fvg_50))
    return fvgs[-3:] if fvgs else []


def detect_immediate_fvg(df: pd.DataFrame) -> list:
    """FVG detection restricted to the most recent candles.

    v6.2: DRY -- no duplicate math here; delegate to detect_fvg with a
    max_age window of 3 so the FVG logic lives in exactly one place.
    """
    return detect_fvg(df, max_age=3)


def detect_breaker_blocks(df: pd.DataFrame, lookback: int = 30) -> list:
    """
    v6.2: Vectorized forward-look validation.
    Tracks forward cumulative max of highs / min of lows with NumPy arrays
    instead of slicing the dataframe inside a row-by-row loop.
    """
    if df is None or len(df) < 6:
        return []

    n = len(df)
    start = max(0, n - lookback)

    high   = df["high"].to_numpy(dtype=float)
    low    = df["low"].to_numpy(dtype=float)
    opens  = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    # Forward cumulative extrema: cum_high[i] = max(high[i..end]),
    # cum_low[i] = min(low[i..end])
    cum_high = np.maximum.accumulate(high[::-1])[::-1]
    cum_low  = np.minimum.accumulate(low[::-1])[::-1]

    # Extrema from the NEXT candle onward (i+1..end) -- displacement check
    fwd_high = np.full(n, np.nan)
    fwd_low  = np.full(n, np.nan)
    if n > 1:
        fwd_high[:-1] = cum_high[1:]
        fwd_low[:-1]  = cum_low[1:]

    # Extrema from i+5 onward (post-break validation window)
    post_high = np.full(n, np.nan)
    post_low  = np.full(n, np.nan)
    if n > 5:
        post_high[:-5] = cum_high[5:]
        post_low[:-5]  = cum_low[5:]

    bearish = closes < opens
    bullish = closes > opens

    # Bullish breaker: bearish candle whose high is subsequently broken,
    # and price never re-trades below its low in the post-break window.
    invalidated_bull = np.zeros(n, dtype=bool)
    if n > 5:
        invalidated_bull[: n - 5] = post_low[: n - 5] < low[: n - 5]
    bullish_ok = bearish & (fwd_high > high) & ~invalidated_bull

    # Bearish breaker: bullish candle whose low is subsequently broken,
    # and price never re-trades above its high in the post-break window.
    invalidated_bear = np.zeros(n, dtype=bool)
    if n > 5:
        invalidated_bear[: n - 5] = post_high[: n - 5] > high[: n - 5]
    bearish_ok = bullish & (fwd_low < low) & ~invalidated_bear

    breakers = []
    for i in range(start, n - 5):
        if bullish_ok[i]:
            breakers.append(("BULLISH_BREAKER", float(low[i]), float(high[i]), i))
        elif bearish_ok[i]:
            breakers.append(("BEARISH_BREAKER", float(low[i]), float(high[i]), i))

    return breakers[-5:]


def detect_unicorn_zone(fvgs: list, breakers: list) -> list:
    unicorns = []

    for fvg_type, fvg_low, fvg_high, fvg_idx, ob, fvg_50 in fvgs:
        for bb_type, bb_low, bb_high, bb_idx in breakers:

            temporal_gap = abs(fvg_idx - bb_idx)
            if temporal_gap > 15:
                continue

            if fvg_type == "BULLISH_FVG" and bb_type == "BULLISH_BREAKER":
                overlap_low  = max(fvg_low,  bb_low)
                overlap_high = min(fvg_high, bb_high)
                if overlap_low < overlap_high:
                    unicorn_mid = overlap_low + (overlap_high - overlap_low) * 0.5
                    unicorns.append((
                        "BULLISH_UNICORN",
                        overlap_low, overlap_high, unicorn_mid,
                        bb_low, bb_high
                    ))

            if fvg_type == "BEARISH_FVG" and bb_type == "BEARISH_BREAKER":
                overlap_low  = max(fvg_low,  bb_low)
                overlap_high = min(fvg_high, bb_high)
                if overlap_low < overlap_high:
                    unicorn_mid = overlap_low + (overlap_high - overlap_low) * 0.5
                    unicorns.append((
                        "BEARISH_UNICORN",
                        overlap_low, overlap_high, unicorn_mid,
                        bb_low, bb_high
                    ))

    return unicorns


def detect_m15_bos(df: pd.DataFrame, lookback: int = 20) -> str | None:
    if df is None or len(df) < lookback + 2:
        return None

    swing_highs, swing_lows = _find_swing_points(df, lookback)

    if not swing_highs and not swing_lows:
        return None

    last_close = df.iloc[-1]["close"]

    if swing_highs and last_close > swing_highs[-1][1]:
        return "BULLISH_BOS"
    if swing_lows  and last_close < swing_lows[-1][1]:
        return "BEARISH_BOS"

    return None


def calculate_adx_wilder(df: pd.DataFrame, period: int = 14):
    if df is None or len(df) < period * 2 + 1:
        return None, None, None
    return _calculate_adx_wilder(
        df["high"].values, df["low"].values, df["close"].values, period
    )


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float | None:
    if df is None or len(df) < period * 2 + 1:
        return None

    high  = df["high"].values
    low   = df["low"].values
    close = df["close"].values
    n     = len(df)

    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i]  - close[i - 1])
        )

    atr_val = float(np.mean(tr[1:period + 1]))

    for i in range(period + 1, n):
        atr_val = (atr_val * (period - 1) + tr[i]) / period

    return round(atr_val, 6)

