#!/usr/bin/env python3
"""
fetch_closed_trades.py - Fetch closed trades from MT5 and calculate actual P&L.
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
    print(f"MT5 connected: Account {account.login} | Balance: ${account.balance:.2f}")
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
        
        symbol = SYMBOL_MAP[deal.symbol]
        
        if deal.type == 2:
            continue
        
        if deal.position_id == 0:
            continue
        
        if deal.position_id not in positions:
            positions[deal.position_id] = {
                "position_id": deal.position_id,
                "symbol": symbol,
                "magic": deal.magic,
                "entry_type": deal.type,
                "entry_price": deal.price,
                "volume": deal.volume,
                "entry_time": datetime.fromtimestamp(deal.time, tz=timezone.utc),
                "exit_type": None,
                "exit_price": None,
                "exit_time": None,
                "profit": 0,
                "commission": deal.commission,
                "swap": deal.swap,
                "comment": deal.comment,
                "exit_reason": "",
            }
        else:
            pos = positions[deal.position_id]
            pos["exit_type"] = deal.type
            pos["exit_price"] = deal.price
            pos["exit_time"] = datetime.fromtimestamp(deal.time, tz=timezone.utc)
            pos["profit"] = deal.profit
            pos["swap"] += deal.swap
            pos["commission"] += deal.commission
            pos["exit_reason"] = deal.comment
    
    return positions

def determine_strategy(magic: int, comment: str) -> str:
    if magic >= 234000 and magic <= 244000:
        if "SB" in comment or "SILVER" in comment or magic % 4 >= 2:
            return "SILVER_BULLET"
        return "INGWE"
    return "UNKNOWN"

def calculate_pnl(closed_trades: list) -> dict:
    if not closed_trades:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "net_pnl": 0}
    
    wins = [t for t in closed_trades if t["profit"] > 0]
    losses = [t for t in closed_trades if t["profit"] < 0]
    breakeven = [t for t in closed_trades if t["profit"] == 0]
    total = len(closed_trades)
    
    gross_profit = sum(t["profit"] for t in wins)
    gross_loss = abs(sum(t["profit"] for t in losses))
    net_pnl = sum(t["profit"] for t in closed_trades)
    total_commission = sum(t["commission"] + t["swap"] for t in closed_trades)
    
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf") if gross_profit > 0 else 0
    win_rate = round(len(wins) / total * 100, 1) if total > 0 else 0
    
    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": win_rate,
        "net_pnl": round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": pf,
        "total_commission": round(total_commission, 2),
    }

def main():
    if not connect_mt5():
        return
    
    positions = get_closed_trades(days_back=90)
    closed_trades = list(positions.values())
    
    print(f"\nFound {len(closed_trades)} closed positions\n")
    
    if not closed_trades:
        print("No closed trades found.")
        mt5.shutdown()
        return
    
    for pos in closed_trades:
        pos["strategy"] = determine_strategy(pos["magic"], pos["comment"])
    
    print("=" * 70)
    print("  ACTUAL P&L FROM MT5 CLOSED TRADES")
    print("=" * 70)
    
    all_stats = calculate_pnl(closed_trades)
    
    print(f"\n  TOTAL (All Strategies)")
    print(f"  {'-' * 50}")
    print(f"  Trades:        {all_stats['total']}")
    print(f"  Wins:          {all_stats['wins']}")
    print(f"  Losses:        {all_stats['losses']}")
    print(f"  Breakeven:     {all_stats['breakeven']}")
    print(f"  Win Rate:      {all_stats['win_rate']}%")
    print(f"  Net P&L:       ${all_stats['net_pnl']:+.2f}")
    print(f"  Gross Profit:  ${all_stats['gross_profit']:+.2f}")
    print(f"  Gross Loss:    ${all_stats['gross_loss']:.2f}")
    print(f"  Profit Factor: {all_stats['profit_factor']}")
    print(f"  Commission:    -${all_stats['total_commission']:.2f}")
    print()
    
    by_strategy = {}
    for t in closed_trades:
        key = f"{t['symbol']}_{t['strategy']}"
        if key not in by_strategy:
            by_strategy[key] = []
        by_strategy[key].append(t)
    
    for key, strat_trades in sorted(by_strategy.items()):
        stats = calculate_pnl(strat_trades)
        print(f"  {key}")
        print(f"  {'-' * 50}")
        print(f"  Trades:        {stats['total']}")
        print(f"  Wins:          {stats['wins']}")
        print(f"  Losses:        {stats['losses']}")
        print(f"  Win Rate:      {stats['win_rate']}%")
        print(f"  Net P&L:       ${stats['net_pnl']:+.2f}")
        print(f"  Profit Factor: {stats['profit_factor']}")
        print()
    
    print(f"  Updated trade log: {TRADE_LOG}")
    print("=" * 70)
    
    mt5.shutdown()

if __name__ == "__main__":
    main()
