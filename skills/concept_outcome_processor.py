"""
Concept Outcome Processor
Processes closed trades and updates concept performance tracking
"""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List, Dict
from collections import defaultdict

import MetaTrader5 as mt5


BASE_DIR = Path(__file__).parent.parent
CONCEPT_STATS_FILE = BASE_DIR / "data" / "concept_performance.json"


class ConceptOutcomeProcessor:
    def __init__(self, stats_file: str = None):
        self.stats_file = Path(stats_file) if stats_file else CONCEPT_STATS_FILE
        self.stats = self._load_stats()
        self._mt5_initialized = False
    
    def _init_mt5(self):
        if not self._mt5_initialized:
            mt5.initialize()
            self._mt5_initialized = True
    
    def _load_stats(self) -> Dict:
        """Load existing concept statistics"""
        if self.stats_file.exists():
            try:
                with open(self.stats_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, KeyError):
                pass
        return {"concepts": {}, "trades": []}
    
    def _save_stats(self):
        """Save concept statistics"""
        self.stats_file.parent.mkdir(exist_ok=True)
        tmp = str(self.stats_file) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.stats, f, indent=2)
        Path(tmp).replace(self.stats_file)
    
    def get_pending_trades(self) -> List[Dict]:
        """Get trades that need outcome recorded"""
        trades = self.stats.get("trades", [])
        pending = []
        
        for trade in trades:
            if trade.get("outcome") is None:
                pending.append(trade)
        
        return pending
    
    def fetch_outcome_from_mt5(self, trade_id: str) -> Optional[Dict]:
        """Fetch trade outcome from MT5 by trade_id (ticket)"""
        self._init_mt5()
        
        try:
            ticket = int(trade_id)
            deals = mt5.history_deals_get(ticket=ticket)
            
            if not deals:
                return None
            
            deal = deals[0]
            profit = deal.profit
            direction = "BUY" if deal.type in (0, 1) else "SELL"
            
            deal_data = {
                "ticket": deal.ticket,
                "profit": profit,
                "direction": direction,
                "entry": deal.price_open,
                "exit": deal.price,
                "volume": deal.volume,
                "time": deal.time,
                "magic": deal.magic
            }
            
            return deal_data
        
        except (ValueError, IndexError, AttributeError):
            return None
    
    def process_pending_trades(self) -> Dict:
        """Process all pending trades and update outcomes"""
        pending = self.get_pending_trades()
        processed = []
        failed = []
        
        for trade in pending:
            trade_id = trade.get("trade_id")
            
            outcome = self.fetch_outcome_from_mt5(trade_id)
            
            if outcome is None:
                failed.append(trade_id)
                continue
            
            profit = outcome.get("profit", 0)
            outcome_str = "win" if profit > 0 else "loss"
            
            entry = float(trade.get("entry_req", 0))
            sl = float(trade.get("sl", 0))
            
            if entry > 0 and sl > 0:
                if trade.get("direction") == "BUY":
                    rr = (profit / 100) / abs(entry - sl)
                else:
                    rr = (profit / 100) / abs(entry - sl)
            else:
                rr = 0
            
            trade["outcome"] = outcome_str
            trade["rr_result"] = rr
            trade["pnl"] = profit
            
            concepts = trade.get("concepts_used", [])
            
            for concept in concepts:
                if concept not in self.stats["concepts"]:
                    self.stats["concepts"][concept] = {
                        "uses": 0,
                        "wins": 0,
                        "losses": 0,
                        "total_rr": 0.0,
                        "win_rr_sum": 0.0,
                        "loss_rr_sum": 0.0
                    }
                
                stats = self.stats["concepts"][concept]
                stats["uses"] += 1
                
                if outcome_str == "win":
                    stats["wins"] += 1
                    stats["win_rr_sum"] += rr
                else:
                    stats["losses"] += 1
                    stats["loss_rr_sum"] += abs(rr)
                
                stats["total_rr"] += rr
            
            processed.append(trade_id)
        
        self._save_stats()
        
        return {
            "processed": processed,
            "failed": failed,
            "total_pending": len(pending)
        }
    
    def get_concept_report(self, min_uses: int = 3) -> List[Dict]:
        """Get performance report for all concepts"""
        report = []
        
        for concept, stats in self.stats.get("concepts", {}).items():
            uses = stats.get("uses", 0)
            if uses < min_uses:
                continue
            
            wins = stats.get("wins", 0)
            win_rate = wins / uses if uses > 0 else 0
            avg_rr = stats.get("total_rr", 0) / uses
            avg_win_rr = stats.get("win_rr_sum", 0) / wins if wins > 0 else 0
            avg_loss_rr = stats.get("loss_rr_sum", 0) / stats.get("losses", 1)
            
            report.append({
                "concept": concept,
                "uses": uses,
                "wins": wins,
                "losses": uses - wins,
                "win_rate": round(win_rate, 2),
                "avg_rr": round(avg_rr, 2),
                "avg_win_rr": round(avg_win_rr, 2),
                "avg_loss_rr": round(avg_loss_rr, 2)
            })
        
        report.sort(key=lambda x: (x["win_rate"], x["avg_rr"]), reverse=True)
        
        return report


def process_closed_concept_trades():
    """Process pending trades after they close"""
    processor = ConceptOutcomeProcessor()
    result = processor.process_pending_trades()
    
    print(f"Processed: {len(result['processed'])} trades")
    print(f"Pending: {result['total_pending']} trades")
    
    if result["processed"]:
        print("\n=== Concept Performance ===")
        for item in processor.get_concept_report():
            print(f"  {item['concept']}: {item['win_rate']:.0%} WR, "
                  f"{item['avg_rr']:.2f} RR, {item['uses']} uses")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--process":
        process_closed_concept_trades()
    else:
        processor = ConceptOutcomeProcessor()
        
        print("=== Concept Outcome Processor ===")
        
        report = processor.get_concept_report()
        if report:
            print(f"\n=== Top Performing Concepts ===")
            for item in report[:10]:
                print(f"  {item['concept']}: {item['win_rate']:.0%} WR, "
                      f"{item['avg_rr']:.2f} RR, {item['uses']} uses")
        else:
            print("No concept data yet.")