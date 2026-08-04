"""analyze_london_open.py -- Post-mortem of the loose London Open replay.

For each trade in a replay results JSON, walks the real M15 series after entry
and computes:
  * whether TP was ever touched (and when relative to SL)
  * max favourable / adverse excursion in R
  * 1h / 2h / 4h follow-through after entry
  * H1 trend (EMA10 vs EMA30) at entry, and the day's bias

Usage:
    python analyze_london_open.py --trades src/vuka/backtest/data/lo_loose_clamped.json
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

SAST_OFFSET = 2
ATR_PERIOD = 14
CONFIG = {
    "ATR_MULTIPLIER": 2.0,
    "MIN_SL_ATR": 1.0,
    "RRR": 2.5,
    "ASIAN_START_SAST": 0,
    "ASIAN_END_SAST": 9,
}


def atr_series(df, period=ATR_PERIOD):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def asian_range_at(df, t):
    day = t.normalize() + pd.Timedelta(hours=SAST_OFFSET)  # midnight SAST
    start = day - pd.Timedelta(hours=SAST_OFFSET)
    end = start + pd.Timedelta(hours=CONFIG["ASIAN_END_SAST"])
    win = df[(df["time"] >= start) & (df["time"] < end)]
    if len(win) == 0:
        return None, None
    return win["high"].max(), win["low"].min()


def pull(symbol: str, days: int = 35):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    out = {}
    for name, tf in (("m15", mt5.TIMEFRAME_M15), ("h1", mt5.TIMEFRAME_H1)):
        rates = mt5.copy_rates_range(symbol, tf, start, now)
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        out[name] = df.reset_index(drop=True)
    return out


def h1_trend_at(h1, t):
    win = h1[h1["time"] <= t].tail(60)
    if len(win) < 30:
        return "NONE"
    e10 = win["close"].tail(10).mean()
    e30 = win["close"].tail(30).mean()
    return "BULLISH" if e10 > e30 else "BEARISH"


def analyze_symbol(symbol, trades, data):
    m15 = data["m15"]
    h1 = data["h1"]
    rows = []
    for tr in trades:
        t = pd.Timestamp(tr["entry_time"])
        idxs = m15.index[m15["time"] == t]
        if len(idxs) == 0:
            continue
        i = idxs[0]
        entry = tr["entry"]
        sl = tr["sl"]
        tp = tr["tp"]
        direction = tr["direction"]
        risk = abs(entry - sl)
        trend = h1_trend_at(h1, t)

        tp_reached = False
        tp_bar = None
        sl_reached = False
        sl_bar = None
        mfe_r = 0.0
        mafe_r = 0.0
        closes = []
        horizon = min(i + 96, len(m15))
        for j in range(i, horizon):
            row = m15.iloc[j]
            if direction == "BUY":
                fav = (row["high"] - entry) / risk if risk else 0.0
                adv = (entry - row["low"]) / risk if risk else 0.0
                if row["high"] >= tp and tp_reached is False:
                    tp_reached, tp_bar = True, j
                if row["low"] <= sl and sl_reached is False:
                    sl_reached, sl_bar = True, j
            else:
                fav = (entry - row["low"]) / risk if risk else 0.0
                adv = (row["high"] - entry) / risk if risk else 0.0
                if row["low"] <= tp and tp_reached is False:
                    tp_reached, tp_bar = True, j
                if row["high"] >= sl and sl_reached is False:
                    sl_reached, sl_bar = True, j
            mfe_r = max(mfe_r, fav)
            mafe_r = max(mafe_r, adv)
            if len(closes) < 16:
                closes.append((row["close"] - entry) / risk if risk else 0.0)

        first_hit = "NONE"
        if tp_reached and sl_reached:
            first_hit = "TP" if tp_bar < sl_bar else "SL"
        elif tp_reached:
            first_hit = "TP"
        elif sl_reached:
            first_hit = "SL"

        rows.append({
            "symbol": symbol, "entry_time": tr["entry_time"], "direction": direction,
            "entry": entry, "sl": sl, "tp": tp, "risk": risk,
            "outcome": tr["outcome"], "pnl_usd": tr["pnl_usd"],
            "trend_at_entry": trend,
            "tp_ever_reached": tp_reached, "sl_ever_reached": sl_reached,
            "first_hit": first_hit,
            "mfe_r": round(mfe_r, 2), "mafe_r": round(mafe_r, 2),
            "fc_1h": round(closes[4], 2) if len(closes) > 4 else None,
            "fc_2h": round(closes[8], 2) if len(closes) > 8 else None,
            "fc_4h": round(closes[15], 2) if len(closes) > 15 else None,
        })
    return rows


def simulate_sl_widths(symbol, trades, data):
    """What if the SL sat *outside* the Asian range (w * ATR beyond the edge)?

    Keeps each trade's recorded TP (fixed price) and entry, rebuilds the SL as
    asian edge +/- w*ATR, then walks forward for first touch of TP vs new SL.
    """
    m15 = data["m15"]
    atr = atr_series(m15)
    print("\n  --- SL outside Asian range (same entry/TP): first-touch ---")
    print(f"  {'width':>6} {'tp_first':>9} {'sl_first':>9} {'open':>6}   net(R)")

    valid = []
    for tr in trades:
        t = pd.Timestamp(tr["entry_time"])
        idxs = m15.index[m15["time"] == t]
        if len(idxs) == 0:
            continue
        i = idxs[0]
        entry = tr["entry"]
        tp = tr["tp"]
        direction = tr["direction"]
        a = atr.iloc[i]
        if not np.isfinite(a) or a <= 0:
            continue
        ah, al = asian_range_at(m15, t)
        if ah is None:
            continue
        valid.append((i, entry, tp, direction, a, ah, al))

    if not valid:
        print("   no valid trades")
        return

    for w in (0.5, 1.0, 1.5, 2.0):
        wins = losses = opens = 0
        win_rrs = []
        for i, entry, tp, direction, a, ah, al in valid:
            if direction == "BUY":
                sl = al - w * a
                if sl >= entry:
                    sl = entry - CONFIG["MIN_SL_ATR"] * a
            else:
                sl = ah + w * a
                if sl <= entry:
                    sl = entry + CONFIG["MIN_SL_ATR"] * a
            risk = abs(entry - sl)
            if risk <= 0:
                continue
            outcome = None
            for j in range(i, min(i + 96, len(m15))):
                row = m15.iloc[j]
                if direction == "BUY":
                    if row["high"] >= tp:
                        outcome, wins, rr = "WIN", wins + 1, (tp - entry) / risk
                        break
                    if row["low"] <= sl:
                        outcome, losses, rr = "LOSS", losses + 1, -1.0
                        break
                else:
                    if row["low"] <= tp:
                        outcome, wins, rr = "WIN", wins + 1, (entry - tp) / risk
                        break
                    if row["high"] >= sl:
                        outcome, losses, rr = "LOSS", losses + 1, -1.0
                        break
            if outcome == "WIN":
                win_rrs.append(rr)
            elif outcome is None:
                opens += 1
        net = sum(win_rrs) - losses
        wr = 100 * wins / (wins + losses) if wins + losses else 0
        avg_win_r = sum(win_rrs) / len(win_rrs) if win_rrs else 0
        print(f"  {w:>6.1f} {wins:>9} {losses:>9} {opens:>6}   {net:>8.2f}R   "
              f"(win% {wr:.0f}, avg_winR {avg_win_r:.2f})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", required=True, type=Path)
    args = parser.parse_args()

    payload = json.loads(args.trades.read_text(encoding="utf-8"))
    if not mt5.initialize():
        sys.exit("MT5 initialize failed")

    data_cache = {}
    for symbol, res in payload["results"].items():
        data_cache[symbol] = pull(symbol)
        rows = analyze_symbol(symbol, res["trades"], data_cache[symbol])

        print(f"\n==== {symbol}  (variant={payload.get('variant')}, phase_gate={payload.get('phase_gate')}) ====")
        header = f"{'time':<22}{'dir':<5}{'riskR':>7}{'MFE':>6}{'MAFE':>6}{'trend':<9}{'1h':>6}{'2h':>6}{'4h':>6}  hit"
        print(header)
        for r in rows:
            print(f"{r['entry_time']:<22}{r['direction']:<5}{r['risk']:>7.5f}"
                  f"{r['mfe_r']:>6.2f}{r['mafe_r']:>6.2f}{r['trend_at_entry']:<9}"
                  f"{r['fc_1h'] or 0:>6.2f}{r['fc_2h'] or 0:>6.2f}{r['fc_4h'] or 0:>6.2f}  {r['first_hit']}")

        decided = [r for r in rows if r["outcome"] in ("WIN", "LOSS")]
        wins = [r for r in decided if r["outcome"] == "WIN"]
        tp_hits = [r for r in decided if r["first_hit"] == "TP"]
        sl_hits = [r for r in decided if r["first_hit"] == "SL"]
        mfe = np.mean([r["mfe_r"] for r in decided]) if decided else 0
        mafe = np.mean([r["mafe_r"] for r in decided]) if decided else 0
        tp_any = sum(1 for r in rows if r["tp_ever_reached"])
        print(f"\n  decided={len(decided)}  wins={len(wins)}  "
              f"first-hit TP={len(tp_hits)}  first-hit SL={len(sl_hits)}")
        print(f"  TP ever touched (incl. after trailing): {tp_any}/{len(rows)}")
        print(f"  avg MFE={mfe:.2f}R  avg MAFE={mafe:.2f}R  "
              f"(MFE>=2R: {sum(1 for r in decided if r['mfe_r']>=2)} / {len(decided)})")

        trend_counts = {}
        for r in rows:
            trend_counts.setdefault(r["trend_at_entry"], {"trades": 0, "wins": 0})
            trend_counts[r["trend_at_entry"]]["trades"] += 1
            if r["outcome"] == "WIN":
                trend_counts[r["trend_at_entry"]]["wins"] += 1
        for trend, c in sorted(trend_counts.items()):
            print(f"  trend {trend:<8}: {c['trades']} trades, {c['wins']} wins")

        simulate_sl_widths(symbol, res["trades"], data_cache[symbol])

    mt5.shutdown()


if __name__ == "__main__":
    main()
