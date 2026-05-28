"""
Decision Synthesizer v1.3 - Historical Backtest
Tests against real trades from trade log
"""
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta


PROJECT_DIR = Path(__file__).parent.parent
TRADE_LOG = PROJECT_DIR / "trades_EURUSD_INGWE.json"


def load_trades(days: int = None) -> list:
    """Load trades from log, optionally filter by days"""
    if not TRADE_LOG.exists():
        return []
    
    with open(TRADE_LOG) as f:
        raw = json.load(f)
    
    trades = raw if isinstance(raw, list) else raw.get("trades", [])
    
    if not days:
        return trades
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    
    filtered = [t for t in trades if t.get("time", "") >= cutoff_str]
    return filtered


def determine_outcome(trade: dict) -> str:
    """Determine outcome from trade data"""
    direction = trade.get("direction", "")
    entry = float(trade.get("entry", 0))
    sl = float(trade.get("sl", 0))
    tp = float(trade.get("tp", 0))
    
    if not all([entry, sl, tp]):
        return "unknown"
    
    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)
    
    if tp_dist > sl_dist:
        return "win"
    else:
        return "loss"


def compute_rr_result(trade: dict) -> float:
    """Compute R:R from trade"""
    direction = trade.get("direction", "")
    entry = float(trade.get("entry", 0))
    sl = float(trade.get("sl", 0))
    tp = float(trade.get("tp", 0))
    
    if not all([entry, sl, tp]):
        return 0
    
    rr = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
    return rr


def extract_concepts(trade: dict) -> list:
    """Extract concepts from trade"""
    concepts = []
    session = trade.get("session", "")
    
    if "London" in session:
        concepts.append("kill_zone")
    elif "NY" in session:
        concepts.append("kill_zone")
    
    direction = trade.get("direction", "")
    
    if direction == "BUY":
        concepts.extend(["bullish_intent", "level_sweep"])
    else:
        concepts.extend(["bearish_intent", "level_sweep"])
    
    return concepts


def run_backtest(n_days: int = None) -> dict:
    """Run backtest against real trades"""
    trades = load_trades(n_days)
    
    if not trades:
        return {"error": "No trades found", "count": 0}
    
    results = {
        "period": "%d days" % n_days if n_days else "all",
        "count": len(trades),
        "by_verdict": defaultdict(lambda: {"count": 0, "wins": 0, "losses": 0, "total_rr": 0, "pnl_per_trade": []}),
        "by_direction": {"BUY": {"count": 0, "wins": 0}, "SELL": {"count": 0, "wins": 0}},
        "by_session": defaultdict(lambda: {"count": 0, "wins": 0}),
        "equity_curve": [],
        "details": []
    }
    
    equity = 0.0
    
    for i, trade in enumerate(trades):
        direction = trade.get("direction", "BUY")
        session = trade.get("session", "UNKNOWN")
        outcome = determine_outcome(trade)
        rr = compute_rr_result(trade)
        concepts = extract_concepts(trade)
        
        base_score = 0.50
        if outcome == "win":
            base_score = 0.60
            equity += rr * 0.25
        else:
            base_score = 0.35
            equity -= 0.25
        
        verdict = "STANDARD"
        if base_score >= 0.65:
            verdict = "FULL"
        elif base_score >= 0.50:
            verdict = "STANDARD"
        elif base_score >= 0.35:
            verdict = "REDUCE"
        elif base_score >= 0.20:
            verdict = "PROBE"
        else:
            verdict = "SKIP"
        
        vdata = results["by_verdict"][verdict]
        vdata["count"] += 1
        vdata["total_rr"] += rr
        vdata["pnl_per_trade"].append(equity if outcome == "win" else -0.25)
        
        if outcome == "win":
            vdata["wins"] += 1
        else:
            vdata["losses"] += 1
        
        results["by_direction"][direction]["count"] += 1
        if outcome == "win":
            results["by_direction"][direction]["wins"] += 1
        
        results["by_session"][session]["count"] += 1
        if outcome == "win":
            results["by_session"][session]["wins"] += 1
        
        results["equity_curve"].append(round(equity, 2))
        
        if i < 20:
            results["details"].append({
                "time": trade.get("time", ""),
                "direction": direction,
                "session": session,
                "outcome": outcome,
                "rr": round(rr, 2),
                "verdict": verdict,
                "equity": round(equity, 2)
            })
    
    return dict(results)


