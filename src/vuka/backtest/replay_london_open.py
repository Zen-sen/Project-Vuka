"""replay_london_open.py -- Faithful LONDON_OPEN replay over the past month.

Drives the REAL evaluate_london_breakout() from src/vuka/strategies/london_open.py
candle-by-candle over M15 history pulled from the live MT5 terminal, mirroring the
gate chain in bot.run_agent():

    weekend -> dead zone -> London Open killzone (SAST) -> once/session/day ->
    market circuit phase (CHOP block) -> sweep -> FVG -> strategy
    (Asian range / PDH-PDL / retest / premium-discount / score / spread)

Differences from bot.py --backtest (which cannot replay London Open faithfully):
  * get_asian_range()/get_pdh_pdl() use the CANDLE's date, not the real clock / live MT5.
  * The order layer is mocked: place_trade/log_trade record locally, nothing is sent.
  * Exits are simulated on M15 bars with the live trailing rules from position_manager
    (SL to BE at 1R, SL to 1R at 2R).
  * The unified logger is silenced so the live vuka_trading.db is not polluted.

Usage:
    python replay_london_open.py [--symbols GBPUSDc,EURUSDc] [--days 35]
                                 [--balance 4169.11] [--spread 0.00012]
                                 [--phase-gate] [--out results/replay_london_open.json]
"""

import argparse
import inspect
import json
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vuka.core.state import s
from vuka.market_structure.ict import (
    detect_liquidity_sweep,
    detect_fvg,
    detect_immediate_fvg,
    calculate_atr,
)

LONDON_OPEN_SUMMER_SAST = (9, 12)
SAST_OFFSET_HOURS = 2
KILLZONE_NAME = "London Open"
DEAD_ZONE_SAST = (12, 15)


class _SilentLogger:
    def __init__(self, sink=None):
        self._sink = sink

    def _rec(self, message):
        if self._sink is not None:
            self._sink.append(message)

    def log(self, level, message, **k):
        self._rec(message)

    def info(self, message, **k):
        self._rec(message)

    def warn(self, message, **k):
        self._rec(message)

    def warning(self, message, **k):
        self._rec(message)

    def error(self, message, **k):
        self._rec(message)

    def trade(self, message, **k):
        self._rec(message)

    def guard(self, message, **k):
        self._rec(message)


def _noop(*a, **k):
    pass


_EVAL_SRC = None


def build_variant(name: str):
    """Return evaluate_london_breakout with configurable strictness.

    Derived from the live source each run so it tracks strategy edits. Flags:
      strict    -- retest + FVG-beyond-Asian-range required (live behaviour)
      no_retest -- FVG still must clear the Asian range; entry need not retest
      loose     -- any directional FVG; entry need not retest
      wider-sl  -- wider stop losses (2.0x ATR multiplier) for survival trading
      survival  -- widest SL + no retest + minimal FVG requirements
    """
    global _EVAL_SRC
    import vuka.strategies.london_open as lo

    if _EVAL_SRC is None:
        _EVAL_SRC = inspect.getsource(lo.evaluate_london_breakout)

    require_beyond = name not in ("loose", "wider-sl", "survival")
    require_retest = name == "strict"

    src = _EVAL_SRC
    if not require_beyond:
        src = src.replace("if fvg_low < asian_high:", "if False:")
        src = src.replace("if fvg_high > asian_low:", "if False:")
    if not require_retest:
        src = src.replace("if not in_retest:", "if False:")
    if name == "survival":
        src = src.replace("if spread_ok:", "if True:")
        src = src.replace("if fvg_low > asian_high + atr * 0.5:", "if True:")

    ns = dict(lo.__dict__)
    exec(compile(src, f"london_open_{name}", "exec"), ns)
    return ns["evaluate_london_breakout"]


@dataclass
class OpenPosition:
    symbol: str
    direction: str
    entry: float
    sl: float
    tp: float
    lot: float
    entry_time: pd.Timestamp
    entry_idx: int
    session: str
    context: dict = field(default_factory=dict)
    bars_held: int = 0
    trail_be_at: float = 1.0


@dataclass
class ClosedTrade:
    symbol: str
    direction: str
    entry: float
    sl: float
    tp: float
    lot: float
    entry_time: str
    exit_time: str
    exit_price: float
    outcome: str
    pnl_usc: float
    pnl_usd: float
    bars_held: int
    session: str = ""
    context: dict = field(default_factory=dict)


