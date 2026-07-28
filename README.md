# Project Vuka — Agent Ingwe

> *"The leopard does not miss because it does not rush."*

**Vuka** is an ICT (Inner Circle Trader) inspired forex trading agent built around a multi-timeframe confluence engine, a Kronos AI veto gate, and a high-performance tick/candle event loop. It runs on MetaTrader 5 via Python and is designed for disciplined, risk-first execution.

---

## Architecture

| Component | Role |
|-----------|------|
| **Agent Ingwe** | Core trading loop — signal generation, execution, position management |
| **Kronos** | Local FastAPI server hosting a transformer model for AI-based trade veto |
| **Supervisor** | Process manager — starts/stops/monitors multiple bot instances |
| **Dashboard** | Real-time TUI monitoring of P&L, open positions, and confluence scores |
| **SQLite** | Persistent trade log with WAL mode, deduplication, and atomic writes |

---

## Requirements

- **Python** 3.10+
- **MetaTrader 5** terminal running (Windows natively; Linux via Wine)
- **Git**
- ~4 GB disk space (includes PyTorch CPU/GPU)

---

## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/Zen-sen/Project-Vuka.git
cd Project-Vuka
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and add your HF_TOKEN and Telegram credentials (optional)
```

### 3. Run a Backtest

```bash
python ingwe.py EURUSD INGWE --backtest
```

### 4. Run Live (Paper / Demo Account Recommended)

```bash
python ingwe.py EURUSD INGWE
```

### 5. Start the Dashboard

```bash
python dashboard.py
```

### 6. Start the Supervisor (Multi-Bot)

```bash
python supervisor.py
```

---

## Commands

| Command | Description |
|---------|-------------|
| `python ingwe.py EURUSD INGWE` | Run live Ingwe strategy on EURUSD |
| `python ingwe.py EURUSD INGWE --backtest` | Backtest mode |
| `python ingwe.py EURUSD INGWE --test` | Test mode (no real orders) |
| `python ingwe.py EURUSD INGWE --check` | Health check only |
| `python ingwe.py EURUSD INGWE --fast` | Fast backtest (no delay) |
| `python dashboard.py` | Launch TUI dashboard |
| `python supervisor.py` | Launch multi-bot supervisor |
| `python kronos_server.py` | Start Kronos AI server |
| `python -m pytest` | Run test suite |

---

## Strategies

| Strategy | Timeframe | Best Session | Description |
|----------|-----------|--------------|-------------|
| **INGWE** | M15 | London / NY | Full confluence: HTF bias + liquidity sweep + FVG + breaker + unicorn |
| **SILVER_BULLET** | M5 | NY Open | ICT Silver Bullet — 1-hour window precision |
| **ICT_M1** | M1 | London | High-frequency M1 scalping with strict filters |
| **LONDON_OPEN** | M15 | London | Breakout strategy targeting the London session open |

---

## Risk Management

- **Per-trade risk**: 0.5% – 2.0% of equity (strategy-dependent)
- **Max daily loss**: Hard stop at -3.0% equity
- **Max drawdown**: Circuit breaker at -5.0% equity
- **Consecutive losses**: Cooldown after 3 consecutive losses
- **Lot cap**: 5.0 lots absolute maximum
- **AI Veto**: Kronos can reject signals below 0.60 confidence

---

## License

MIT License — see `LICENSE` for details.
