from pathlib import Path

import pytest

from core.booking import app
from core.booking.client import cell_reason, rejection_code, submit
from core.booking.session import BookingError, EntryTicket
from test_http_booking import target, plan, FakeClient, FakeSolver


def setup_batch(monkeypatch, tmp_path, callback):
    monkeypatch.setattr(app, "discover_entry", lambda _: EntryTicket({"uid": "1234"}, Path("fake")))
    monkeypatch.setattr(app, "data_root", lambda: tmp_path)
    monkeypatch.setattr(app.time, "sleep", lambda _: None)
    monkeypatch.setattr(app, "run_target", callback)


def test_live_quota_phrase_classified_and_original_response_retained(tmp_path):
    message = "当前已选【03 2026-10-14 19:00-20:00】预约名额已满"
    result = submit(FakeClient([{"status": 0, "info": message}]), target(), plan(), FakeSolver(), tmp_path)
    assert result["code"] == "SLOT_OCCUPIED" and result["message"] == message
    assert result["server_status"] == 0


def test_lock_zero_capacity_and_unknown_unavailable_are_not_occupancy():
    data = {"personal_num": 1, "is_can_choose": 1}
    assert cell_reason(data, {"status": "available", "selected": 1, "max_count": 1, "is_lock": 1}) == "SLOT_OCCUPIED"
    assert cell_reason(data, {"status": "locked", "selected": 1, "max_count": 1}) == "SLOT_LOCKED"
    assert cell_reason(data, {"status": "available", "selected": 0, "max_count": 0}) == "SLOT_UNAVAILABLE"
    assert cell_reason(data, {"status": "unavailable", "selected": 0, "max_count": 1}) == "SLOT_UNAVAILABLE"
    assert rejection_code("预约已截止") == "BOOKING_CLOSED"
    assert rejection_code("不在预约时间内") == "NOT_OPEN"
    assert rejection_code("其他错误") == "API_REJECTED"
    assert rejection_code("每个账号每天最多只能预约1个时段(2026-10-13)") == "ACCOUNT_LIMIT"


def test_only_explicit_candidate_order_is_used_and_finished_batch_is_reused(monkeypatch, tmp_path):
    calls = []
    def run(item, *args, **options):
        calls.append(item.court)
        assert item.date == target().date and item.time == target().time
        if len(calls) == 1:
            return {"ok": False, "state": "rejected", "code": "SLOT_OCCUPIED"}
        return {"ok": True, "state": "submitted", "verified": True}
    setup_batch(monkeypatch, tmp_path, run)
    result = app.run_candidates(target(), fallbacks=[{"court": "03"}, {"court": "04"}], commit=True, idempotency_key="batch")
    assert result["ok"] and calls == ["02", "03"]
    assert len(result["attempts"]) == 2
    assert app.run_candidates(target(), fallbacks=[{"court": "03"}, {"court": "04"}], commit=True, idempotency_key="batch") == result
    assert calls == ["02", "03"]
    with pytest.raises(BookingError) as error:
        app.run_candidates(target(), fallbacks=[{"court": "04"}], commit=True, idempotency_key="batch")
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("code", ["SLOT_LOCKED", "BOOKING_CLOSED", "NOT_OPEN", "ACCOUNT_LIMIT", "SESSION_EXPIRED", "API_REJECTED", "CAPTCHA_REJECTED", "HTTP_FAILED"])
def test_nonoccupancy_failure_stops_candidates(monkeypatch, tmp_path, code):
    calls = []
    def run(item, *args, **options):
        calls.append(item)
        raise BookingError(code, "failure")
    setup_batch(monkeypatch, tmp_path, run)
    result = app.run_candidates(target(), fallbacks=[{"court": "03"}],
                                fallback_on=["SLOT_OCCUPIED"], commit=True)
    assert result["code"] == code and len(calls) == 1


def test_uncertain_submission_blocks_later_candidates_and_restart(monkeypatch, tmp_path):
    calls = []
    def run(item, *args, **options):
        calls.append(item)
        raise BookingError("SUBMIT_UNKNOWN", "timeout")
    setup_batch(monkeypatch, tmp_path, run)
    with pytest.raises(BookingError) as error:
        app.run_candidates(target(), fallbacks=[{"court": "03"}], commit=True)
    assert error.value.code == "SUBMIT_UNKNOWN" and len(calls) == 1
    with pytest.raises(BookingError) as error:
        app.run_candidates(target(), fallbacks=[{"court": "03"}], commit=True)
    assert error.value.code == "RESULT_UNCONFIRMED" and len(calls) == 1


def test_ambiguous_addorder_reply_never_allows_fallback(tmp_path):
    with pytest.raises(BookingError) as error:
        submit(FakeClient([{"info": "预约名额已满"}]), target(), plan(), FakeSolver(), tmp_path)
    assert error.value.code == "SUBMIT_UNKNOWN"


@pytest.mark.parametrize("values", [[{"date": "2026-10-14"}], [{"venue": "other"}], [None], "03", [{}]])
def test_candidate_scope_cannot_silently_change(values):
    with pytest.raises(BookingError):
        app.fallback_targets(target(), values)
