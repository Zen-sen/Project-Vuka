"""
notifier.py -- Desktop toast + Telegram notifications for Project Vuka.

Non-blocking: failures in any channel are logged but never crash the caller.
Config read from config_v4.6.json and cached with an mtime check (live-reload
friendly without a disk read on every send()).
"""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path("config_v4.6.json")
logger = logging.getLogger("Notifier")

# Telegram flood control: at most one message per title per cooldown window.
TELEGRAM_COOLDOWN_SEC = 30.0
_last_sent: dict[tuple, float] = {}

# Config cache: {mtime: float, data: dict}
_config_cache: dict = {"mtime": 0.0, "data": {}}


@dataclass
class NotificationResult:
    """Per-channel delivery status so callers can escalate on failure."""

    desktop_ok: bool = False
    telegram_ok: bool = False
    desktop_reason: str = ""
    telegram_reason: str = ""

    @property
    def delivered(self) -> bool:
        """True if at least one channel delivered the notification."""
        return self.desktop_ok or self.telegram_ok



def _load_config():
    try:
        if not CONFIG_PATH.exists():
            return {}
        mtime = CONFIG_PATH.stat().st_mtime
        if mtime != _config_cache["mtime"]:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            _config_cache["mtime"] = mtime
            _config_cache["data"] = cfg.get("notifications", {})
        return _config_cache["data"]
    except Exception as e:
        logger.warning(f"Failed to load notification config: {e}")
    return {}


def _send_desktop(title: str, message: str, timeout: int = 5) -> bool:
    if sys.platform != "win32":
        # The whole toast chain (WinRT PowerShell, win10toast, ctypes.windll)
        # is Windows-only. creationflags is invalid on POSIX, so bail early.
        logger.debug("Desktop toasts are Windows-only; skipping.")
        return False

    # Method 1: Native Windows toast via PowerShell (WinRT).
    # The title/message travel as environment variables, never interpolated
    # into the command string, so '$', '`' and ';' in the text cannot be
    # executed by the shell.
    try:
        import subprocess
        ps = (
            '[Windows.UI.Notifications.ToastNotificationManager, '
            'Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;'
            '$t = [Windows.UI.Notifications.ToastNotificationManager]::'
            'GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
            '$t.GetElementsByTagName("text").Item(0).AppendChild('
            '$t.CreateTextNode($env:VUKA_TITLE)) | Out-Null;'
            '$t.GetElementsByTagName("text").Item(1).AppendChild('
            '$t.CreateTextNode($env:VUKA_MSG)) | Out-Null;'
            '[Windows.UI.Notifications.ToastNotificationManager]::'
            'CreateToastNotifier("Project Vuka").Show('
            '[Windows.UI.Notifications.ToastNotification]::new($t))'
        )
        env = dict(os.environ)
        env["VUKA_TITLE"] = title
        env["VUKA_MSG"] = message
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=timeout, creationflags=0x08000000, env=env,
        )
        return True
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"PowerShell toast failed: {e}")

    # Method 2: win10toast fallback (may fail on newer Python)
    try:
        from win10toast import ToastNotifier
        n = ToastNotifier()
        n.show_toast(title, message, duration=timeout, threaded=True)
        return True
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"win10toast failed: {e}")

    # Method 3: ctypes MessageBox (blocking but always works)
    try:
        import ctypes
        ret = ctypes.windll.user32.MessageBoxW(0, message, title, 0x00000000 | 0x00000040)
        return ret != 0
    except Exception as e:
        logger.debug(f"MessageBox fallback failed: {e}")
    return False


def _send_telegram(message: str, bot_token: str = "", chat_id: str = "") -> bool:
    if not bot_token or not chat_id:
        return False
    try:
        import requests
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"Telegram send failed: {resp.status_code} {resp.text[:200]}")
            return False
        return True
    except ImportError:
        logger.debug("requests not installed -- telegram notifications disabled")
        return False
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")
        return False


def _tg_escape(text: str) -> str:
    """Escape Telegram Markdown v1 special characters so raw user text cannot
    break the message formatting or trigger a Telegram 400 parse error."""
    for ch in "\\_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, "\\" + ch)
    return text


def _cooldown_ok(channel: str, title: str) -> bool:
    """Per-channel-per-title rate limit. Returns True if a send is allowed."""
    now = time.monotonic()
    key = (channel, title)
    if now - _last_sent.get(key, 0.0) < TELEGRAM_COOLDOWN_SEC:
        logger.debug(f"{channel} rate-limited for title '{title}'")
        return False
    _last_sent[key] = now
    return True


def send(
    title: str,
    message: str,
    level: str = "INFO",
    desktop: bool | None = None,
    telegram: bool | None = None,
) -> NotificationResult:
    """
    Send a notification through enabled channels.

    Args:
        title: Short title / event name.
        message: Main notification body.
        level: INFO, WARN, ERROR, GUARD, TRADE.
        desktop: Override config enable/disable for desktop.
        telegram: Override config enable/disable for telegram.

    Returns:
        NotificationResult with per-channel delivery status; callers can
        escalate (e.g. to an alarm channel) when ``delivered`` is False.
    """
    cfg = _load_config()
    result = NotificationResult()

    # Desktop toast
    desk_cfg = cfg.get("desktop", {})
    desk_enabled = desk_cfg.get("enabled", True) if desktop is None else desktop
    if desk_enabled:
        result.desktop_ok = _send_desktop(title, message, desk_cfg.get("timeout", 5))
        if not result.desktop_ok:
            result.desktop_reason = "no desktop channel available"
    else:
        result.desktop_reason = "disabled"

    # Telegram
    tel_cfg = cfg.get("telegram", {})
    tel_enabled = tel_cfg.get("enabled", False) if telegram is None else telegram
    if tel_enabled:
        if _cooldown_ok("telegram", title):
            tag = f"[{_tg_escape(level)}]" if level else ""
            formatted = f"{tag} *{_tg_escape(title)}*\n\n{_tg_escape(message)}"
            result.telegram_ok = _send_telegram(
                formatted, tel_cfg.get("bot_token", ""), tel_cfg.get("chat_id", "")
            )
            if not result.telegram_ok:
                result.telegram_reason = "telegram send failed"
        else:
            result.telegram_reason = "rate-limited"
    else:
        result.telegram_reason = "disabled"

    return result
