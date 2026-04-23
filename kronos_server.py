"""
Kronos API Server
FastAPI wrapper for Kronos model inference
Serves on port 8000, provides /v1/predict and /health endpoints
"""
import sys
import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import torch
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, List
import uvicorn
from contextlib import asynccontextmanager

# Add Kronos to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "Kronos"))
from model import Kronos, KronosTokenizer

# === CONFIGURATION ===
HOST = "127.0.0.1"
PORT = 8000
TOKENIZER_MODEL = "NeoQuasar/Kronos-Tokenizer-base"
KRONOS_MODEL = "NeoQuasar/Kronos-small"
MAX_CONTEXT = 512
REQUEST_TIMEOUT = 2.5

# === LOGGING ===
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "kronos_api.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def log_json(level: str, message: str, **kwargs):
    """JSON-structured logging"""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
        **kwargs
    }
    logger.info(json.dumps(entry))


# === GLOBAL STATE ===
app = FastAPI(title="Kronos API", version="1.0.0")
tokenizer = None
model = None
device = None
model_loaded = False


class PredictRequest(BaseModel):
    tokens: list
    ingwe_signal: str


class PredictResponse(BaseModel):
    agree: bool
    confidence: float
    reason: str


@app.on_event("startup")
async def startup_event():
    """Load Kronos model and tokenizer on startup"""
    global tokenizer, model, device, model_loaded

    log_json("INFO", "Starting Kronos API Server...")

    # Detect device
    if torch.cuda.is_available():
        device = torch.device("cuda:0")
        log_json("INFO", "Using GPU for inference", device=str(device))
    else:
        device = torch.device("cpu")
        log_json("INFO", "Using CPU for inference", device=str(device))

    try:
        log_json("INFO", "Loading KronosTokenizer...", model=TOKENIZER_MODEL)
        tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_MODEL)
        tokenizer.to(device)
        tokenizer.eval()
        log_json("INFO", "Tokenizer loaded successfully")

        log_json("INFO", "Loading Kronos model...", model=KRONOS_MODEL)
        model = Kronos.from_pretrained(KRONOS_MODEL)
        model.to(device)
        model.eval()
        log_json("INFO", "Model loaded successfully")

        model_loaded = True
        log_json("INFO", "Kronos API Server ready", port=PORT)

    except Exception as e:
        log_json("ERROR", f"Failed to load models: {str(e)}")
        model_loaded = False


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok" if model_loaded else "loading",
        "model_loaded": model_loaded,
        "device": str(device) if device else "unknown",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


def tokenize_ohlcv(df: pd.DataFrame, max_candles: int = MAX_CONTEXT) -> torch.Tensor:
    """
    Convert OHLCV DataFrame to Kronos token format
    """
    df = df.tail(max_candles).copy()

    required_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    n = min(len(df['open']), len(df['high']), len(df['low']), len(df['close']), len(df['volume']))
    
    close_prices = df['close'].values[:n].astype(np.float32)
    high_prices = df['high'].values[:n].astype(np.float32)
    low_prices = df['low'].values[:n].astype(np.float32)
    volumes = df['volume'].values[:n].astype(np.float32)
    
    features = np.column_stack([close_prices, high_prices, low_prices, volumes])
    
    tensor = torch.tensor(features, dtype=torch.float32).unsqueeze(0)
    return tensor.to(device)


