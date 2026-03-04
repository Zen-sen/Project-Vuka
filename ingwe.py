import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone
import pytz
import pandas as pd
import time
import json
import os
import numpy as np

# ============================================================
#   PROJECT VUKA — AGENT INGWE
#   "The leopard does not miss because it does not rush."
#   Built in South Africa | Est. February 2026
# ============================================================

SYMBOL = "EURUSDc"
TIMEFRAME = mt5.TIMEFRAME_M15
RISK_PERCENT = 1.0          # Risk 1% of equity per trade
RISK_REWARD_RATIO = 3.0     # 1:3 risk:reward
ATR_PERIOD = 14
ATR_MULTIPLIER = 1.5        # ATR x 1.5 for stop loss
ADX_MIN_THRESHOLD = 0       # Disabled — other filters are sufficient
MIN_SPREAD = 0.0002         # Max 2 pips spread
MAX_DAILY_LOSS = 50.0       # Daily loss limit in USC
MAX_DRAWDOWN = 10.0         # 10% equity drawdown limit
MAX_LOT_SIZE = 0.10         # Hard cap — never exceed this
LOG_FILE = "trades.json"
SESSIONS_FILE = "sessions_today.json"

# Killzones (SAST GMT+2)
KILLZONES = {
    "Asian":         (1, 4),
    "London Open":   (8, 10),
    "New York Open": (13, 15),
    "London Close":  (16, 17),
}

# News blackout periods (local time hours)
NEWS_BLACKOUTS = [
    (6, 30, 7, 30),
    (12, 0, 13, 0),
]

# Session quality weights
SESSION_WEIGHTS = {
    "Asian":         0.8,
    "London Open":   1.5,
    "New York Open": 1.3,
    "London Close":  0.9,
}

initial_equity = None
consecutive_losses = 0
circuit_breaker_timestamp = None
last_3_days_pnl = []

# ─── SESSION PERSISTENCE ────────────────────────────────────

def load_sessions():
    """Load today's traded sessions from file — survives restarts"""
    try:
        if os.path.exists(SESSIONS_FILE):
            with open(SESSIONS_FILE, "r") as f:
                data = json.load(f)
                if data.get("date") == datetime.now().strftime("%Y-%m-%d"):
                    return set(data.get("sessions", []))
    except Exception:
        pass
    return set()

def save_sessions(sessions):
    """Save traded sessions to file"""
    try:
        with open(SESSIONS_FILE, "w") as f:
            json.dump({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "sessions": list(sessions)
            }, f)
    except Exception as e:
        print(f"Warning: Could not save sessions: {e}")

sessions_traded_today = load_sessions()

# ─── TIMEZONE & DST ─────────────────────────────────────────

def get_last_sunday(year, month):
    if month == 12:
        last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = datetime(year, month + 1, 1) - timedelta(days=1)
    while last_day.weekday() != 6:
        last_day -= timedelta(days=1)
    return last_day

def is_dst():
    today = datetime.now()
    return get_last_sunday(today.year, 3) <= today <= get_last_sunday(today.year, 10)

def get_tz_offset():
    return 3 if is_dst() else 2

# ─── ACCOUNT ────────────────────────────────────────────────

def get_initial_equity():
    global initial_equity
    if initial_equity is None:
        account = mt5.account_info()
        initial_equity = account.equity
    return initial_equity

def check_equity_drawdown():
    account = mt5.account_info()
    initial = get_initial_equity()
    if account is None:
        return False
    drawdown_percent = ((initial - account.equity) / initial) * 100
    return drawdown_percent > MAX_DRAWDOWN

# ─── SESSION ────────────────────────────────────────────────

def get_current_session():
    offset = get_tz_offset()
    now = datetime.now(timezone.utc) + timedelta(hours=offset)
    hour = now.hour
    for session, (start, end) in KILLZONES.items():
        if start <= hour < end:
            return session
    return None