class SymbolSpec:
    def __init__(self, symbol: str):
        info = mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol_info unavailable for {symbol}")
        self.symbol = symbol
        self.tick_size = float(info.trade_tick_size)
        self.tick_value = float(info.trade_tick_value)
        self.volume_min = float(info.volume_min)
        self.volume_step = float(info.volume_step)
        self.volume_max = float(info.volume_max)
        self.digits = int(info.digits)


def pull_rates(symbol: str, days: int):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    out = {}
    for name, tf in (("m15", mt5.TIMEFRAME_M15), ("h1", mt5.TIMEFRAME_H1)):
        rates = mt5.copy_rates_range(symbol, tf, start, now)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"copy_rates_range returned nothing for {symbol} {name}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        out[name] = df.reset_index(drop=True)
    return out


def run_replay(symbol: str, data: dict, spec: SymbolSpec, cfg: dict) -> dict:
    m15 = data["m15"]
    h1 = data["h1"]
    n = len(m15)

    balance_usc = cfg["balance_usc"]
    start_balance_usc = balance_usc
    sessions_traded = set()
    sessions_day = None
    open_positions: list[OpenPosition] = []
    closed: list[ClosedTrade] = []

    gate = {
        "candles_scanned": 0,
        "weekend_skip": 0,
        "not_london_open": 0,
        "dead_zone_skip": 0,
        "already_traded": 0,
        "phase_blocked": 0,
        "phase_unknown": 0,
        "no_sweep": 0,
        "no_fvg": 0,
        "reached_strategy": 0,
        "trades_placed": 0,
    }

    dbg_sink = []
    cfg.setdefault("debug", False)

    circuit = None
    if cfg["phase_gate"]:
        from skills import market_circuit as mc_mod

        mc_mod.logger = _SilentLogger()
        circuit = mc_mod.MarketCircuit()
        circuit._save = _noop

    replay_index = {"i": 0}

    def asian_range_for(df):
        t = df["time"].iloc[-1]
        day = t.date()
        start = pd.Timestamp(day, tz="UTC")
        end = start + timedelta(hours=4)
        w = df[(df["time"] >= start) & (df["time"] < end)]
        if len(w) < 4:
            return None, None
        return float(w["high"].max()), float(w["low"].min())

    def pdh_pdl_for():
        t = m15["time"].iloc[replay_index["i"]]
        day = t.date()
        prior = None
        for d in m15["time"].dt.date:
            if d < day:
                prior = d
        if prior is None:
            return None, None
        w = m15[m15["time"].dt.date == prior]
        return float(w["high"].max()), float(w["low"].min())

    def sim_lot_size(sl_distance):
        if not sl_distance or sl_distance <= 0:
            return spec.volume_min
        sl_ticks = sl_distance / spec.tick_size
        if sl_ticks <= 0:
            return spec.volume_min
        risk_usc = balance_usc * (s.RISK_PERCENT / 100.0)
        lot = risk_usc / (sl_ticks * spec.tick_value)
        step = spec.volume_step
        lot = round(lot / step) * step
        return min(max(lot, spec.volume_min), cfg["hard_lot_cap"])

    cur_atr = {"value": 0.0005}

    def fake_place(direction, entry, sl, tp, lot, session=None, **kw):
        if cfg.get("clamp_sl"):
            min_stop = cur_atr["value"] * s.MIN_SL_ATR_MULTIPLIER
            if direction == "BUY" and sl >= entry:
                sl = entry - min_stop
            if direction == "SELL" and sl <= entry:
                sl = entry + min_stop
        return types.SimpleNamespace(retcode=10009)

    placed_ctx = {}

    def fake_log_trade(direction, entry, sl, tp, res, lot_size, session, context=None, **kw):
        if cfg.get("clamp_sl"):
            min_stop = cur_atr["value"] * s.MIN_SL_ATR_MULTIPLIER
            if direction == "BUY" and sl >= entry:
                sl = entry - min_stop
            if direction == "SELL" and sl <= entry:
                sl = entry + min_stop
        placed_ctx["last"] = {
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "lot": lot_size,
            "session": session,
            "context": context or {},
        }

    import vuka.strategies.london_open as lo
    import vuka.risk.filters as filters
    import vuka.risk.portfolio as portfolio

    lo._logger = _SilentLogger(dbg_sink if cfg.get("debug") else None)
    filters._logger = _SilentLogger()
    filters.log = _noop
    portfolio._log = _noop

    lo.get_spread = lambda: cfg["spread"]
    filters.get_spread = lambda: cfg["spread"]
    lo.calculate_lot_size = sim_lot_size
    lo.place_trade = fake_place
    lo.log_trade = fake_log_trade
    lo._mark_session_traded = _noop

    fake_bot = types.ModuleType("vuka.core.bot")
    fake_bot.get_asian_range = asian_range_for
    fake_bot.get_pdh_pdl = pdh_pdl_for
    fake_bot.save_sessions = _noop
    sys.modules["vuka.core.bot"] = fake_bot

    evaluate_fn = build_variant(cfg.get("variant", "strict"))

    def exit_checks(candle_idx: int):
        nonlocal balance_usc
        row = m15.iloc[candle_idx]
        high, low, close = float(row["high"]), float(row["low"]), float(row["close"])
        still_open = []
        for pos in open_positions:
            dist = abs(pos.entry - pos.sl)
            if dist == 0:
                still_open.append(pos)
                continue
            exit_price = None
            outcome = None
            if pos.direction == "BUY":
                if close >= pos.entry + dist * 2 and pos.sl < pos.entry + dist:
                    pos.sl = round(pos.entry + dist, spec.digits)
                elif close >= pos.entry + dist and pos.sl < pos.entry:
                    pos.sl = round(pos.entry, spec.digits)
                dist = abs(pos.entry - pos.sl)
                if low <= pos.sl:
                    exit_price, outcome = pos.sl, "WIN" if pos.sl > pos.entry else ("BE" if pos.sl == pos.entry else "LOSS")
                elif high >= pos.tp:
                    exit_price, outcome = pos.tp, "WIN"
            else:
                if close <= pos.entry - dist * 2 and pos.sl > pos.entry - dist:
                    pos.sl = round(pos.entry - dist, spec.digits)
                elif close <= pos.entry - dist and pos.sl > pos.entry:
                    pos.sl = round(pos.entry, spec.digits)
                dist = abs(pos.entry - pos.sl)
                if high >= pos.sl:
                    exit_price, outcome = pos.sl, "WIN" if pos.sl < pos.entry else ("BE" if pos.sl == pos.entry else "LOSS")
                elif low <= pos.tp:
                    exit_price, outcome = pos.tp, "WIN"

            if exit_price is None:
                pos.bars_held += 1
                still_open.append(pos)
                continue

            ticks = (exit_price - pos.entry) / spec.tick_size
            pnl_usc = ticks * spec.tick_value * pos.lot
            if pos.direction == "SELL":
                pnl_usc = -pnl_usc
            balance_usc += pnl_usc
            closed.append(
                ClosedTrade(
                    symbol=symbol, direction=pos.direction, entry=pos.entry,
                    sl=pos.sl, tp=pos.tp, lot=pos.lot,
                    entry_time=str(pos.entry_time), exit_time=str(m15.iloc[candle_idx]["time"]),
                    exit_price=exit_price, outcome=outcome, pnl_usc=pnl_usc,
                    pnl_usd=pnl_usc / 100.0, bars_held=pos.bars_held, session=pos.session,
                    context=pos.context,
                )
            )
        open_positions[:] = still_open

    for i in range(n):
        t = m15["time"].iloc[i]
        gate["candles_scanned"] += 1

        if t.weekday() >= 5:
            gate["weekend_skip"] += 1
            continue

        sast_hour = (t + timedelta(hours=SAST_OFFSET_HOURS)).hour
        if not (LONDON_OPEN_SUMMER_SAST[0] <= sast_hour < LONDON_OPEN_SUMMER_SAST[1]):
            gate["not_london_open"] += 1
            exit_checks(i)
            continue
        if DEAD_ZONE_SAST[0] <= sast_hour < DEAD_ZONE_SAST[1]:
            gate["dead_zone_skip"] += 1
            exit_checks(i)
            continue

        day = t.date()
        if day != sessions_day:
            sessions_traded.clear()
            sessions_day = day

        exit_checks(i)

        if KILLZONE_NAME in sessions_traded:
            gate["already_traded"] += 1
            continue

        market_phase = "UNKNOWN"
        phase_adj = {}
        if circuit is not None:
            h1_win = h1[h1["time"] <= t].tail(240)
            m15_win = m15[m15["time"] <= t].tail(96)
            if len(h1_win) >= 30:
                market_phase = circuit.detect(
                    pd.DataFrame({"close": m15_win["close"], "high": m15_win["high"], "low": m15_win["low"]}),
                    pd.DataFrame({"close": m15_win["close"], "high": m15_win["high"], "low": m15_win["low"]}),
                    h1_win,
                    "NONE",
                )
            else:
                market_phase = "UNKNOWN"
                gate["phase_unknown"] += 1
            if market_phase in cfg["block_phases"]:
                gate["phase_blocked"] += 1
                continue

        replay_index["i"] = i
        df = m15.iloc[max(0, i - 199): i + 1].copy()

        sweep, sweep_level = detect_liquidity_sweep(df)
        if not sweep:
            gate["no_sweep"] += 1
            continue

        fvgs = detect_fvg(df, max_age=20)
        if not fvgs:
            fvgs = detect_immediate_fvg(df)
        if not fvgs:
            gate["no_fvg"] += 1
            continue

        atr = calculate_atr(df, s.ATR_PERIOD)
        if atr is None:
            continue
        cur_atr["value"] = atr

        price = float(m15.iloc[i]["close"])
        est_lot = sim_lot_size(atr * s.ATR_MULTIPLIER)
        placed_ctx.pop("last", None)

        gate["reached_strategy"] += 1
        dbg_sink.clear()
        evaluate_fn(
            df, fvgs, sweep, sweep_level, price, atr, est_lot, KILLZONE_NAME,
            market_phase=market_phase, phase_adj=phase_adj,
        )

        last = placed_ctx.get("last")
        if last:
            gate["trades_placed"] += 1
            sessions_traded.add(KILLZONE_NAME)
            open_positions.append(
                OpenPosition(
                    symbol=symbol, direction=last["direction"], entry=last["entry"],
                    sl=last["sl"], tp=last["tp"], lot=last["lot"],
                    entry_time=t, entry_idx=i, session=last["session"],
                    context=last["context"],
                )
            )
        elif cfg.get("debug") and dbg_sink:
            print(f"    [{t}] SWEEP={sweep} price={price:.5f}")
            for _m in dbg_sink[-12:]:
                print(f"      | {_m}")

    for pos in open_positions:
        closed.append(
            ClosedTrade(
                symbol=symbol, direction=pos.direction, entry=pos.entry, sl=pos.sl,
                tp=pos.tp, lot=pos.lot, entry_time=str(pos.entry_time), exit_time="OPEN",
                exit_price=0.0, outcome="OPEN", pnl_usc=0.0, pnl_usd=0.0,
                bars_held=pos.bars_held, session=pos.session, context=pos.context,
            )
        )

    decided = [c for c in closed if c.outcome in ("WIN", "LOSS", "BE")]
    wins = [c for c in decided if c.outcome == "WIN"]
    losses = [c for c in decided if c.outcome == "LOSS"]
    be = [c for c in decided if c.outcome == "BE"]
    opens = [c for c in closed if c.outcome == "OPEN"]

    net_usc = sum(c.pnl_usc for c in closed)
    gross_win = sum(c.pnl_usc for c in wins)
    gross_loss = abs(sum(c.pnl_usc for c in losses))
    win_rate = (len(wins) / (len(wins) + len(losses)) * 100) if (len(wins) + len(losses)) else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    peak = start_balance_usc
    running = start_balance_usc
    max_dd = 0.0
    for c in closed:
        running += c.pnl_usc
        if running > peak:
            peak = running
        dd = (peak - running) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "symbol": symbol,
        "start_balance_usc": round(start_balance_usc, 2),
        "end_balance_usc": round(balance_usc, 2),
        "net_pnl_usc": round(net_usc, 2),
        "net_pnl_usd": round(net_usc / 100.0, 2),
        "return_pct": round(net_usc / start_balance_usc * 100, 3),
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "be": len(be),
        "open": len(opens),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
        "max_drawdown_pct": round(max_dd, 3),
        "avg_bars_held": round(sum(c.bars_held for c in decided) / len(decided), 1) if decided else 0,
        "gates": gate,
        "trades": [
            {
                "direction": c.direction, "entry": c.entry, "sl": c.sl, "tp": c.tp,
                "lot": c.lot, "entry_time": c.entry_time, "exit_time": c.exit_time,
                "exit_price": c.exit_price, "outcome": c.outcome,
                "pnl_usc": round(c.pnl_usc, 2), "pnl_usd": round(c.pnl_usd, 2),
                "bars_held": c.bars_held,
                "setup_type": c.context.get("setup_type", ""),
                "fvg_type": c.context.get("fvg_type", ""),
                "sweep": c.context.get("sweep", ""),
                "score": c.context.get("confluence_score", 0),
                "level_sweep": c.context.get("level_sweep", False),
                "market_phase": c.context.get("market_phase", ""),
                "session": c.session,
            }
            for c in closed
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="London Open month replay")
    parser.add_argument("--symbols", default="GBPUSDc,EURUSDc")
    parser.add_argument("--days", type=int, default=35)
    parser.add_argument("--balance", type=float, default=4169.11, help="Starting balance in USC")
    parser.add_argument("--spread", type=float, default=0.00012, help="Assumed spread (price units)")
    parser.add_argument("--risk", type=float, default=1.0, help="Risk percent per trade")
    parser.add_argument("--rrr", type=float, default=2.5, help="Risk:Reward ratio")
    parser.add_argument("--atr-mult", type=float, default=2.0, help="ATR multiplier for SL")
    parser.add_argument("--min-sl-atr", type=float, default=1.0, help="Minimum SL ATR multiplier")
    parser.add_argument("--lot-cap", type=float, default=0.20, help="Hard lot cap")
    parser.add_argument("--phase-gate", action="store_true", help="Apply market circuit CHOP block")
    parser.add_argument("--debug", action="store_true", help="Print strategy guard messages")
    parser.add_argument("--variant", choices=["strict", "no_retest", "loose"], default="strict",
                        help="strict=live rules, no_retest=drop retest only, loose=drop retest + beyond-range")
    parser.add_argument("--out", type=str, default=None)
    args = parser.parse_args()

    if not mt5.initialize():
        sys.exit("MT5 initialize failed -- is the terminal running?")

    s.STRATEGY = "LONDON_OPEN"
    s._arg_symbol = ""
    s.RISK_PERCENT = args.risk
    s.RISK_REWARD_RATIO = args.rrr
    s.ATR_PERIOD = 14
    s.ATR_MULTIPLIER = args.atr_mult
    s.MIN_SL_ATR_MULTIPLIER = args.min_sl_atr
    s.MIN_SPREAD_PIPS = 0.0002
    s.KRONOS_VETO_GATE = None
    s.HARD_LOT_CAP = args.lot_cap

    cfg = {
        "balance_usc": args.balance * 100.0,
        "spread": args.spread,
        "hard_lot_cap": args.lot_cap,
        "phase_gate": args.phase_gate,
        "block_phases": ["CHOP"],
        "debug": args.debug,
        "variant": args.variant,
        "clamp_sl": True,
    }

    symbols = [x.strip() for x in args.symbols.split(",") if x.strip()]
    all_results = {}
    for symbol in symbols:
        print(f"\nPulling {args.days}d of M15/H1 for {symbol}...")
        data = pull_rates(symbol, args.days)
        spec = SymbolSpec(symbol)
        print(f"  M15 rows: {len(data['m15'])}  H1 rows: {len(data['h1'])}")
        res = run_replay(symbol, data, spec, cfg)
        all_results[symbol] = res

        g = res["gates"]
        print(f"\n=== {symbol}  (start {args.balance} USD / {res['start_balance_usc']} USC)  variant={args.variant}")
        print(f"  Trades: {res['total_trades']}  W:{res['wins']} L:{res['losses']} BE:{res['be']} OPEN:{res['open']}")
        print(f"  Win rate: {res['win_rate_pct']}%  Profit factor: {res['profit_factor']}")
        print(f"  Net P&L: {res['net_pnl_usd']:+.2f} USD ({res['net_pnl_usc']:+.0f} USC)  "
              f"Return: {res['return_pct']:+.3f}%  MaxDD: {res['max_drawdown_pct']}%")
        print(f"  Gates: scans={g['candles_scanned']} london={g['candles_scanned']-g['not_london_open']-g['weekend_skip']-g['dead_zone_skip']} "
              f"phase_blocked={g['phase_blocked']} no_sweep={g['no_sweep']} no_fvg={g['no_fvg']} "
              f"reached_strategy={g['reached_strategy']} placed={g['trades_placed']}")
        for t in res["trades"]:
            print(f"  [{t['entry_time']}] {t['direction']:<4} entry={t['entry']:.5f} tp={t['tp']:.5f} "
                  f"outcome={t['outcome']:<5} pnl={t['pnl_usd']:+.2f}USD "
                  f"({t['sweep']} {t['fvg_type']} score={t['score']} phase={t['market_phase']})")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({
            "run_date": datetime.now(timezone.utc).isoformat(),
            "strategy": "LONDON_OPEN",
            "days": args.days,
            "balance_usd": args.balance,
            "spread": args.spread,
            "phase_gate": args.phase_gate,
            "variant": args.variant,
            "results": all_results,
        }, indent=2))
        print(f"\n[OK] Saved to {out_path}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
