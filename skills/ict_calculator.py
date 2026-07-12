#!/usr/bin/env python3
"""
ict_calculator.py — ICT factor computation from OHLCV data
Project Vuka | Computes FVG, OB, retracement depth, and HTF bias
for each trade entry point.
"""
import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.parent
SESSIONS_DIR = BASE_DIR / "data" / "sessions"

SYMBOL_MAP = {
    "EURUSD": "EURUSDc",
    "GBPUSD": "GBPUSDc",
}

PIP_VALUES = {
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
}


def _pip_value(symbol: str) -> float:
    return PIP_VALUES.get(symbol, 0.0001)


def _points_to_pips(points: int, symbol: str) -> float:
    return round(points / 10, 1)


def _find_candle_file(symbol: str, timeframe: str, entry_dt: datetime) -> Optional[Path]:
    canon = SYMBOL_MAP.get(symbol, symbol + "c")
    pattern = f"{canon}_{timeframe}_"
    candidates = sorted(SESSIONS_DIR.glob(f"{pattern}*.csv"))
    candidates = [p for p in candidates if "30days" not in p.stem.lower()]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    date_key = entry_dt.strftime("%Y%m%d")
    for p in candidates:
        if date_key in p.stem:
            return p
    for p in candidates:
        parts = p.stem.replace(canon + "_" + timeframe + "_", "").split("_")
        if len(parts) >= 2:
            try:
                start = parts[0]
                end = parts[1].split(".")[0]
                if start <= date_key <= end:
                    return p
            except (ValueError, IndexError):
                continue
    return candidates[-1]


def _load_candles(path: Path, entry_dt: datetime, before: int = 60, after: int = 20, with_spread: bool = False) -> list[dict]:
    if not path or not path.exists():
        return []
    candles = []
    entry_ts = entry_dt.timestamp()
    try:
        with open(path) as f:
            reader = csv.reader(f, delimiter="\t")
            for row in reader:
                if len(row) < 6 or row[0].startswith("<"):
                    continue
                try:
                    ts = datetime.strptime(f"{row[0]} {row[1]}", "%Y.%m.%d %H:%M:%S").timestamp()
                except (ValueError, IndexError):
                    continue
                if ts < entry_ts - before * 60:
                    continue
                if ts > entry_ts + after * 60:
                    break
                c = {
                    "time": ts,
                    "open": float(row[2]),
                    "high": float(row[3]),
                    "low": float(row[4]),
                    "close": float(row[5]),
                }
                if with_spread and len(row) >= 9:
                    c["spread"] = int(row[8])
                candles.append(c)
    except Exception:
        return []
    return candles


def find_fvg(candles: list[dict], direction: str) -> bool:
    for i in range(len(candles) - 2):
        c1, c2, c3 = candles[i], candles[i + 1], candles[i + 2]
        if direction == "BUY":
            if c1["low"] > c3["high"] and c2["close"] > c2["open"]:
                return True
        elif direction == "SELL":
            if c1["high"] < c3["low"] and c2["close"] < c2["open"]:
                return True
    return False


def find_order_block(candles: list[dict], direction: str) -> bool:
    for i in range(len(candles) - 2):
        c1, c2 = candles[i], candles[i + 1]
        body1 = abs(c1["close"] - c1["open"])
        body2 = abs(c2["close"] - c2["open"])
        range2 = c2["high"] - c2["low"]
        if range2 == 0:
            continue
        if direction == "BUY":
            if c1["close"] < c1["open"] and c2["close"] > c2["open"] and body2 / range2 > 0.6:
                return True
        elif direction == "SELL":
            if c1["close"] > c1["open"] and c2["close"] < c2["open"] and body2 / range2 > 0.6:
                return True
    return False


def compute_retracement(candles: list[dict], entry_price: float, direction: str) -> Optional[float]:
    if not candles:
        return None
    if direction == "BUY":
        low_before = min(c["low"] for c in candles[:10])
        if low_before == 0:
            return None
        retrace = abs(entry_price - low_before) / low_before * 100
    else:
        high_before = max(c["high"] for c in candles[:10])
        if high_before == 0:
            return None
        retrace = abs(high_before - entry_price) / high_before * 100
    return round(retrace, 2)


def compute_htf_bias(entry_price: float, htf_candles: list[dict], direction: str) -> str:
    if not htf_candles or len(htf_candles) < 3:
        return "SPLIT"
    recent = htf_candles[-3:]
    htf_trend = "BULLISH" if recent[-1]["close"] > recent[0]["open"] else "BEARISH"
    if direction == "BUY" and htf_trend == "BULLISH":
        return "ALIGNED"
    elif direction == "SELL" and htf_trend == "BEARISH":
        return "ALIGNED"
    else:
        return "SPLIT"


