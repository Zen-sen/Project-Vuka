import numpy as np


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
