"""Scheduler state machine tests; no network, no real booking."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from app import config
from app import scheduler as scheduler_module
from app.daily import Store


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "STATE_ROOT", state)
    monkeypatch.setattr(config, "CONFIG_PATH", state / "config.json")
    store = Store(state)
    store.save({"sport": "健身房", "venue": "翔安校区健身房",
                "target_time_start": "13:30", "target_time_end": "15:00",
                "allow_time_fallback": False}, "a" * 64)
    value = config.load()
    value["scheduler_enabled"] = True
    config.save(value)
    return state, store


def test_status_is_enabled_and_has_next_run(env):
    state, store = env
    sched = scheduler_module.Scheduler(store=store, now=lambda: datetime(2026, 10, 31, 6, 0, tzinfo=config.CHINA))
    assert sched.status_string() == "enabled"
    status = sched.status()
    assert status["enabled"] is True
    assert status["next_run"].startswith("2026-10-31T06:58")


def test_tick_runs_once_inside_window(env):
    state, store = env
    calls = []
    sched = scheduler_module.Scheduler(
        store=store,
        now=lambda: datetime(2026, 10, 31, 6, 59, tzinfo=config.CHINA),
        runner=lambda date: calls.append(date) or (True, "ok"),
    )
    assert sched.tick() is True
    assert sched.tick() is False
    assert calls == ["2026-10-31"]
    saved = json.loads((state / "scheduler.json").read_text())
    assert saved["state"] == "done"


def test_tick_does_nothing_outside_window(env):
    state, store = env
    calls = []
    sched = scheduler_module.Scheduler(
        store=store,
        now=lambda: datetime(2026, 10, 31, 12, 0, tzinfo=config.CHINA),
        runner=lambda date: calls.append(date) or (True, "ok"),
    )
    assert sched.tick() is False
    assert calls == []


def test_disabled_scheduler_never_runs(env):
    state, store = env
    value = config.load()
    value["scheduler_enabled"] = False
    config.save(value)
    calls = []
    sched = scheduler_module.Scheduler(
        store=store,
        now=lambda: datetime(2026, 10, 31, 7, 0, tzinfo=config.CHINA),
        runner=lambda date: calls.append(date) or (True, "ok"),
    )
    assert sched.status_string() == "paused"
    assert sched.tick() is False
    assert calls == []


def test_failed_run_is_recorded(env):
    state, store = env
    sched = scheduler_module.Scheduler(
        store=store,
        now=lambda: datetime(2026, 10, 31, 7, 0, tzinfo=config.CHINA),
        runner=lambda date: (False, "boom"),
    )
    assert sched.tick() is True
    saved = json.loads((state / "scheduler.json").read_text())
    assert saved["state"] == "failed"
    assert "boom" in saved["message"]
