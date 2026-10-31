import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from core.booking.client import Client
from core.booking.diagnostics import Diagnostics, phase_changed
from core.booking.session import BookingError, EntryTicket


@pytest.mark.parametrize("failure,category", [
    (requests.Timeout("secret-signed-url"), "timeout"),
    (requests.ConnectionError("secret-password"), "connection"),
    (ValueError("secret-response"), "invalid_json"),
])
def test_metrics_classify_wrapped_errors_without_secrets(failure, category):
    def fail(*args, **kwargs):
        raise failure
    client = Client(EntryTicket({"uid":"secret-uid"}, Path("fake")), http=SimpleNamespace(headers={}, post=fail))
    metrics = Diagnostics()
    token = metrics.enter()
    try:
        for _ in range(2):
            with pytest.raises(BookingError):
                client.post("/index/Index/checkLogin", {"token":"secret-token"}, "secret-referer")
        value = metrics.snapshot()
    finally:
        metrics.leave(token)
    assert value["http"]["login"]["errors"] == {category:2}
    assert value["http"]["login"]["count"] == 2
    assert "secret" not in json.dumps(value)


def test_metrics_capture_success_and_aggregate_repeated_phase(monkeypatch):
    ticks = iter([0, 1, 2, 3, 4, 5, 6, 7])
    monkeypatch.setattr("core.booking.diagnostics.time.monotonic", lambda: next(ticks))
    metrics = Diagnostics()
    token = metrics.enter()
    try:
        phase_changed("authenticating")  # t1
        phase_changed("checking_orders")  # t2
        phase_changed("authenticating")  # t3
        value = metrics.snapshot()  # t4,5
    finally:
        metrics.leave(token)
    assert value["phase_ms"] == {"authenticating":2000, "checking_orders":1000}


def test_unrecognized_api_path_is_never_serialized():
    from core.booking.diagnostics import request_timing
    metrics = Diagnostics()
    token = metrics.enter()
    try:
        with request_timing("https://example.invalid/?token=secret"):
            pass
        value = metrics.snapshot()
    finally:
        metrics.leave(token)
    assert value["http"]["other"]["count"] == 1
    assert "secret" not in json.dumps(value)


def exchange_client(response=None, failure=None):
    def post(*args, **kwargs):
        if failure:
            raise failure
        return response
    return Client(EntryTicket({"uid":"private-user", "token":"private-ticket"}, Path("fake")),
                  http=SimpleNamespace(headers={}, cookies=[], post=post))


def reply(body, status=200, text=""):
    return SimpleNamespace(status_code=status, headers={"Content-Type":"application/json"},
                           is_redirect=False, text=text, json=lambda: body, raise_for_status=lambda: None)


def capture_call(client, fields=None):
    events = []
    metrics = Diagnostics(exchange_sink=events.append)
    token = metrics.enter()
    try:
        client.require("/stadium/interval/getInterval", fields or {})
    except BookingError:
        pass
    finally:
        metrics.leave(token)
    return events


def test_business_rejection_keeps_complete_nested_reply_and_actual_parameters():
    upstream = {"status":0, "info":"参数错误", "data":{"reason":17, "extra":["venue unavailable"]}, "trace_id":"upstream-42"}
    events = capture_call(exchange_client(reply(upstream)),
                          {"stadium_id":"16","venue_id":"14","category_id":"8","user_range":"[67]"})
    assert len(events) == 1
    assert events[0]["response"] == upstream
    assert events[0]["request"]["user_range"] == "[67]"
    assert events[0]["http_status"] == 200
    assert events[0]["response_truncated"] is False


def test_payment_package_and_prepay_tokens_are_redacted():
    from core.booking.diagnostics import redact
    value = redact({'data': {'package': 'mock-package', 'prepay_id':'mock-prepay', 'paySign':'mock-sign'}})
    assert all(v == '[redacted]' for v in value['data'].values())