class ICTCalculator:
    def __init__(self, symbol: str, entry_time: str, entry_price: float, direction: str):
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry_price
        try:
            self.entry_dt = datetime.strptime(entry_time, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            self.entry_dt = None

        self.m1_candles: list[dict] = []
        self.m15_candles: list[dict] = []
        self.h1_candles: list[dict] = []
        self._loaded = False

    def load(self, verbose=False):
        if self._loaded or not self.entry_dt:
            return
        m1_path = _find_candle_file(self.symbol, "M1", self.entry_dt)
        m15_path = _find_candle_file(self.symbol, "M15", self.entry_dt)
        h1_path = _find_candle_file(self.symbol, "H1", self.entry_dt)
        if verbose:
            print(f"  M1 path: {m1_path}")
            print(f"  M15 path: {m15_path}")
            print(f"  H1 path: {h1_path}")
        if m1_path:
            self.m1_candles = _load_candles(m1_path, self.entry_dt, before=5, after=5, with_spread=True)
        if m15_path:
            self.m15_candles = _load_candles(m15_path, self.entry_dt, with_spread=True)
        if h1_path:
            self.h1_candles = _load_candles(h1_path, self.entry_dt, before=120, after=10)
        if verbose:
            print(f"  M1 candles loaded: {len(self.m1_candles)}")
            print(f"  M15 candles loaded: {len(self.m15_candles)}")
            print(f"  H1 candles loaded: {len(self.h1_candles)}")
        self._loaded = True

    def _nearest_candle(self) -> Optional[dict]:
        if not self.entry_dt:
            return None
        entry_ts = self.entry_dt.timestamp()
        if self.m1_candles:
            return min(self.m1_candles, key=lambda c: abs(c["time"] - entry_ts))
        if self.m15_candles:
            return min(self.m15_candles, key=lambda c: abs(c["time"] - entry_ts))
        return None

    @property
    def fvg_confirmed(self) -> bool:
        self.load()
        return find_fvg(self.m15_candles, self.direction)

    @property
    def ob_present(self) -> bool:
        self.load()
        return find_order_block(self.m15_candles, self.direction)

    @property
    def retracement_depth(self) -> Optional[float]:
        self.load()
        return compute_retracement(self.m15_candles, self.entry_price, self.direction)

    @property
    def htf_bias(self) -> str:
        self.load()
        return compute_htf_bias(self.entry_price, self.h1_candles, self.direction)

    @property
    def spread_at_entry(self) -> str:
        self.load()
        best = self._nearest_candle()
        if not best:
            return "N/A"
        sp = best.get("spread")
        if sp is None:
            return "N/A"
        return str(_points_to_pips(sp, self.symbol))

    @property
    def slippage(self) -> str:
        self.load()
        if not self.entry_price:
            return "N/A"
        best = self._nearest_candle()
        if not best:
            return "N/A"
        sp = best.get("spread", 0)
        if sp is None:
            return "N/A"
        pip = _pip_value(self.symbol)
        half_spread_price = (sp / 2) * (pip / 10)
        open_price = best["open"]
        if self.direction == "BUY":
            expected_mid = open_price - half_spread_price
            diff = self.entry_price - expected_mid
        else:
            expected_mid = open_price + half_spread_price
            diff = expected_mid - self.entry_price
        slip_pips = round(diff / pip, 1)
        if slip_pips >= 0:
            prefix = "+"
        else:
            prefix = ""
        return f"{prefix}{slip_pips}"


if __name__ == "__main__":
    import json
    test_trades = [
        {"symbol": "EURUSD", "entry_time": "2026-03-10 03:09:29", "entry": 1.16139, "direction": "BUY"},
        {"symbol": "EURUSD", "entry_time": "2026-03-03 06:56:33", "entry": 1.16696, "direction": "SELL"},
        {"symbol": "GBPUSD", "entry_time": "2026-03-20 09:00:35", "entry": 1.34164, "direction": "BUY"},
    ]
    for t in test_trades:
        calc = ICTCalculator(t["symbol"], t["entry_time"], t["entry"], t["direction"])
        calc.load(verbose=True)
        print(f"\n{t['symbol']} {t['direction']} @ {t['entry_time']}:")
        print(f"  FVG: {calc.fvg_confirmed} | OB: {calc.ob_present} | Retrace: {calc.retracement_depth} | HTF: {calc.htf_bias} | Spread: {calc.spread_at_entry} | Slippage: {calc.slippage}")