def reset_daily_sessions():
    global consecutive_losses, sessions_traded_today
    offset = get_tz_offset()
    now = datetime.now(timezone.utc) + timedelta(hours=offset)
    if now.hour == 0 and now.minute < 15:
        sessions_traded_today.clear()
        save_sessions(sessions_traded_today)
        consecutive_losses = 0
        print("Midnight reset — sessions and loss counter cleared.")

# ─── TREND ──────────────────────────────────────────────────

def get_h1_trend():
    """H1 trend via EMA20 vs EMA50 crossover"""
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, 50)
    if rates is None or len(rates) < 50:
        return None
    df = pd.DataFrame(rates)
    ema20 = df["close"].ewm(span=20).mean().iloc[-1]
    ema50 = df["close"].ewm(span=50).mean().iloc[-1]
    if ema20 > ema50:
        return "BULLISH"
    elif ema20 < ema50:
        return "BEARISH"
    return None

# ─── CANDLES ────────────────────────────────────────────────

def get_candles():
    rates = mt5.copy_rates_from_pos(SYMBOL, TIMEFRAME, 0, 200)
    if rates is None:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df

# ─── SWEEP DETECTION ────────────────────────────────────────

def detect_liquidity_sweep(df):
    """Detect break of previous high/low within last 5 candles"""
    recent = df.tail(5)
    prev_high = recent["high"].iloc[:-1].max()
    prev_low = recent["low"].iloc[:-1].min()
    last = recent.iloc[-1]
    if last["high"] > prev_high:
        return "SWEEP_HIGH", prev_high
    if last["low"] < prev_low:
        return "SWEEP_LOW", prev_low
    return None, None

# ─── FVG DETECTION ──────────────────────────────────────────

def check_displacement_validity(c1, c2):
    c1_range = c1["high"] - c1["low"]
    c2_range = c2["high"] - c2["low"]
    if c1_range == 0:
        return True
    return c2_range > (c1_range * 1.5)

def detect_fvg(df):
    """Detect Fair Value Gaps with displacement confirmation"""
    fvgs = []
    for i in range(2, len(df) - 1):
        c1 = df.iloc[i-2]
        c2 = df.iloc[i-1]
        c3 = df.iloc[i]
        if not check_displacement_validity(c1, c2):
            continue
        # Bullish FVG
        if c1["high"] < c3["low"]:
            fvg_gap = c3["low"] - c1["high"]
            fvg_50 = c1["high"] + (fvg_gap * 0.5)
            fvgs.append(("BULLISH_FVG", c1["high"], c3["low"], i, c2, fvg_50))
        # Bearish FVG
        if c3["high"] < c1["low"]:
            fvg_gap = c1["low"] - c3["high"]
            fvg_50 = c1["low"] - (fvg_gap * 0.5)
            fvgs.append(("BEARISH_FVG", c3["high"], c1["low"], i, c2, fvg_50))
    return fvgs[-3:] if fvgs else []

# ─── INDICATORS ─────────────────────────────────────────────

def calculate_adx_wilder(df, period=14):
    if len(df) < period * 2:
        return None, None, None
    high_diff = df['high'].diff()
    low_diff = -df['low'].diff()
    plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0)
    minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0)
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            np.abs(df['high'] - df['close'].shift(1)),
            np.abs(df['low'] - df['close'].shift(1))
        )
    )
    tr_sum = df['tr'].tail(period).sum()
    if tr_sum == 0:
        return 0, 0, 0
    plus_di = 100 * (np.sum(plus_dm[-period:]) / tr_sum)
    minus_di = 100 * (np.sum(minus_dm[-period:]) / tr_sum)
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) != 0 else 0
    return dx, plus_di, minus_di