def test_successful_directory_is_preserved_and_nested_credentials_are_redacted():
    body = {"status":1,"data":{"stadium_name":"翔安校区健身房", "venue_id":14,
                              "uid":"private-user", "phone":"13800138000",
                              "student_num":123456789, "card_id":123456789, "nonce":"private-nonce",
                              "nested":{"access_token":"private-token"},
                              "encoded":'{"PHPSESSID":"private-session"}',
                              "info":"private-ticket https://example.invalid/?token=private-url"}}
    events = capture_call(exchange_client(reply(body)), {"captcha":"private-code", "uid":"private-user"})
    encoded = json.dumps(events)
    assert "private-" not in encoded
    assert "13800138000" not in encoded
    assert "123456789" not in encoded
    assert events[0]["response"]["data"]["stadium_name"] == "翔安校区健身房"
    assert events[0]["response"]["data"]["venue_id"] == 14


def test_http_failure_keeps_status_and_redacted_non_json_body():
    events = capture_call(exchange_client(reply(None, status=503, text="upstream unavailable private-ticket")))
    assert events[0]["http_status"] == 503
    assert events[0]["error"] == "SERVER_BUSY"
    assert events[0]["response"] == "upstream unavailable [redacted]"


def test_transport_failure_keeps_request_but_never_exception_text():
    events = capture_call(exchange_client(failure=requests.Timeout("private-url")), {"venue_id":"14"})
    assert events[0]["request"] == {"venue_id":"14"}
    assert events[0]["error"] == "timeout"
    assert "http_status" not in events[0]
    assert "private" not in json.dumps(events)


def test_large_replies_explicitly_report_truncation():
    from core.booking.diagnostics import MAX_BODY_CHARS
    events = capture_call(exchange_client(reply({"status":0,"info":"x" * (MAX_BODY_CHARS+100)})))
    assert events[0]["response_truncated"] is True
    assert len(events[0]["response"]) == MAX_BODY_CHARS
    assert events[0]["response_chars"] > MAX_BODY_CHARS


def test_diagnostic_sink_failure_does_not_change_booking_response():
    def fail(_):
        raise OSError("disk full")
    metrics = Diagnostics(exchange_sink=fail)
    token = metrics.enter()
    try:
        assert exchange_client(reply({"status":1,"data":{"ok":True}})).require("/stadium/interval/getInterval", {}) == {"ok":True}
        assert metrics.snapshot()["exchange_write_errors"] == 1
    finally:
        metrics.leave(token)


def test_public_cookie_flags_and_login_type_do_not_corrupt_venue_ids():
    client = exchange_client(reply({"status":1,"data":{"venue_id":"14","user_range":"[67]"}}))
    client.ticket.fields.update(login_type="4", type="0", course_id="/")
    client.http.cookies = [SimpleNamespace(name="login_type",value="4"),
                           SimpleNamespace(name="COOKIE_IS_APP_UI",value="0"),
                           SimpleNamespace(name="PHPSESSID",value="private-session")]
    events = capture_call(client, {"venue_id":"14", "user_range":"[67]"})
    assert events[0]["request"] == {"venue_id":"14", "user_range":"[67]"}
    assert events[0]["response"]["data"]["venue_id"] == "14"
    assert events[0]["content_type"] == "application/json"


def test_name_variants_are_private_but_venue_names_and_boolean_controls_survive():
    from core.booking.diagnostics import redact, record_exchange
    private = {"user_name":"private-person", "real_name":"private-person", "name":"private-person",
               "nested":[{"user_name":"private-person"}], "verify_phone":1, "phone":"13800138000"}
    clean = redact(private)
    assert "private-person" not in json.dumps(clean)
    assert clean["verify_phone"] == 1 and clean["phone"] == "[redacted]"
    assert redact({"verify_phone":"13800138000"})["verify_phone"] == "[redacted]"
    events=[]
    diagnostics = Diagnostics(exchange_sink=events.append)
    token=diagnostics.enter()
    try:
        record_exchange("/index/Stadium/getStadiumDetails", {"id":"16"}, response=reply({}),
                        result={"status":1,"data":{"name":"翔安校区健身房", "user_name":"private-person",
                                "venue":[{"name":"健身房", "category_id":8}]}})
    finally:
        diagnostics.leave(token)
    assert events[0]["response"]["data"]["name"] == "翔安校区健身房"
    assert events[0]["response"]["data"]["venue"][0]["name"] == "健身房"
    assert "private-person" not in json.dumps(events)
