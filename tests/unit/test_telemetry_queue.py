"""TelemetryQueue tests — handlers persist to DB/JSON off the hot path.

The worker thread never calls MT5; these tests exercise the handlers directly
(synchronously) and the full submit/flush round-trip on the real worker.
"""
import json
from unittest.mock import MagicMock, patch

from vuka.core.state import s
from vuka.utils.telemetry_queue import TelemetryQueue


def _make_trade_entry(**overrides):
    entry = {
        "symbol": "EURUSD",
        "position_id": 7,
        "direction": "BUY",
        "lot_size": 0.10,
        "entry_fill": 1.10000,
        "pnl_usd": None,
    }
    entry.update(overrides)
    return entry


class TestTradeHandler:
    def test_db_insert_when_available(self):
        s.DB_AVAILABLE = True
        s.DB = MagicMock()
        q = TelemetryQueue()
        entry = _make_trade_entry()
        with patch.object(q, "_append_json"), \
             patch("skills.concept_tracker.record_concept_trade"):
            q._handle_trade({"trade_entry": entry, "trade_id": "7"})
        s.DB.insert_trade.assert_called_once_with(entry)

    def test_json_fallback_when_db_unavailable(self):
        s.DB_AVAILABLE = False
        q = TelemetryQueue()
        entry = _make_trade_entry()
        with patch.object(q, "_append_json") as aj, \
             patch("skills.concept_tracker.record_concept_trade"):
            q._handle_trade({"trade_entry": entry, "trade_id": "7"})
        aj.assert_called_once_with(None, entry)

    def test_json_written_when_db_insert_raises(self):
        s.DB_AVAILABLE = True
        s.DB = MagicMock()
        s.DB.insert_trade.side_effect = RuntimeError("locked")
        q = TelemetryQueue()
        entry = _make_trade_entry()
        with patch.object(q, "_append_json") as aj, \
             patch("skills.concept_tracker.record_concept_trade"):
            q._handle_trade({"trade_entry": entry, "trade_id": "7"})
        aj.assert_called_once()


class TestSlMoveHandler:
    def test_json_fallback(self):
        s.DB_AVAILABLE = False
        q = TelemetryQueue()
        with patch.object(q, "_append_json") as aj:
            q._handle_sl_move({"entry": {"ticket": 1, "label": "BE"}, "log_file": None})
        aj.assert_called_once_with(None, {"ticket": 1, "label": "BE"})

    def test_db_insert_when_available(self):
        s.DB_AVAILABLE = True
        s.DB = MagicMock()
        q = TelemetryQueue()
        entry = {"ticket": 1, "label": "BE"}
        q._handle_sl_move({"entry": entry, "log_file": None})
        s.DB.insert_sl_movement.assert_called_once_with(entry)


class TestSessionsHandler:
    def test_json_fallback(self):
        s.DB_AVAILABLE = False
        q = TelemetryQueue()
        with patch.object(q, "_write_json") as wj:
            q._handle_sessions({
                "sessions": ["London Open", "NY"],
                "date": "2026-01-01",
                "sessions_file": None,
            })
        path, data = wj.call_args[0]
        assert data == {
            "date": "2026-01-01",
            "sessions": ["London Open", "NY"],
            "consecutive_losses": 0,
            "last_counted_ticket": 0,
        }

    def test_db_mark_when_available(self):
        s.DB_AVAILABLE = True
        s.DB = MagicMock()
        q = TelemetryQueue()
        q._handle_sessions({
            "sessions": ["London Open"],
            "date": "2026-01-01",
            "symbol": "EURUSD",
            "strategy": "INGWE",
            "sessions_file": None,
        })
        s.DB.mark_session_traded.assert_called_once_with(
            "2026-01-01", "EURUSD", "INGWE", "London Open"
        )


class TestMemoryStateHandler:
    def test_writes_handover_and_state(self, tmp_path):
        q = TelemetryQueue()
        hf = tmp_path / "handover.json"
        with patch("vuka.utils.memory_manager.MemoryManager") as mm:
            q._handle_memory_state({
                "state_data": {"equity": 10000.0},
                "handover_file": str(hf),
                "handover": {"epoch": 3, "symbol": "EURUSD"},
            })
        mm.return_value.update_state.assert_called_once_with({"equity": 10000.0})
        assert json.loads(hf.read_text()) == {"epoch": 3, "symbol": "EURUSD"}


