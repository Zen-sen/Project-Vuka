"""bot.py entry-point hardening tests.

P0 regression: importing ``vuka.core.bot`` must never touch ``sys.argv`` or
exit with SystemExit. Argument parsing is deferred to ``main()``.
"""
import sys
from unittest.mock import patch

import pytest

from vuka.core.bot import _build_parser
from vuka.core.state import s


def test_import_does_not_parse_args():
    # The import above must have succeeded -- the pre-fix module called
    # parser.parse_args() at import time and would have raised SystemExit here.
    import vuka.core.bot as bot
    assert bot._arg_symbol == ""      # import-safe default, not parsed
    assert bot._instance_tag == "_"   # placeholder until main() runs


def test_build_parser_rejects_missing_positionals():
    with pytest.raises(SystemExit):
        _build_parser().parse_args([])


def test_build_parser_accepts_valid_invocation():
    args = _build_parser().parse_args(["EURUSD", "INGWE", "--check"])
    assert args.symbol == "EURUSD"
    assert args.strategy == "INGWE"
    assert args.check is True


def test_main_syncs_state_and_runs_check_mode():
    import vuka.core.bot as bot

    with patch.object(sys, "argv", ["ingwe", "EURUSD", "INGWE", "--check"]), \
         patch.object(bot.mt5, "initialize", return_value=True), \
         patch.object(bot.mt5, "shutdown") as mock_shutdown, \
         patch.object(bot, "load_sessions", return_value=set()), \
         patch.object(bot, "get_initial_equity"), \
         patch.object(bot, "load_consecutive_losses"), \
         patch.object(bot, "run_agent") as mock_run:
        bot.main()

    # Module globals were finalized from parsed args...
    assert bot.STRATEGY == "INGWE"
    assert bot.SYMBOL == "EURUSDc"
    assert bot._instance_tag == "EURUSD_INGWE"

    # ...and mirrored onto the shared state singleton.
    assert s.STRATEGY == "INGWE"
    assert s.SYMBOL == "EURUSDc"
    assert s._instance_tag == "EURUSD_INGWE"
    assert s.SCAN_INTERVAL_SEC == 900

    # --check runs exactly one scan, then shuts MT5 down cleanly.
    mock_run.assert_called_once()
    mock_shutdown.assert_called_once()


def test_htf_bias_cache_lives_on_state():
    import vuka.core.bot as bot

    assert isinstance(s.htf_bias_cache, dict)
    # BACKTEST_MODE guard returns before touching MT5 / the cache.
    s.BACKTEST_MODE = True
    with patch.object(bot.mt5, "copy_rates_from_pos", return_value=None):
        assert bot.get_htf_bias() is None
