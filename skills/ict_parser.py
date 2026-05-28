"""
ICT Transcript Parser
Extracts concepts from ICT trading transcripts and creates structured JSON chunks
"""
import json
import re
from pathlib import Path
from datetime import datetime


CONCEPT_PATTERNS = {
    "fair_value_gap": [
        r"fair value gap",
        r"fvg",
        r"first presented.*fair value",
        r"inversion.*fair value",
        r"reclaimed.*fair value",
        r"bearish fair value gap",
        r"bullish fair value gap"
    ],
    "order_block": [
        r"order block",
        r"ob\b",
        r"rejection block",
        r"suspension block"
    ],
    "liquidity": [
        r"sell.?side",
        r"buy.?side",
        r"liquidity",
        r"liquidity pool",
        r"sell side imbalance",
        r"buy side imbalance",
        r"bsi",
        r"sbi",
        r"liquidity sweep",
        r"stop hunt"
    ],
    "consequent_encroachment": [
        r"consequent encroachment",
        r"consequent encroach",
        r"ce\b"
    ],
    "turtle_soup": [
        r"turtle soup",
        r"displacement"
    ],
    "first_hour_dealing_range": [
        r"first hour",
        r"dealing range",
        r"opening range"
    ],
    "kill_zone": [
        r"kill zone",
        r"london open",
        r"new york open",
        r"asian session",
        r"950.?1010",
        r"lunch macro"
    ],
    "session": [
        r"regular trading hours",
        r"rth\b",
        r"electronic trading hours",
        r"eth\b",
        r"9:30",
        r"10:00"
    ],
    "pattern": [
        r"relative equal high",
        r"relative equal low",
        r"reh\b",
        r"rel\b",
        r"high.*lower high",
        r"lower high"
    ],
    "fibonacci": [
        r"fib",
        r"fibonacci",
        r"50%.*level",
        r"midpoint",
        r"equilibrium",
        r"octant",
        r"quadrant"
    ],
    "market_structure": [
        r"market structure",
        r"bos\b",
        r"break of structure",
        r"change of character",
        r"trend"
    ],
    "time_concept": [
        r"time.*price",
        r"time first",
        r"time distortion",
        r"macr(o|o)o"
    ],
    "optimal_trade_entry": [
        r"ote\b",
        r"optimal trade entry",
        r"62%.*79%"
    ],
    "vision": [
        r"cast.*vision",
        r"drawing to",
        r"where is it drawing"
    ],
    "manipulation": [
        r"manipulation",
        r"stop hunt",
        r"stop run",
        r"liquidity grab"
    ]
}


CONCEPT_DEFINITIONS = {
    "fair_value_gap": "A three-candle pattern where the middle candle creates a gap between the wicks of surrounding candles. Represents inefficiency where price will likely fill.",
    "order_block": "The last consecutive candle before a strong move in one direction. Areas where institutions placed orders.",
    "liquidity": "Pools of stop orders or pending orders that market makers target. Buy-side = highs, sell-side = lows.",
    "consequent_encroachment": "The midpoint of a candle's range. Price failing to reach it indicates weakness.",
    "turtle_soup": "Pattern where price breaks a recent low/high, then quickly reverses to trap traders.",
    "first_hour_dealing_range": "The range established from 9:30 AM to 10:30 AM. Projects potential moves for the day.",
    "kill_zone": "Specific time windows when institutional trading is most active.",
    "relative_equal_highs": "Two or more highs at similar price levels. Liquidity pools for sweeps.",
    "fibonacci_retracement": "Using 50%, 62%, 79% levels to find potential support/resistance.",
    "vision": "Hypothesis of where price is likely to go based on liquidity targets and market structure."
}


def extract_concepts(text):
    """Extract all concepts mentioned in text"""
    found = set()
    text_lower = text.lower()
    
    for concept, patterns in CONCEPT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                found.add(concept)
                break
    
    return list(found)


def parse_timestamp(ts_str):
    """Parse timestamp like (01:13) or (1:13:30)"""
    ts_str = ts_str.strip("() ")
    parts = ts_str.split(":")
    
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


def format_timestamp(seconds):
    """Format seconds to HH:MM:SS"""
    hrs = seconds // 3600
    mins = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hrs:02d}:{mins:02d}:{secs:02d}"


def parse_transcript(transcript_text, source_name):
    """Parse a transcript into structured chunks"""
    chunks = []
    
    timestamp_pattern = r"\((\d{1,2}:\d{2}(?::\d{2})?)\)"
    
    segments = re.split(timestamp_pattern, transcript_text)
    
    current_time = 0
    
    for i in range(1, len(segments), 2):
        timestamp_str = segments[i].strip()
        content = segments[i + 1].strip() if i + 1 < len(segments) else ""
        
        if not content:
            continue
            
        time_seconds = parse_timestamp(timestamp_str)
        
        concepts = extract_concepts(content)
        
        if not concepts:
            continue
        
        chunk = {
            "source": source_name,
            "timestamp_seconds": time_seconds,
            "timestamp_formatted": format_timestamp(time_seconds),
            "content": content,
            "concepts": concepts,
            "extracted_at": datetime.now().isoformat()
        }
        
        chunks.append(chunk)
    
    return chunks


def create_knowledge_base(chunks):
    """Create knowledge base from chunks"""
    concept_map = {}
    
    for chunk in chunks:
        for concept in chunk["concepts"]:
            if concept not in concept_map:
                concept_map[concept] = {
                    "concept_id": concept,
                    "name": concept.replace("_", " ").title(),
                    "definition": CONCEPT_DEFINITIONS.get(concept, "ICT concept"),
                    "examples": [],
                    "tags": [concept]
                }
            
            concept_map[concept]["examples"].append({
                "source": chunk["source"],
                "timestamp": chunk["timestamp_formatted"],
                "excerpt": chunk["content"][:300]
            })
    
    return concept_map


def save_chunks(chunks, output_dir):
    """Save chunks to JSON files"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for chunk in chunks:
        source_clean = chunk["source"].replace(" ", "_").lower()
        filename = f"{source_clean}_{chunk['timestamp_seconds']:06d}.json"
        
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(chunk, f, indent=2, ensure_ascii=False)


def save_knowledge_base(concept_map, output_file):
    """Save knowledge base to single JSON"""
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(concept_map, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python ict_parser.py <transcript_file> [source_name]")
        sys.exit(1)
    
    transcript_file = sys.argv[1]
    source_name = sys.argv[2] if len(sys.argv) > 2 else Path(transcript_file).stem
    
    with open(transcript_file, "r", encoding="utf-8") as f:
        transcript = f.read()
    
    print(f"Parsing {transcript_file}...")
    chunks = parse_transcript(transcript, source_name)
    print(f"Found {len(chunks)} concept chunks")
    
    base_dir = Path("data/ict_transcripts")
    chunks_dir = base_dir / "chunks"
    save_chunks(chunks, chunks_dir)
    print(f"Saved chunks to {chunks_dir}")
    
    kb_file = base_dir / "knowledge_base.json"
    concept_map = create_knowledge_base(chunks)
    save_knowledge_base(concept_map, kb_file)
    print(f"Saved knowledge base to {kb_file}")
    print(f"Total concepts: {len(concept_map)}")
