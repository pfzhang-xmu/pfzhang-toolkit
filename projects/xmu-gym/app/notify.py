"""Optional notification helpers.

The page is the primary interface.  A webhook is intentionally generic so users
can wire it to Hermes, a bot, or their own service without the project knowing
their private setup.
"""
from __future__ import annotations

import json

import requests

from app import config


def latest_message() -> str:
    try:
        value = json.loads((config.STATE_ROOT / "scheduler.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(value, dict):
        return ""
    message = value.get("message")
    return message.strip() if isinstance(message, str) else ""


def send_webhook(url: str, message: str) -> None:
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ValueError("webhook 地址无效")
    response = requests.post(url, json={"text": message, "source": "xmu-gym"}, timeout=10)
    response.raise_for_status()
