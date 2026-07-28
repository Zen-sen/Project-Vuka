"""
notifier.py -- Desktop toast + Telegram notifications for Project Vuka.

Non-blocking: failures in any channel are logged but never crash the caller.
Config read from config_v4.6.json at send() time (live-reload friendly).
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path("config_v4.6.json")
logger = logging.getLogger("Notifier")


def _load_config():
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return cfg.get("notifications", {})
    except Exception as e:
        logger.warning(f"Failed to load notification config: {e}")
    return {}


def _send_desktop(title: str, message: str, timeout: int = 5):
    # Method 1: Native Windows toast via PowerShell (WinRT)
    try:
        import subprocess
        import xml.sax.saxutils as saxutils
        etitle = saxutils.escape(title)
        emsg = saxutils.escape(message)
        ps = (
            f'[Windows.UI.Notifications.ToastNotificationManager, '
            f'Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;'
            f'$t = [Windows.UI.Notifications.ToastNotificationManager]::'
            f'GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
            f'$t.GetElementsByTagName("text").Item(0).AppendChild('
            f'$t.CreateTextNode("{etitle}")) | Out-Null;'
            f'$t.GetElementsByTagName("text").Item(1).AppendChild('
            f'$t.CreateTextNode("{emsg}")) | Out-Null;'
            f'[Windows.UI.Notifications.ToastNotificationManager]::'
            f'CreateToastNotifier("Project Vuka").Show('
            f'[Windows.UI.Notifications.ToastNotification]::new($t))'
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=timeout, creationflags=0x08000000  # CREATE_NO_WINDOW
        )
        return
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.debug(f"PowerShell toast failed: {e}")

    # Method 2: win10toast fallback (may fail on newer Python)
    try:
        from win10toast import ToastNotifier
        n = ToastNotifier()
        n.show_toast(title, message, duration=timeout, threaded=True)
        return
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"win10toast failed: {e}")

    # Method 3: ctypes MessageBox (blocking but always works)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x00000000 | 0x00000040)
    except Exception as e:
        logger.debug(f"MessageBox fallback failed: {e}")


def _send_telegram(message: str, bot_token: str = "", chat_id: str = ""):
    if not bot_token or not chat_id:
        return
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
    except ImportError:
        logger.debug("requests not installed -- telegram notifications disabled")
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")


def send(
    title: str,
    message: str,
    level: str = "INFO",
    desktop: Optional[bool] = None,
    telegram: Optional[bool] = None,
):
    """
    Send a notification through enabled channels.

    Args:
        title: Short title / event name.
        message: Main notification body.
        level: INFO, WARN, ERROR, GUARD, TRADE.
        desktop: Override config enable/disable for desktop.
        telegram: Override config enable/disable for telegram.
    """
    cfg = _load_config()

    # Desktop toast
    desk_cfg = cfg.get("desktop", {})
    if desk_cfg.get("enabled", True) if desktop is None else desktop:
        _send_desktop(title, message, desk_cfg.get("timeout", 5))

    # Telegram
    tel_cfg = cfg.get("telegram", {})
    if tel_cfg.get("enabled", False) if telegram is None else telegram:
        tag = f"[{level}]" if level else ""
        formatted = f"{tag} *{title}*\n\n{message}"
        _send_telegram(formatted, tel_cfg.get("bot_token", ""), tel_cfg.get("chat_id", ""))
