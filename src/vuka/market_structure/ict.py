import pandas as pd
import numpy as np
from typing import Optional, Any, List, Tuple

from .indicators import calculate_adx_wilder as _calculate_adx_wilder
from vuka.core.state import s

def detect_liquidity_sweep(df: pd.DataFrame):
    recent    = df.tail(5)
    prev_high = recent["high"].iloc[:-1].max()
    prev_low  = recent["low"].iloc[:-1].min()
    last      = recent.iloc[-1]
    if last["high"] > prev_high:   return "SWEEP_HIGH", prev_high
    if last["low"]  < prev_low:    return "SWEEP_LOW",  prev_low
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
    fvgs = []
    for i in range(max(2, len(df) - 3), len(df) - 1):
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


def detect_breaker_blocks(df: pd.DataFrame, lookback: int = 30) -> list:
    breakers = []
    start    = max(0, len(df) - lookback)

    for i in range(start, len(df) - 5):
        candle     = df.iloc[i]
        subsequent = df.iloc[i + 1:]
        post_break = df.iloc[i + 5:]

        if candle["close"] < candle["open"]:
            if subsequent["high"].max() > candle["high"]:
                if not post_break.empty and (post_break["low"] < candle["low"]).any():
                    continue
                breakers.append(("BULLISH_BREAKER", candle["low"], candle["high"], i))

        if candle["close"] > candle["open"]:
            if subsequent["low"].min() < candle["low"]:
                if not post_break.empty and (post_break["high"] > candle["high"]).any():
                    continue
                breakers.append(("BEARISH_BREAKER", candle["low"], candle["high"], i))

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

    recent = df.tail(lookback).reset_index(drop=True)
    n      = len(recent)

    swing_highs: list[tuple[int, float]] = []
    swing_lows:  list[tuple[int, float]] = []

    for i in range(1, n - 1):
        h = recent.iloc[i]["high"]
        l = recent.iloc[i]["low"]
        if (h > recent.iloc[i - 1]["high"] and
                h > recent.iloc[i + 1]["high"]):
            swing_highs.append((i, h))
        if (l < recent.iloc[i - 1]["low"] and
                l < recent.iloc[i + 1]["low"]):
            swing_lows.append((i, l))

    if not swing_highs and not swing_lows:
        return None

    last_close = recent.iloc[-1]["close"]

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

