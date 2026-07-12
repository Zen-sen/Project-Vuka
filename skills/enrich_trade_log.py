#!/usr/bin/env python3
"""
enrich_trade_log.py — Data enrichment layer for trade_log.json
Project Vuka
Cross-references MT5 trade log against source trade files and Kronos veto
decisions to populate microstructure, confluence, and infrastructure fields.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Any

from ict_calculator import ICTCalculator

BASE_DIR = Path(__file__).parent.parent
TRADE_LOG = BASE_DIR / "data" / "trade_log.json"
KRONOS_DECISIONS = BASE_DIR / "data" / "kronos_decisions.json"

FALLBACK_DEFAULTS: dict[str, Any] = {
    "htf_bias": "SPLIT",
    "kronos_decision": "ALLOW",
    "kronos_confidence": 0.0,
    "circuit_breaker": "CLOSED",
    "api_latency_ms": 0.0,
    "spread_at_entry": "N/A",
    "slippage": "N/A",
    "fvg_confirmed": False,
    "ob_present": False,
    "retracement_depth": "N/A",
    "confluence_score": 0,
    "setup_type": "N/A",
    "exit_reason": "N/A",
}

SOURCE_PATTERNS = [
    BASE_DIR / "trades_EURUSD_INGWE.json",
    BASE_DIR / "trades_EURUSD_SILVER_BULLET.json",
    BASE_DIR / "trades_EURUSD_ICT_M1.json",
    BASE_DIR / "trades_GBPUSD_INGWE.json",
    BASE_DIR / "trades_GBPUSD_SILVER_BULLET.json",
    BASE_DIR / "trades_GBPUSD_LONDON_OPEN.json",
]

ENRICHMENT_FIELDS = [
    "session", "slippage", "market_mode", "entry_req", "entry_fill",
    "effective_rr", "position_id", "spread_at_entry",
    "htf_bias", "kronos_decision", "kronos_confidence",
    "circuit_breaker", "api_latency_ms", "fvg_confirmed", "ob_present",
    "confluence_score", "setup_type",
]

KRONOS_FIELDS = {
    "decision": "kronos_decision",
    "confidence": "kronos_confidence",
    "circuit_breaker_state": "circuit_breaker",
    "api_latency_ms": "api_latency_ms",
}


def load_json(path):
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("trades", [])


def parse_trade_id(trade_id: str):
    m = re.match(r"^(EURUSD|GBPUSD|USDJPY|BTCUSD)_(\d+)$", trade_id)
    if m:
        return m.group(1), int(m.group(2))
    return None, None


def time_delta_seconds(t1: str, t2: str) -> Optional[float]:
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"]:
        try:
            dt1 = datetime.strptime(t1[:19], "%Y-%m-%d %H:%M:%S")
            dt2 = datetime.strptime(t2[:19], "%Y-%m-%d %H:%M:%S")
            return abs((dt1 - dt2).total_seconds())
        except (ValueError, IndexError):
            continue
    return None


def extract_symbol_from_filename(path: Path) -> str:
    name = path.stem
    for sym in ["EURUSD", "GBPUSD", "USDJPY", "BTCUSD"]:
        if sym in name:
            return sym
    return "UNKNOWN"


def normalize_symbol(sym: str) -> str:
    return sym.replace("c", "").strip()


def compute_session(entry_time_str: str) -> str:
    try:
        dt = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return "UNKNOWN"
    h = dt.hour
    m = dt.minute
    if h < 7 or (h == 7 and m == 0):
        return "ASIAN"
    elif h < 12 or (h == 12 and m == 0):
        return "LONDON_OPEN"
    elif h < 17 or (h == 17 and m == 0):
        return "LONDON_CLOSE"
    else:
        return "NY_OPEN"


def infer_market_mode(entry_time_str: str) -> str:
    try:
        dt = datetime.strptime(entry_time_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return "winter"
    month = dt.month
    return "summer" if 4 <= month <= 10 else "winter"


def apply_fallback_defaults(trade: dict) -> dict:
    t = dict(trade)
    entry_time = t.get("entry_time", "")

    if not t.get("session") or t["session"] in ("", "UNKNOWN"):
        t["session"] = compute_session(entry_time)
    if not t.get("market_mode"):
        t["market_mode"] = infer_market_mode(entry_time)

    for field, default in FALLBACK_DEFAULTS.items():
        if field not in t or t[field] is None or t[field] == "":
            t[field] = default

    return t


def build_source_index(source_files):
    idx_by_posid = {}
    idx_by_time = {}
    for fpath in source_files:
        if not fpath.exists():
            continue
        file_symbol = extract_symbol_from_filename(fpath)
        trades = load_json(fpath)
        for t in trades:
            if t.get("retcode") != 10009:
                continue
            pos_id = t.get("position_id")
            if pos_id:
                idx_by_posid[pos_id] = t
            ts = t.get("time") or t.get("entry_time", "")
            sym = normalize_symbol(t.get("symbol", "") or file_symbol)
            if sym and ts:
                key = (sym, ts[:16])
                idx_by_time.setdefault(key, []).append(t)
    return idx_by_posid, idx_by_time


def find_source_match(trade, idx_by_posid, idx_by_time):
    _, pos_id = parse_trade_id(trade.get("trade_id", ""))
    if pos_id and pos_id in idx_by_posid:
        return idx_by_posid[pos_id]

    sym = trade.get("symbol", "")
    ts = trade.get("entry_time", "")
    if not sym or not ts:
        return None

    # Source timestamps are in local SAST (+2h from MT5 UTC)
    # Try multiple offsets to find the matching source entry
    for offset_hours in [2, 0, 1, -2]:
        try:
            dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
            adjusted_dt = dt + timedelta(hours=offset_hours)
            minute_key = adjusted_dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, IndexError):
            continue
        candidates = idx_by_time.get((sym, minute_key), [])
        if not candidates:
            continue

        best = None
        best_delta = float("inf")
        for c in candidates:
            source_ts = c.get("time") or c.get("entry_time", "")
            try:
                source_dt = datetime.strptime(source_ts[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, IndexError):
                continue
            delta = abs((source_dt - adjusted_dt).total_seconds())
            if delta < best_delta:
                best_delta = delta
                best = c

        if best and best_delta < 60:
            return best
    return None


def normalize_ts(ts: str) -> str:
    """Normalize timestamp formats to YYYY-MM-DD HH:MM."""
    return ts[:10] + " " + ts[11:16]


def build_kronos_index(decisions):
    idx = {}
    for d in decisions:
        ts = d.get("timestamp", "")
        sym = d.get("symbol", "")
        if sym and ts:
            key = (sym, normalize_ts(ts))
            idx.setdefault(key, []).append(d)
    return idx


def find_kronos_match(trade, kronos_idx):
    sym = trade.get("symbol", "")
    ts = trade.get("entry_time", "")
    if not sym or not ts:
        return None

    # Kronos timestamps are in local time (SAST, +2h from MT5 UTC)
    for offset_hours in [2, 0, 1, -2]:
        try:
            dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
            adjusted = (dt + timedelta(hours=offset_hours)).strftime("%Y-%m-%d %H:%M")
        except (ValueError, IndexError):
            continue
        key = (sym, adjusted)
        candidates = kronos_idx.get(key, [])
        if candidates:
            best = None
            best_delta = float("inf")
            for c in candidates:
                kronos_ts = c.get("timestamp", "")
                try:
                    kronos_dt = datetime.strptime(kronos_ts[:19], "%Y-%m-%dT%H:%M:%S")
                    adjusted_dt = dt + timedelta(hours=offset_hours)
                    delta = abs((kronos_dt - adjusted_dt).total_seconds())
                    if delta < best_delta:
                        best_delta = delta
                        best = c
                except (ValueError, IndexError):
                    continue
            if best and best_delta < 60:
                return best
    return None


def enrich_trade(trade, source_trade, kronos_decision):
    enriched = dict(trade)

    if source_trade:
        for field in ENRICHMENT_FIELDS:
            val = source_trade.get(field)
            if val is not None:
                enriched[field] = val

    if kronos_decision:
        for src_field, target_field in KRONOS_FIELDS.items():
            val = kronos_decision.get(src_field)
            if val is not None:
                enriched[target_field] = val

    if source_trade:
        session = source_trade.get("session")
        if session:
            enriched["session"] = session

    return enriched


def normalize_sessions(trades):
    mapping = {
        "Asian": "ASIAN", "London": "LONDON_OPEN",
        "London Open": "LONDON_OPEN", "London Close": "LONDON_CLOSE",
        "New York Open": "NY_OPEN", "NY": "NY_OPEN", "NY_Open": "NY_OPEN",
        "SB_Window1": "LONDON_OPEN", "SB_Window2": "LONDON_CLOSE",
    }
    for t in trades:
        s = t.get("session", "")
        if s in mapping:
            t["session"] = mapping[s]


def main():
    print("Loading trade_log.json...")
    trades = load_json(TRADE_LOG)
    if not trades:
        print("No trades found.")
        return

    print("Building source file index...")
    idx_by_posid, idx_by_time = build_source_index(SOURCE_PATTERNS)

    print("Loading Kronos decisions...")
    kronos_decisions = load_json(KRONOS_DECISIONS)
    kronos_idx = build_kronos_index(kronos_decisions)

    matched_src = 0
    matched_kronos = 0

    enriched_trades = []
    for t in trades:
        src = find_source_match(t, idx_by_posid, idx_by_time)
        kd = find_kronos_match(t, kronos_idx)
        if src:
            matched_src += 1
        if kd:
            matched_kronos += 1
        enriched = enrich_trade(t, src, kd)
        enriched_trades.append(enriched)

    normalize_sessions(enriched_trades)

    ict_hits = {"fvg": 0, "ob": 0, "retracement": 0, "htf_bias": 0, "spread": 0, "slippage": 0}
    for t in enriched_trades:
        if t.get("entry_time") and t.get("entry") and t.get("direction"):
            calc = ICTCalculator(
                t.get("symbol", ""),
                t.get("entry_time", ""),
                t.get("entry", 0),
                t.get("direction", ""),
            )
            calc.load()
            if calc.fvg_confirmed:
                t["fvg_confirmed"] = True
                ict_hits["fvg"] += 1
            if calc.ob_present:
                t["ob_present"] = True
                ict_hits["ob"] += 1
            rd = calc.retracement_depth
            if rd is not None:
                t["retracement_depth"] = rd
                ict_hits["retracement"] += 1
            htb = calc.htf_bias
            if htb != "SPLIT":
                t["htf_bias"] = htb
                ict_hits["htf_bias"] += 1
            sp = calc.spread_at_entry
            if sp != "N/A":
                t["spread_at_entry"] = sp
                ict_hits["spread"] += 1
            sl = calc.slippage
            if sl != "N/A":
                t["slippage"] = sl
                ict_hits["slippage"] += 1

    enriched_trades = [apply_fallback_defaults(t) for t in enriched_trades]
    print(f"  ICT: OB={ict_hits['ob']} FVG={ict_hits['fvg']} retrace={ict_hits['retracement']} htf={ict_hits['htf_bias']} spread={ict_hits['spread']} slippage={ict_hits['slippage']}")

    TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_LOG, "w") as f:
        json.dump(enriched_trades, f, indent=2)

    total = len(enriched_trades)
    print(f"\nEnriched {total} trades:")
    print(f"  Source file matches: {matched_src}/{total}")
    print(f"  Kronos decision matches: {matched_kronos}/{total}")

    enriched_count = sum(
        1 for t in enriched_trades
        if t.get("kronos_decision") not in ("ALLOW", None, "")
        or t.get("session")
        or t.get("slippage") not in (None, "")
    )
    print(f"  Trades with any enrichment: {enriched_count}/{total}")

    all_covered = [
        "session", "market_mode", "htf_bias", "kronos_decision",
        "kronos_confidence", "circuit_breaker", "api_latency_ms",
        "exit_reason"
    ]
    partial = [
        "spread_at_entry", "slippage", "fvg_confirmed", "ob_present",
        "retracement_depth", "confluence_score", "setup_type"
    ]
    real_values = {f: 0 for f in all_covered + partial}
    for t in enriched_trades:
        for f in all_covered + partial:
            val = t.get(f)
            if val not in (None, "", "N/A", 0.0, False, 0):
                real_values[f] = real_values.get(f, 0) + 1
    print("\nField coverage (non-default):")
    for f, count in sorted(real_values.items()):
        status = "FULL" if count == total else f"{count}/{total}"
        print(f"  {f}: {status}")


if __name__ == "__main__":
    main()
