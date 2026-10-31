"""Demo mode must never touch WeChat or submit a booking."""
from __future__ import annotations

from app.demo import DEMO_ACCOUNT, DemoBackend, DemoStore


def test_demo_auth_and_profiles_are_read_only():
    backend = DemoBackend()
    assert backend.auth() == DEMO_ACCOUNT
    rows = backend.profiles()
    assert rows and rows[0]["id"] == "webview_demo"


def test_demo_availability_and_orders_have_ui_fields():
    backend = DemoBackend()
    slots = backend.availability({"date": "2026-11-01"})
    assert slots["slots"][0]["cells"][0]["court"] == "01"
    orders = backend.list_orders(DEMO_ACCOUNT)["orders"]
    assert orders[0]["order_id"] and orders[0]["details_id"]


def test_demo_refuses_real_side_effects():
    backend = DemoBackend()
    for call in (lambda: backend.book({}, DEMO_ACCOUNT),
                 lambda: backend.cancel_order(DEMO_ACCOUNT, "1", "2")):
        try:
            call()
        except ValueError as exc:
            assert "演示" in str(exc)
        else:
            raise AssertionError("demo must refuse side effects")


def test_demo_store_round_trips():
    store = DemoStore()
    task, account = store.load()
    assert account == DEMO_ACCOUNT
    store.save(dict(task, target_time_start="15:00"), account)
    assert store.load()[0]["target_time_start"] == "15:00"
