"""Project paths and small user settings.

Runtime data lives under ``state/`` next to the code.  It is deliberately
separate from the checkout so ``.gitignore`` can keep account fingerprints,
booking journals and QR images out of Git.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web"
STATE_ROOT = Path(os.environ.get("XMU_GYM_STATE", ROOT / "state")).expanduser()
CONFIG_PATH = STATE_ROOT / "config.json"
CHINA = timezone(timedelta(hours=8))
DEFAULT_PORT = 1420

DEFAULTS = {
    "port": DEFAULT_PORT,
    "scheduler_enabled": False,
    "keep_awake": True,
    "wechat_profile": "",
    "notify": {
        "webhook_url": "",
        "hermes": False,
    },
}


def ensure_state() -> Path:
    STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE_ROOT.is_symlink() or not STATE_ROOT.is_dir():
        raise RuntimeError("state 目录无效")
    try:
        os.chmod(STATE_ROOT, 0o700)
    except OSError:
        pass
    return STATE_ROOT


def _merge(base: dict, value: dict) -> dict:
    result = dict(base)
    for key, item in value.items():
        if key in result and isinstance(result[key], dict) and isinstance(item, dict):
            result[key] = _merge(result[key], item)
        elif key in result:
            result[key] = item
    return result


def load() -> dict:
    ensure_state()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(DEFAULTS)
    except (OSError, ValueError):
        return dict(DEFAULTS)
    if not isinstance(raw, dict):
        return dict(DEFAULTS)
    return _merge(DEFAULTS, raw)


def save(config: dict) -> None:
    ensure_state()
    value = _merge(DEFAULTS, config)
    fd, name = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=STATE_ROOT)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, CONFIG_PATH)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def selected_profile() -> str:
    value = load().get("wechat_profile")
    return value if isinstance(value, str) else ""


def set_selected_profile(value: str) -> None:
    config = load()
    config["wechat_profile"] = value if isinstance(value, str) else ""
    save(config)


def outbox() -> Path:
    root = ensure_state() / "outbox"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root
