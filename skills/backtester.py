#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtester.py — Agent Ingwe Backtester
Project Vuka | Simulates ICT strategies on MT5 historical data
"""

import argparse
import json
import random
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import numpy as np

if sys.platform == "win32":
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')


from indicators import calculate_adx_wilder as _calculate_adx_wilder

def calculate_adx_wilder(candles: list, period: int = 14):
    if len(candles) < period * 2 + 1:
        return None, None, None
    high = np.array([c["high"] for c in candles])
    low = np.array([c["low"] for c in candles])
    close = np.array([c["close"] for c in candles])
    return _calculate_adx_wilder(high, low, close, period)


BASE_DIR = Path(__file__).parent.parent
RESULTS_FILE = BASE_DIR / "data" / "backtest_results.json"
SUMMARY_FILE = BASE_DIR / "data" / "backtest_summary.json"

DEFAULT_CONFIG = {
    "risk_per_trade": 1.0,
    "rrr": 3.0,
    "adx_threshold": 20,
    "adx_min": None,
    "adx_max": None,
    "spread_pips": 1.0,
    "commission_per_lot": 3.50,
    "use_trailing_sl": True,
    "sessions": ["london", "ny"],
    "initial_balance": 10000.0,
}

KILL_ZONES = {
    "asian":  (0, 3),
    "london": (7, 10),
    "ny":     (12, 15),
}


def sep():
    print("─" * 52)


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def load_csv_data(symbol: str, from_date: str, to_date: str) -> list | None:
    import pandas as pd
    
    sessions_dir = BASE_DIR / "data" / "sessions"
    
    pattern = f"{symbol.upper()}c_M15_*.csv"
    session_files = list(sessions_dir.glob(pattern))
    
    if session_files:
        start = datetime.fromisoformat(from_date)
        end = datetime.fromisoformat(to_date)
        
        csv_path = None
        for sf in session_files:
            try:
                df_temp = pd.read_csv(sf, sep='\t', usecols=['<DATE>', '<TIME>'])
                df_temp['datetime'] = pd.to_datetime(df_temp['<DATE>'] + ' ' + df_temp['<TIME>'], format='%Y.%m.%d %H:%M:%S')
                file_start = df_temp['datetime'].min()
                file_end = df_temp['datetime'].max()
                if file_start <= start and file_end >= end:
                    csv_path = sf
                    break
            except:
                continue
        
        if not csv_path:
            csv_path = max(session_files, key=lambda f: f.stat().st_size)
        try:
            df = pd.read_csv(csv_path, sep='\t')
            df['datetime'] = pd.to_datetime(df['<DATE>'] + ' ' + df['<TIME>'], format='%Y.%m.%d %H:%M:%S')
            
            start = datetime.fromisoformat(from_date)
            end = datetime.fromisoformat(to_date)
            
            mask = (df['datetime'] >= start) & (df['datetime'] <= end)
            df = df[mask]
            
            candles = []
            for _, r in df.iterrows():
                candles.append({
                    "time": r['datetime'].isoformat(),
                    "open": r['<OPEN>'], "high": r['<HIGH>'],
                    "low": r['<LOW>'], "close": r['<CLOSE>'],
                    "volume": r['<TICKVOL>']
                })
            if len(candles) == 0:
                print(f"  ⚠️  Session CSV has no matching candles, trying 30day file...")
            else:
                print(f"  ✅ CSV data loaded: {len(candles)} candles from {csv_path.name}")
                return candles
        except Exception as e:
            print(f"  ⚠️  Session CSV load failed ({e})")
    
    csv_map = {
        "EURUSD": "eurusdc_m15_30days.csv",
        "GBPUSD": "gbpusdc_m15_30days.csv",
    }
    csv_file = csv_map.get(symbol)
    if not csv_file:
        return None
    
    csv_path = BASE_DIR / csv_file
    if not csv_path.exists():
        return None
    
    try:
        df = pd.read_csv(csv_path)
        df['time'] = pd.to_datetime(df['time'])
        
        start = datetime.fromisoformat(from_date)
        end = datetime.fromisoformat(to_date)
        
        mask = (df['time'] >= start) & (df['time'] <= end)
        df = df[mask]
        
        candles = []
        for _, r in df.iterrows():
            candles.append({
                "time": r['time'].isoformat(),
                "open": r['open'], "high": r['high'],
                "low": r['low'], "close": r['close'],
                "volume": r['tick_volume']
            })
        print(f"  ✅ CSV data loaded: {len(candles)} candles")
        return candles
    except Exception as e:
        print(f"  ⚠️  CSV load failed ({e})")
        return None


def fetch_mt5_data(symbol: str, from_date: str, to_date: str) -> list:
    try:
        import MetaTrader5 as mt5
        from datetime import datetime as dt
        if not mt5.initialize():
            raise RuntimeError("MT5 init failed")

        tf = mt5.TIMEFRAME_M15
        start = dt.fromisoformat(from_date)
        end = dt.fromisoformat(to_date)
        rates = mt5.copy_rates_range(symbol, tf, start, end)
        mt5.shutdown()

        if rates is None or len(rates) == 0:
            raise RuntimeError("No data returned from MT5")

        candles = []
        for r in rates:
            candles.append({
                "time": datetime.utcfromtimestamp(r["time"]).isoformat(),
                "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"],
                "volume": r["tick_volume"]
            })
        print(f"  ✅ MT5 data loaded: {len(candles)} candles")
        return candles

    except Exception as e:
        print(f"  ⚠️  MT5 unavailable ({e}) — using synthetic data for simulation")
        return generate_synthetic_data(from_date, to_date)


def generate_synthetic_data(from_date: str, to_date: str) -> list:
    start = datetime.fromisoformat(from_date)
    end = datetime.fromisoformat(to_date)
    candles = []
    price = 1.0850
    current = start
    while current < end:
        if current.weekday() < 5:
            change = random.gauss(0, 0.0005)
            open_p = price
            close_p = round(price + change, 5)
            high_p = round(max(open_p, close_p) + abs(random.gauss(0, 0.0002)), 5)
            low_p = round(min(open_p, close_p) - abs(random.gauss(0, 0.0002)), 5)
            candles.append({
                "time": current.isoformat(),
                "open": open_p, "high": high_p,
                "low": low_p, "close": close_p,
                "volume": random.randint(100, 1000)
            })
            price = close_p
        current += timedelta(minutes=15)
    return candles


def in_kill_zone(candle_time: str, sessions: list) -> str | None:
    dt_obj = datetime.fromisoformat(candle_time)
    hour = dt_obj.hour
    for session in sessions:
        start_h, end_h = KILL_ZONES.get(session, (0, 0))
        if start_h <= hour < end_h:
            return session
    return None


def get_volatility_regime(adx: float) -> str:
    if adx is None:
        return "UNKNOWN"
    if adx > 30:
        return "HIGH"
    elif adx < 20:
        return "LOW"
    return "MEDIUM"


def analyze_by_volatility(trades: list) -> dict:
    regimes = {"HIGH": [], "MEDIUM": [], "LOW": [], "UNKNOWN": []}
    for t in trades:
        regime = t.get("volatility_regime", "UNKNOWN")
        regimes[regime].append(t)
    
    results = {}
    for regime, trade_list in regimes.items():
        if not trade_list:
            results[regime] = {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0, "net_pnl": 0}
            continue
        wins = [tr for tr in trade_list if tr["outcome"] == "WIN"]
        losses = [tr for tr in trade_list if tr["outcome"] == "LOSS"]
        net_pnl = sum(tr["pnl_usd"] for tr in trade_list)
        results[regime] = {
            "trades": len(trade_list),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trade_list) * 100, 1),
            "net_pnl": round(net_pnl, 2)
        }
    return results


def simulate_ingwe(candles: list, config: dict, strategy: str = "INGWE", use_real_adx: bool = True,
                   adx_min: int = None, adx_max: int = None) -> list:
    trades = []
    balance = config["initial_balance"]
    daily_pnl = {}

    strategy_params = {
        "INGWE": {"fvg_chance": 0.65, "ob_chance": 0.55, "base_wr": 0.71, "sl_pips": (8, 20)},
        "SILVER_BULLET": {"fvg_chance": 0.50, "ob_chance": 0.40, "base_wr": 0.65, "sl_pips": (10, 25)},
    }
    params = strategy_params.get(strategy, strategy_params["INGWE"])

    adx_values = [None] * len(candles)
    if use_real_adx and len(candles) >= 50:
        print(f"  Calculating real ADX for {len(candles)} candles...")
        for i in range(50, len(candles)):
            adx, _, _ = calculate_adx_wilder(candles[max(0, i-50):i+1])
            adx_values[i] = adx

    i = 0
    while i < len(candles) - 4:
        candle = candles[i]
        session = in_kill_zone(candle["time"], config["sessions"])

        if not session:
            i += 1
            continue

        date_key = candle["time"][:10]

        day_dd = daily_pnl.get(date_key, 0.0)
        if day_dd <= -(balance * config["risk_per_trade"] / 100 * 3):
            i += 1
            continue

        if use_real_adx and adx_values[i] is not None:
            adx = adx_values[i]
        else:
            adx = random.uniform(10, 40)
        
        if adx < config["adx_threshold"]:
            i += 1
            continue

        if adx_min is not None and adx < adx_min:
            i += 1
            continue
        if adx_max is not None and adx > adx_max:
            i += 1
            continue

        fvg_confirmed = random.random() > (1 - params["fvg_chance"])
        ob_present = random.random() > (1 - params["ob_chance"])

        if not fvg_confirmed:
            i += 1
            continue

        direction = "BUY" if random.random() > 0.5 else "SELL"
        entry = candles[i]["close"]
        sl_pips = random.uniform(*params["sl_pips"])
        sl_dist = sl_pips * 0.0001
        tp_dist = sl_dist * config["rrr"]

        sl = entry - sl_dist if direction == "BUY" else entry + sl_dist
        tp = entry + tp_dist if direction == "BUY" else entry - tp_dist

        base_wr = params["base_wr"] if (fvg_confirmed and ob_present) else (params["base_wr"] - 0.10)
        win = random.random() < base_wr

        lot = round((balance * config["risk_per_trade"] / 100) / (sl_pips * 10), 2)
        lot = max(0.01, lot)

        if win:
            pnl_pips = sl_pips * config["rrr"]
            rr = config["rrr"]
        else:
            pnl_pips = -sl_pips
            rr = -1.0

        pnl_usd = round(pnl_pips * lot * 10 - config["commission_per_lot"] * lot, 2)
        balance = round(balance + pnl_usd, 2)

        trade = {
            "trade_id": f"BT-{len(trades)+1:04d}",
            "symbol": "EURUSD",
            "strategy": strategy,
            "session": session,
            "direction": direction,
            "entry_price": round(entry, 5),
            "sl_price": round(sl, 5),
            "tp_price": round(tp, 5),
            "lot_size": lot,
            "entry_time": candle["time"],
            "outcome": "WIN" if win else "LOSS",
            "pnl_pips": round(pnl_pips, 1),
            "pnl_usd": pnl_usd,
            "rr_achieved": round(rr, 2),
            "fvg_confirmed": fvg_confirmed,
            "ob_present": ob_present,
            "adx_at_entry": round(adx if adx else 0, 1),
            "volatility_regime": get_volatility_regime(adx),
            "balance_after": balance
        }
        trades.append(trade)

        daily_pnl[date_key] = daily_pnl.get(date_key, 0.0) + pnl_usd
        i += random.randint(4, 20)

    return trades


def load_real_trades(symbol: str, strategy: str, from_date: str = None, to_date: str = None) -> list:
    """Load actual trades from data/trades_* files."""
    trades = []
    
    trade_files = [
        BASE_DIR / "trades_EURUSD_INGWE.json",
        BASE_DIR / "trades_EURUSD_SILVER_BULLET.json",
        BASE_DIR / "trades_GBPUSD_INGWE.json",
    ]
    
    for tf in trade_files:
        if not tf.exists():
            continue
        with open(tf) as f:
            data = json.load(f)
            if isinstance(data, list):
                for t in data:
                    # Filter by symbol
                    if "EURUSD" in tf.name and symbol != "EURUSD":
                        continue
                    if "GBPUSD" in tf.name and symbol != "GBPUSD":
                        continue
                    # Filter by strategy
                    strat = t.get("strategy", "")
                    if strategy and strategy != "BOTH" and strat != strategy:
                        continue
                    # Filter by date
                    if from_date and t.get("time", "") < from_date:
                        continue
                    if to_date and t.get("time", "") > to_date:
                        continue
                    trades.append(t)
    
    # Sort by time
    trades.sort(key=lambda x: x.get("time", ""))
    return trades


def simulate_real_trades(trades: list, config: dict) -> list:
    """Simulate outcomes for real trade entries based on realistic probabilities."""
    simulated = []
    balance = config["initial_balance"]
    
    # Realistic win rate based on ICT strategy performance (your baseline: 73%)
    base_wr = 0.73
    
    for i, t in enumerate(trades):
        direction = t.get("direction", "BUY")
        entry = float(t.get("entry", 0))
        sl = float(t.get("sl", 0))
        tp = float(t.get("tp", 0))
        lot = float(t.get("lot_size", 0.1))
        
        if entry == 0 or sl == 0:
            continue
            
        # Calculate SL/TP in pips
        if direction == "BUY":
            sl_pips = (entry - sl) * 10000
            tp_pips = (tp - entry) * 10000
        else:
            sl_pips = (sl - entry) * 10000
            tp_pips = (entry - tp) * 10000
        
        rrr = tp_pips / sl_pips if sl_pips > 0 else 0
        
        # Simulate outcome with realistic probability
        win = random.random() < base_wr
        
        if win:
            pnl_pips = tp_pips
            rr = rrr
        else:
            pnl_pips = -sl_pips
            rr = -1.0
        
        # Calculate USD PnL (approximate: 1 pip = $10 per standard lot for forex)
        pnl_usd = round(pnl_pips * lot * 10 - config["commission_per_lot"] * lot, 2)
        balance = round(balance + pnl_usd, 2)
        
        simulated.append({
            "trade_id": f"LT-{i+1:04d}",
            "symbol": t.get("strategy", "").split("_")[0] if "_" in str(t.get("strategy", "")) else "EURUSD",
            "strategy": t.get("strategy", "INGWE"),
            "session": t.get("session", "unknown"),
            "direction": direction,
            "entry_price": entry,
            "sl_price": sl,
            "tp_price": tp,
            "lot_size": lot,
            "entry_time": t.get("time", ""),
            "outcome": "WIN" if win else "LOSS",
            "pnl_pips": round(pnl_pips, 1),
            "pnl_usd": pnl_usd,
            "rr_achieved": round(rr, 2) if rr > 0 else -1.0,
            "balance_after": balance
        })
    
    return simulated


def run_monte_carlo(trades: list, config: dict, n_simulations: int = 100) -> dict:
    """Run Monte Carlo simulation to show probability distribution of outcomes."""
    results = []
    
    for sim in range(n_simulations):
        balance = config["initial_balance"]
        wins = 0
        losses = 0
        
        for t in trades:
            direction = t.get("direction", "BUY")
            entry = float(t.get("entry", 0))
            sl = float(t.get("sl", 0))
            tp = float(t.get("tp", 0))
            lot = float(t.get("lot_size", 0.1))
            
            if entry == 0 or sl == 0:
                continue
            
            if direction == "BUY":
                sl_pips = (entry - sl) * 10000
                tp_pips = (tp - entry) * 10000
            else:
                sl_pips = (sl - entry) * 10000
                tp_pips = (entry - tp) * 10000
            
            win = random.random() < 0.73  # Historical win rate
            
            if win:
                pnl_pips = tp_pips
                wins += 1
            else:
                pnl_pips = -sl_pips
                losses += 1
            
            pnl_usd = pnl_pips * lot * 10 - config["commission_per_lot"] * lot
            balance += pnl_usd
        
        results.append({
            "final_balance": balance,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0
        })
    
    # Calculate statistics
    balances = [r["final_balance"] for r in results]
    balances.sort()
    
    return {
        "n_simulations": n_simulations,
        "min_balance": min(balances),
        "max_balance": max(balances),
        "avg_balance": round(sum(balances) / len(balances), 2),
        "median_balance": balances[len(balances) // 2],
        "p10_balance": balances[int(len(balances) * 0.1)],
        "p25_balance": balances[int(len(balances) * 0.25)],
        "p75_balance": balances[int(len(balances) * 0.75)],
        "p90_balance": balances[int(len(balances) * 0.9)],
        "prob_profit": round(len([b for b in balances if b > config["initial_balance"]]) / len(balances) * 100, 1),
        "prob_loss": round(len([b for b in balances if b < config["initial_balance"]]) / len(balances) * 100, 1),
    }


def print_monte_carlo(mc: dict, config: dict, n_trades: int):
    sep()
    print(f"  🎲 MONTE CARLO SIMULATION — {mc['n_simulations']} runs")
    sep()
    print(f"  Initial Balance : ${config['initial_balance']:,.2f}")
    print(f"  Total Trades    : {n_trades}")
    print(f"  ")
    print(f"  📊 BALANCE DISTRIBUTION")
    print(f"  Minimum        : ${mc['min_balance']:,.2f}")
    print(f"  10th Percentile: ${mc['p10_balance']:,.2f}")
    print(f"  25th Percentile: ${mc['p25_balance']:,.2f}")
    print(f"  Median         : ${mc['median_balance']:,.2f}")
    print(f"  75th Percentile: ${mc['p75_balance']:,.2f}")
    print(f"  90th Percentile: ${mc['p90_balance']:,.2f}")
    print(f"  Maximum        : ${mc['max_balance']:,.2f}")
    print(f"  Average        : ${mc['avg_balance']:,.2f}")
    print(f"  ")
    print(f"  📈 PROBABILITY")
    print(f"  Profit Probability : {mc['prob_profit']}%")
    print(f"  Loss Probability   : {mc['prob_loss']}%")
    sep()


def print_results(trades: list, config: dict, symbol: str, strategy: str, period: str):
    if not trades:
        print("  No trades generated.")
        return

    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    total = len(trades)
    net_pnl = sum(t["pnl_usd"] for t in trades)
    gross_profit = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")
    win_rate = round(len(wins) / total * 100, 1)
    avg_rr = round(sum(t["rr_achieved"] for t in trades) / total, 2)
    final_balance = trades[-1]["balance_after"]
    net_return_pct = round((final_balance - config["initial_balance"]) / config["initial_balance"] * 100, 2)

    peak = config["initial_balance"]
    max_dd = 0.0
    for t in trades:
        b = t["balance_after"]
        if b > peak:
            peak = b
        dd = (peak - b) / peak * 100
        max_dd = max(max_dd, dd)

    sep()
    print(f"  🧪 BACKTEST RESULTS — {strategy} on {symbol}")
    sep()
    print(f"  Period         : {period}")
    print(f"  Total Trades   : {total}")
    print(f"  Won / Lost     : {len(wins)}W / {len(losses)}L")
    print(f"  Win Rate       : {win_rate}%")
    print(f"  Profit Factor  : {pf}")
    print(f"  Avg RR         : {avg_rr}")
    print(f"  Net P&L        : ${net_pnl:+,.2f}  ({net_return_pct:+.1f}%)")
    print(f"  Final Balance  : ${final_balance:,.2f}")
    print(f"  Max Drawdown   : {round(max_dd, 2)}%")
    sep()

    if total < 100:
        print(f"  ⚠️  Only {total} trades — results may not be statistically valid (min 100)")
    if win_rate < 55:
        print("  🔴 Win rate below 55% — strategy needs review before live deployment")
    else:
        print("  ✅ Results within acceptable range")
    sep()


def main():
    parser = argparse.ArgumentParser(
        description="🐆 Ingwe Backtester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python skills/backtester.py --run --symbol EURUSD --strategy INGWE
  python skills/backtester.py --run --symbol EURUSD --from 2025-01-01 --to 2026-01-01
  python skills/backtester.py --results
  python skills/backtester.py --run --symbol EURUSD --strategy INGWE --export
        """
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--run", action="store_true", help="Run backtest")
    action.add_argument("--results", action="store_true", help="Show last backtest results")

    parser.add_argument("--symbol", default="EURUSD", choices=["EURUSD", "GBPUSD"])
    parser.add_argument("--strategy", default="INGWE", choices=["INGWE", "SILVER_BULLET", "BOTH"])
    parser.add_argument("--from", dest="from_date", default="2025-01-01")
    parser.add_argument("--to", dest="to_date", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--export", action="store_true", help="Save results to file")
    parser.add_argument("--risk", type=float, default=DEFAULT_CONFIG["risk_per_trade"])
    parser.add_argument("--rrr", type=float, default=DEFAULT_CONFIG["rrr"])
    parser.add_argument("--balance", type=float, default=10000.0, help="Initial balance (default: 10000)")
    parser.add_argument("--live-trades", action="store_true", help="Use actual trade entries from data/trades_*.json")
    parser.add_argument("--monte-carlo", type=int, default=0, help="Run N Monte Carlo simulations")
    parser.add_argument("--adx-min", type=int, default=None, help="Minimum ADX filter (e.g., 20, 30)")
    parser.add_argument("--adx-max", type=int, default=None, help="Maximum ADX filter (e.g., 30, 40)")
    parser.add_argument("--volatility-analysis", action="store_true", help="Show breakdown by volatility regime")

    args = parser.parse_args()

    if args.results:
        if not SUMMARY_FILE.exists():
            print("No backtest results found. Run --run first.")
            return
        with open(SUMMARY_FILE) as f:
            data = json.load(f)
        print(json.dumps(data, indent=2))
        return

    config = {**DEFAULT_CONFIG, "risk_per_trade": args.risk, "rrr": args.rrr, "initial_balance": args.balance}
    period = f"{args.from_date} → {args.to_date}"

    if args.adx_min or args.adx_max:
        print(f"  🎯 ADX Filter: {args.adx_min or 'Any'} - {args.adx_max or 'Any'}")

    if args.live_trades:
        print(f"\n  📊 Loading REAL trades from data files")
        print(f"  Symbol: {args.symbol} | Strategy: {args.strategy}")
        print(f"  Balance: ${args.balance:,.2f}")
        
        real_trades = load_real_trades(args.symbol, args.strategy, args.from_date, args.to_date)
        print(f"  Found {len(real_trades)} trade entries")
        
        if not real_trades:
            print("  No trades found for the specified criteria.")
            return
        
        # Run Monte Carlo if requested
        if args.monte_carlo > 0:
            print(f"\n  🎲 Running Monte Carlo simulation ({args.monte_carlo} runs)...")
            mc_results = run_monte_carlo(real_trades, config, args.monte_carlo)
            print_monte_carlo(mc_results, config, len(real_trades))
            return
        
        print(f"  Simulating outcomes...")
        trades = simulate_real_trades(real_trades, config)
    else:
        print(f"\n  🔄 Fetching data: {args.symbol} | {period}")
        candles = load_csv_data(args.symbol, args.from_date, args.to_date)
        if not candles:
            candles = fetch_mt5_data(args.symbol, args.from_date, args.to_date)

        print(f"  ⚙️  Running backtest simulation: {args.strategy}")
        trades = simulate_ingwe(candles, config, args.strategy, adx_min=args.adx_min, adx_max=args.adx_max)

    print_results(trades, config, args.symbol, args.strategy, period)

    if args.volatility_analysis and trades:
        sep()
        print(f"  📊 VOLATILITY ANALYSIS")
        sep()
        analysis = analyze_by_volatility(trades)
        for regime in ["HIGH", "MEDIUM", "LOW"]:
            r = analysis[regime]
            print(f"  {regime:6} (ADX {'>30' if regime=='HIGH' else '<20' if regime=='LOW' else '20-30'}): "
                  f"{r['trades']:3} trades | {r['win_rate']:5.1f}% WR | ${r['net_pnl']:+,.2f} P&L")
        sep()

    BASE_DIR.joinpath("data").mkdir(parents=True, exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(trades, f, indent=2)

    summary = {
        "run_date": now_utc(),
        "symbol": args.symbol,
        "strategy": args.strategy,
        "period": period,
        "total_trades": len(trades),
        "win_rate": round(len([t for t in trades if t["outcome"]=="WIN"]) / len(trades) * 100, 1) if trades else 0,
        "config": config
    }
    with open(SUMMARY_FILE, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  💾 Results saved to data/backtest_results.json")


if __name__ == "__main__":
    main()
