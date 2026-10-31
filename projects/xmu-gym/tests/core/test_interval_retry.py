"""Offline replay of the observed getInterval denial and submission boundaries."""
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from core.booking import app, client as client_module
from core.booking.client import API, Client
from core.booking.diagnostics import Diagnostics
from core.booking.session import BookingError, EntryTicket
from test_flow_auth import runtime
from test_http_booking import FakeSolver, interval_data, plan, target


DENIAL = {"status": 0, "info": "参数错误", "data": []}
SUCCESS = {"status": 1, "data": interval_data()}


def make_client(replies):
    replies = iter(replies)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        value = next(replies)
        if isinstance(value, Exception):
            raise value
        response = requests.Response()
        response.status_code = value if type(value) is int else 200
        response.headers["Content-Type"] = "application/json"
        response._content = json.dumps(value, ensure_ascii=False).encode("utf-8")
        response.encoding = "utf-8"
        return response

    http = SimpleNamespace(headers={}, cookies=[], post=post)
    return Client(EntryTicket({"uid": "1234"}, Path("fake")), http=http), calls


@pytest.fixture
def sleeps(monkeypatch):
    delays = []
    monkeypatch.setattr(client_module.time, "sleep", delays.append)
    return delays


def test_retries_same_read_only_request_and_preserves_both_exchanges(sleeps):
    client, calls = make_client([DENIAL, SUCCESS])
    phases, exchanges = [], []
    diagnostics = Diagnostics(exchange_sink=exchanges.append)
    token = diagnostics.enter()
    try:
        data = client.intervals(plan()["venue"], progress=phases.append)
    finally:
        diagnostics.leave(token)
    assert data == interval_data()
    assert sleeps == [2] and phases == ["waiting_slot_retry", "querying_slots"]
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0][0] == API + "/stadium/interval/getInterval"
    assert [e["sequence"] for e in exchanges] == [1, 2]
    assert [e["response"]["status"] for e in exchanges] == [0, 1]
    assert exchanges[0]["request"] == exchanges[1]["request"] == plan()["venue"].fields()
    assert diagnostics.snapshot()["http"]["intervals"]["count"] == 2


def test_repeated_denial_stops_after_two_calls(sleeps):
    client, calls = make_client([DENIAL, DENIAL])
    with pytest.raises(BookingError) as error:
        client.intervals(plan()["venue"])
    assert error.value.public() == {
        "ok": False, "code": "API_REJECTED", "message": "参数错误", "phase": "querying_slots",
        "server_status": 0, "details": {"api": "intervals"},
    }
    assert len(calls) == 2 and sleeps == [2]


def test_success_does_not_reset_session_budget_for_later_query(sleeps):
    client, calls = make_client([DENIAL, SUCCESS, DENIAL])
    client.intervals(plan()["venue"])
    with pytest.raises(BookingError):
        client.intervals(plan()["venue"])
    assert len(calls) == 3 and sleeps == [2]


@pytest.mark.parametrize("reply,code", [
    ({"status": -1, "info": "参数错误"}, "SESSION_EXPIRED"),
    ({"status": "0", "info": "参数错误"}, "API_REJECTED"),
    ({"status": False, "info": "参数错误"}, "API_REJECTED"),
    ({"status": 0, "info": "其他参数错误"}, "API_REJECTED"),
    ({"status": 0, "info": "预约名额已满"}, "SLOT_OCCUPIED"),
    ({"status": 0, "info": "尚未开放"}, "NOT_OPEN"),
    ({"status": 1, "data": []}, "SLOT_SCHEMA_CHANGED"),
    ([], "INVALID_RESPONSE"),
    (429, "RATE_LIMITED"), (503, "SERVER_BUSY"),
    (requests.Timeout("offline fixture"), "HTTP_FAILED"),
    (ValueError("invalid JSON fixture"), "HTTP_FAILED"),
])
def test_other_failures_do_not_retry(sleeps, reply, code):
    client, calls = make_client([reply])
    with pytest.raises(BookingError) as error:
        client.intervals(plan()["venue"])
    assert error.value.code == code
    assert len(calls) == 1 and sleeps == []


def test_second_transport_failure_is_not_retried(sleeps):
    client, calls = make_client([DENIAL, requests.ConnectionError("offline fixture")])
    with pytest.raises(BookingError) as error:
        client.intervals(plan()["venue"])
    assert error.value.code == "HTTP_FAILED" and len(calls) == 2 and sleeps == [2]


def test_stop_interrupts_retry_wait_without_another_request(sleeps):
    class StopDuringWait(threading.Event):
        def wait(self, timeout):
            assert timeout == 2
            self.set()
            return True

    client, calls = make_client([DENIAL])
    with pytest.raises(BookingError) as error:
        client.intervals(plan()["venue"], cancelled=StopDuringWait())
    assert error.value.code == "CANCELLED" and len(calls) == 1 and sleeps == []


def test_stop_before_query_sends_nothing(sleeps):
    client, calls = make_client([])
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(BookingError) as error:
        client.intervals(plan()["venue"], cancelled=cancelled)
    assert error.value.code == "CANCELLED" and not calls and not sleeps


def test_stop_after_wait_is_rechecked_before_the_second_request(sleeps):
    client, calls = make_client([DENIAL])
    cancelled = threading.Event()

    def progress(phase):
        if phase == "querying_slots":
            cancelled.set()

    # Simulate a completed wait; the stop arrives immediately before the read.
    cancelled.wait = lambda _: False
    with pytest.raises(BookingError) as error:
        client.intervals(plan()["venue"], cancelled=cancelled, progress=progress)
    assert error.value.code == "CANCELLED" and len(calls) == 1 and sleeps == []


