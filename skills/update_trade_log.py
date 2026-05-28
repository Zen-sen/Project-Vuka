#!/usr/bin/env python3
"""
update_trade_log.py - Update trade_log.json with actual outcomes from MT5.
"""

import MetaTrader5 as mt5
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

TRADE_LOG = Path(__file__).parent.parent / "data" / "trade_log.json"

SYMBOL_MAP = {
    "EURUSDc": "EURUSD",
    "GBPUSDc": "GBPUSD",
    "USDJPYc": "USDJPY",
}

def connect_mt5():
    if not mt5.initialize():
        print(f"MT5 initialization failed: {mt5.last_error()}")
        return False
    account = mt5.account_info()
    print(f"MT5 connected: Account {account.login}")
    return True

def get_closed_trades(days_back: int = 90) -> dict:
    to_date = datetime.now() + timedelta(hours=2)
    from_date = to_date - timedelta(days=days_back)
    
    all_deals = mt5.history_deals_get(from_date, to_date)
    if all_deals is None:
        return {}
    
    positions = {}
    
    for deal in all_deals:
        if deal.symbol not in SYMBOL_MAP:
            continue
        
        if deal.type == 2:
            continue
        
        if deal.position_id == 0:
            continue
        
        symbol = SYMBOL_MAP[deal.symbol]
        
        if deal.position_id not in positions:
            positions[deal.position_id] = {
                "position_id": deal.position_id,
                "symbol": symbol,
                "magic": deal.magic,
                "entry_time": datetime.fromtimestamp(deal.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "exit_time": "",
                "direction": "BUY" if deal.type == 0 else "SELL",
                "entry": deal.price,
                "volume": deal.volume,
                "profit": 0,
                "commission": deal.commission,
                "swap": deal.swap,
                "exit_reason": "",
            }
        else:
            pos = positions[deal.position_id]
            pos["exit_time"] = datetime.fromtimestamp(deal.time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            pos["profit"] = deal.profit
            pos["swap"] += deal.swap
            pos["exit_reason"] = deal.comment
    
    return positions

def determine_strategy(magic: int, comment: str) -> str:
    if magic >= 234000 and magic <= 244000:
        if "SB" in comment or "SILVER" in comment:
            return "SILVER_BULLET"
        return "INGWE"
    return "UNKNOWN"

def main():
    if not connect_mt5():
        return
    
    positions = get_closed_trades(days_back=90)
    closed_trades = list(positions.values())
    
    for pos in closed_trades:
        pos["strategy"] = determine_strategy(pos["magic"], pos.get("exit_reason", ""))
    
    updated_log = []
    for pos in closed_trades:
        outcome = "WIN" if pos["profit"] > 0 else ("LOSS" if pos["profit"] < 0 else "BREAKEVEN")
        entry_time = datetime.fromisoformat(pos["entry_time"].replace("+00:00", ""))
        
        trade = {
            "trade_id": f"{pos['symbol']}_{pos['position_id']}",
            "entry_time": pos["entry_time"],
            "exit_time": pos["exit_time"],
            "symbol": pos["symbol"],
            "strategy": pos["strategy"],
            "direction": pos["direction"],
            "entry": pos["entry"],
            "volume": pos["volume"],
            "pnl_usd": round(pos["profit"], 2),
            "outcome": outcome,
            "commission": round(pos["commission"], 2),
            "swap": round(pos["swap"], 2),
            "exit_reason": pos["exit_reason"],
            "source": "MT5"
        }
        updated_log.append(trade)
    
    updated_log.sort(key=lambda x: x["entry_time"])
    
    with open(TRADE_LOG, "w") as f:
        json.dump(updated_log, f, indent=2)
    
    print(f"Updated {len(updated_log)} trades in {TRADE_LOG}")
    
    wins = [t for t in updated_log if t["outcome"] == "WIN"]
    losses = [t for t in updated_log if t["outcome"] == "LOSS"]
    be = [t for t in updated_log if t["outcome"] == "BREAKEVEN"]
    total_pnl = sum(t["pnl_usd"] for t in updated_log)
    
    print(f"\nSummary:")
    print(f"  Total: {len(updated_log)} | {len(wins)}W / {len(losses)}L / {len(be)}B")
    print(f"  Win Rate: {len(wins)/len(updated_log)*100:.1f}%")
    print(f"  Net P&L: ${total_pnl:+.2f}")
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
