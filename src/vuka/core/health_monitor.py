import atexit
import json
import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vuka.utils.notifier import send as send_notification

logger = logging.getLogger(__name__)

# ── Hard limits (audit XXII) ─────────────────────────────────────
MAX_ERROR_LENGTH = 500            # truncate stored error text (bounded memory)
ANOMALY_CACHE_TTL_SECONDS = 5.0   # detect_anomalies() recomputed at most ~12x/min
NOTIFY_COOLDOWN_SECONDS = 300     # max 1 notification per anomaly type per 5 min


class HealthMonitor:
    """
    Real-time system health monitoring with anomaly detection.

    Tracks:
    - Scan history (sweeps, FVGs, confluence, signals)
    - Kronos validation decisions
    - Trade execution
    - Errors and warnings

    Detects:
    - Signal generation failures
    - Validation mismatches
    - Execution blocks
    - Connectivity issues
    """

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.scan_history = deque(maxlen=window_size)
        self.error_history = deque(maxlen=window_size)
        self.trade_history = deque(maxlen=50)
        self.kronos_decisions = deque(maxlen=100)
        self.start_time = datetime.now(timezone.utc)
        self.alert_log_file = Path("logs") / "health_alerts.log"
        self.alert_log_file.parent.mkdir(exist_ok=True)
        # Single file handle, opened once and flushed per write. Avoids the
        # open/close churn of one handle-per-alert under rapid anomaly bursts.
        # Deliberately a persistent handle, not a context-managed open.
        self._alert_file = open(self.alert_log_file, "a", encoding="utf-8")  # noqa: SIM115
        self._anomaly_cache: dict | None = None
        self._anomaly_cache_time = 0.0
        self._notify_cooldowns: dict[str, float] = {}
        atexit.register(self.close)

    def _invalidate_anomaly_cache(self):
        """Force the next detect_anomalies() call to rescan the histories."""
        self._anomaly_cache = None
        self._anomaly_cache_time = 0.0

    def record_scan(self, scan_data: dict):
        """
        Record results from one 15-minute scan cycle.

        Expected fields:
        - timestamp: ISO8601 datetime
        - session: "Asian" | "London Open" | "New York Open" | "Dead Zone"
        - sweep_detected: bool
        - sweep_level: float (optional)
        - fvg_detected: bool
        - fvg_type: "BULLISH_FVG" | "BEARISH_FVG" | None
        - confluence_score: 0-120 int
        - htf_bias_ok: bool
        - m15_bos: "BULLISH" | "BEARISH" | None
        - adx: float
        - adx_ok: bool
        - signal_direction: "BUY" | "SELL" | None
        - kronos_agree: bool | None
        - kronos_confidence: float (0-1) | None
        - trade_executed: bool
        - error: str | None (error message if any)
        """
        # Ensure timestamp
        if "timestamp" not in scan_data:
            scan_data["timestamp"] = datetime.now(timezone.utc).isoformat()

        self.scan_history.append(scan_data)
        self._invalidate_anomaly_cache()

        # Track errors separately
        if scan_data.get("error"):
            error_entry = {
                "timestamp": scan_data["timestamp"],
                "session": scan_data.get("session", "unknown"),
                # Truncate stack traces / verbose reprs so 100 deque entries
                # cannot accumulate hundreds of KB in a long-running process.
                "error": str(scan_data["error"])[:MAX_ERROR_LENGTH],
                "type": self._classify_error(scan_data["error"])
            }
            self.error_history.append(error_entry)

    def record_kronos_decision(self, decision: dict):
        """
        Record Kronos validation decision from veto log.

        Expected fields:
        - timestamp
        - signal: "BUY" | "SELL"
        - symbol: "EURUSD" | "GBPUSD"
        - kronos_agree: bool
        - confidence: float (0-1)
        - decision: "ALLOW" | "VETO_BLOCKED" | etc.
        - reason: str
        """
        self.kronos_decisions.append(decision)
        self._invalidate_anomaly_cache()

    def record_trade(self, trade: dict):
        """Record successfully executed trade."""
        if "timestamp" not in trade:
            trade["timestamp"] = datetime.now(timezone.utc).isoformat()
        self.trade_history.append(trade)
        self._invalidate_anomaly_cache()

    def _classify_error(self, error_msg: str) -> str:
        """Classify error type for grouping."""
        error_lower = error_msg.lower()

        if "mt5" in error_lower or "connection" in error_lower:
            return "MT5_CONNECTION"
        elif "kronos" in error_lower or "api" in error_lower:
            return "KRONOS_API"
        elif "json" in error_lower or "parse" in error_lower:
            return "DATA_PARSE"
        elif "timeout" in error_lower:
            return "TIMEOUT"
        else:
            return "OTHER"

    def detect_anomalies(self) -> dict:
        """
        Analyze recent scans for anomalies.

        The full history scan is expensive (O(n) over every deque), and
        should_alert(), get_critical_alerts() and get_health_report() all call
        this in one dashboard refresh. Results are cached for a short TTL so
        the scan happens at most once per ANOMALY_CACHE_TTL_SECONDS.
        """
        now = time.monotonic()
        if self._anomaly_cache is not None and now - self._anomaly_cache_time < ANOMALY_CACHE_TTL_SECONDS:
            return self._anomaly_cache
        result = self._detect_anomalies_uncached()
        self._anomaly_cache = result
        self._anomaly_cache_time = now
        return result

    def _detect_anomalies_uncached(self) -> dict:
        """
        Analyze recent scans for anomalies.

        Returns:
        {
            "status": "HEALTHY" | "ANOMALIES_DETECTED",
            "anomalies": [{"type": str, "severity": "INFO|WARN|ERROR", "message": str, ...}],
            "metrics": {...}
        }
        """
        anomalies = []

        if not self.scan_history:
            return {
                "status": "INSUFFICIENT_DATA",
                "anomalies": [],
                "metrics": {}
            }

        recent_scans = list(self.scan_history)[-20:]

        # ANOMALY 1: No sweeps detected
        sweeps = sum(1 for s in recent_scans if s.get("sweep_detected"))
        if sweeps == 0 and len(recent_scans) >= 5:
            anomalies.append({
                "type": "NO_SWEEPS",
                "severity": "WARN",
                "message": f"No liquidity sweeps detected in {len(recent_scans)} scans",
                "scans_analyzed": len(recent_scans),
                "sweeps_found": sweeps,
                "impact": "Signal generation may have failed"
            })

        # ANOMALY 2: Low confluence scores
        scores = [s.get("confluence_score", 0) for s in recent_scans if s.get("confluence_score") is not None]
        if scores and max(scores) < 50:
            anomalies.append({
                "type": "LOW_CONFLUENCE",
                "severity": "INFO",
                "message": f"Maximum confluence score is {max(scores)}/120 (expected >70 for trades)",
                "max_score": max(scores),
                "avg_score": sum(scores) / len(scores),
                "impact": "Fewer trades generated (might be normal in choppy markets)"
            })

        # ANOMALY 3: High error rate
        errors = sum(1 for s in recent_scans if s.get("error"))
        error_rate = errors / len(recent_scans) if recent_scans else 0
        if error_rate > 0.2:
            anomalies.append({
                "type": "HIGH_ERROR_RATE",
                "severity": "ERROR",
                "message": f"Error rate {error_rate:.0%} in last {len(recent_scans)} scans",
                "error_count": errors,
                "scan_count": len(recent_scans),
                "impact": "Connectivity or data quality issues"
            })

        # ANOMALY 4: High Kronos veto rate
        recent_decisions = list(self.kronos_decisions)[-15:]
        if recent_decisions:
            vetoes = sum(1 for d in recent_decisions if not d.get("kronos_agree", True))
            veto_rate = vetoes / len(recent_decisions)
            if veto_rate > 0.7:
                anomalies.append({
                    "type": "HIGH_VETO_RATE",
                    "severity": "WARN",
                    "message": f"Kronos veto rate {veto_rate:.0%} (possible validation mismatch)",
                    "veto_count": vetoes,
                    "total_decisions": len(recent_decisions),
                    "impact": "Many setups blocked by validation"
                })

        # ANOMALY 5: Low Kronos confidence
        confidences = [d.get("confidence", 0.5) for d in recent_decisions if d.get("confidence") is not None]
        if confidences and sum(confidences) / len(confidences) < 0.4:
            anomalies.append({
                "type": "LOW_KRONOS_CONFIDENCE",
                "severity": "WARN",
                "message": f"Average Kronos confidence {sum(confidences)/len(confidences):.0%} (expecting >0.75)",
                "avg_confidence": sum(confidences) / len(confidences),
                "min_confidence": min(confidences),
                "impact": "Low confidence in validation decisions"
            })

        # ANOMALY 6: No trades despite activity
        recent_trades = [t for t in self.trade_history
                        if datetime.fromisoformat(t.get("timestamp", "1970-01-01T00:00:00Z")) >
                           (datetime.now(timezone.utc) - timedelta(hours=4))]

        active_scans = sum(1 for s in recent_scans if s.get("session") in ["London Open", "New York Open"])

        if not recent_trades and active_scans > 10:
            anomalies.append({
                "type": "NO_TRADES_NO_EXECUTION",
                "severity": "WARN",
                "message": f"No trades in {active_scans} active scans (4-hour window)",
                "scans_analyzed": active_scans,
                "trades_found": len(recent_trades),
                "impact": "Possible execution block or no confluent setups"
            })

        # ANOMALY 7: High ADX but low confluence (parameter drift?)
        high_adx_scans = [s for s in recent_scans if s.get("adx", 0) > 25]
        high_adx_low_score = [s for s in high_adx_scans if s.get("confluence_score", 0) < 60]
        if high_adx_scans and len(high_adx_low_score) / len(high_adx_scans) > 0.5:
            anomalies.append({
                "type": "PARAMETER_DRIFT",
                "severity": "INFO",
                "message": f"High ADX ({[s.get('adx') for s in high_adx_scans[:3]]} but low confluence scores",
                "high_adx_count": len(high_adx_scans),
                "low_score_count": len(high_adx_low_score),
                "impact": "Possible confluence scoring miscalibration"
            })

        return {
            "status": "HEALTHY" if not anomalies else "ANOMALIES_DETECTED",
            "anomalies": anomalies,
            "metrics": {
                "scans_analyzed": len(recent_scans),
                "sweeps_detected": sweeps,
                "trades_executed": len(self.trade_history),
                "errors_total": len(self.error_history),
                "error_rate": error_rate,
                "kronos_decisions": len(recent_decisions) if recent_decisions else 0,
                "veto_rate": veto_rate if recent_decisions else 0
            }
        }

    def get_health_report(self) -> dict:
        """Generate comprehensive health report."""
        anomalies_result = self.detect_anomalies()

        # Categorize errors
        error_types = {}
        for error in self.error_history:
            error_type = error.get("type", "OTHER")
            error_types[error_type] = error_types.get(error_type, 0) + 1

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system_status": anomalies_result["status"],
            "uptime": str(datetime.now(timezone.utc) - self.start_time),
            "metrics": {
                "total_scans": len(self.scan_history),
                "total_trades": len(self.trade_history),
                "total_errors": len(self.error_history),
                "error_breakdown": error_types
            },
            "recent_anomalies": anomalies_result["anomalies"],
            "recent_errors": [
                {
                    "timestamp": e.get("timestamp"),
                    "type": e.get("type"),
                    "error": e.get("error")
                }
                for e in list(self.error_history)[-5:]
            ]
        }

    def should_alert(self) -> bool:
        """Return True if system is in ANOMALIES_DETECTED state."""
        anomalies = self.detect_anomalies()
        return anomalies["status"] == "ANOMALIES_DETECTED"

    def get_critical_alerts(self) -> list[dict]:
        """Return only ERROR-severity anomalies."""
        anomalies = self.detect_anomalies()
        return [a for a in anomalies["anomalies"] if a.get("severity") == "ERROR"]

    def log_alert(self, alert: dict):
        """Log alert to file for analysis and send notification."""
        try:
            if self._alert_file is None:
                self._alert_file = open(self.alert_log_file, "a", encoding="utf-8")  # noqa: SIM115
            line = json.dumps(
                self._sanitize_alert(alert), ensure_ascii=False, default=str
            )
            self._alert_file.write(line + "\n")
            self._alert_file.flush()
        except Exception as e:
            logger.error(f"Alert logging failed: {str(e)}")

        severity = alert.get("severity", "INFO")
        if severity in ("ERROR", "WARN") and self._notify_allowed(alert):
            send_notification(
                title=f"HEALTH {severity}",
                message=alert.get("message", str(alert)),
                level=severity,
            )

    @staticmethod
    def _sanitize_alert(alert: dict) -> dict:
        """
        Copy the alert with embedded newlines replaced, so the serialized
        record stays on exactly one line and the JSONL log remains parseable.
        """
        def _clean(value):
            if isinstance(value, str):
                return value.replace("\r", " ").replace("\n", " ")
            if isinstance(value, dict):
                return {k: _clean(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_clean(v) for v in value]
            return value
        return _clean(alert)

    def _notify_allowed(self, alert: dict) -> bool:
        """
        Per-alert-type cooldown. Returns False if a notification for this
        anomaly type was sent within NOTIFY_COOLDOWN_SECONDS, so a broken
        bot state cannot spam the operator.
        """
        alert_type = alert.get("type") or alert.get("title") or "GENERAL"
        now = time.monotonic()
        if now - self._notify_cooldowns.get(alert_type, 0.0) < NOTIFY_COOLDOWN_SECONDS:
            return False
        self._notify_cooldowns[alert_type] = now
        return True

    def close(self):
        """Flush and release the alert log file handle (idempotent)."""
        handle = self._alert_file
        self._alert_file = None
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except Exception as e:
                logger.error(f"Alert log close failed: {str(e)}")


if __name__ == "__main__":
    # Quick test
    logging.basicConfig(level=logging.INFO)

    monitor = HealthMonitor()

    # Simulate some scans
    for i in range(10):
        monitor.record_scan({
            "timestamp": (datetime.now(timezone.utc) - timedelta(minutes=15*i)).isoformat(),
            "session": "London Open" if i < 5 else "Dead Zone",
            "sweep_detected": i < 3,
            "confluence_score": 80 if i < 3 else 40,
            "adx": 28 if i < 5 else 12,
            "signal_direction": "BUY" if i == 1 else None,
            "kronos_agree": True if i == 1 else None,
            "trade_executed": i == 1
        })

    # Check health
    report = monitor.get_health_report()
    print(f"[OK] Status: {report['system_status']}")
    print(f"[OK] Anomalies: {len(report['recent_anomalies'])}")
    print(f"[OK] Trades: {report['metrics']['total_trades']}")
    monitor.close()

