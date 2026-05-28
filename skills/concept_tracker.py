"""
Concept Performance Tracker (v2.1)
Tracks trade outcomes attributed to ICT concepts with context-aware learning
Includes: session breakdown, volatility filtering, combo tracking, expectancy, confidence tiers
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any


DATA_DIR = Path("data")
CONCEPT_STATS_FILE = DATA_DIR / "concept_performance.json"

MIN_SAMPLES = 30
DEFAULT_WEIGHT = 0.5
WIN_RATE_WEIGHT = 0.7
RECENT_WEIGHT = 0.3

CONFIDENCE_TIERS = {
    "low": 30,
    "medium": 100,
    "high": 200
}


def get_confidence_tier(uses: int) -> str:
    if uses >= 200:
        return "high"
    if uses >= 100:
        return "medium"
    if uses >= 30:
        return "low"
    return "insufficient"


def get_position_size_multiplier(confidence: str, win_rate: float) -> float:
    multipliers = {
        "high": 1.0,
        "medium": 0.75,
        "low": 0.5,
        "insufficient": 0.25
    }
    return multipliers.get(confidence, 0.25)


class ConceptTracker:
    def __init__(self, stats_file: str = None):
        self.stats_file = Path(stats_file) if stats_file else CONCEPT_STATS_FILE
        self.stats = self._load_stats()
    
    def _load_stats(self) -> Dict:
        if self.stats_file.exists():
            try:
                with open(self.stats_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, KeyError):
                pass
        return {"concepts": {}, "combos": {}, "trades": []}
    
    def _save_stats(self):
        DATA_DIR.mkdir(exist_ok=True)
        tmp = str(self.stats_file) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.stats, f, indent=2)
        Path(tmp).replace(self.stats_file)
    
    def _extract_combo_key(self, concepts: List[str]) -> str:
        return "+".join(sorted(set(concepts)))
    
    def record_trade(
        self,
        trade_id: str,
        direction: str,
        concepts_used: List[str],
        kronos_decision: str,
        setup_type: str = "UNKNOWN"
    ):
        trade_entry = {
            "trade_id": trade_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "concepts_used": concepts_used,
            "kronos_decision": kronos_decision,
            "setup_type": setup_type,
            "outcome": None,
            "rr_result": None,
            "market_context": None
        }
        
        self.stats.setdefault("trades", []).append(trade_entry)
        self._save_stats()
    
    def record_outcome(
        self,
        trade_id: str,
        outcome: str,
        rr_result: float,
        pnl: Optional[float] = None,
        market_context: Optional[Dict] = None
    ):
        """
        Record trade outcome with market context for conditional learning
        """
        trades = self.stats.get("trades", [])
        
        for trade in trades:
            if trade.get("trade_id") == trade_id:
                trade["outcome"] = outcome
                trade["rr_result"] = rr_result
                trade["pnl"] = pnl
                trade["market_context"] = market_context or {}
                
                concepts = trade.get("concepts_used", [])
                combo_key = self._extract_combo_key(concepts)
                mc = market_context or {}
                session = mc.get("session", "unknown")
                volatility = mc.get("volatility", "normal")
                
                self._update_concept_stats(concepts, outcome, rr_result, session, volatility)
                self._update_combo_stats(combo_key, concepts, outcome, rr_result)
                
                break
        
        self._save_stats()
    
    def _update_concept_stats(
        self,
        concepts: List[str],
        outcome: str,
        rr_result: float,
        session: str,
        volatility: str
    ):
        for concept in concepts:
            if concept not in self.stats["concepts"]:
                self.stats["concepts"][concept] = {
                    "uses": 0, "wins": 0, "losses": 0,
                    "total_rr": 0.0,
                    "win_rr_sum": 0.0, "loss_rr_sum": 0.0,
                    "session_stats": {},
                    "volatility_stats": {},
                    "recent_wins": 0,
                    "recent_losses": 0,
                    "recent_rr_sum": 0.0
                }
            
            stats = self.stats["concepts"][concept]
            stats["uses"] += 1
            
            if outcome == "win":
                stats["wins"] += 1
                stats["win_rr_sum"] += rr_result
                stats["recent_wins"] += 1
                stats["recent_rr_sum"] += rr_result
            else:
                stats["losses"] += 1
                stats["loss_rr_sum"] += abs(rr_result)
                stats["recent_losses"] += 1
                stats["recent_rr_sum"] -= abs(rr_result)
            
            stats["total_rr"] += rr_result
            
            ss = stats.setdefault("session_stats", {}).setdefault(session, {"uses": 0, "wins": 0, "total_rr": 0.0})
            ss["uses"] += 1
            if outcome == "win":
                ss["wins"] += 1
            ss["total_rr"] += rr_result
            
            vs = stats.setdefault("volatility_stats", {}).setdefault(volatility, {"uses": 0, "wins": 0, "total_rr": 0.0})
            vs["uses"] += 1
            if outcome == "win":
                vs["wins"] += 1
            vs["total_rr"] += rr_result
    
    def _update_combo_stats(self, combo_key: str, concepts: List[str], outcome: str, rr_result: float):
        if combo_key not in self.stats["combos"]:
            self.stats["combos"][combo_key] = {
                "concepts": concepts,
                "uses": 0, "wins": 0, "losses": 0,
                "total_rr": 0.0
            }
        
        combo = self.stats["combos"][combo_key]
        combo["uses"] += 1
        
        if outcome == "win":
            combo["wins"] += 1
        else:
            combo["losses"] += 1
        
        combo["total_rr"] += rr_result
    
    def get_expectancy(self, concept: str) -> float:
        """Calculate expectancy: (WR × AvgWinRR) - (LR × AvgLossRR)"""
        stats = self.stats.get("concepts", {}).get(concept)
        if not stats:
            return 0.0
        
        uses = stats.get("uses", 0)
        if uses < MIN_SAMPLES:
            return 0.0
        
        wins = stats.get("wins", 0)
        losses = uses - wins
        
        avg_win_rr = stats.get("win_rr_sum", 0) / wins if wins > 0 else 0
        avg_loss_rr = stats.get("loss_rr_sum", 0) / losses if losses > 0 else 1.0
        
        win_rate = wins / uses
        loss_rate = losses / uses
        
        expectancy = (win_rate * avg_win_rr) - (loss_rate * avg_loss_rr)
        return round(expectancy, 3)
    
    def get_combo_expectancy(self, combo_key: str) -> float:
        """Calculate combo expectancy"""
        combo = self.stats.get("combos", {}).get(combo_key)
        if not combo:
            return 0.0
        
        uses = combo.get("uses", 0)
        if uses < MIN_SAMPLES:
            return 0.0
        
        wins = combo.get("wins", 0)
        losses = uses - wins
        
        avg_win_rr = combo.get("total_rr", 0) / wins if wins > 0 else 0
        avg_loss_rr = abs(combo.get("total_rr", 0) / losses) if losses > 0 else 1.0
        
        win_rate = wins / uses
        expectancy = (win_rate * avg_win_rr) - ((1 - win_rate) * avg_loss_rr)
        return round(expectancy, 3)
    
    def get_confidence_tier(self, concept: str) -> str:
        """Get statistical confidence: low/medium/high based on sample size"""
        stats = self.stats.get("concepts", {}).get(concept)
        if not stats:
            return "insufficient"
        return get_confidence_tier(stats.get("uses", 0))
    
    def get_confidence_score(self, concept: str) -> float:
        """Get 0-1 confidence score with sample protection"""
        stats = self.stats.get("concepts", {}).get(concept)
        if not stats:
            return 0.25
        
        uses = stats.get("uses", 0)
        if uses < 10:
            return 0.25
        
        wins = stats.get("wins", 0)
        win_rate = wins / uses
        confidence = get_confidence_tier(uses)
        
        conf_values = {"insufficient": 0.25, "low": 0.5, "medium": 0.75, "high": 1.0}
        base_conf = conf_values.get(confidence, 0.25)
        
        win_rate_bonus = min(win_rate * 0.2, 0.2)
        return min(base_conf + win_rate_bonus, 1.0)
    
    def get_position_size(self, concept: str) -> float:
        """Get position size multiplier (0-1) based on confidence + expectancy"""
        stats = self.stats.get("concepts", {}).get(concept)
        if not stats:
            return 0.25
        
        exp = self.get_expectancy(concept)
        conf_score = self.get_confidence_score(concept)
        
        if exp > 0.5 and conf_score > 0.7:
            return get_position_size_multiplier("high", conf_score)
        elif exp > 0.2 and conf_score > 0.5:
            return get_position_size_multiplier("medium", conf_score)
        elif exp > 0:
            return get_position_size_multiplier("low", conf_score)
        else:
            return 0.25
    
    def get_weighted_score(self, concept: str) -> float:
        stats = self.stats.get("concepts", {}).get(concept)
        if not stats:
            return DEFAULT_WEIGHT
        
        uses = stats.get("uses", 0)
        if uses < MIN_SAMPLES:
            return DEFAULT_WEIGHT
        
        wins = stats.get("wins", 0)
        win_rate = wins / uses
        
        recent_uses = stats.get("recent_wins", 0) + stats.get("recent_losses", 0)
        if recent_uses > 0:
            recent_win_rate = stats.get("recent_wins", 0) / recent_uses
        else:
            recent_win_rate = win_rate
        
        score = (win_rate * WIN_RATE_WEIGHT) + (recent_win_rate * RECENT_WEIGHT)
        return round(score, 3)
    
    def get_session_weighted_score(self, concept: str, session: str) -> float:
        stats = self.stats.get("concepts", {}).get(concept)
        if not stats:
            return DEFAULT_WEIGHT
        
        ss = stats.get("session_stats", {}).get(session)
        if not ss or ss.get("uses", 0) < 5:
            return self.get_weighted_score(concept)
        
        return ss.get("wins", 0) / ss.get("uses", 1)
    
    def get_volatility_filtered_score(self, concept: str, volatility: str) -> float:
        stats = self.stats.get("concepts", {}).get(concept)
        if not stats:
            return DEFAULT_WEIGHT
        
        vs = stats.get("volatility_stats", {}).get(volatility)
        if not vs or vs.get("uses", 0) < 5:
            return self.get_weighted_score(concept)
        
        return vs.get("wins", 0) / vs.get("uses", 1)
    
    def get_concept_stats(self, concept: str) -> Optional[Dict]:
        return self.stats.get("concepts", {}).get(concept)
    
    def get_all_stats(self) -> Dict:
        return self.stats.get("concepts", {})
    
    def get_combo_stats(self) -> Dict:
        return self.stats.get("combos", {})
    
    def get_weighted_context(self, system_state: Dict) -> str:
        concepts = system_state.get("concepts_used", [])
        mc = system_state.get("market_context", {})
        session = mc.get("session", "unknown")
        volatility = mc.get("volatility", "normal")
        
        if not concepts:
            return ""
        
        lines = ["=== CONCEPT PERFORMANCE (CONTEXT-AWARE) ==="]
        
        combo_key = self._extract_combo_key(concepts)
        combo_stats = self.stats.get("combos", {}).get(combo_key)
        
        if combo_stats and combo_stats.get("uses", 0) >= 5:
            wr = combo_stats["wins"] / combo_stats["uses"]
            avg_rr = combo_stats["total_rr"] / combo_stats["uses"]
            lines.append(f"[COMBO: {combo_key}] {combo_stats['uses']} uses, {wr:.0%} WR, avg RR {avg_rr:.1f}")
        else:
            lines.append(f"[COMBO: {combo_key}] - insufficient data")
        
        for concept in concepts:
            stats = self.stats.get("concepts", {}).get(concept)
            if stats:
                uses = stats.get("uses", 0)
                wins = stats.get("wins", 0)
                win_rate = wins / uses if uses > 0 else 0
                avg_rr = stats.get("total_rr", 0) / uses if uses > 0 else 0
                weighted = self.get_weighted_score(concept)
                session_wr = self.get_session_weighted_score(concept, session)
                volatility_wr = self.get_volatility_filtered_score(concept, volatility)
                
                lines.append(
                    f"{concept}: {uses} uses, {win_rate:.0%} raw, weighted {weighted:.0%}\n"
                    f"  session({session}): {session_wr:.0%}, volatility({volatility}): {volatility_wr:.0%}\n"
                    f"  avg RR: {avg_rr:.1f}"
                )
            else:
                lines.append(f"{concept}: no data yet (default weight)")
        
        lines.append("=== END PERFORMANCE ===")
        
        return "\n".join(lines)
    
    def get_top_concepts(self, min_uses: int = MIN_SAMPLES) -> List[tuple]:
        concept_stats = []
        
        for concept, stats in self.stats.get("concepts", {}).items():
            if stats.get("uses", 0) >= min_uses:
                weighted = self.get_weighted_score(concept)
                avg_rr = stats.get("total_rr", 0) / stats["uses"]
                concept_stats.append((concept, weighted, avg_rr, stats["uses"]))
        
        concept_stats.sort(key=lambda x: x[1], reverse=True)
        return concept_stats
    
    def get_bottom_concepts(self, min_uses: int = MIN_SAMPLES) -> List[tuple]:
        top = self.get_top_concepts(min_uses)
        return sorted(top, key=lambda x: x[1])
    
    def get_top_combos(self, min_uses: int = 10) -> List[tuple]:
        combo_list = []
        
        for combo_key, stats in self.stats.get("combos", {}).items():
            if stats.get("uses", 0) >= min_uses:
                wr = stats["wins"] / stats["uses"]
                avg_rr = stats["total_rr"] / stats["uses"]
                combo_list.append((combo_key, wr, avg_rr, stats["uses"]))
        
        combo_list.sort(key=lambda x: x[1], reverse=True)
        return combo_list
    
    def get_decision_context(self, system_state: Dict) -> Dict:
        """
        Get full decision context for Kronos - includes expectancy, confidence, position sizing
        """
        concepts = system_state.get("concepts_used", [])
        mc = system_state.get("market_context", {})
        session = mc.get("session", "unknown")
        volatility = mc.get("volatility", "normal")
        
        combo_key = self._extract_combo_key(concepts)
        
        context = {
            "concepts": concepts,
            "combo": combo_key,
            "combo_uses": 0,
            "combo_wr": 0.0,
            "combo_expectancy": 0.0,
            "combo_confidence": "insufficient",
            "position_size": 0.25,
            "concept_details": []
        }
        
        combo_stats = self.stats.get("combos", {}).get(combo_key)
        if combo_stats:
            context["combo_uses"] = combo_stats.get("uses", 0)
            context["combo_wr"] = round(combo_stats["wins"] / combo_stats["uses"], 2) if combo_stats["uses"] > 0 else 0
            context["combo_expectancy"] = self.get_combo_expectancy(combo_key)
            context["combo_confidence"] = get_confidence_tier(context["combo_uses"])
        
        total_position_size = []
        for concept in concepts:
            stats = self.stats.get("concepts", {}).get(concept)
            if stats:
                uses = stats.get("uses", 0)
                wr = stats.get("wins", 0) / uses if uses > 0 else 0
                exp = self.get_expectancy(concept)
                conf = self.get_confidence_score(concept)
                pos_size = self.get_position_size(concept)
                
                context["concept_details"].append({
                    "concept": concept,
                    "uses": uses,
                    "win_rate": round(wr, 2),
                    "expectancy": exp,
                    "confidence": conf,
                    "position_size": pos_size
                })
                total_position_size.append(pos_size)
        
        if total_position_size:
            context["position_size"] = min(total_position_size) * (0.8 + 0.2 * len(total_position_size))
        
        return context
    
    def detect_regime(self, prices: List[float]) -> str:
        """
        Detect market regime from price series
        Returns: 'trending', 'chop', 'ranging', 'volatile'
        """
        if not prices or len(prices) < 20:
            return "unknown"
        
        recent = prices[-20:]
        
        directional = 0
        for i in range(1, len(recent)):
            if recent[i] > recent[i-1]:
                directional += 1
            elif recent[i] < recent[i-1]:
                directional -= 1
        
        direction_pct = abs(directional) / len(recent)
        
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        std_dev = variance ** 0.5
        cv = std_dev / mean if mean > 0 else 0
        
        if direction_pct > 0.65 and cv > 0.002:
            return "trending"
        elif direction_pct < 0.35 and cv < 0.003:
            return "ranging"
        elif direction_pct < 0.40 and cv > 0.005:
            return "volatile"
        else:
            return "chop"
    
    def get_regime_adjusted_weight(self, concept: str, regime: str) -> float:
        """Get concept weight adjusted for market regime"""
        stats = self.stats.get("concepts", {}).get(concept)
        if not stats:
            return DEFAULT_WEIGHT
        
        regime_stats = stats.get("regime_stats", {}).get(regime)
        if not regime_stats or regime_stats.get("uses", 0) < 10:
            return self.get_weighted_score(concept)
        
        return regime_stats.get("wins", 0) / regime_stats.get("uses", 1)


def record_concept_trade(trade_id: str, direction: str, concepts_used: List[str],
                    kronos_decision: str, setup_type: str = "UNKNOWN"):
    tracker = ConceptTracker()
    tracker.record_trade(trade_id, direction, concepts_used, kronos_decision, setup_type)


def record_concept_outcome(trade_id: str, outcome: str, rr_result: float,
                        pnl: Optional[float] = None, market_context: Optional[Dict] = None):
    tracker = ConceptTracker()
    tracker.record_outcome(trade_id, outcome, rr_result, pnl, market_context)


if __name__ == "__main__":
    tracker = ConceptTracker()
    
    print("=== Concept Performance Tracker v2.0 ===")
    print(f"Trades: {len(tracker.stats.get('trades', []))}")
    print(f"Concepts: {len(tracker.stats.get('concepts', {}))}")
    print(f"Combos: {len(tracker.stats.get('combos', {}))}")
    
    if tracker.get_top_concepts():
        print("\n=== Top Concepts ===")
        for concept, wr, rr, uses in tracker.get_top_concepts()[:5]:
            print(f"  {concept}: {wr:.0%} weighted, {rr:.2f} RR, {uses} uses")
    
    if tracker.get_top_combos():
        print("\n=== Top Combos ===")
        for combo, wr, rr, uses in tracker.get_top_combos()[:5]:
            print(f"  {combo}: {wr:.0%} WR, {rr:.2f} RR, {uses} uses")


# === LEARNING VETO GATE FUNCTIONS ===

def get_pattern_win_rate(symbol: str, session: str, setup_type: str, direction: str = None, min_samples: int = 10) -> tuple[float, int]:
    """
    Get historical win rate for a specific pattern.
    Used for learning-based veto gate.
    
    Returns: (win_rate, sample_count)
    """
    tracker = ConceptTracker()
    trades = tracker.stats.get("trades", [])
    
    # Filter by pattern
    matching_trades = []
    for t in trades:
        if t.get("outcome") is None:
            continue  # Skip unresolved trades
        
        mc = t.get("market_context", {})
        
        # Match criteria
        if mc.get("symbol") != symbol:
            continue
        if mc.get("session") != session:
            continue
        if t.get("setup_type") != setup_type:
            continue
        if direction and t.get("direction") != direction:
            continue
            
        matching_trades.append(t)
    
    if len(matching_trades) < min_samples:
        return 0.5, len(matching_trades)  # Not enough data
    
    wins = sum(1 for t in matching_trades if t.get("outcome") == "WIN")
    return wins / len(matching_trades), len(matching_trades)


def should_auto_veto(symbol: str, session: str, setup_type: str, direction: str, min_samples: int = 15, veto_threshold: float = 0.40) -> tuple[bool, str]:
    """
    Determine if a pattern should be automatically vetoed based on historical performance.
    
    Args:
        symbol: e.g., "GBPUSD"
        session: e.g., "London Open"
        setup_type: e.g., "CONTINUATION", "REVERSAL"
        direction: "BUY" or "SELL"
        min_samples: Minimum trades needed before making decision
        veto_threshold: Win rate below this = auto-veto
    
    Returns:
        (should_veto: bool, reason: str)
    """
    win_rate, samples = get_pattern_win_rate(symbol, session, setup_type, direction, min_samples)
    
    if samples < min_samples:
        return False, f"Insufficient data ({samples} trades)"
    
    if win_rate < veto_threshold:
        return True, f"Pattern has {win_rate:.0%} win rate ({samples} trades) - below {veto_threshold:.0%} threshold"
    
    if win_rate > 0.65:
        return False, f"Pattern has {win_rate:.0%} win rate ({samples} trades) - ABOVE 65%, boost confidence"
    
    return False, f"Pattern has {win_rate:.0%} win rate ({samples} trades) - normal"


# Example usage
if __name__ == "__main__":
    # Test the learning veto
    print("=== Pattern Learning System ===")
    veto, reason = should_auto_veto("GBPUSD", "London Open", "CONTINUATION", "BUY")
    print(f"GBPUSD + London + CONTINUATION + BUY: {veto} - {reason}")