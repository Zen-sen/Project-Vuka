"""
Decision Synthesizer (v1.3)
Collapses multiple signals into single decision with layered scoring

v1.3 Changes:
- Asymmetric stability: decay penalizes harder than improvement rewards
- Adaptive thresholds: tightens when system struggles
- Soft conflict penalty: preserves mid-range setups
- Non-linear position sizing: score^2 for hesitation
"""
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class SynthesizedDecision:
    base_edge: float
    confidence: float
    conflict_score: float
    final_score: float
    tier: str
    conflict_level: str
    conflict_signals: List[str]
    supporting_factors: List[str]
    concern_factors: List[str]
    critique: str
    position_size: float
    verdict: str


class DecisionSynthesizer:
    """
    Collapses context signals into single decision.
    Architecture:
    base_edge = "this setup should work"
    confidence = "we trust that belief"  
    conflict_score = "how messy is this setup"
    raw_score = base_edge * confidence
    risk_adjustment = 1 - (conflict ** 1.5)
    final_score = raw_score * risk_adjustment
    """
    
    VERDICT_TIERS = {
        "FULL": 0.65,
        "STANDARD": 0.50,
        "REDUCE": 0.35,
        "PROBE": 0.20
    }
    
    def __init__(self, concept_tracker=None):
        self.tracker = concept_tracker
        self._system_wr = 0.5
    
    def update_system_performance(self, recent_wr: float):
        """Update system-wide performance for adaptive thresholds"""
        self._system_wr = max(0.3, min(0.7, recent_wr))
    
    def _get_adaptive_thresholds(self) -> dict:
        """Adaptive thresholds based on recent system performance"""
        base = self.VERDICT_TIERS.copy()
        
        if self._system_wr < 0.45:
            return {k: v + 0.05 for k, v in base.items()}
        elif self._system_wr < 0.50:
            return {k: v + 0.03 for k, v in base.items()}
        elif self._system_wr > 0.60:
            return {k: max(0.1, v - 0.03) for k, v in base.items()}
        
        return base
    
    def synthesize(
        self,
        concepts: List[str],
        market_context: Dict,
        setup_direction: str,
        ohlcv_data: Optional[Dict] = None
    ) -> SynthesizedDecision:
        supporting = []
        concerns = []
        conflict_signals = []
        
        base_edge = 0.5
        stat_confidence = "insufficient"
        pos_size = 0.25
        
        if self.tracker:
            ctx = self.tracker.get_decision_context({
                "concepts_used": concepts,
                "market_context": market_context
            })
            
            combo_wr = ctx.get("combo_wr", 0)
            combo_conf = ctx.get("combo_confidence", "insufficient")
            combo_exp = ctx.get("combo_expectancy", 0)
            pos_size = ctx.get("position_size", 0.25)
            
            if combo_wr >= 0.65:
                supporting.append(f"Strong combo: {combo_wr:.0%}")
                base_edge = 0.65
            elif combo_wr >= 0.55:
                supporting.append(f"Moderate combo: {combo_wr:.0%}")
                base_edge = 0.55
            elif combo_wr >= 0.45:
                concerns.append(f"Weak combo: {combo_wr:.0%}")
                base_edge = 0.40
            else:
                concerns.append(f"Poor combo: {combo_wr:.0%}")
                base_edge = 0.25
            
            if combo_exp > 0.5:
                supporting.append(f"Strong expectancy: {combo_exp:.2f}")
            elif combo_exp > 0.2:
                supporting.append(f"Positive expectancy: {combo_exp:.2f}")
            
            stat_confidence = combo_conf
        
        session = market_context.get("session", "unknown")
        volatility = market_context.get("volatility", "normal")
        trend = market_context.get("trend_strength", "unknown")
        
        if session in ("London", "NY_Open"):
            supporting.append(f"High-activity: {session}")
        
        if volatility == "high":
            supporting.append("High volatility")
        elif volatility == "low":
            concerns.append("Low volatility")
        
        if trend == "strong":
            supporting.append("Trending direction")
        elif trend == "weak":
            concerns.append("Weak trend")
        
        if ohlcv_data and self.tracker:
            prices = ohlcv_data.get("closes", [])
            if len(prices) >= 20:
                regime = self.tracker.detect_regime(prices)
                if regime == "trending":
                    supporting.append("Trending regime")
                elif regime == "chop":
                    concerns.append("Chop regime")
        
        conflict_signals = self._detect_conflicts(
            concepts, market_context, setup_direction, supporting, concerns
        )
        conflict_score = self._compute_conflict_score(conflict_signals)
        conflict_level = self._get_conflict_level(conflict_score)
        
        stability = 1.0
        if self.tracker:
            stability = self._compute_stability(concepts)
        
        confidence = self._compute_confidence(
            stat_confidence, len(supporting), len(concerns), stability=stability
        )
        
        raw_score = base_edge * confidence
        
        if conflict_score > 0:
            risk_adjustment = 1.0 - (conflict_score ** 1.5)
        else:
            risk_adjustment = 1.0
        
        final_score = raw_score * risk_adjustment
        
        tier = self._get_tier(final_score)
        
        critique = self._generate_critique(
            final_score, tier, base_edge, confidence, conflict_score,
            supporting, concerns, conflict_signals
        )
        
        verdict = self._get_verdict(final_score, conflict_score, confidence)
        
        position_size = self._compute_position_size(pos_size, final_score)
        
        return SynthesizedDecision(
            base_edge=round(base_edge, 2),
            confidence=round(confidence, 2),
            conflict_score=round(conflict_score, 2),
            final_score=round(final_score, 2),
            tier=tier,
            conflict_level=conflict_level,
            conflict_signals=conflict_signals,
            supporting_factors=supporting,
            concern_factors=concerns,
            critique=critique,
            position_size=round(position_size, 2),
            verdict=verdict
        )
    
    def _detect_conflicts(
        self,
        concepts: List[str],
        market_context: Dict,
        direction: str,
        supporting: List[str],
        concerns: List[str]
    ) -> List[str]:
        conflicts = []
        
        sweep_count = sum(1 for c in concepts if "sweep" in c.lower())
        if sweep_count >= 2:
            conflicts.append("Multiple sweeps")
        
        if market_context.get("news_event"):
            conflicts.append("News event")
        
        if len(concerns) >= 3 and market_context.get("volatility") == "high":
            conflicts.append("High vol + concerns")
        
        htf_bias = market_context.get("htf_bias", "unknown")
        if htf_bias == "bullish" and direction == "SELL":
            conflicts.append("HTF vs direction")
        elif htf_bias == "bearish" and direction == "BUY":
            conflicts.append("HTF vs direction")
        
        if market_context.get("trend_strength") == "weak" and "continuation" in concepts:
            conflicts.append("Continuation in weak trend")
        
        return conflicts
    
    def _compute_conflict_score(self, conflicts: List[str]) -> float:
        if not conflicts:
            return 0.0
        
        severity_map = {
            "HTF": 0.4,
            "sweep": 0.3,
            "news": 0.5,
            "continuation": 0.3,
            "multiple": 0.2,
            "chop": 0.2,
            "concerns": 0.15
        }
        
        score = 0.0
        for conflict in conflicts:
            conflict_lower = conflict.lower()
            for key, val in severity_map.items():
                if key.lower() in conflict_lower:
                    score = max(score, val)
                    break
        
        score += min(len(conflicts) * 0.1, 0.2)
        return min(score, 1.0)
    
    def _get_conflict_level(self, score: float) -> str:
        if score <= 0.1:
            return "clean"
        if score <= 0.3:
            return "mild"
        if score <= 0.5:
            return "moderate"
        if score <= 0.7:
            return "high"
        return "structural"
    
    def _compute_stability(self, concepts: List[str]) -> float:
        if not self.tracker:
            return 1.0
        
        stability_scores = []
        for concept in concepts:
            stats = self.tracker.get_concept_stats(concept)
            if not stats or stats.get("uses", 0) < 10:
                stability_scores.append(1.0)
                continue
            
            uses = stats.get("uses", 0)
            recent_wins = stats.get("recent_wins", 0)
            recent_losses = stats.get("recent_losses", 0)
            recent_total = recent_wins + recent_losses
            
            if recent_total < 5:
                stability_scores.append(0.9)
                continue
            
            recent_wr = recent_wins / recent_total
            overall_wr = stats.get("wins", 0) / uses
            
            deviation = abs(recent_wr - overall_wr)
            
            if recent_wr < overall_wr:
                stability = 1.0 - (deviation * 2.0)
            else:
                stability = 1.0 - (deviation * 0.5)
            
            stability_scores.append(max(0.5, min(1.0, stability)))
        
        if not stability_scores:
            return 1.0
        
        return min(stability_scores) if len(stability_scores) == 1 else sum(stability_scores) / len(stability_scores)
    
    def _compute_confidence(
        self,
        stat_confidence: str,
        num_supporting: int,
        num_concerns: int,
        stability: float = 1.0
    ) -> float:
        conf_map = {"high": 1.0, "medium": 0.75, "low": 0.5, "insufficient": 0.25}
        base = conf_map.get(stat_confidence, 0.25)
        
        supporting_bonus = min(num_supporting * 0.03, 0.12)
        concern_penalty = min(num_concerns * 0.05, 0.20)
        
        raw = max(0.1, min(1.0, base + supporting_bonus - concern_penalty))
        
        return raw * stability
    
    def _get_tier(self, score: float) -> str:
        if score >= 0.70:
            return "strong"
        if score >= 0.55:
            return "moderate"
        if score >= 0.35:
            return "weak"
        if score >= 0.20:
            return "marginal"
        return "skip"
    
    def _get_verdict(self, score: float, conflict: float, confidence: float) -> str:
        tiers = self._get_adaptive_thresholds()
        
        if score < tiers["PROBE"]:
            return "SKIP"
        if score >= tiers["FULL"] and conflict <= 0.3 and confidence >= 0.60:
            return "FULL"
        if score >= tiers["STANDARD"]:
            return "STANDARD"
        if score >= tiers["REDUCE"]:
            return "REDUCE"
        return "PROBE"
    
    def _compute_position_size(self, base_size: float, final_score: float) -> float:
        size = base_size * (final_score ** 2)
        return max(0.05, min(1.0, size))
    
    def _generate_critique(
        self,
        final_score: float,
        tier: str,
        base_edge: float,
        confidence: float,
        conflict_score: float,
        supporting: List[str],
        concerns: List[str],
        conflicts: List[str]
    ) -> str:
        if tier == "strong" and not conflicts:
            return "Strong conviction. Proceed with full."
        
        lines = []
        
        if conflicts:
            lines.append(f"CONFLICTS ({len(conflicts)}): {', '.join(conflicts)}")
        
        if base_edge < 0.45:
            lines.append("Weak edge foundation")
        
        if confidence < 0.50:
            lines.append("Low confidence")
        
        if conflict_score > 0.4:
            lines.append("Significant conflict")
        
        if not lines:
            if tier == "moderate":
                return "Moderate conviction. Standard exposure."
            else:
                return "Acceptable setup."
        
        return " ".join(lines)
    
    def format_for_kronos(self, decision: SynthesizedDecision) -> str:
        lines = [
            "=== SYNTHESIZED DECISION ===",
            f"Score: {decision.final_score:.2f} (Tier: {decision.tier.upper()})",
            f"Edge: {decision.base_edge:.0%} | Confidence: {decision.confidence:.0%}",
            f"Conflicts: {decision.conflict_level.upper()} ({decision.conflict_score:.0%})",
            f"Verdict: {decision.verdict} | Position: {decision.position_size:.0%}",
            "",
            "Supporting:",
        ]
        
        for s in decision.supporting_factors[:5]:
            lines.append(f"  + {s}")
        
        if decision.concern_factors:
            lines.append("")
            lines.append("Concerns:")
            for c in decision.concern_factors[:3]:
                lines.append(f"  - {c}")
        
        if decision.conflict_signals:
            lines.append("")
            lines.append("Conflicts:")
            for c in decision.conflict_signals:
                lines.append(f"  ⚠ {c}")
        
        lines.extend([
            "",
            f"Critique: {decision.critique}",
            "",
            "=== KRONOS TASK ===",
            "Review: AGREE / OVERCONFIDENT / UNDERESTIMATED",
            "=== END ==="
        ])
        
        return "\n".join(lines)


def synthesize_decision(
    concepts: List[str],
    market_context: Dict,
    direction: str,
    ohlcv_data: Optional[Dict] = None
) -> SynthesizedDecision:
    from skills.concept_tracker import ConceptTracker
    tracker = ConceptTracker()
    synthesizer = DecisionSynthesizer(tracker)
    return synthesizer.synthesize(concepts, market_context, direction, ohlcv_data)


if __name__ == "__main__":
    synth = DecisionSynthesizer()
    
    d = synth.synthesize(
        ["fvg_after_sweep", "bos_aligned"],
        {"session": "London", "volatility": "high", "trend_strength": "strong"},
        "BUY"
    )
    
    print(f"Score: {d.final_score} | Verdict: {d.verdict}")
    print(f"Edge: {d.base_edge} | Conf: {d.confidence}")
    print(f"Conflict: {d.conflict_level} ({d.conflict_score})")
    print(f"Position: {d.position_size:.0%}")