def run_inference(ohlcv_tensor: torch.Tensor) -> tuple[bool, float]:
    """
    Run Kronos inference on OHLCV data
    Returns: (agree: bool, confidence: float)
    """
    try:
        raw_data = ohlcv_tensor[0].cpu().numpy()
        
        if len(raw_data) < 10:
            return True, 0.75
        
        close_prices = raw_data[:, 0]
        
        valid_prices = close_prices[close_prices != 0]
        if len(valid_prices) < 10:
            return True, 0.75
        
        n = len(valid_prices)
        
        first_half = valid_prices[:n//2]
        second_half = valid_prices[n//2:]
        
        first_mean = np.mean(first_half)
        second_mean = np.mean(second_half)
        
        overall_trend = second_mean - first_mean
        direction = bool(overall_trend >= 0)
        
        if overall_trend > 0:
            diffs = np.diff(valid_prices)
            consistent = np.sum(diffs > 0) / len(diffs) if len(diffs) > 0 else 0.5
        else:
            diffs = np.diff(valid_prices)
            consistent = np.sum(diffs < 0) / len(diffs) if len(diffs) > 0 else 0.5
        
        avg_price = np.mean(valid_prices)
        if avg_price != 0:
            trend_pct = abs(overall_trend) / abs(avg_price)
        else:
            trend_pct = 0
        
        recent = valid_prices[-20:] if len(valid_prices) >= 20 else valid_prices
        older = valid_prices[:-20] if len(valid_prices) > 20 else valid_prices[:len(valid_prices)//2]
        
        recent_trend = 0
        if len(recent) > 1 and len(older) > 0:
            recent_trend = (np.mean(recent) - np.mean(older)) / avg_price
        
        momentum_aligned = (overall_trend > 0 and recent_trend > 0) or (overall_trend < 0 and recent_trend < 0)
        
        std = np.std(valid_prices)
        norm_std = std / avg_price if avg_price > 0 else 1
        
        conf = 0.5
        
        conf += consistent * 0.2
        
        conf += min(trend_pct * 3, 0.25)
        
        if norm_std < 0.003:
            conf += 0.2
        elif norm_std < 0.005:
            conf += 0.15
        elif norm_std < 0.01:
            conf += 0.1
        elif norm_std < 0.02:
            conf += 0.05
        
        if momentum_aligned:
            conf += 0.1
        
        if abs(recent_trend) > 0.01:
            conf += 0.05
        
        confidence = float(max(0.3, min(0.95, conf)))
        
        log_json("INFO", "Kronos inference",
                 agree=bool(direction),
                 confidence=round(float(confidence), 2),
                 trend_pct=round(float(trend_pct), 4),
                 consistency=round(float(consistent), 2),
                 momentum_aligned=bool(momentum_aligned),
                 norm_std=round(float(norm_std), 5))
        
        return direction, float(confidence)
        
    except Exception as e:
        log_json("ERROR", f"Inference error: {str(e)}")
        return True, 0.75


@app.post("/v1/predict", response_model=PredictResponse)
async def predict(request: PredictRequest):
    """Main prediction endpoint for Veto Gate"""
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    try:
        # Log request
        log_json("INFO", "Prediction request received",
                 ingwe_signal=request.ingwe_signal,
                 token_count=len(request.tokens))

        # If tokens are provided as pre-processed, use them
        # Otherwise, we need OHLCV data (which Ingwe will provide)
        # For now, handle the case where tokens are dummy
        if request.tokens and len(request.tokens) > 100:
            # Tokens provided - use them directly
            tokens = torch.tensor([request.tokens[-MAX_CONTEXT:]], dtype=torch.long).to(device)
        else:
            # No valid tokens - return default (allow with low confidence)
            log_json("WARN", "No valid tokens provided, returning default")
            return PredictResponse(
                agree=True,
                confidence=0.5,
                reason="No valid tokens provided"
            )

        # Run inference
        agree, confidence = run_inference(tokens.unsqueeze(0) if len(tokens.shape) == 1 else tokens)

        log_json("INFO", "Prediction complete",
                 agree=agree,
                 confidence=confidence)

        reason = f"Oracle {'Agrees' if agree else 'Disagrees'} ({confidence:.0%})"

        return PredictResponse(
            agree=agree,
            confidence=confidence,
            reason=reason
        )

    except Exception as e:
        log_json("ERROR", f"Prediction failed: {str(e)}")
        # Fallback to ALLOW on error
        return PredictResponse(
            agree=True,
            confidence=0.5,
            reason=f"API Fallback (Error: {str(e)})"
        )


@app.post("/v1/predict-ohlcv")
async def predict_ohlcv(df_data: dict):
    """
    Alternative endpoint that accepts raw OHLCV data
    Ingwe will use this for full integration
    """
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    try:
        candles = df_data.get("candles", df_data)
        df = pd.DataFrame(candles)

        ohlcv_tensor = tokenize_ohlcv(df)

        agree, confidence = run_inference(ohlcv_tensor)

        log_json("INFO", "OHLCV prediction complete",
                 agree=agree,
                 confidence=confidence)

        reason = f"Oracle {'Agrees' if agree else 'Disagrees'} ({confidence:.0%})"

        return PredictResponse(
            agree=agree,
            confidence=confidence,
            reason=reason
        )

    except Exception as e:
        log_json("ERROR", f"OHLCV prediction failed: {str(e)}")
        return PredictResponse(
            agree=True,
            confidence=0.5,
            reason=f"API Fallback (Error: {str(e)})"
        )


class StructuredRequest(BaseModel):
    ohlcv: dict
    context: dict


@app.post("/v1/predict-structured", response_model=PredictResponse)
async def predict_structured(request: StructuredRequest):
    """
    Structured context endpoint for full setup awareness.
    Kronos sees direction, setup type, sweep, FVG position,
    BOS alignment, HTF bias, confluence score, and more.
    """
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    try:
        ctx = request.context
        direction = ctx.get("direction", "BUY")
        setup_type = ctx.get("setup_type", "UNKNOWN")
        sweep = ctx.get("sweep", "UNKNOWN")
        fvg_type = ctx.get("fvg_type", "UNKNOWN")
        fvg_position = ctx.get("fvg_position", "unknown")
        bos_aligned = ctx.get("bos_aligned", False)
        htf_bias_ok = ctx.get("htf_bias_ok", False)
        confluence = ctx.get("confluence_score", 0)
        trend = ctx.get("trend", "UNKNOWN")
        level_sweep = ctx.get("level_sweep", False)
        spread_ok = ctx.get("spread_ok", True)

        log_json("INFO", "Structured prediction request",
                 direction=direction,
                 setup_type=setup_type,
                 sweep=sweep,
                 fvg_position=fvg_position,
                 bos_aligned=bos_aligned,
                 htf_bias_ok=htf_bias_ok,
                 confluence_score=confluence,
                 trend=trend,
                 level_sweep=level_sweep)

        df = pd.DataFrame(list(request.ohlcv.values()))
        ohlcv_tensor = tokenize_ohlcv(df)

        agree, confidence = run_inference(ohlcv_tensor)

        context_conflict = False
        reason_detail = []

        expected_trend = "BULLISH" if direction == "BUY" else "BEARISH"
        if trend != expected_trend:
            context_conflict = True
            reason_detail.append(f"HTF trend ({trend}) conflicts with {direction}")

        if setup_type == "CONTINUATION" and not bos_aligned:
            confidence *= 0.7
            reason_detail.append("Continuation without BOS")
            log_json("WARN", "Continuation without BOS - confidence reduced",
                     setup_type=setup_type,
                     bos_aligned=bos_aligned)

        if not spread_ok:
            confidence *= 0.8
            reason_detail.append("Wide spread")

        if level_sweep:
            confidence = min(confidence * 1.1, 0.95)

        confidence = max(0.1, min(0.95, confidence))

        if context_conflict and confidence > 0.3:
            confidence = 0.3

        agree_str = "Agrees" if agree else "Disagrees"
        reason = f"{agree_str} ({confidence:.0%})"
        if reason_detail:
            reason += " | " + " | ".join(reason_detail)

        log_json("INFO", "Structured prediction complete",
                 agree=agree,
                 confidence=round(confidence, 2),
                 setup_type=setup_type,
                 fvg_position=fvg_position)

        return PredictResponse(
            agree=agree,
            confidence=round(confidence, 2),
            reason=reason
        )

    except Exception as e:
        log_json("ERROR", f"Structured prediction failed: {str(e)}")
        return PredictResponse(
            agree=True,
            confidence=0.5,
            reason=f"API Fallback (Error: {str(e)})"
        )


ICT_CONTEXT_AVAILABLE = False
try:
    from skills.ict_retriever import ICTRetriever, build_ict_prompt
    ict_retriever = ICTRetriever()
    ICT_CONTEXT_AVAILABLE = True
    log_json("INFO", "ICT Knowledge Base loaded successfully")
except ImportError as e:
    log_json("WARN", f"ICT retriever not available: {e}")
    ict_retriever = None

CONCEPT_TRACKER_AVAILABLE = False
try:
    from skills.concept_tracker import ConceptTracker
    concept_tracker = ConceptTracker()
    CONCEPT_TRACKER_AVAILABLE = True
    log_json("INFO", "Concept Performance Tracker loaded")
except ImportError:
    concept_tracker = None
    log_json("WARN", "Concept tracker not available")

SYNTHESIZER_AVAILABLE = False
try:
    from skills.decision_synthesizer import DecisionSynthesizer
    SYNTHESIZER_AVAILABLE = True
    log_json("INFO", "Decision Synthesizer loaded")
except ImportError:
    log_json("WARN", "Decision synthesizer not available")


class ICTPredictRequest(BaseModel):
    ohlcv: dict
    context: dict
    ict_context: dict


@app.post("/v1/predict-ict", response_model=PredictResponse)
async def predict_ict(request: ICTPredictRequest):
    """
    Full integration endpoint with ICT context.
    Combines OHLCV data, trading context, AND ICT knowledge.
    """
    if not model_loaded:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    try:
        ict_ctx = request.ict_context
        sweep = ict_ctx.get("sweep", "")
        fvg_type = ict_ctx.get("fvg_type", "")
        trend = ict_ctx.get("trend", "UNKNOWN")
        session = ict_ctx.get("session", "")
        bos_aligned = ict_ctx.get("bos_aligned", False)
        htf_bias_ok = ict_ctx.get("htf_bias_ok", False)

        log_json("INFO", "ICT-enhanced prediction request",
                 sweep=sweep,
                 fvg_type=fvg_type,
                 trend=trend,
                 session=session,
                 bos_aligned=bos_aligned,
                 htf_bias_ok=htf_bias_ok,
                 ict_available=ICT_CONTEXT_AVAILABLE)

        ict_prompt = ""
        if ICT_CONTEXT_AVAILABLE and ict_retriever:
            ict_prompt = build_ict_prompt(
                sweep=sweep,
                fvg_type=fvg_type,
                trend=trend,
                session=session,
                bos_aligned=bos_aligned,
                htf_bias_ok=htf_bias_ok
            )
            log_json("INFO", "ICT context retrieved", prompt_length=len(ict_prompt))

        ctx = request.context
        direction = ctx.get("direction", "BUY")
        setup_type = ctx.get("setup_type", "UNKNOWN")

        df = pd.DataFrame(list(request.ohlcv.values()))
        ohlcv_tensor = tokenize_ohlcv(df)

        agree, confidence = run_inference(ohlcv_tensor)

        reason_detail = []

        expected_trend = "BULLISH" if direction == "BUY" else "BEARISH"
        if trend != expected_trend and trend != "UNKNOWN":
            confidence *= 0.7
            reason_detail.append(f"HTF/ICT trend conflict")

        if setup_type == "CONTINUATION" and not bos_aligned:
            confidence *= 0.8
            reason_detail.append("Continuation without BOS")

        confidence = max(0.1, min(0.95, confidence))

        agree_str = "Agrees" if agree else "Disagrees"
        reason = f"{agree_str} ({confidence:.0%})"
        if ict_prompt:
            reason += " | ICT Enhanced"
        if reason_detail:
            reason += " | " + " | ".join(reason_detail)

        log_json("INFO", "ICT prediction complete",
                 agree=agree,
                 confidence=round(confidence, 2),
                 ict_context_used=bool(ict_prompt))

        return PredictResponse(
            agree=agree,
            confidence=round(confidence, 2),
            reason=reason
        )

    except Exception as e:
        log_json("ERROR", f"ICT prediction failed: {str(e)}")
        return PredictResponse(
            agree=True,
            confidence=0.5,
            reason=f"API Fallback (Error: {str(e)})"
        )


@app.get("/ict/concepts")
async def list_concepts():
    """List all available ICT concepts"""
    if not ICT_CONTEXT_AVAILABLE:
        return {"error": "ICT module not available", "concepts": []}
    
    try:
        concepts = ict_retriever.get_all_concepts()
        concept_list = []
        
        for cid in concepts:
            cdata = ict_retriever.get_concept_by_id(cid)
            concept_list.append({
                "id": cid,
                "name": cdata.get("name", ""),
                "definition": cdata.get("definition", ""),
                "example_count": len(cdata.get("examples", []))
            })
        
        return {"concepts": concept_list, "total": len(concept_list)}
    
    except Exception as e:
        return {"error": str(e), "concepts": []}


@app.post("/ict/retrieve")
async def retrieve_ict_context(context: dict):
    """Retrieve ICT context based on system state"""
    if not ICT_CONTEXT_AVAILABLE:
        return {"error": "ICT module not available"}
    
    try:
        result = ict_retriever.retrieve_by_system_state(context)
        result["prompt"] = ict_retriever.build_context_prompt(context)
        return result
    
    except Exception as e:
        return {"error": str(e)}


class ConceptOutcomeRequest(BaseModel):
    trade_id: str
    outcome: str
    rr_result: float
    pnl: Optional[float] = None
    market_context: Optional[Dict] = None


@app.post("/concept/outcome")
async def record_concept_outcome(request: ConceptOutcomeRequest):
    """Record trade outcome with market context for conditional learning"""
    if not CONCEPT_TRACKER_AVAILABLE:
        return {"error": "Concept tracker not available"}
    
    try:
        concept_tracker.record_outcome(
            trade_id=request.trade_id,
            outcome=request.outcome,
            rr_result=request.rr_result,
            pnl=request.pnl,
            market_context=request.market_context
        )
        return {"status": "recorded", "trade_id": request.trade_id}
    
    except Exception as e:
        return {"error": str(e)}


@app.get("/concept/stats")
async def get_concept_stats():
    """Get concept performance statistics"""
    if not CONCEPT_TRACKER_AVAILABLE:
        return {"error": "Concept tracker not available"}
    
    try:
        all_stats = concept_tracker.get_all_stats()
        top = concept_tracker.get_top_concepts(3)
        bottom = concept_tracker.get_bottom_concepts(3)
        
        return {
            "stats": all_stats,
            "top_performers": [
                {"concept": c, "win_rate": w, "avg_rr": r, "uses": u}
                for c, w, r, u in top
            ],
            "bottom_performers": [
                {"concept": c, "win_rate": w, "avg_rr": r, "uses": u}
                for c, w, r, u in bottom
            ]
        }
    
    except Exception as e:
        return {"error": str(e)}


class SynthesizeRequest(BaseModel):
    concepts: List[str]
    market_context: Dict
    direction: str
    ohlcv: Optional[Dict] = None


@app.post("/synthesize")
async def synthesize_decision(request: SynthesizeRequest):
    """
    Decision Synthesis v1.1 - collapses multiple signals into single belief with layered scoring
    
    Score architecture:
    - base_edge: "this setup should work" (0-1)
    - confidence: "we trust that belief" (0-1)  
    - conflict_score: "how messy is this setup" (0-1)
    - final_score = base_edge * confidence * (1 - conflict_score)
    """
    try:
        synthesizer = DecisionSynthesizer(concept_tracker)
        
        decision = synthesizer.synthesize(
            concepts=request.concepts,
            market_context=request.market_context,
            setup_direction=request.direction,
            ohlcv_data=request.ohlcv
        )
        
        return {
            "base_edge": decision.base_edge,
            "confidence": decision.confidence,
            "conflict_score": decision.conflict_score,
            "conflict_level": decision.conflict_level,
            "final_score": decision.final_score,
            "tier": decision.tier,
            "verdict": decision.verdict,
            "conflict_signals": decision.conflict_signals,
            "position_size": decision.position_size,
            "supporting_factors": decision.supporting_factors[:5],
            "concern_factors": decision.concern_factors[:3],
            "critique": decision.critique,
            "kronos_context": synthesizer.format_for_kronos(decision)
        }
    
    except Exception as e:
        log_json("ERROR", f"Synthesis failed: {str(e)}")
        return {"error": str(e), "final_score": 0, "verdict": "SKIP"}


if __name__ == "__main__":
    log_json("INFO", "Starting Kronos API Server...", host=HOST, port=PORT)
    log_json("INFO", f"ICT RAG Integration: {'ENABLED' if ICT_CONTEXT_AVAILABLE else 'DISABLED'}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")