def calculate_atr(df, period=14):
    if df is None or len(df) < period:
        return None
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    tr = np.zeros(len(df))
    tr[0] = high[0] - low[0]
    for i in range(1, len(df)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    return np.mean(tr[-period:])

# ─── RISK & POSITION ────────────────────────────────────────

def calculate_lot_size(atr=None):
    """1% risk with ATR stop — hard capped at MAX_LOT_SIZE"""
    account = mt5.account_info()
    if account is None:
        return 0.01
    if atr is None:
        atr = 0.0005
    risk_amount = account.equity * (RISK_PERCENT / 100)
    stop_points = atr * ATR_MULTIPLIER
    lot_size = risk_amount / (stop_points * 100000)
    lot_size = max(lot_size, 0.01)
    lot_size = min(lot_size, MAX_LOT_SIZE)  # Hard cap — Ingwe never over-extends
    return round(lot_size, 2)

def get_session_overlap_multiplier():
    offset = get_tz_offset()
    now = datetime.now(timezone.utc) + timedelta(hours=offset)
    if 13 <= now.hour < 17:
        return 1.2
    return 1.0

# ─── GUARDS ─────────────────────────────────────────────────

def get_spread():
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        return None
    return tick.ask - tick.bid

def is_in_news_blackout():
    offset = get_tz_offset()
    now = datetime.now(timezone.utc) + timedelta(hours=offset)
    hour, minute = now.hour, now.minute
    for start_h, start_m, end_h, end_m in NEWS_BLACKOUTS:
        if (start_h < hour < end_h) or (start_h == hour and minute >= start_m) or (end_h == hour and minute < end_m):
            return True
    return False

def check_panic_candle(df, atr):
    if atr is None or len(df) < 1:
        return False
    current_range = df.iloc[-1]["high"] - df.iloc[-1]["low"]
    is_panic = current_range > (atr * 2)
    if is_panic:
        print(f"Panic candle detected (range {current_range:.5f} > 2xATR {atr*2:.5f}). Rejecting.")
    return is_panic

def check_pre_trade_spread():
    spread = get_spread()
    if spread is None or spread > MIN_SPREAD * 2:
        print(f"Pre-trade spread check FAILED: {spread*10000:.1f} pips")
        return False
    return True

def check_premium_discount_zone(df, current_price, direction):
    if len(df) < 20:
        return True
    recent_20 = df.iloc[-20:]
    range_high = recent_20["high"].max()
    range_low = recent_20["low"].min()
    price_range = range_high - range_low
    if price_range == 0:
        return True
    if direction == "BUY":
        return current_price <= range_low + (price_range * 0.50)
    else:
        return current_price >= range_high - (price_range * 0.50)

def get_daily_loss():
    deals = mt5.history_deals_get(
        datetime.now().replace(hour=0, minute=0, second=0),
        datetime.now()
    )
    if deals is None:
        return 0.0
    return sum(d.profit for d in deals)

def check_consecutive_losses():
    global consecutive_losses
    deals = mt5.history_deals_get(
        datetime.now().replace(hour=0, minute=0, second=0),
        datetime.now()
    )
    if deals is None or len(deals) < 2:
        return False
    last_two = list(deals)[-2:]
    if all(deal.profit < 0 for deal in last_two):
        consecutive_losses = 2
        return True
    consecutive_losses = 0
    return False

# ─── CONFLUENCE ─────────────────────────────────────────────

def get_dynamic_confluence_threshold(atr, df):
    adx, _, _ = calculate_adx_wilder(df)
    if adx is None or adx < ADX_MIN_THRESHOLD:
        return 80
    elif adx > 40:
        return 60
    return 70

def calculate_confluence_score(trend_strength, ob_fvg_confirmed, in_zone, spread_tight, adx_valid):
    score = 0
    if trend_strength in ("BULLISH", "BEARISH"):
        score += 40
    if ob_fvg_confirmed:
        score += 30
    if in_zone:
        score += 20
    if spread_tight:
        score += 10
    return score

# ─── TRADE EXECUTION ────────────────────────────────────────

def log_trade(direction, entry, sl, tp, result, lot_size):
    log = []
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as f:
            log = json.load(f)
    log.append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "lot_size": lot_size,
        "retcode": result.retcode
    })
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)
    print(f"Trade logged to {LOG_FILE}")

