import numpy as np
from typing import Optional


def calculate_adx_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14):
    if len(high) < period * 2 + 1:
        return None, None, None

    n = len(high)
    tr_arr = np.zeros(n)
    plus_dm_arr = np.zeros(n)
    minus_dm_arr = np.zeros(n)

    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        tr_arr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )
        plus_dm_arr[i] = up if (up > down and up > 0) else 0.0
        minus_dm_arr[i] = down if (down > up and down > 0) else 0.0

    atr_s = float(np.sum(tr_arr[1:period + 1]))
    pdm_s = float(np.sum(plus_dm_arr[1:period + 1]))
    mdm_s = float(np.sum(minus_dm_arr[1:period + 1]))

    if atr_s == 0:
        return 0.0, 0.0, 0.0

    dx_arr = np.zeros(n)
    pdi = 100.0 * pdm_s / atr_s
    mdi = 100.0 * mdm_s / atr_s
    di_sum = pdi + mdi
    dx_arr[period] = 100.0 * abs(pdi - mdi) / di_sum if di_sum else 0.0

    for i in range(period + 1, n):
        atr_s += -atr_s / period + tr_arr[i]
        pdm_s += -pdm_s / period + plus_dm_arr[i]
        mdm_s += -mdm_s / period + minus_dm_arr[i]
        if atr_s == 0:
            dx_arr[i] = 0.0
            continue
        pdi = 100.0 * pdm_s / atr_s
        mdi = 100.0 * mdm_s / atr_s
        di_sum = pdi + mdi
        dx_arr[i] = 100.0 * abs(pdi - mdi) / di_sum if di_sum else 0.0

    adx_seed_end = period * 2 - 1
    adx = float(np.mean(dx_arr[period:adx_seed_end + 1]))

    for i in range(adx_seed_end + 1, n):
        adx = (adx * (period - 1) + dx_arr[i]) / period

    return round(adx, 1), round(pdi, 1), round(mdi, 1)


def calculate_bollinger_bands(close: np.ndarray, period: int = 20, std_mult: float = 2.0):
    if len(close) < period:
        return None, None, None
    sma = np.mean(close[-period:])
    std = np.std(close[-period:])
    upper = sma + std_mult * std
    lower = sma - std_mult * std
    width = (upper - lower) / sma if sma > 0 else 0
    return upper, sma, lower, width


def calculate_keltner_channels(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                                ema_period: int = 20, atr_period: int = 14, atr_mult: float = 1.5):
    if len(close) < ema_period or len(high) < atr_period:
        return None, None, None, None
    ema = np.mean(close[-ema_period:])
    tr_arr = np.zeros(len(high))
    for i in range(1, len(high)):
        tr_arr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )
    atr = float(np.mean(tr_arr[-atr_period:]))
    upper = ema + atr_mult * atr
    lower = ema - atr_mult * atr
    width = (upper - lower) / ema if ema > 0 else 0
    return upper, ema, lower, width


def detect_range_ratio(high: np.ndarray, low: np.ndarray, short_period: int = 15, long_period: int = 60) -> Optional[float]:
    if len(high) < long_period:
        return None
    short_range = float(np.max(high[-short_period:]) - np.min(low[-short_period:]))
    long_range = float(np.max(high[-long_period:]) - np.min(low[-long_period:]))
    if long_range == 0:
        return None
    return short_range / long_range
