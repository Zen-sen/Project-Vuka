"""Notifier tests — config caching, per-title rate limiting, POSIX gating."""
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

import vuka.utils.notifier as n


@pytest.fixture(autouse=True)
def reset_notifier_state():
    n._last_sent.clear()
    n._config_cache["mtime"] = 0.0
    n._config_cache["data"] = {}
    yield
    n._last_sent.clear()


class TestConfigCache:
    def test_load_config_caches_until_mtime_changes(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps({"notifications": {"desktop": {"enabled": True}}}),
            encoding="utf-8",
        )
        with patch.object(n, "CONFIG_PATH", cfg):
            assert n._load_config() == {"desktop": {"enabled": True}}
            cached_mtime = n._config_cache["mtime"]
            # Content changed but mtime restored to the cached value -> cache hit.
            cfg.write_text(
                json.dumps({"notifications": {"desktop": {"enabled": False}}}),
                encoding="utf-8",
            )
            os.utime(cfg, (cached_mtime, cached_mtime))
            assert n._load_config() == {"desktop": {"enabled": True}}
            # Bump the mtime -> reload.
            os.utime(cfg, (cached_mtime + 5.0, cached_mtime + 5.0))
            assert n._load_config() == {"desktop": {"enabled": False}}

    def test_missing_config_returns_empty(self, tmp_path):
        with patch.object(n, "CONFIG_PATH", tmp_path / "missing.json"):
            assert n._load_config() == {}


class TestRateLimit:
    def test_telegram_rate_limit_per_title(self):
        cfg = {
            "desktop": {"enabled": False},
            "telegram": {"enabled": True, "bot_token": "T", "chat_id": "C"},
        }
        with patch.object(n, "_load_config", return_value=cfg), \
             patch.object(n, "_send_telegram") as st:
            n.send("ALERT", "first", level="GUARD")
            n.send("ALERT", "second", level="GUARD")
            n.send("OTHER", "third", level="GUARD")
        assert st.call_count == 2
        assert st.call_args_list[0][0][0].startswith("[GUARD] *ALERT*")
        assert st.call_args_list[1][0][0].startswith("[GUARD] *OTHER*")

    def test_desktop_not_rate_limited(self):
        cfg = {
            "desktop": {"enabled": True, "timeout": 3},
            "telegram": {"enabled": False},
        }
        with patch.object(n, "_load_config", return_value=cfg), \
             patch.object(n, "_send_desktop") as sd:
            n.send("T", "m")
            n.send("T", "m")
        assert sd.call_count == 2


class TestNotificationResult:
    def test_reports_delivery_per_channel(self):
        cfg = {
            "desktop": {"enabled": True},
            "telegram": {"enabled": True, "bot_token": "T", "chat_id": "C"},
        }
        with patch.object(n, "_load_config", return_value=cfg), \
             patch.object(n, "_send_desktop", return_value=True), \
             patch.object(n, "_send_telegram", return_value=True):
            result = n.send("HEALTH", "ok", level="INFO")
        assert result.desktop_ok is True
        assert result.telegram_ok is True
        assert result.delivered is True

    def test_reports_disabled_channels(self):
        cfg = {
            "desktop": {"enabled": False},
            "telegram": {"enabled": False},
        }
        with patch.object(n, "_load_config", return_value=cfg):
            result = n.send("SILENT", "x")
        assert result.delivered is False
        assert result.desktop_reason == "disabled"
        assert result.telegram_reason == "disabled"

    def test_reports_telegram_send_failure(self):
        cfg = {
            "desktop": {"enabled": False},
            "telegram": {"enabled": True, "bot_token": "T", "chat_id": "C"},
        }
        with patch.object(n, "_load_config", return_value=cfg), \
             patch.object(n, "_send_telegram", return_value=False):
            result = n.send("ALERT", "x")
        assert result.telegram_ok is False
        assert result.telegram_reason == "telegram send failed"
        assert result.delivered is False

    def test_reports_rate_limited_channel(self):
        cfg = {
            "desktop": {"enabled": False},
            "telegram": {"enabled": True, "bot_token": "T", "chat_id": "C"},
        }
        with patch.object(n, "_load_config", return_value=cfg), \
             patch.object(n, "_send_telegram") as st:
            n.send("SPAM", "one")
            result = n.send("SPAM", "two")
        assert result.telegram_ok is False
        assert result.telegram_reason == "rate-limited"
        assert st.call_count == 1


class TestDesktopPath:
    def test_desktop_skipped_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(n.sys, "platform", "linux")
        cfg = {
            "desktop": {"enabled": True},
            "telegram": {"enabled": False},
        }
        with patch.object(n, "_load_config", return_value=cfg):
            result = n.send("T", "M")
        assert result.desktop_ok is False
        assert result.delivered is False
        assert result.desktop_reason == "no desktop channel available"

    def test_powershell_uses_env_vars_not_interpolation(self, monkeypatch):
        """User text must travel via env vars, never in the command string."""
        fake_subprocess = types.ModuleType("subprocess")
        calls = []

        def run(args, **kwargs):
            calls.append((args, kwargs))

        fake_subprocess.run = run
        monkeypatch.setitem(sys.modules, "subprocess", fake_subprocess)
        monkeypatch.setattr(n.sys, "platform", "win32")

        title = "Profit: $500; rm -rf"
        body = "backtick ` and quote \" here"
        n._send_desktop(title, body, timeout=5)

        args, kwargs = calls[0]
        assert title not in args[1]
        assert body not in args[1]
        assert kwargs["env"]["VUKA_TITLE"] == title
        assert kwargs["env"]["VUKA_MSG"] == body


class TestTelegramSend:
    def test_posts_to_telegram_api(self):
        resp = MagicMock()
        resp.status_code = 200
        with patch.object(n, "_load_config", return_value={}), \
             patch("requests.post", return_value=resp) as post:
            n._send_telegram("*Hi*", "TOKEN", "CHAT")
        post.assert_called_once()
        url, payload = post.call_args[0][0], post.call_args.kwargs["json"]
        assert "TOKEN" in url
        assert payload["chat_id"] == "CHAT"
        assert payload["parse_mode"] == "Markdown"

    def test_noop_without_credentials(self):
        with patch("requests.post") as post:
            n._send_telegram("*Hi*", "", "")
        post.assert_not_called()
