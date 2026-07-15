#!/usr/bin/env python3
"""
Consolidate trade files into trade_log.json for performance reporter.
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
TRADE_LOG = BASE_DIR / "data" / "trade_log.json"

SOURCE_FILES = [
    BASE_DIR / "trades_EURUSD_INGWE.json",
    BASE_DIR / "trades_EURUSD_SILVER_BULLET.json",
    BASE_DIR / "trades_EURUSD_ICT_M1.json",
    BASE_DIR / "trades_GBPUSD_INGWE.json",
    BASE_DIR / "trades_GBPUSD_SILVER_BULLET.json",
    BASE_DIR / "trades_GBPUSD_LONDON_OPEN.json",
]

def extract_symbol(filename: str) -> str:
    for symbol in ["EURUSD", "GBPUSD"]:
        if symbol in filename:
            return symbol
    return "UNKNOWN"

def transform_trade(trade: dict, filename: str) -> dict:
    symbol = extract_symbol(filename)
    
    # Enrichment fields from source
    use_position_id = trade.get("position_id")
    trade_id = f"{symbol}_{use_position_id}" if use_position_id else (
        f"{symbol}_{trade.get('time', '').replace(':', '').replace('-', '').replace(' ', '_')}"
    )
    
    return {
        "trade_id": trade_id,
        "entry_time": trade.get("time", ""),
        "exit_time": "",
        "symbol": symbol,
        "strategy": trade.get("strategy", ""),
        "market_mode": trade.get("market_mode", ""),
        "session": trade.get("session", ""),
        "direction": trade.get("direction", ""),
        "entry": trade.get("entry", 0),
        "sl": trade.get("sl", 0),
        "tp": trade.get("tp", 0),
        "lot_size": trade.get("lot_size", 0),
        "pnl_usd": 0,
        "outcome": "",
        "rr_achieved": 0,
        "comment": trade.get("comment", ""),
        "retcode": trade.get("retcode", 0),
        "source_file": filename,
        "slippage": trade.get("slippage"),
        "entry_req": trade.get("entry_req"),
        "entry_fill": trade.get("entry_fill"),
        "effective_rr": trade.get("effective_rr"),
        "position_id": use_position_id,
        "htf_bias": trade.get("htf_bias"),
        "kronos_decision": trade.get("kronos_decision"),
        "kronos_confidence": trade.get("kronos_confidence"),
        "circuit_breaker": trade.get("circuit_breaker"),
        "api_latency_ms": trade.get("api_latency_ms"),
        "fvg_confirmed": trade.get("fvg_confirmed"),
        "ob_present": trade.get("ob_present"),
        "confluence_score": trade.get("confluence_score"),
        "spread_at_entry": trade.get("spread_at_entry"),
        "setup_type": trade.get("setup_type")
    }

def main():
    consolidated = []
    
    for file_path in SOURCE_FILES:
        if not file_path.exists():
            print(f"Warning: {file_path.name} not found")
            continue
            
        with open(file_path) as f:
            trades = json.load(f)
        
        for trade in trades:
            transformed = transform_trade(trade, file_path.name)
            consolidated.append(transformed)
    
    consolidated.sort(key=lambda t: t.get("entry_time", ""))
    
    TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_LOG, "w") as f:
        json.dump(consolidated, f, indent=2)
    
    print(f"Consolidated {len(consolidated)} trades to {TRADE_LOG}")
    
    summary = {}
    for t in consolidated:
        key = f"{t['symbol']}_{t['strategy']}"
        summary[key] = summary.get(key, 0) + 1
    
    print("\nBreakdown:")
    for key, count in sorted(summary.items()):
        print(f"  {key}: {count} trades")

if __name__ == "__main__":
    main()
