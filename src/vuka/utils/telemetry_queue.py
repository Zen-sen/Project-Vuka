"""
TelemetryQueue -- async I/O decoupling for Project Vuka.

The scan loop must never stall on "paperwork". Every JSON/DB write payload is
pushed onto an in-memory ``queue.Queue`` and drained by a single dedicated
daemon worker thread, so disk and SQLite operations never block the trade path.

Design rules:
  * All file/database I/O happens on the worker thread only (single writer ->
    no interleaving corruption of the JSON logs).
  * The worker thread NEVER calls MT5 -- the MetaTrader5 module is used from
    the main execution thread only.
  * ``DatabaseManager`` uses thread-local connections, so writes from the
    worker thread are safe (each thread lazily opens its own connection).
  * ``submit`` is a fast, non-blocking enqueue; on a full queue the payload is
    dropped with a warning rather than freezing the bot.
"""

import contextlib
import json
import os
import queue
import threading
from typing import Any

from vuka.core.state import s
from vuka.utils.unified_logger import get_logger

_logger = get_logger("Telemetry")


class TelemetryQueue:
    """Processes JSON/DB write payloads on a background worker thread."""

    def __init__(self, maxsize: int = 1000):
        self._q: queue.Queue[tuple[str, dict]] = queue.Queue(maxsize=maxsize)
        self._worker = threading.Thread(
            target=self._run, name="TelemetryWorker", daemon=True
        )
        self._worker.start()

    # ── Producer API ────────────────────────────────────────────────────

    def submit(self, kind: str, payload: dict) -> None:
        """Enqueue a write payload for the background worker."""
        try:
            self._q.put((kind, payload), timeout=1.0)
        except queue.Full:
            self._warn(f"Telemetry queue full -- dropping {kind} payload")

    def flush(self, timeout: float = 30.0) -> None:
        """Block until all queued payloads have been written."""
        self._q.join()

    # ── Worker loop ─────────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            kind, payload = self._q.get()
            try:
                self._dispatch(kind, payload)
            except Exception as e:
                self._warn(f"Telemetry {kind} failed: {e}")
            finally:
                self._q.task_done()

    def _dispatch(self, kind: str, payload: dict) -> None:
        if kind == "trade":
            self._handle_trade(payload)
        elif kind == "sl_move":
            self._handle_sl_move(payload)
        elif kind == "sessions":
            self._handle_sessions(payload)
        elif kind == "loss_tracking":
            self._handle_loss_tracking(payload)
        elif kind == "pnl_backfill":
            self._handle_pnl_backfill(payload)
        elif kind == "memory_state":
            self._handle_memory_state(payload)
        else:
            self._warn(f"Unknown telemetry kind: {kind}")

    # ── Handlers ────────────────────────────────────────────────────────

    def _handle_trade(self, payload: dict) -> None:
        """Persist a filled trade: DB insert + JSON dual-write + concept record."""
        trade_entry = payload["trade_entry"]
        if s.DB_AVAILABLE:
            try:
                s.DB.insert_trade(trade_entry)
            except Exception as e:
                self._warn(f"Database write error: {e}. Falling back to JSON.")
        self._append_json(payload.get("log_file"), trade_entry)
        try:
            from skills.concept_tracker import record_concept_trade

            record_concept_trade(
                payload.get("trade_id", "unknown"),
                payload.get("direction", "UNKNOWN"),
                payload.get("concepts_used", []),
                payload.get("kronos_decision", "ALLOW"),
                setup_type=payload.get("setup_type", "UNKNOWN"),
            )
        except Exception as e:
            self._warn(f"Concept tracker record_trade error: {e}")

    def _handle_sl_move(self, payload: dict) -> None:
        """Persist an SL movement: DB primary, JSON fallback."""
        entry = payload["entry"]
        if s.DB_AVAILABLE:
            try:
                s.DB.insert_sl_movement(entry)
                return
            except Exception as e:
                self._warn(f"Database write error: {e}. Falling back to JSON.")
        self._append_json(payload.get("log_file"), entry)

    def _handle_sessions(self, payload: dict) -> None:
        """Persist traded sessions: DB primary, JSON fallback."""
        sessions = payload.get("sessions", [])
        today = payload.get("date", "")
        if s.DB_AVAILABLE:
            try:
                for session_name in sessions:
                    s.DB.mark_session_traded(
                        today, payload.get("symbol", ""), payload.get("strategy", ""), session_name
                    )
                return
            except Exception as e:
                self._warn(f"Database write error: {e}. Falling back to JSON.")
        self._write_json(
            payload.get("sessions_file"),
            {
                "date": today,
                "sessions": list(sessions),
                "consecutive_losses": s.consecutive_losses if s.consecutive_losses is not None else 0,
                "last_counted_ticket": s.last_counted_ticket,
            },
        )

    def _handle_loss_tracking(self, payload: dict) -> None:
        """Persist the consecutive-loss counter: DB primary, JSON fallback."""
        today = payload.get("date", "")
        if s.DB_AVAILABLE:
            try:
                s.DB.update_loss_tracking(
                    today, s._arg_symbol, s.STRATEGY,
                    payload.get("count", 0), payload.get("last_ticket", 0)
                )
                return
            except Exception as e:
                self._warn(f"Database write error: {e}. Falling back to JSON.")
        # JSON fallback -- merge into the sessions file that also holds loss state
        path = s.SESSIONS_FILE
        data: dict = {"date": today, "sessions": list(s.sessions_traded_today)}
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    data.update(existing)
            except Exception:
                pass
        data["consecutive_losses"] = payload.get("count", 0)
        data["last_counted_ticket"] = payload.get("last_ticket", 0)
        self._write_json(path, data)

    def _handle_pnl_backfill(self, payload: dict) -> None:
        """Backfill PnL/exit info into the JSON trade log, then update the
        concept tracker and the database. All file/DB I/O, no MT5 calls."""
        log_file = payload.get("log_file")
        if not log_file or not os.path.exists(log_file):
            return
        trade_log_data = self._read_log_entries(log_file)

        pnl_by_pos = payload.get("pnl_by_pos", {})
        exit_price_by_pos = payload.get("exit_price_by_pos", {})
        deal_list = payload.get("deal_list", [])
        updated = []

        for t in trade_log_data:
            pos_id = t.get("position_id", 0)
            if pos_id in pnl_by_pos and t.get("pnl_usd") is None:
                self._apply_exit(t, pnl_by_pos[pos_id], exit_price_by_pos.get(pos_id, 0))
                updated.append(t)

        # Fallback match for trades with position_id == 0
        updated_ids = {t.get("position_id", 0) for t in updated}
        for t in trade_log_data:
            if t.get("pnl_usd") is not None:
                continue
            if t.get("position_id", 0) != 0:
                continue
            t_dir = 0 if t.get("direction") == "BUY" else 1
            t_lot = t.get("lot_size", 0)
            t_fill = t.get("entry_fill", 0)
            for d in deal_list:
                if d["position_id"] in updated_ids:
                    continue
                if d["type"] != t_dir:
                    continue
                if abs(d["volume"] - t_lot) > 0.01:
                    continue
                if abs(d["price"] - t_fill) > 0.002:
                    continue
                self._apply_exit(t, round(d["profit"], 2), d["price"])
                updated.append(t)
                updated_ids.add(d["position_id"])
                break

        if not updated:
            return

        self._write_jsonl(log_file, trade_log_data)
        for t in updated:
            self._record_outcome_and_update_db(t)

    def _handle_memory_state(self, payload: dict) -> None:
        """Write Memory.md state blocks and handover.json off the hot path."""
        try:
            from vuka.utils.memory_manager import MemoryManager

            MemoryManager().update_state(payload.get("state_data", {}))
        except Exception as e:
            self._warn(f"Memory.md update failed: {e}")
        try:
            with open(payload.get("handover_file", "handover.json"), "w") as f:
                json.dump(payload.get("handover", {}), f, indent=2)
        except Exception as e:
            self._warn(f"Handover write failed: {e}")

    # ── Shared helpers ──────────────────────────────────────────────────

    @staticmethod
    def _apply_exit(trade: dict, pnl_val: float, exit_price: float) -> None:
        trade["pnl_usd"] = pnl_val
        trade["exit_price"] = round(exit_price, 5)
        trade["exit_time"] = datetime_now_iso()
        if pnl_val > 0:
            trade["exit_reason"] = "TP_HIT"
        elif pnl_val < 0:
            trade["exit_reason"] = "SL_HIT"
        else:
            trade["exit_reason"] = "BE_SCRATCH"

    def _record_outcome_and_update_db(self, t: dict) -> None:
        """Close the feedback loop: concept tracker + DB PnL/exit updates."""
        try:
            from skills.concept_tracker import record_concept_outcome

            pnl_val = t.get("pnl_usd", 0)
            if pnl_val > 0:
                outcome = "win"
            elif pnl_val < 0:
                outcome = "loss"
            else:
                outcome = "breakeven"
            market_context = {
                "symbol": t.get("symbol", s.SYMBOL),
                "session": t.get("session", "unknown"),
                "direction": t.get("direction", "unknown"),
                "setup_type": t.get("setup_type", "UNKNOWN"),
                "confluence_score": t.get("confluence_score", 0),
                "market_phase": t.get("market_phase", "UNKNOWN"),
                "sweep_direction": t.get("sweep_direction", "UNKNOWN"),
                "volatility": "normal",
                "exit_reason": t.get("exit_reason", "UNKNOWN"),
            }
            record_concept_outcome(
                str(t.get("position_id", 0)) if t.get("position_id") else t.get("time", "unknown"),
                outcome,
                t.get("effective_rr", 0) or 0,
                pnl_val,
                market_context,
            )
        except Exception as e:
            self._warn(f"Concept tracker record_outcome error: {e}")

        if s.DB_AVAILABLE:
            try:
                t_pos_id = t.get("position_id", 0)
                if t_pos_id:
                    s.DB.update_trade_pnl_by_position_id(
                        t_pos_id,
                        t.get("pnl_usd"),
                        exit_price=t.get("exit_price"),
                        exit_reason=t.get("exit_reason"),
                        exit_time=t.get("exit_time"),
                    )
                else:
                    s.DB.update_trade_pnl(
                        t.get("symbol", s.SYMBOL),
                        t.get("strategy", s.STRATEGY),
                        t.get("time", ""),
                        t.get("direction", ""),
                        t.get("pnl_usd"),
                        exit_price=t.get("exit_price"),
                        exit_reason=t.get("exit_reason"),
                        exit_time=t.get("exit_time"),
                    )
            except Exception as e:
                self._warn(f"s.DB PnL update error for trade: {e}")

    def _append_json(self, path, entry: dict) -> None:
        """Append one entry as a JSONL line (no read-before-write).

        A legacy JSON-array file is migrated to JSONL once, on first append,
        so the 55 pre-migration trades survive the format change."""
        if not path:
            return
        self._migrate_jsonl(path)
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            self._warn(f"JSONL append failed for {path}: {e}")

    @staticmethod
    def _migrate_jsonl(path: str) -> None:
        """Rewrite a legacy JSON-array file as JSONL, once."""
        try:
            with open(path) as f:
                raw = f.read()
        except OSError:
            return
        if not raw.strip():
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(data, list):
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in data:
                f.write(json.dumps(e) + "\n")
        os.replace(tmp, path)

    @staticmethod
    def _read_log_entries(path: str) -> list:
        """Read a JSON trade log as a list of dicts, tolerating both legacy
        JSON-array files and newline-delimited JSON (JSONL)."""
        if not path or not os.path.exists(path):
            return []
        try:
            with open(path) as f:
                raw = f.read()
        except OSError:
            return []
        if not raw.strip():
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
        entries = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    @staticmethod
    def _write_jsonl(path: str, entries: list) -> None:
        """Rewrite a list of entries as JSONL, atomically."""
        if not path:
            return
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        os.replace(tmp, path)

    def _write_json(self, path, data: Any) -> None:
        if not path:
            return
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

    def _warn(self, msg: str) -> None:
        with contextlib.suppress(Exception):
            _logger.log(level="WARN", message=msg)


def datetime_now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat()


# ── Process-wide singleton ───────────────────────────────────────────────
_telemetry_instance: "TelemetryQueue | None" = None


def get_telemetry() -> TelemetryQueue:
    """Return the process-wide TelemetryQueue, creating it on first use."""
    global _telemetry_instance
    if _telemetry_instance is None:
        _telemetry_instance = TelemetryQueue()
    return _telemetry_instance
