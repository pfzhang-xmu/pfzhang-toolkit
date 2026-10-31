import json
import threading
from dataclasses import asdict
from pathlib import Path

import pytest

from core.booking.client import Client, Target, reconcile_submission, submit
from core.booking.session import BookingError, EntryTicket
from test_http_booking import FakeClient, FakeSolver, plan, target


def confirmed():
    return {"verified": True, "order_id": 42, "date": "2026-10-13", "time": "19:30–21:00", "court": "02"}


class DelayedOrder(FakeClient):
    def __init__(self, response, reads):
        super().__init__([response])
        self.reads = iter(reads)
        self.lookups = []

    def find_existing(self, goal):
        self.lookups.append(goal)
        if not self.posts:
            return None
        result = next(self.reads, None)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.parametrize("response", [BookingError("HTTP_FAILED", "timeout after send"), {}, {"status": 2}])
def test_unknown_submit_is_confirmed_by_delayed_order_without_resending(tmp_path, response):
    client = DelayedOrder(response, [None, BookingError("HTTP_FAILED", "still offline"), confirmed()])
    phases = []
    result = submit(client, target(court=None, end=None), plan(), FakeSolver(), tmp_path, progress=phases.append)
    assert result["ok"] and result["verified"] and result["reconciled"]
    assert len(client.posts) == client.images == 1
    assert result["reconciliation"]["checks_attempted"] == 3
    assert all(goal.court == "02" and goal.end == "21:00" for goal in client.lookups[1:])
    journal = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert journal["state"] == "submitted" and journal["verified"]
    assert journal["submitted_target"]["court"] == "02"
    assert phases[-1] == "order_reconciled"


@pytest.mark.parametrize("reads", [[None] * 5, [BookingError("HTTP_FAILED", "offline")] * 5])
def test_absent_or_unreadable_orders_remain_unknown_and_block_replay(tmp_path, reads):
    client = DelayedOrder({}, reads)
    with pytest.raises(BookingError) as error:
        submit(client, target(), plan(), FakeSolver(), tmp_path)
    assert error.value.code == "SUBMIT_UNKNOWN" and error.value.phase == "order_unconfirmed"
    journal = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert journal["state"] == "unknown" and journal["reconciliation"]["checks_attempted"] == 5
    with pytest.raises(BookingError) as error:
        submit(client, target(), plan(), FakeSolver(), tmp_path)
    assert error.value.code == "RESULT_UNCONFIRMED"
    assert len(client.posts) == client.images == 1


def test_user_stop_interrupts_wait_without_claiming_not_submitted(tmp_path):
    event = threading.Event()
    class CancelAfterSend(DelayedOrder):
        def post(self, *args):
            result = super().post(*args)
            event.set()
            return result
    client = CancelAfterSend({}, [])
    with pytest.raises(BookingError) as error:
        submit(client, target(), plan(), FakeSolver(), tmp_path, cancelled=event)
    assert error.value.code == "SUBMIT_UNKNOWN"
    journal = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert journal["state"] == "unknown" and journal["reconciliation"]["stopped_by_user"]
    assert len(client.posts) == 1 and len(client.lookups) == 1


@pytest.mark.parametrize("code", ["SESSION_EXPIRED", "ACCOUNT_MISMATCH", "RATE_LIMITED"])
def test_terminal_lookup_errors_stop_checks_but_never_resend(tmp_path, code):
    client = DelayedOrder({}, [BookingError(code, "read denied")])
    with pytest.raises(BookingError) as error:
        submit(client, target(), plan(), FakeSolver(), tmp_path)
    assert error.value.details["reconciliation"]["last_error"] == code
    assert error.value.details["reconciliation"]["checks_attempted"] == 1
    assert len(client.posts) == 1


def test_read_only_backoff_schedule_and_stop_are_bounded(monkeypatch):
    monkeypatch.setattr("core.booking.client.RECONCILE_DELAYS", (1, 2, 4, 8, 15))
    waits = []
    class StopAfterThirdWait:
        def wait(self, delay):
            waits.append(delay)
            return len(waits) == 3
    client = type("Reader", (), {"find_existing": lambda self, goal: None})()
    result, summary = reconcile_submission(client, target(), StopAfterThirdWait())
    assert result is None and waits == [1, 2, 4]
    assert summary["checks_attempted"] == 2 and summary["stopped_by_user"]


