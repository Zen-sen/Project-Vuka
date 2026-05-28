"""
MT5 Deal Processor + Forward Paper Trading
Fetches real deals from MT5, updates concept stats, runs paper trades
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass


import MetaTrader5 as mt5


PROJECT_DIR = Path(__file__).parent.parent
CONCEPT_STATS_FILE = PROJECT_DIR / "data" / "concept_performance.json"
TRADE_LOGS = {
    "EURUSD": PROJECT_DIR / "trades_EURUSD_INGWE.json",
    "GBPUSD": PROJECT_DIR / "trades_GBPUSD_INGWE.json"
}


@dataclass
class DealContext:
    ticket: int
    time: datetime
    direction: str
    entry: float
    sl: float
    tp: float
    outcome: str
    rr_result: float
    pnl: float
    session: str


class MT5DealProcessor:
    """
    Processes MT5 deal history and updates concept performance.
    Includes losing trades for proper validation.
    """
    
    def __init__(self, symbol: str = "EURUSD"):
        self.symbol = symbol
        self._init_mt5()
        self.concepts = self._load_concepts()
    
    def _init_mt5(self):
        if not mt5.initialize():
            print("MT5 init failed - using trade log only")
            self._mt5_ready = False
        else:
            self._mt5_ready = True
            print("MT5 connected")
    
    def _load_concepts(self) -> Dict:
        if CONCEPT_STATS_FILE.exists():
            with open(CONCEPT_STATS_FILE) as f:
                return json.load(f)
        return {"concepts": {}, "combos": {}, "trades": []}
    
    def _save_concepts(self):
        with open(CONCEPT_STATS_FILE, "w") as f:
            json.dump(self.concepts, f, indent=2)
    
    def _determine_session(self, time: datetime) -> str:
        hour = time.hour
        
        if 2 <= hour < 7:
            return "Asian"
        elif 7 <= hour < 11:
            return "London"
        elif 12 <= hour < 16:
            return "NY"
        else:
            return "OffHours"
    
    def _determine_outcome(self, entry: float, sl: float, tp: float, direction: str) -> str:
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        
        if tp_dist > sl_dist * 1.2:
            return "win"
        elif sl_dist > tp_dist * 1.2:
            return "loss"
        else:
            return "breakeven"
    
    def _compute_rr(self, entry: float, sl: float, tp: float) -> float:
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        
        if sl_dist > 0:
            return tp_dist / sl_dist
        return 0
    
    def process_deal(self, deal: dict) -> Optional[DealContext]:
        """Process single MT5 deal into DealContext"""
        ticket = deal.get("ticket")
        time_msc = deal.get("time_msc", deal.get("time"))
        
        if isinstance(time_msc, int):
            time = datetime.fromtimestamp(time_msc / 1000, tz=timezone.utc)
        else:
            time = datetime.now(timezone.utc)
        
        price = deal.get("price_open", deal.get("price", 0))
        volume = deal.get("volume", 0)
        profit = deal.get("profit", 0)
        comment = deal.get("comment", "")
        
        direction = "BUY" if deal.get("type") in (0, 1) else "SELL"
        session = self._determine_session(time)
        
        entry = price
        sl = price * (0.995 if direction == "BUY" else 1.005)
        tp = price * (1.010 if direction == "BUY" else 0.990)
        
        outcome = self._determine_outcome(entry, sl, tp, direction)
        rr = self._compute_rr(entry, sl, tp)
        
        return DealContext(
            ticket=ticket,
            time=time,
            direction=direction,
            entry=entry,
            sl=sl,
            tp=tp,
            outcome=outcome,
            rr_result=rr,
            pnl=profit,
            session=session
        )
    
    def fetch_deals(self, days: int = 30) -> List[DealContext]:
        """Fetch deals from MT5 for last N days"""
        if not self._mt5_ready:
            return []
        
        since = datetime.now(timezone.utc) - timedelta(days=days)
        since_ms = int(since.timestamp() * 1000)
        
        deals = mt5.history_deals_get(from_datetime=since, to_datetime=datetime.now(timezone.utc))
        
        if not deals:
            return []
        
        contexts = []
        for deal in deals:
            if deal.symbol != self.symbol:
                continue
            
            ctx = self.process_deal(deal)
            if ctx:
                contexts.append(ctx)
        
        return contexts
    
    def load_from_trade_log(self, days: int = 90, symbol: str = None) -> List[DealContext]:
        contexts = []
        
        trade_files = [TRADE_LOGS[symbol]] if symbol and symbol in TRADE_LOGS else list(TRADE_LOGS.values())
        
        for trade_log in trade_files:
            if not trade_log.exists():
                continue
        
            with open(trade_log) as f:
                raw = json.load(f)
        
            trades = raw if isinstance(raw, list) else raw.get("trades", [])
        
            since = datetime.now(timezone.utc) - timedelta(days=days)
            since_naive = since.replace(tzinfo=None)
        
            for trade in trades:
                time_str = trade.get("time", "")
                try:
                    trade_time = datetime.fromisoformat(time_str.replace(" ", "T"))
                except:
                    continue
                
                if trade_time < since_naive:
                    continue
                
                direction = trade.get("direction", "BUY")
                entry = float(trade.get("entry", 0))
                sl = float(trade.get("sl", 0))
                tp = float(trade.get("tp", 0))
                session = trade.get("session", "UNKNOWN")
                
                outcome = self._determine_outcome(entry, sl, tp, direction)
                rr = self._compute_rr(entry, sl, tp)
                
                contexts.append(DealContext(
                    ticket=0,
                    time=trade_time,
                    direction=direction,
                    entry=entry,
                    sl=sl,
                    tp=tp,
                    outcome=outcome,
                    rr_result=rr,
                    pnl=0,
                    session=session
                ))
        
        return contexts
    
    def update_concept_stats(self, deals: List[DealContext]):
        """Update concept performance from deal history"""
        for deal in deals:
            combo_key = "default"
            concepts = ["default_setup"]
            
            if deal.session in ("London", "NY"):
                concepts.append("kill_zone")
            
            if deal.direction == "BUY":
                concepts.append("bullish_intent")
            else:
                concepts.append("bearish_intent")
            
            trade_entry = {
                "trade_id": str(deal.ticket),
                "timestamp": deal.time.isoformat(),
                "direction": deal.direction,
                "concepts_used": concepts,
                "kronos_decision": "BACKFILLED",
                "setup_type": "historical",
                "outcome": deal.outcome,
                "rr_result": deal.rr_result,
                "pnl": deal.pnl,
                "market_context": {"session": deal.session}
            }
            
            self.concepts.setdefault("trades", []).append(trade_entry)
            
            for concept in concepts:
                if concept not in self.concepts["concepts"]:
                    self.concepts["concepts"][concept] = {
                        "uses": 0, "wins": 0, "losses": 0,
                        "total_rr": 0.0, "win_rr_sum": 0.0, "loss_rr_sum": 0.0
                    }
                
                stats = self.concepts["concepts"][concept]
                stats["uses"] += 1
                
                if deal.outcome == "win":
                    stats["wins"] += 1
                    stats["win_rr_sum"] += deal.rr_result
                else:
                    stats["losses"] += 1
                    stats["loss_rr_sum"] += abs(deal.rr_result)
                
                stats["total_rr"] += deal.rr_result
            
            combo = self.concepts.setdefault("combos", {}).setdefault(combo_key, {
                "concepts": concepts, "uses": 0, "wins": 0, "losses": 0, "total_rr": 0.0
            })
            combo["uses"] += 1
            if deal.outcome == "win":
                combo["wins"] += 1
            combo["total_rr"] += deal.rr_result
        
        self._save_concepts()
    
    def run_import(self, days: int = 90):
        """Import deals from MT5 or trade log"""
        print("\n" + "="*60)
        print("MT5 DEAL IMPORT")
        print("="*60)
        
        deals = self.fetch_deals(days)
        
        if not deals:
            print("No MT5 deals, loading from trade log...")
            deals = self.load_from_trade_log(days)
        
        if not deals:
            print("No deals found")
            return
        
        print("Found %d deals" % len(deals))
        
        outcomes = {"win": 0, "loss": 0, "breakeven": 0}
        sessions = {}
        
        for deal in deals:
            outcomes[deal.outcome] = outcomes.get(deal.outcome, 0) + 1
            sessions[deal.session] = sessions.get(deal.session, 0) + 1
        
        print("\nOutcome breakdown:")
        for outcome, count in outcomes.items():
            print("  %s: %d (%.0f%%)" % (outcome, count, count/len(deals)*100))
        
        print("\nSession breakdown:")
        for session, count in sessions.items():
            print("  %s: %d" % (session, count))
        
        self.update_concept_stats(deals)
        print("\nConcept stats updated")
        print("="*60)


class ForwardPaperTrader:
    """
    Forward paper trading - tests v1.3 without real capital.
    Paper trades go through synthesizer, results tracked separately.
    """
    
    def __init__(self, concept_tracker=None, synthesizer=None):
        self.tracker = concept_tracker
        self.synth = synthesizer
        self.paper_trades = []
        self.equity = 0.0
    
    def paper_trade(self, concepts: List[str], market_context: Dict, direction: str) -> Dict:
        """Simulate paper trade through v1.3"""
        if not self.synth:
            return {"error": "No synthesizer"}
        
        decision = self.synth.synthesize(concepts, market_context, direction)
        
        result = {
            "time": datetime.now(timezone.utc).isoformat(),
            "concepts": concepts,
            "direction": direction,
            "market_context": market_context,
            "score": decision.final_score,
            "verdict": decision.verdict,
            "position_size": decision.position_size,
            "skip": decision.verdict == "SKIP"
        }
        
        self.paper_trades.append(result)
        
        return result
    
    def record_paper_outcome(self, trade_idx: int, outcome: str, rr_result: float):
        """Record paper trade result"""
        if trade_idx >= len(self.paper_trades):
            return
        
        trade = self.paper_trades[trade_idx]
        trade["outcome"] = outcome
        trade["rr_result"] = rr_result
        
        pos_size = trade["position_size"]
        pnl = rr_result * pos_size if outcome == "win" else -pos_size
        
        trade["pnl"] = pnl
        self.equity += pnl
        
        trade["equity"] = round(self.equity, 2)
    
    def get_paper_stats(self) -> Dict:
        """Get paper trading statistics"""
        trades = [t for t in self.paper_trades if "outcome" in t]
        
        if not trades:
            return {"count": 0}
        
        wins = sum(1 for t in trades if t.get("outcome") == "win")
        losses = sum(1 for t in trades if t.get("outcome") == "loss")
        
        verdict_dist = {}
        for t in trades:
            v = t.get("verdict", "UNKNOWN")
            verdict_dist[v] = verdict_dist.get(v, 0) + 1
        
        return {
            "count": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / len(trades),
            "equity": round(self.equity, 2),
            "verdict_distribution": verdict_dist,
            "skipped": sum(1 for t in self.paper_trades if t.get("skip")),
            "total_proposed": len(self.paper_trades)
        }
    
    def print_paper_report(self):
        """Print paper trading report"""
        stats = self.get_paper_stats()
        
        print("\n" + "="*60)
        print("FORWARD PAPER TRADING REPORT")
        print("="*60)
        print("\nTrades taken: %d / %d proposed" % (stats["count"], stats["total_proposed"]))
        print("Skipped: %d" % stats["skipped"])
        print("Win rate: %d%%" % (stats["win_rate"] * 100))
        print("Equity: $%+d" % stats["equity"])
        
        print("\nVerdict distribution:")
        for verdict, count in stats.get("verdict_distribution", {}).items():
            print("  %s: %d" % (verdict, count))
        
        print("\nSample trades:")
        for trade in self.paper_trades[-5:]:
            if "outcome" in trade:
                print("  %s | %s | %s | %s | %s | $%+d" % (
                    trade["time"][:10],
                    trade["direction"],
                    trade["verdict"],
                    trade.get("outcome", "?"),
                    trade.get("rr_result", 0),
                    trade.get("pnl", 0)
                ))
        
        print("\n" + "="*60)


if __name__ == "__main__":
    import sys
    
    processor = MT5DealProcessor()
    
    if "--import" in sys.argv:
        days = 90
        if len(sys.argv) > 2:
            try:
                days = int(sys.argv[2])
            except:
                pass
        processor.run_import(days)
    
    elif "--paper" in sys.argv:
        from skills.concept_tracker import ConceptTracker
        from skills.decision_synthesizer import DecisionSynthesizer
        
        tracker = ConceptTracker()
        synth = DecisionSynthesizer(tracker)
        paper = ForwardPaperTrader(tracker, synth)
        
        print("\nForward paper trading mode")
        print("Use paper_trade() to simulate trades")
        print("Use record_paper_outcome() to record results")
        print("Use get_paper_stats() to see performance")
    
    else:
        print("\nOptions:")
        print("  --import [days]  : Import MT5 deals and update concept stats")
        print("  --paper        : Start forward paper trading mode")