def install_interval_replay(monkeypatch, tmp_path, replies):
    calls = runtime(monkeypatch, tmp_path)
    original = app.Client.require
    replies = iter(replies)

    def require(client, path, *args):
        if path == "/stadium/interval/getInterval":
            calls.append("intervals")
            value = next(replies)
            if isinstance(value, Exception):
                raise value
            return value
        return original(client, path, *args)

    monkeypatch.setattr(app.Client, "require", require)
    monkeypatch.setattr(app.Client, "intervals", Client.intervals)
    return calls


def denied():
    return BookingError("API_REJECTED", "参数错误", phase="querying_slots", server_status=0,
                        details={"api": "intervals"})


@pytest.mark.parametrize("response,state", [
    ({"status": 1, "info": "预约成功"}, "submitted"),
    (DENIAL, "rejected"),
    ({}, "unknown"),
    (BookingError("HTTP_FAILED", "timeout after send"), "unknown"),
])
def test_query_recovery_does_not_replay_booking_or_submit(monkeypatch, tmp_path, response, state):
    calls = install_interval_replay(monkeypatch, tmp_path, [denied(), interval_data()])
    monkeypatch.setattr(client_module, "RECONCILE_DELAYS", (0,))
    phases = []

    def post(client, path, fields, ref):
        calls.append("addOrder")
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(app.Client, "post", post)
    options = dict(commit=True, captcha_first=True, solver=FakeSolver(), idempotency_key="fixed-run",
                   progress=lambda value: phases.append(value["phase"]))
    if state == "unknown":
        with pytest.raises(BookingError) as error:
            app.run_target(target(), **options)
        assert error.value.code == "SUBMIT_UNKNOWN"
    else:
        result = app.run_target(target(), **options)
        assert result["state"] == state
    assert calls.count("login") == calls.count("captcha") == calls.count("addOrder") == 1
    assert calls.count("intervals") == 2 and phases.count("waiting_slot_retry") == 1
    journals = list((tmp_path / "requests").glob("*.json"))
    assert len(journals) == 1
    assert json.loads(journals[0].read_text(encoding="utf-8"))["state"] == state
    # The same frozen run ID must still block a replay after an uncertain send.
    if state == "unknown":
        with pytest.raises(BookingError) as error:
            app.run_target(target(), **options)
        assert error.value.code == "RESULT_UNCONFIRMED" and calls.count("addOrder") == 1


def test_exhausted_query_retry_stops_without_fallback_or_submission(monkeypatch, tmp_path):
    calls = install_interval_replay(monkeypatch, tmp_path, [denied(), denied()])

    def forbidden_fallback():
        pytest.fail("parameter rejection must not restart booking via a fallback")

    result = app.run_candidates(target(), fallbacks=forbidden_fallback, commit=True, captcha_first=True,
                                solver=FakeSolver(), idempotency_key="fixed-run")
    assert result["code"] == "API_REJECTED"
    assert calls.count("intervals") == 2 and "addOrder" not in calls
    journal = json.loads(next((tmp_path / "requests").glob("*.json")).read_text(encoding="utf-8"))
    assert journal["state"] == "not_submitted" and "submitted_target" not in journal


def test_open_date_loop_cannot_renew_query_retry_budget(monkeypatch, tmp_path):
    calls = install_interval_replay(monkeypatch, tmp_path, [
        denied(), {"time_slot_list": []}, denied(),
    ])
    phases = []
    with pytest.raises(BookingError) as error:
        app.run_target(target(), commit=True, captcha_first=True, solver=FakeSolver(), watch_seconds=5,
                       progress=lambda value: phases.append(value["phase"]))
    assert error.value.code == "API_REJECTED"
    assert calls.count("intervals") == 3 and phases.count("waiting_slot_retry") == 1
    assert phases.count("waiting_open") == 1 and "addOrder" not in calls


def test_cancelled_retry_marks_journal_not_submitted(monkeypatch, tmp_path):
    calls = install_interval_replay(monkeypatch, tmp_path, [denied()])

    class StopDuringWait(threading.Event):
        def wait(self, timeout):
            self.set()
            return True

    with pytest.raises(BookingError) as error:
        app.run_target(target(), commit=True, captcha_first=True, solver=FakeSolver(), cancelled=StopDuringWait())
    assert error.value.code == "CANCELLED"
    assert calls.count("intervals") == 1 and "addOrder" not in calls
    journal = json.loads(next((tmp_path / "requests").glob("*.json")).read_text(encoding="utf-8"))
    assert journal["state"] == "not_submitted" and journal["code"] == "CANCELLED"


@pytest.mark.parametrize("endpoint", ["/index/stadium/chooseVerify", "/index/Stadium/getVenueConfig"])
def test_parameter_error_in_precheck_or_config_does_not_retry(monkeypatch, tmp_path, endpoint):
    calls = install_interval_replay(monkeypatch, tmp_path, [interval_data()])
    original = app.Client.require

    def require(client, path, *args):
        if path == endpoint:
            calls.append(path)
            raise denied()
        return original(client, path, *args)

    monkeypatch.setattr(app.Client, "require", require)
    phases = []
    with pytest.raises(BookingError) as error:
        app.run_target(target(), commit=True, captcha_first=True, solver=FakeSolver(),
                       progress=lambda value: phases.append(value["phase"]))
    assert error.value.code == "API_REJECTED"
    assert calls.count(endpoint) == 1 and calls.count("intervals") == 1
    assert "waiting_slot_retry" not in phases and "addOrder" not in calls