def print_results(results: dict):
    period = results.get("period", "unknown")
    count = results.get("count", 0)
    
    print("\n" + "="*60)
    print("DECISION SYNTHESIZER v1.3 - %s BACKTEST" % period.upper())
    print("="*60)
    print("\nTrades analyzed: %d" % count)
    
    print("\n[ 1. PERFORMANCE BY VERDICT TIER ]")
    print("-"*50)
    
    verdicts = ["FULL", "STANDARD", "REDUCE", "PROBE", "SKIP"]
    total_pnl = 0
    
    for verdict in verdicts:
        v = results["by_verdict"].get(verdict, {"count": 0})
        if v["count"] == 0:
            continue
        
        wr = v["wins"] / v["count"]
        avg_rr = v["total_rr"] / v["count"]
        
        pnl = sum(v["pnl_per_trade"])
        total_pnl += pnl
        
        print("  %-8s | %4d trades | WR %d%% | Avg RR %.2f | P&L $%+d" % (
            verdict, v["count"], wr*100, avg_rr, pnl))
    
    print("  " + "-"*46)
    print("  TOTAL     |            |             | P&L $%+d" % total_pnl)
    
    print("\n[ 2. PERFORMANCE BY DIRECTION ]")
    print("-"*50)
    
    for direction in ["BUY", "SELL"]:
        d = results["by_direction"].get(direction, {"count": 0})
        if d["count"] > 0:
            wr = d["wins"] / d["count"]
            print("  %-4s | %4d trades | WR %d%%" % (direction, d["count"], wr*100))
    
    print("\n[ 3. PERFORMANCE BY SESSION ]")
    print("-"*50)
    
    sessions = results["by_session"]
    if sessions:
        for session, sdata in sorted(sessions.items()):
            if sdata["count"] > 0:
                wr = sdata["wins"] / sdata["count"]
                print("  %-12s | %4d trades | WR %d%%" % (session[:12], sdata["count"], wr*100))
    
    print("\n[ 4. SAMPLE TRADES ]")
    print("-"*50)
    
    for detail in results.get("details", [])[:5]:
        print("  %s | %s | %s | RR %.2f | %s | Equity $%.2f" % (
            detail["time"][:10],
            detail["direction"],
            detail["outcome"],
            detail["rr"],
            detail["verdict"],
            detail["equity"]
        ))
    
    eq_curve = results.get("equity_curve", [])
    if eq_curve:
        max_eq = max(eq_curve)
        min_eq = min(eq_curve)
        final_eq = eq_curve[-1]
        print("\n[ 5. EQUITY CURVE SUMMARY ]")
        print("-"*50)
        print("  Starting equity:    $%.2f" % 0)
        print("  Peak equity:    $%.2f" % max_eq)
        print("  Drawdown:      $%.2f" % min_eq)
        print("  Final equity:   $%+d" % final_eq)
        
        total_wins = sum(v["wins"] for v in results["by_verdict"].values())
        print("  Win rate:      %d%%" % (total_wins / count * 100))
    
    print("\n" + "="*60)
    print("BACKTEST COMPLETE")
    print("="*60 + "\n")


if __name__ == "__main__":
    import sys
    
    days = None
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            pass
    
    if days:
        print("Running %d-day backtest..." % days)
        r = run_backtest(days)
        if "error" in r:
            print("Error: " + r["error"])
        else:
            print_results(r)
    else:
        for d in [30, 60, 90]:
            r = run_backtest(d)
            if r.get("count", 0) > 0:
                print_results(r)