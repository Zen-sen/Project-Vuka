"""HealthMonitor tests — JSONL sanitize, notify cooldown, anomaly cache (audit XXII)."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from vuka.core import health_monitor as hm


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    """HealthMonitor writing its alert log under tmp_path, not the repo cwd."""
    real_path = Path

    def _path(*parts):
        if parts == ("logs",):
            return real_path(tmp_path) / "logs"
        return real_path(*parts)

    monkeypatch.setattr(hm, "Path", _path)
    m = hm.HealthMonitor()
    yield m
    m.close()


class TestLogAlert:
    def test_sanitize_replaces_newlines_recursively(self):
        alert = {
            "type": "HIGH_ERROR_RATE",
            "severity": "ERROR",
            "message": "line1\nline2\rline3",
            "extra": {"note": "a\nb", "n": 1},
            "tags": ["x\ny", "plain"],
        }
        out = hm.HealthMonitor._sanitize_alert(alert)
        assert "\n" not in out["message"] and "\r" not in out["message"]
        assert "\n" not in out["extra"]["note"]
        assert "\n" not in out["tags"][0]

    def test_jsonl_stays_single_line_and_parseable(self, monitor):
        monitor.log_alert({
            "type": "TEST",
            "severity": "ERROR",
            "message": "multi\nline",
            "detail": {"raw": "a\rb"},
        })
        lines = Path(monitor.alert_log_file).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        assert parsed["message"] == "multi line"
        assert parsed["detail"]["raw"] == "a b"

    def test_handle_reused_and_close_idempotent(self, monitor):
        first = monitor._alert_file
        monitor.log_alert({"type": "A", "message": "x"})
        assert monitor._alert_file is first
        monitor.close()
        assert monitor._alert_file is None
        monitor.close()  # second close is a no-op


class TestNotifyCooldown:
    def test_second_alert_within_cooldown_suppressed(self, monitor):
        with patch.object(hm, "send_notification") as send:
            a1 = {"type": "HIGH_ERROR_RATE", "severity": "ERROR", "message": "one"}
            a2 = {"type": "HIGH_ERROR_RATE", "severity": "ERROR", "message": "two"}
            assert monitor._notify_allowed(a1) is True
            assert monitor._notify_allowed(a2) is False
            send.assert_not_called()

    def test_different_types_share_no_cooldown(self, monitor):
        with patch.object(hm, "send_notification") as send:
            monitor.log_alert({"type": "A", "severity": "ERROR", "message": "x"})
            monitor.log_alert({"type": "B", "severity": "ERROR", "message": "y"})
            assert send.call_count == 2

    def test_info_alert_never_notifies(self, monitor):
        with patch.object(hm, "send_notification") as send:
            monitor.log_alert({"type": "LOW_CONFLUENCE", "severity": "INFO", "message": "quiet"})
            send.assert_not_called()

    def test_error_alert_notifies_with_level(self, monitor):
        with patch.object(hm, "send_notification") as send:
            monitor.log_alert({"type": "X", "severity": "ERROR", "message": "boom"})
            send.assert_called_once()
            assert send.call_args.kwargs["level"] == "ERROR"


class TestAnomalyCache:
    def _seed_scans(self, monitor, count=5):
        for _ in range(count):
            monitor.record_scan({"session": "Dead Zone", "confluence_score": 40})

    def test_cached_within_ttl(self, monitor):
        self._seed_scans(monitor)
        r1 = monitor.detect_anomalies()
        r2 = monitor.detect_anomalies()
        assert r1 is r2

    def test_record_scan_invalidates_cache(self, monitor):
        self._seed_scans(monitor)
        r1 = monitor.detect_anomalies()
        monitor.record_scan({"session": "Dead Zone", "confluence_score": 40})
        r2 = monitor.detect_anomalies()
        assert r1 is not r2

    def test_detect_after_new_trade_not_cached(self, monitor):
        self._seed_scans(monitor)
        r1 = monitor.detect_anomalies()
        monitor.record_trade({"symbol": "EURUSD", "profit": 10})
        r2 = monitor.detect_anomalies()
        assert r1 is not r2


class TestErrorTruncation:
    def test_error_message_truncated_to_max_length(self, monitor):
        monitor.record_scan({"error": "E" * 2000, "session": "Dead Zone"})
        stored = monitor.error_history[-1]
        assert len(stored["error"]) == hm.MAX_ERROR_LENGTH
