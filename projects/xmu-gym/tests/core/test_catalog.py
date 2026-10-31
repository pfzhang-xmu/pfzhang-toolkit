import json
from pathlib import Path

import pytest

from core.booking import catalog
from core.booking.client import Client
from core.booking.session import BookingError, EntryTicket


def observation():
    return catalog.describe_intervals({"personal_num": 1, "is_can_choose": 1,
        "time_slot_list": [{"date": "2026-10-13", "time_range": "19:30-21:00", "slots": [
            {"column_id": 49, "area_name": "01", "max_count": 1, "selected": 1, "status": "available"},
            {"column_id": 50, "area_name": "02", "max_count": 1, "selected": 0, "status": "available"},
        ]}]})


def test_full_cells_remain_in_inventory_but_are_unavailable():
    data = observation()
    assert data["court_count"] == 2
    assert [cell["available"] for cell in data["slots"][0]["cells"]] == [False, True]
    assert data["time_slots"] == [{"start": "19:30", "end": "21:00"}]


def test_pacing_and_read_only_allowlist(monkeypatch):
    clock = [10.0]
    waits = []
    monkeypatch.setattr(catalog.time, "monotonic", lambda: clock[0])
    def sleep(duration):
        waits.append(duration)
        clock[0] += duration
    monkeypatch.setattr(catalog.time, "sleep", sleep)
    monkeypatch.setattr(Client, "post", lambda *args: {"status": 1})
    client = catalog.InventoryClient(EntryTicket({"uid": "1234"}, Path("fake")))
    try:
        client.post("/index/Stadium/getStadiumList", {})
        client.post("/index/Stadium/getStadiumDetails", {})
        assert waits == [1.5]
        with pytest.raises(BookingError, match="只允许"):
            client.post("/index/Stadium/addOrder", {})
        assert client.request_count == 2
    finally:
        client.http.close()


@pytest.mark.parametrize("interval", [0, float("nan"), float("inf")])
def test_cannot_disable_rate_limit(interval):
    with pytest.raises(BookingError):
        catalog.InventoryClient(EntryTicket({"uid": "1234"}, Path("fake")), interval)


def test_failed_scan_stops_without_retry_and_saves_partial_snapshot(monkeypatch, tmp_path):
    calls = []
    class FakeClient:
        request_count = 0
        http = type("Http", (), {"close": lambda self: None})()
        def __init__(self, *args):
            pass
        def login(self):
            pass
        def require(self, path, *args):
            calls.append(path)
            if path.endswith("getStadiumList"):
                return {"stadium": [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]}
            raise BookingError("HTTP_FAILED", "限流或网络错误")
    monkeypatch.setattr(catalog, "discover_entry", lambda _: None)
    monkeypatch.setattr(catalog, "InventoryClient", FakeClient)
    path = tmp_path / "snapshot.json"
    with pytest.raises(BookingError):
        catalog.collect_inventory(path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert not saved["complete"] and saved["errors"][0]["code"] == "HTTP_FAILED"
    assert len(calls) == 2


def test_merge_preserves_unobserved_venues_and_accepts_fullwidth_parentheses(tmp_path):
    path = tmp_path / "catalog.json"
    catalog.write_json(path, {"sports": [{"ocr_label": "运动", "venues": [
        {"ocr_label": "a(b)", "has_courts": False, "time_slots": []},
        {"ocr_label": "未采集", "time_slots": [{"start": "09:00", "end": "10:00"}]},
    ]}]})
    snap = {"observed_at": "now", "venues": [{"venue": {"sport": "运动", "name": "a（b）"}, **observation()}]}
    assert catalog.update_catalog(path, snap) == 1
    venues = json.loads(path.read_text(encoding="utf-8"))["sports"][0]["venues"]
    assert venues[0]["courts"] == []
    assert venues[1]["time_slots"][0]["start"] == "09:00"


def test_daily_locks_never_remove_known_schedule_templates(tmp_path):
    path = tmp_path / "catalog.json"
    catalog.write_json(path, {"sports": [{"ocr_label": "运动", "venues": [
        {"ocr_label": "a", "has_courts": True, "time_slots": [{"start": "09:00", "end": "10:00"}]}]}]})
    data = observation()
    for cell in data["slots"][0]["cells"]:
        cell.update(status="locked", reason_code="SLOT_LOCKED", available=False)
    snapshot = {"observed_at": "now", "complete": True, "venues": [{"venue": {"sport": "运动", "name": "a"}, **data}]}
    catalog.update_catalog(path, snapshot)
    venue = json.loads(path.read_text(encoding="utf-8"))["sports"][0]["venues"][0]
    assert venue["time_slots"] == [{"start": "09:00", "end": "10:00"}, {"start": "19:30", "end": "21:00"}]
    assert "status" not in json.dumps(venue)
    output = tmp_path / "structure.json"
    catalog.write_structure(output, snapshot)
    structure = json.loads(output.read_text(encoding="utf-8"))
    assert structure["venues"][0]["time_slots"] == data["time_slots"]
    assert not any(key in output.read_text(encoding="utf-8") for key in ("SLOT_LOCKED", "lock_reason", "remaining"))