def test_reconciliation_reports_round_and_next_wait_without_changing_send_semantics(monkeypatch):
    monkeypatch.setattr("core.booking.client.RECONCILE_DELAYS", (1, 2))
    class Waiter:
        def wait(self, delay): return False
    events = []
    reader = type("Reader", (), {"find_existing":lambda self,goal:None})()
    reconcile_submission(reader, target(), Waiter(), progress_detail=lambda phase,**detail:events.append({"phase":phase,**detail}))
    waiting = [event for event in events if event["phase"] == "waiting_order_sync"]
    assert waiting == [{"phase":"waiting_order_sync","check_attempt":1,"check_total":2,"retry_after_seconds":1},
                       {"phase":"waiting_order_sync","check_attempt":2,"check_total":2,"retry_after_seconds":2}]


def order(order_id=42, **values):
    return {"order_id": order_id, "uid": "1234", "stadium_name": "翔安校区乒乓球室", "project_name": "乒乓球", "audit_status": "1", **values}


def detail(**values):
    return {"uid": "1234", "details": [{"date": "2026-10-13", "interval_time": "19:30–21:00", "area_name": "02", **values}]}


class OrderReader(Client):
    def __init__(self, pages, details):
        self.ticket = EntryTicket({"uid": "1234"}, Path("fake"))
        self.pages, self.details, self.calls = pages, details, []

    def require(self, path, fields, referer=None, **options):
        self.calls.append((path, fields, options))
        if path.endswith("mySubscribe"):
            return self.pages.get(fields["p"], [])
        assert path.endswith("subDetails"), "lookup may only list and read orders"
        return self.details[fields["order_id"]]


@pytest.mark.parametrize("changes", [{"date": "2026-10-14"}, {"interval_time": "19:30–20:30"}, {"area_name": "03"}])
def test_detail_must_match_exact_date_entire_interval_and_selected_court(changes):
    client = OrderReader({"1": [order()]}, {"42": detail(**changes)})
    assert client.find_existing(target(end="21:00")) is None


def test_pagination_finds_expected_order_without_mistaking_cancelled_or_other_venues():
    first = [order(i, stadium_name="其他场馆") for i in range(10)]
    client = OrderReader({"1": first, "2": [order(41, audit_status="3"), order()]}, {"42": detail()})
    assert client.find_existing(target(end="21:00"))["order_id"] == 42
    assert [fields["p"] for path, fields, _ in client.calls if path.endswith("mySubscribe")] == ["1", "2"]
    assert all("timeout" in options for _, _, options in client.calls)


def test_duplicate_pages_are_bounded():
    page = [order(i, stadium_name="其他场馆") for i in range(10)]
    client = OrderReader({"1": page, "2": page}, {})
    assert client.find_existing(target()) is None
    assert len(client.calls) == 2


def test_other_account_order_is_never_accepted():
    client = OrderReader({"1": [order(uid="different")]}, {"42": detail()})
    with pytest.raises(BookingError, match="原提交账号") as error:
        client.find_existing(target())
    assert error.value.code == "ACCOUNT_MISMATCH" and len(client.calls) == 1


@pytest.mark.parametrize("status", ["1", "2", "4"])
def test_confirmed_active_or_historical_bookings_are_positive_evidence(status):
    client = OrderReader({"1": [order(audit_status=status)]}, {"42": detail(status=status)})
    assert client.find_existing(target(end="21:00"))["verified"]


@pytest.mark.parametrize('status', ['2', '3', '4', None])
def test_swimming_requires_explicit_active_detail(status):
    swimming = target(sport='游泳馆', venue='翔安校区游泳馆', end='21:00')
    listing = order(stadium_name=swimming.venue, project_name=swimming.sport)
    body = detail(status=status); body['audit_status'] = '1'
    client = OrderReader({'1':[listing]}, {'42':body})
    assert client.find_existing(swimming) is None


@pytest.mark.parametrize("where", ["list", "detail", "row"])
def test_cancelled_orders_and_cancelled_detail_rows_are_never_accepted(where):
    listing=order(audit_status="3" if where=="list" else "1")
    body=detail(status="3" if where=="row" else "1")
    body["audit_status"]="3" if where=="detail" else "1"
    client=OrderReader({"1":[listing]},{"42":body})
    assert client.find_existing(target()) is None


def test_lookup_deadline_is_not_treated_as_absence(monkeypatch):
    client = OrderReader({"1": [order()]}, {"42": detail()})
    monkeypatch.setattr("core.booking.client.ORDER_LOOKUP_SECONDS", 0)
    with pytest.raises(BookingError) as error:
        client.find_existing(target())
    assert error.value.code == "ORDER_CHECK_TIMEOUT" and not client.calls
