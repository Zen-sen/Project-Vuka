"""
ICT Knowledge Retriever
Retrieves relevant ICT concepts based on system state
"""
import json
from pathlib import Path
from typing import Dict, List, Optional, Any


class ICTRetriever:
    def __init__(self, knowledge_base_path: str = "data/ict_transcripts/knowledge_base.json"):
        self.knowledge_base_path = Path(knowledge_base_path)
        self.knowledge_base = self._load_knowledge_base()
        
    def _load_knowledge_base(self) -> Dict:
        """Load knowledge base from JSON"""
        if self.knowledge_base_path.exists():
            with open(self.knowledge_base_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
    
    def _load_chunks(self) -> List[Dict]:
        """Load all concept chunks"""
        chunks_dir = Path("data/ict_transcripts/chunks")
        chunks = []
        
        if chunks_dir.exists():
            for chunk_file in chunks_dir.glob("*.json"):
                with open(chunk_file, "r", encoding="utf-8") as f:
                    chunks.append(json.load(f))
        
        return sorted(chunks, key=lambda x: x.get("timestamp_seconds", 0))
    
    def get_concept_by_id(self, concept_id: str) -> Optional[Dict]:
        """Get concept by ID"""
        return self.knowledge_base.get(concept_id)
    
    def get_all_concepts(self) -> List[str]:
        """Get list of all concept IDs"""
        return list(self.knowledge_base.keys())
    
    def retrieve_by_system_state(self, system_state: Dict) -> Dict:
        """
        Retrieve relevant concepts based on Ingwe's system state
        """
        relevant_concepts = []
        context_parts = []
        
        sweep = system_state.get("sweep", "")
        fvg_type = system_state.get("fvg_type", "")
        trend = system_state.get("trend", "")
        session = system_state.get("session", "")
        setup_type = system_state.get("setup_type", "")
        dol = system_state.get("draw_on_liquidity", "UNKNOWN")
        
        # Weighted Context Flags
        adx_ok = system_state.get("adx_ok", True)
        htf_ok = system_state.get("htf_bias_ok", True)
        score_ok = system_state.get("score_ok", True)
        
        if setup_type == "SILVER_BULLET":
            concept_ids = ["fair_value_gap", "liquidity", "kill_zone", "time_concept"]
            context_parts.append(f"SILVER BULLET SETUP: Active window {session}. Draw on Liquidity is {dol}.")
            context_parts.append("Priority: Time + Price alignment. Look for sharp displacement toward the DOL.")
        elif sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG":
            concept_ids = ["fair_value_gap", "liquidity", "consequent_encroachment", "turtle_soup"]
            context_parts.append("BUY SETUP: Price swept liquidity, looking for bullish FVG entry")
        elif sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG":
            concept_ids = ["fair_value_gap", "liquidity", "consequent_encroachment", "turtle_soup"]
            context_parts.append("SELL SETUP: Price swept liquidity, looking for bearish FVG entry")
        elif trend == "BULLISH":
            concept_ids = ["liquidity", "market_structure", "kill_zone"]
            context_parts.append("BULLISH TREND: Focus on buy-side liquidity and continuation patterns")
        elif trend == "BEARISH":
            concept_ids = ["liquidity", "market_structure", "kill_zone"]
            context_parts.append("BEARISH TREND: Focus on sell-side liquidity and continuation patterns")
        else:
            concept_ids = ["vision", "pattern", "time_concept"]
            context_parts.append("NEUTRAL: Cast vision, wait for clear setup")
        
        # Inject Weighted Risks
        if not adx_ok:
            context_parts.append("RISK: Low ADX detected (market may be ranging/choppy).")
        if not htf_ok:
            context_parts.append("RISK: HTF Bias is conflicted or unavailable.")
        if not score_ok:
            context_parts.append("RISK: Confluence score is below standard threshold.")
        
        if "London" in session or "NewYork" in session:
            concept_ids.append("kill_zone")
            context_parts.append(f"ACTIVE SESSION: {session}")
        
        for concept_id in set(concept_ids):
            concept_data = self.get_concept_by_id(concept_id)
            if concept_data:
                relevant_concepts.append({
                    "id": concept_id,
                    "name": concept_data.get("name", ""),
                    "definition": concept_data.get("definition", ""),
                    "examples": concept_data.get("examples", [])[:2]
                })
        
        return {
            "system_state": system_state,
            "context_summary": " | ".join(context_parts),
            "concepts": relevant_concepts,
            "knowledge_source": "ict_rag_v1"
        }
    
    def retrieve_by_keywords(self, keywords: List[str], max_results: int = 3) -> List[Dict]:
        """Retrieve concepts by keywords"""
        results = []
        
        for keyword in keywords:
            keyword_lower = keyword.lower()
            
            for concept_id, concept_data in self.knowledge_base.items():
                if keyword_lower in concept_id.lower() or keyword_lower in concept_data.get("name", "").lower():
                    results.append({
                        "id": concept_id,
                        "name": concept_data.get("name", ""),
                        "definition": concept_data.get("definition", ""),
                        "examples": concept_data.get("examples", [])[:2]
                    })
        
        return results[:max_results]
    
    def build_context_prompt(self, system_state: Dict) -> str:
        """
        Build a context prompt for Kronos based on system state
        
        Returns a formatted string suitable for prepend to Kronos prompt
        """
        retrieval = self.retrieve_by_system_state(system_state)
        
        prompt_lines = [
            "=== ICT CONTEXT ===",
            f"System State: {retrieval['system_state']}",
            f"Summary: {retrieval['context_summary']}",
            "",
            "Relevant Concepts:"
        ]
        
        for concept in retrieval["concepts"]:
            prompt_lines.append(f"\n## {concept['name']}")
            prompt_lines.append(f"Definition: {concept['definition']}")
            
            if concept["examples"]:
                prompt_lines.append("Examples:")
                for ex in concept["examples"]:
                    prompt_lines.append(f"  - [{ex['source']} @ {ex['timestamp']}]: {ex['excerpt'][:150]}...")
        
        prompt_lines.append("\n=== END ICT CONTEXT ===")
        
        return "\n".join(prompt_lines)


def get_ict_context(sweep: str = "", fvg_type: str = "", trend: str = "", 
                    session: str = "", bos_aligned: bool = False, htf_bias_ok: bool = False) -> Dict:
    """
    Convenience function to get ICT context
    
    Example usage:
        ict_context = get_ict_context(
            sweep="SWEEP_LOW",
            fvg_type="BULLISH_FVG", 
            trend="BULLISH",
            session="London",
            bos_aligned=True,
            htf_bias_ok=True
        )
    """
    retriever = ICTRetriever()
    
    system_state = {
        "sweep": sweep,
        "fvg_type": fvg_type,
        "trend": trend,
        "session": session,
        "bos_aligned": bos_aligned,
        "htf_bias_ok": htf_bias_ok
    }
    
    return retriever.retrieve_by_system_state(system_state)


def build_ict_prompt(sweep: str = "", fvg_type: str = "", trend: str = "",
                     session: str = "", bos_aligned: bool = False, htf_bias_ok: bool = False) -> str:
    """Build ICT context prompt for Kronos"""
    retriever = ICTRetriever()
    
    system_state = {
        "sweep": sweep,
        "fvg_type": fvg_type,
        "trend": trend,
        "session": session,
        "bos_aligned": bos_aligned,
        "htf_bias_ok": htf_bias_ok
    }
    
    return retriever.build_context_prompt(system_state)


if __name__ == "__main__":
    retriever = ICTRetriever()
    
    print("=== ICT Knowledge Base ===")
    print(f"Total concepts: {len(retriever.get_all_concepts())}")
    print()
    
    print("Available concepts:")
    for cid in retriever.get_all_concepts():
        cdata = retriever.get_concept_by_id(cid)
        print(f"  - {cid}: {cdata.get('name', 'N/A')} ({len(cdata.get('examples', []))} examples)")
    
    print("\n=== Test Retrieval ===")
    
    test_states = [
        {"sweep": "SWEEP_LOW", "fvg_type": "BULLISH_FVG", "trend": "BULLISH", "session": "London"},
        {"sweep": "SWEEP_HIGH", "fvg_type": "BEARISH_FVG", "trend": "BEARISH", "session": "NewYork"},
        {"sweep": "", "fvg_type": "", "trend": "BULLISH", "session": "Asian"},
    ]
    
    for state in test_states:
        print(f"\n--- State: {state.get('sweep', 'NONE')} + {state.get('fvg_type', 'NONE')} ---")
        result = retriever.retrieve_by_system_state(state)
        print(f"Summary: {result['context_summary']}")
        print(f"Concepts found: {len(result['concepts'])}")