class TestPnlBackfillHandler:
    def test_backfills_json_and_records_outcome(self, tmp_path):
        log = tmp_path / "trades.json"
        log.write_text(json.dumps([
            {
                "symbol": "EURUSD", "position_id": 10, "direction": "BUY",
                "lot_size": 0.10, "entry_fill": 1.1000, "pnl_usd": None,
            },
            {
                "symbol": "EURUSD", "position_id": 0, "direction": "SELL",
                "lot_size": 0.20, "entry_fill": 1.1000, "pnl_usd": None,
                "time": "2026-01-01 10:00:00",
            },
        ]))
        q = TelemetryQueue()
        with patch.object(q, "_record_outcome_and_update_db") as ro:
            q._handle_pnl_backfill({
                "log_file": str(log),
                "pnl_by_pos": {10: 25.0},
                "exit_price_by_pos": {10: 1.1040},
                "deal_list": [
                    {"position_id": 20, "profit": -8.5, "price": 1.0990,
                     "volume": 0.20, "type": 1},
                ],
            })
        data = [json.loads(line) for line in log.read_text().splitlines()]
        t1 = next(t for t in data if t["position_id"] == 10)
        assert t1["pnl_usd"] == 25.0 and t1["exit_reason"] == "TP_HIT"
        assert t1["exit_price"] == 1.1040
        t2 = next(t for t in data if t["position_id"] == 0)
        assert t2["pnl_usd"] == -8.5 and t2["exit_reason"] == "SL_HIT"
        assert ro.call_count == 2

    def test_noop_when_no_updates(self, tmp_path):
        log = tmp_path / "trades.json"
        log.write_text(json.dumps([_make_trade_entry(pnl_usd=12.0)]))
        q = TelemetryQueue()
        with patch.object(q, "_record_outcome_and_update_db") as ro:
            q._handle_pnl_backfill({
                "log_file": str(log),
                "pnl_by_pos": {7: 12.0},
                "exit_price_by_pos": {7: 1.1050},
                "deal_list": [],
            })
        ro.assert_not_called()


class TestDispatch:
    def test_unknown_kind_warns(self):
        q = TelemetryQueue()
        with patch.object(q, "_warn") as w:
            q._dispatch("bogus", {})
            w.assert_called_once()


class TestAsyncPath:
    def test_submit_flush_roundtrip(self, tmp_path):
        """Payload enqueued on the main thread is persisted by the worker."""
        log = tmp_path / "trades.json"
        s.DB_AVAILABLE = False
        q = TelemetryQueue()
        entry = _make_trade_entry(position_id=99)
        with patch("skills.concept_tracker.record_concept_trade"):
            q.submit("trade", {
                "log_file": str(log),
                "trade_entry": entry,
                "trade_id": "99",
                "direction": "BUY",
                "concepts_used": ["fvg_bullish"],
                "kronos_decision": "ALLOW",
                "setup_type": "LONDON_OPEN",
            })
            q.flush()
        assert [json.loads(line) for line in log.read_text().splitlines()] == [entry]

    def test_append_migrates_legacy_json_array_to_jsonl(self, tmp_path):
        """A pre-migration JSON-array trade log is converted on first append."""
        log = tmp_path / "trades.json"
        legacy = [{"position_id": 1, "pnl_usd": None}]
        log.write_text(json.dumps(legacy))
        q = TelemetryQueue()
        q._append_json(str(log), {"position_id": 2, "pnl_usd": None})
        entries = [json.loads(line) for line in log.read_text().splitlines()]
        assert entries == [{"position_id": 1, "pnl_usd": None},
                           {"position_id": 2, "pnl_usd": None}]

    def test_flush_waits_for_worker(self, tmp_path):
        log = tmp_path / "sessions.json"
        s.DB_AVAILABLE = False
        q = TelemetryQueue()
        q.submit("sessions", {
            "sessions": ["London Open"],
            "date": "2026-01-01",
            "sessions_file": str(log),
        })
        q.flush()
        assert json.loads(log.read_text()) == {
            "date": "2026-01-01",
            "sessions": ["London Open"],
            "consecutive_losses": 0,
            "last_counted_ticket": 0,
        }