def place_trade(direction, entry, sl, tp, lot_size):
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": lot_size,
        "type": order_type,
        "price": entry,
        "sl": sl,
        "tp": tp,
        "deviation": 10,
        "magic": 234000,
        "comment": "Project Vuka - Agent Ingwe",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }
    return mt5.order_send(request)

# ─── MAIN LOOP ──────────────────────────────────────────────

def run_agent():
    global sessions_traded_today

    print(f"\n--- Ingwe Scan: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    print(f"Timezone: GMT+{get_tz_offset()}")

    reset_daily_sessions()

    # Guard: Drawdown
    if check_equity_drawdown():
        account = mt5.account_info()
        initial = get_initial_equity()
        drawdown = ((initial - account.equity) / initial) * 100
        print(f"DRAWDOWN LIMIT EXCEEDED ({drawdown:.2f}%). Ingwe stands down.")
        return

    # Guard: Circuit breaker
    if check_consecutive_losses():
        print("2 CONSECUTIVE LOSSES — Ingwe paused. Psychological protection active.")
        return

    # Guard: News blackout
    if is_in_news_blackout():
        print("News blackout — Ingwe waits...")
        return

    # Guard: Daily loss
    daily_pnl = get_daily_loss()
    print(f"Daily P&L: ${daily_pnl:.2f}")
    if daily_pnl <= -MAX_DAILY_LOSS:
        print(f"DAILY LOSS LIMIT REACHED (${MAX_DAILY_LOSS}). Ingwe rests for today.")
        return

    # H1 Trend
    trend = get_h1_trend()
    if not trend:
        print("H1 trend unclear. Waiting...")
        return
    print(f"H1 Trend: {trend}")

    # Killzone
    session = get_current_session()
    if not session:
        print("No killzone active. Ingwe watches...")
        return
    print(f"KILLZONE ACTIVE: {session}")

    # Session lock — persisted to file
    if session in sessions_traded_today:
        print(f"Already traded {session} today. Ingwe waits for next session.")
        return

    # Candles
    df = get_candles()
    if df is None or df.empty:
        print("Failed to get candles.")
        return

    # ADX
    adx, plus_di, minus_di = calculate_adx_wilder(df)
    if adx is None or adx < ADX_MIN_THRESHOLD:
        adx_display = f"{adx:.1f}" if adx else "N/A"
        print(f"ADX ({adx_display}) below threshold. Waiting...")
        return
    print(f"ADX: {adx:.1f} (+DI: {plus_di:.1f}, -DI: {minus_di:.1f})")

    # Sweep
    sweep, level = detect_liquidity_sweep(df)
    if not sweep:
        print("No sweep detected. Ingwe waits...")
        return
    print(f"SWEEP: {sweep} at {level:.5f}")

    # FVG
    fvgs = detect_fvg(df)
    if not fvgs:
        print("No confirmed FVG. Ingwe waits...")
        return

    # Current price & ATR
    tick = mt5.symbol_info_tick(SYMBOL)
    current_price = tick.bid
    atr = calculate_atr(df, ATR_PERIOD)
    if atr is None:
        print("Insufficient data for ATR.")
        return

    # Lot size (capped)
    lot_size = calculate_lot_size(atr)
    overlap_multiplier = get_session_overlap_multiplier()
    if overlap_multiplier > 1.0:
        lot_size = min(round(lot_size * overlap_multiplier, 2), MAX_LOT_SIZE)
        print(f"London/NY Overlap — Position: {lot_size} lots (1.2x, capped at {MAX_LOT_SIZE})")

    # Spread
    spread = get_spread()
    spread_tight = spread is not None and spread < MIN_SPREAD
    print(f"Spread: {spread*10000:.1f} pips" if spread else "Spread: N/A")
    print(f"Current Price: {current_price:.5f} | ATR: {atr:.5f} | Lot: {lot_size}")

    confluence_threshold = get_dynamic_confluence_threshold(atr, df)
    print(f"Confluence Threshold: {confluence_threshold}/100")

    for fvg in fvgs:
        fvg_type, fvg_low, fvg_high, fvg_idx, ob_candle, fvg_50_level = fvg

        # Guard: Panic candle
        if check_panic_candle(df, atr):
            print("Panic candle — Ingwe does not chase.")
            continue

        # ── BULLISH SETUP ──────────────────────────────────
        if sweep == "SWEEP_LOW" and fvg_type == "BULLISH_FVG" and trend == "BULLISH":
            if plus_di <= minus_di:
                print(f"Directional filter failed: +DI ({plus_di:.1f}) <= -DI ({minus_di:.1f}). Skipping.")
                continue
            in_discount = check_premium_discount_zone(df, current_price, "BUY")
            if not in_discount:
                print("Price not in discount zone. Skipping.")
                continue
            score = calculate_confluence_score(trend, True, True, spread_tight, True)
            print(f"Confluence Score: {score}/100")
            if score < confluence_threshold:
                print(f"Score {score} below threshold {confluence_threshold}. Skipping.")
                continue
            if not check_pre_trade_spread():
                continue
            atr_stop = atr * ATR_MULTIPLIER
            sl = round(current_price - atr_stop, 5)
            tp = round(current_price + (atr_stop * RISK_REWARD_RATIO), 5)
            result = place_trade("BUY", current_price, sl, tp, lot_size)
            print(f"🐆 BUY PLACED: Entry={current_price:.5f} SL={sl:.5f} TP={tp:.5f} Lot={lot_size} Code={result.retcode}")
            log_trade("BUY", current_price, sl, tp, result, lot_size)
            sessions_traded_today.add(session)
            save_sessions(sessions_traded_today)
            return

        # ── BEARISH SETUP ──────────────────────────────────
        if sweep == "SWEEP_HIGH" and fvg_type == "BEARISH_FVG" and trend == "BEARISH":
            if minus_di <= plus_di:
                print(f"Directional filter failed: -DI ({minus_di:.1f}) <= +DI ({plus_di:.1f}). Skipping.")
                continue
            in_premium = check_premium_discount_zone(df, current_price, "SELL")
            if not in_premium:
                print("Price not in premium zone. Skipping.")
                continue
            score = calculate_confluence_score(trend, True, True, spread_tight, True)
            print(f"Confluence Score: {score}/100")
            if score < confluence_threshold:
                print(f"Score {score} below threshold {confluence_threshold}. Skipping.")
                continue
            if not check_pre_trade_spread():
                continue
            atr_stop = atr * ATR_MULTIPLIER
            sl = round(current_price + atr_stop, 5)
            tp = round(current_price - (atr_stop * RISK_REWARD_RATIO), 5)
            result = place_trade("SELL", current_price, sl, tp, lot_size)
            print(f"🐆 SELL PLACED: Entry={current_price:.5f} SL={sl:.5f} TP={tp:.5f} Lot={lot_size} Code={result.retcode}")
            log_trade("SELL", current_price, sl, tp, result, lot_size)
            sessions_traded_today.add(session)
            save_sessions(sessions_traded_today)
            return

    print("Conditions not aligned. Ingwe waits for the right moment...")


# ─── STARTUP ────────────────────────────────────────────────

if not mt5.initialize():
    print("MT5 connection failed")
else:
    print("=" * 55)
    print("   PROJECT VUKA — AGENT INGWE")
    print("   The leopard does not miss because it does not rush.")
    print("=" * 55)
    mt5.symbol_select(SYMBOL, True)
    get_initial_equity()
    while True:
        run_agent()
        time.sleep(900)
