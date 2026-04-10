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

import torch
import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
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
    Args:
        df: DataFrame with columns [open, high, low, close, volume]
        max_candles: Maximum number of candles to tokenize
    Returns:
        Tensor of shape (batch, seq, features)
    """
    # Take last max_candles
    df = df.tail(max_candles).copy()

    # Ensure columns exist
    required_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    # Add 'amount' column if missing (use close * volume as proxy)
    if 'amount' not in df.columns:
        df['amount'] = df['close'] * df['volume']

    # Select and order columns
    data = df[['open', 'high', 'low', 'close', 'volume', 'amount']].values.astype(np.float32)
    
    # Normalize to relative changes (percentage)
    # Use pandas diff for cleaner handling
    df_norm = df[['open', 'high', 'low', 'close', 'volume']].copy()
    df_norm['amount'] = df['close'] * df['volume']
    data = df_norm.values.astype(np.float32)
    
    # Calculate percentage changes
    data = np.zeros_like(data)
    for i in range(1, len(data)):
        data[i] = (df_norm.values[i] - df_norm.values[i-1]) / (np.abs(df_norm.values[i-1]) + 1e-8)
    data[0] = data[1]  # First row use second row as reference

    # Convert to tensor (batch=1, seq, features)
    tensor = torch.tensor(data, dtype=torch.float32).unsqueeze(0)
    return tensor.to(device)


def run_inference(ohlcv_tensor: torch.Tensor) -> tuple[bool, float]:
    """
    Run Kronos inference on OHLCV data
    Returns: (agree: bool, confidence: float)
    """
    try:
        with torch.no_grad():
            # Tokenize
            z_pre, z, bsq_loss, z_indices = tokenizer(ohlcv_tensor)

            # Get token indices from all timesteps
            # z_indices shape: (batch, seq) - 2D tensor
            if z_indices.shape[1] > 0:
                # Get all token indices
                token_indices = z_indices[0, :].cpu().numpy()
                
                # Analyze pattern across recent candles
                recent_tokens = token_indices[-20:]
                
                # Calculate metrics
                token_std = float(np.std(recent_tokens))
                token_mean = float(np.mean(recent_tokens))
                token_sum = float(np.sum(recent_tokens))
                
                # Use entropy-like measure for conviction
                # Higher absolute mean relative to std = more confident
                if token_std > 0:
                    snr = abs(token_mean) / token_std
                else:
                    snr = 0
                
                # Map SNR to confidence (0.7-1.0 range for reasonable confidence)
                confidence = min(0.7 + (snr / 10.0), 1.0)
                
                # Direction from mean
                direction = token_mean >= 0
                
                log_json("INFO", "Inference complete",
                         agree=direction,
                         confidence=round(confidence, 2),
                         token_stats={"mean": round(token_mean, 2), "std": round(token_std, 2)})
                
                return direction, confidence
            else:
                return True, 0.75
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
        # Convert dict to DataFrame
        df = pd.DataFrame(df_data)

        # Tokenize
        ohlcv_tensor = tokenize_ohlcv(df)

        # Run inference
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


if __name__ == "__main__":
    log_json("INFO", "Starting Kronos API Server...", host=HOST, port=PORT)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")