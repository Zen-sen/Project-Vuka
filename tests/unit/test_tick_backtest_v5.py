"""TickBacktester tests — seed reproducibility, Monte Carlo, slippage, outcomes (audit XXVI)."""
import numpy as np
import pandas as pd
import pytest

from vuka.core.tick_backtest_v5 import (
    ExecutionMode,
    TickBacktester,
    _summarize_metric,
    _summarize_mode,
)


@pytest.fixture
def candle_csv(tmp_path):
    dates = pd.date_range("2026-01-01", periods=100, freq="1min")
    rng = np.random.default_rng(0)
    close = 1.1000 + np.cumsum(rng.normal(0, 0.0002, 100))
    df = pd.DataFrame({
        "time": dates,
        "open": close - 0.0001,
        "high": close + 0.0002,
        "low": close - 0.0002,
        "close": close,
        "volume": rng.integers(100, 1000, 100),
    })
    path = tmp_path / "candles.csv"
    df.to_csv(path, index=False)
    return path


def _backtester(candle_csv, **kwargs):
    return TickBacktester(str(candle_csv), **kwargs)


class TestReproducibility:
    def test_same_seed_reproduces_metrics(self, candle_csv):
        bt1 = _backtester(candle_csv, seed=7)
        bt2 = _backtester(candle_csv, seed=7)
        r1 = bt1.backtest_polling_vs_event_driven()
        r2 = bt2.backtest_polling_vs_event_driven()
        assert r1["event_driven"]["avg_latency_ms"] == r2["event_driven"]["avg_latency_ms"]
        assert r1["polling"]["15s"]["win_rate_pct"] == r2["polling"]["15s"]["win_rate_pct"]

    def test_different_seeds_differ(self, candle_csv):
        bt1 = _backtester(candle_csv, seed=1)
        bt2 = _backtester(candle_csv, seed=2)
        r1 = bt1.backtest_polling_vs_event_driven()
        r2 = bt2.backtest_polling_vs_event_driven()
        # Latency draws differ across seeds; event-driven latency is uniform
        # (0, 0.01ms) so at least one metric almost certainly differs.
        assert r1["event_driven"]["avg_latency_ms"] != r2["event_driven"]["avg_latency_ms"]


class TestExecutionModel:
    def test_event_driven_enters_every_candle(self, candle_csv):
        bt = _backtester(candle_csv, seed=5)
        entries = bt.simulate_event_driven_execution()
        assert len(entries) == len(bt.candle_data)

    def test_event_driven_latency_sub_millisecond(self, candle_csv):
        bt = _backtester(candle_csv, seed=5)
        latencies = [e["latency_ms"] for e in bt.simulate_event_driven_execution()]
        assert all(0 <= lat < 0.01 for lat in latencies)

    def test_slippage_against_direction(self, candle_csv):
        bt = _backtester(candle_csv, seed=5)
        df = pd.DataFrame(bt.simulate_event_driven_execution())
        buys = df[df["direction"] == "BUY"]
        sells = df[df["direction"] == "SELL"]
        assert (buys["entry_price"] >= buys["close_price"]).all()
        assert (sells["entry_price"] <= sells["close_price"]).all()

    def test_point_size_used_in_slippage(self, candle_csv):
        bt = _backtester(candle_csv, seed=5, point_size=0.01)
        assert bt.point_size == 0.01


class TestOutcomes:
    def test_resolved_against_following_candle(self, candle_csv):
        bt = _backtester(candle_csv, seed=5)
        closes = bt.candle_data["close"].values
        df = pd.DataFrame([
            {"candle_index": 0, "direction": "BUY", "entry_price": closes[1] + 0.0001},
            {"candle_index": 0, "direction": "SELL", "entry_price": closes[1] - 0.0001},
        ])
        outcomes = bt._simulate_trade_outcomes(df)
        assert outcomes[0]["won"] is False  # BUY above next close
        assert outcomes[1]["won"] is False  # SELL below next close
        assert outcomes[0]["pips"] > 0

    def test_win_and_loss_both_possible(self, candle_csv):
        bt = _backtester(candle_csv, seed=5)
        res = bt._backtest_single_mode(ExecutionMode.EVENT_DRIVEN, seed=5)
        assert res["trades_won"] + res["trades_lost"] == res["entries_count"]
        # Driven by real price moves, not a fixed probability.
        assert 0 < res["win_rate_pct"] < 100


class TestMonteCarlo:
    def test_summary_shape(self, candle_csv):
        bt = _backtester(candle_csv, seed=1)
        summary = bt.run_monte_carlo(n_runs=3, seed_start=10)
        assert summary["n_runs"] == 3
        assert summary["seeds"] == [10, 11, 12]
        ed = summary["event_driven"]
        assert ed["runs"] == 3
        for key in ("win_rate_pct", "avg_latency_ms", "avg_slippage_pips"):
            assert ed[key]["mean"] is not None
        assert "15s" in summary["polling"]

    def test_summarize_metric_skips_nonfinite(self):
        s = _summarize_metric([1.0, 3.0, float("nan"), None, 5.0])
        assert s["mean"] == 3.0
        assert s["min"] == 1.0
        assert s["max"] == 5.0

    def test_summarize_metric_empty(self):
        s = _summarize_metric([])
        assert s["mean"] is None

    def test_summarize_mode_error_passthrough(self):
        assert _summarize_mode([{"error": "boom", "entries_count": 0}]) == {"error": "boom"}
