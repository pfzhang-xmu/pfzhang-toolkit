"""Built-in 06:58 scheduler.

The scheduler is intentionally a small thread inside the local server: no
launchd/Task Scheduler XML is required to use the project.  The OS autostart
scripts only keep the server running after login.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from app import config

WINDOW_START = (6, 58)
WINDOW_END = (7, 20)


def _now_china(now=None) -> datetime:
    value = now or datetime.now(config.CHINA)
    if value.utcoffset() is None:
        value = value.replace(tzinfo=config.CHINA)
    return value.astimezone(config.CHINA)


def _at(day, hour, minute) -> datetime:
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


class Scheduler:
    def __init__(self, store=None, now=None, runner=None):
        self.store = store
        self._now = now or (lambda: datetime.now(config.CHINA))
        self._runner = runner or self._run_subprocess
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.RLock()
        self._state_path = config.STATE_ROOT / "scheduler.json"

    # ------------------------------------------------------------- lifecycle
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="xmu-gym-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self):
        while not self._stop.wait(20):
            try:
                self.tick()
            except Exception:
                # A scheduler failure must never take the page down.
                pass

    # ---------------------------------------------------------------- status
    def _has_task(self) -> bool:
        try:
            if self.store is None:
                from app.daily import Store
                self.store = Store()
            self.store.load()
            return True
        except FileNotFoundError:
            return False
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def status_string(self) -> str:
        try:
            enabled = bool(config.load().get("scheduler_enabled"))
            if enabled and self._has_task():
                return "enabled"
            if self._has_task():
                return "paused"
            return "not_configured"
        except (OSError, ValueError, KeyError, TypeError):
            return "unknown"

    def _read_state(self) -> dict:
        try:
            value = json.loads(self._state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write_state(self, value: dict) -> None:
        config.ensure_state()
        import tempfile
        fd, name = tempfile.mkstemp(prefix=".scheduler-", suffix=".json", dir=config.STATE_ROOT)
        try:
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):
                pass
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(value, out, ensure_ascii=False, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self._state_path)
        except BaseException:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise

    def status(self) -> dict:
        now = _now_china(self._now())
        state = self._read_state()
        today = now.date().isoformat()
        next_run = _at(now, *WINDOW_START)
        if now >= next_run:
            next_run += timedelta(days=1)
        last = state if state else None
        current = "idle"
        if state.get("date") == today:
            current = state.get("state") or "idle"
        return {
            "enabled": self.status_string() == "enabled",
            "state": current,
            "next_run": next_run.isoformat(),
            "last": last,
            "window": f"{WINDOW_START[0]:02d}:{WINDOW_START[1]:02d}–{WINDOW_END[0]:02d}:{WINDOW_END[1]:02d}",
        }

    def set_enabled(self, enabled: bool) -> dict:
        value = config.load()
        value["scheduler_enabled"] = bool(enabled)
        config.save(value)
        return self.status()

    # ------------------------------------------------------------------ tick
    def _in_window(self, now: datetime) -> bool:
        start = _at(now, *WINDOW_START)
        end = _at(now, *WINDOW_END)
        return start <= now <= end

    def tick(self, now=None) -> bool:
        now = _now_china(now or self._now())
        if self.status_string() != "enabled" or not self._in_window(now):
            return False
        state = self._read_state()
        today = now.date().isoformat()
        if state.get("date") == today and state.get("state") in ("running", "done"):
            return False
        with self._lock:
            # Re-read under the lock: another tick may have started it.
            state = self._read_state()
            if state.get("date") == today and state.get("state") in ("running", "done"):
                return False
            self._write_state({
                "date": today,
                "state": "running",
                "started_at": now.isoformat(),
                "pid": os.getpid(),
            })
        try:
            ok, message = self._runner(today)
        except Exception as exc:
            self._write_state({
                "date": today, "state": "failed", "started_at": now.isoformat(),
                "finished_at": _now_china(self._now()).isoformat(),
                "message": str(exc)[:300],
            })
            return True
        self._write_state({
            "date": today,
            "state": "done" if ok else "failed",
            "started_at": now.isoformat(),
            "finished_at": _now_china(self._now()).isoformat(),
            "message": (message or "").strip()[:2000],
        })
        return True

    def _run_subprocess(self, run_date: str):
        env = dict(os.environ)
        env["XMU_GYM_STATE"] = str(config.STATE_ROOT)
        env["PYTHONUNBUFFERED"] = "1"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        done = subprocess.run(
            [sys.executable, "-m", "app.daily_prestart"],
            cwd=str(config.ROOT), env=env, capture_output=True, text=True,
            timeout=25 * 60, creationflags=flags,
        )
        output = (done.stdout or "").strip()
        detail = (done.stderr or "").strip()
        if done.returncode != 0:
            return False, (detail or output or f"exit {done.returncode}")[-2000:]
        return True, output[-2000:]
