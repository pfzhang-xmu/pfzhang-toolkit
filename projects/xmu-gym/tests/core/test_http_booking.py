from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from core.booking.app import BookingService, handler_for, parse_fire_at, wait_until
from core.booking.client import (
    CaptchaSolver, Client, Target, Venue, add_order_fields, captcha_query, pick_slot, submit,
)
from core.booking.session import BookingError, EntryTicket, latest_values, parse_entry


def target(**overrides):
    return Target(**{"sport": "乒乓球", "venue": "翔安校区乒乓球室", "date": "2026-10-13",
                     "time": "19:30", "court": "02", **overrides})


def interval_data():
    # Matches the important live protocol contradictions: available/full and is_lock=1.
    return {"personal_num": 1, "is_can_choose": 1, "time_slot_list": [{
        "date": "2026-10-13", "time_range": "19:30-21:00", "week": "2", "week_name": "周二",
        "slots": [
            {"column_id": "49", "interval_id": "3110", "area_name": "01", "selected": 1,
             "max_count": 1, "price": 0, "status": "available", "is_lock": 1},
            {"column_id": "50", "interval_id": "3110", "area_name": "02", "selected": 0,
             "max_count": 1, "price": 0, "status": "available", "is_lock": 1},
        ],
    }]}


def plan():
    return {"venue": Venue("15", "13", "2", "翔安校区乒乓球室", "乒乓球", "[49,50]"),
            "selected": pick_slot(interval_data(), target()), "referer": "https://xdty.xmu.edu.cn/confirm.html"}


def test_live_slot_semantics_and_absolute_date():
    selected = pick_slot(interval_data(), target(court=None))
    assert selected["area_name"] == "02"
    assert selected["area_id"] == "50"
    assert selected["interval_id"] == "3110"
    for changed in (target(date="2026-10-14"), target(court="01"), target(time="09:30"), target(end="20:00")):
        with pytest.raises(BookingError, match="没有"):
            pick_slot(interval_data(), changed)


def test_locked_status_and_paid_slots_are_not_booked():
    data = interval_data()
    data["time_slot_list"][0]["slots"][1]["status"] = "locked"
    with pytest.raises(BookingError) as error:
        pick_slot(data, target())
    assert error.value.code == "SLOT_LOCKED"
    data["time_slot_list"][0]["slots"][1].update(status="available", price="2.50")
    with pytest.raises(BookingError) as error:
        pick_slot(data, target())
    assert error.value.code == "PAYMENT_REQUIRED"


def test_swimming_preserves_exact_price_in_selection_and_h5_form():
    from urllib.parse import parse_qs, urlparse
    from core.booking.client import confirm_url
    data = interval_data()
    data['time_slot_list'][0]['slots'][1]['price'] = '2.50'
    swimming = target(sport='游泳馆', venue='翔安校区游泳馆')
    selected = pick_slot(data, swimming)
    venue = Venue('1', '2', '3', swimming.venue, swimming.sport, '[]')
    assert selected['price'] == '2.50'
    assert add_order_fields(venue, selected, 'fake')['details[0][price]'] == '2.50'
    assert parse_qs(urlparse(confirm_url(venue, selected)).query)['price'] == ['2.50']


def test_zero_capacity_does_not_fall_back_to_a_nonzero_personal_limit():
    data = interval_data()
    data["time_slot_list"][0]["slots"][1]["max_count"] = 0
    with pytest.raises(BookingError) as error:
        pick_slot(data, target())
    assert error.value.code == "SLOT_UNAVAILABLE"


def test_php_form_details_have_the_actual_column_and_no_login_ticket():
    fields = add_order_fields(plan()["venue"], plan()["selected"], "a1b2")
    assert fields["details[0][area_id]"] == "50"
    assert fields["details[0][date]"] == "2026-10-13"
    assert "details" not in fields
    assert not ({"uid", "token", "sign", "PHPSESSID"} & fields.keys())


def test_captcha_signs_uid_but_does_not_send_it_as_a_query_field():
    query = captcha_query("1234", "0.25", 1700000000)
    assert set(query) == {"r", "t", "sign"}
    assert query["sign"] == hashlib.md5(b"r0.25t1700000000uid1234Lptiyu_123!%$").hexdigest()


def test_deleted_newest_login_ticket_is_not_resurrected():
    record = lambda seq, live, value: SimpleNamespace(
        script_key="hrefstr", leveldb_seq_number=seq, is_live=live, value=value)
    assert latest_values([record(5, True, "old"), record(7, False, None)]) == {}
    assert latest_values([record(5, True, "old"), record(7, False, None), record(8, True, "new")]) == {"hrefstr": "new"}


def test_login_import_is_origin_and_school_scoped():
    entry = "uid=1234&school_id=788&token=fake&sign=fake&timestamp=1700000000&nonce=fake"
    assert parse_entry(entry, Path("fake")).uid == "1234"
    for value, new in ((entry.replace("788", "999"), None),
                       ("https://example.com/?" + entry, None), (entry, "1")):
        with pytest.raises(BookingError):
            parse_entry(value, Path("fake"), new)


def test_invalid_targets_and_naive_fire_time_are_rejected():
    for kwargs in ({"time": None}, {"date": "2026-02-30"}, {"time": "25:30"}, {"sport": []}):
        with pytest.raises(BookingError):
            target(**kwargs)
    with pytest.raises(BookingError):
        parse_fire_at("2026-10-14T07:00:00")
    assert parse_fire_at("2026-10-14T07:00:00+08:00").utcoffset() == timedelta(hours=8)


def test_stopping_service_cancels_future_booking_wait():
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(BookingError) as error:
        wait_until(datetime.now(timezone.utc) + timedelta(hours=1), stopped)
    assert error.value.code == "CANCELLED"


def test_ocr_keeps_letters_and_rejects_ambiguous_length():
    solver = CaptchaSolver()
    solver._engine = SimpleNamespace(classification=lambda _: "A1b2")
    assert solver.solve(b"fake") == "a1b2"
    solver._engine.classification = lambda _: "abcde"
    with pytest.raises(BookingError):
        solver.solve(b"fake")


class FakeSolver:
    def warm(self):
        pass

    def solve(self, image):
        return {b"1": "a1b2", b"2": "c3d4"}[image]


class FakeClient:
    ticket = EntryTicket({"uid": "1234"}, Path("fake"))

    def __init__(self, replies):
        self.replies = iter(replies)
        self.posts = []
        self.images = 0
        self.accepted = False

    def find_existing(self, _):
        return {"verified": True, "order_id": 42} if self.accepted else None

    def captcha(self, _):
        self.images += 1
        return str(self.images).encode()

    def post(self, path, fields, _):
        self.posts.append(fields.copy())
        response = next(self.replies)
        if isinstance(response, Exception):
            raise response
        self.accepted = response.get("status") == 1
        return response


@pytest.mark.parametrize('captcha_first', [False, True])
def test_payment_package_never_claims_success_or_replays(tmp_path, captcha_first):
    class PaymentClient(FakeClient):
        def find_existing(self, _):
            return None
    client = PaymentClient([{'status': 1, 'data': {'package': 'mock-payment-token'}}])
    p = plan()
    p['selected']['price'] = '2.50'
    kwargs = {'prepare_after_captcha': lambda: p} if captcha_first else {}
    result = submit(client, target(), None if captcha_first else p, FakeSolver(), tmp_path, **kwargs)
    assert result['ok'] is False and result['state'] == 'unknown' and result['code'] == 'PAYMENT_REQUIRED'
    assert not result.get('verified') and 'mock-payment-token' not in json.dumps(result)
    assert client.posts[0]['details[0][price]'] == '2.50'
    with pytest.raises(BookingError) as exc:
        submit(client, target(), p, FakeSolver(), tmp_path)
    assert exc.value.code == 'RESULT_UNCONFIRMED' and len(client.posts) == 1


def test_paid_accepted_order_is_unconfirmed_until_active_detail_found(tmp_path):
    class NoVerifiedOrder(FakeClient):
        def find_existing(self, _):
            return None
    client = NoVerifiedOrder([{'status': 1, 'data': {}}])
    p = plan(); p['selected']['price'] = '2'
    result = submit(client, target(), p, FakeSolver(), tmp_path)
    assert result['ok'] is False and result['code'] == 'RESULT_UNCONFIRMED'
    assert json.loads(next(tmp_path.glob('*.json')).read_text())['state'] == 'unknown'


def test_paid_acceptance_is_journaled_unknown_before_verification(tmp_path):
    class CrashDuringRead(FakeClient):
        def find_existing(self, _):
            if self.accepted:
                raise RuntimeError('simulated crash')
            return None
    p = plan(); p['selected']['price'] = '2'
    with pytest.raises(RuntimeError):
        submit(CrashDuringRead([{'status':1}]), target(), p, FakeSolver(), tmp_path)
    assert json.loads(next(tmp_path.glob('*.json')).read_text())['state'] == 'unknown'


@pytest.mark.parametrize('price', [None, '', '-2', 'NaN', 'inf', True, '2e2', '1,00'])
def test_invalid_price_fails_closed(price):
    data = interval_data(); data['time_slot_list'][0]['slots'][1]['price'] = price
    with pytest.raises(BookingError) as error:
        pick_slot(data, target(sport='游泳馆', venue='翔安校区游泳馆'))
    assert error.value.code == 'INVALID_PRICE'


def test_submit_timeout_is_not_replayed_on_restart(tmp_path):
    client = FakeClient([BookingError("HTTP_FAILED", "timeout after send")])
    with pytest.raises(BookingError) as error:
        submit(client, target(), plan(), FakeSolver(), tmp_path)
    assert error.value.code == "SUBMIT_UNKNOWN"
    assert len(client.posts) == 1
    assert json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))["state"] == "unknown"
    with pytest.raises(BookingError) as error:
        submit(client, target(), plan(), FakeSolver(), tmp_path)
    assert error.value.code == "RESULT_UNCONFIRMED"
    assert len(client.posts) == 1
    assert client.images == 1


def test_only_explicit_captcha_rejection_allows_a_new_code_retry(tmp_path):
    client = FakeClient([{"status": 0, "info": "验证码错误"}, {"status": 1, "info": "预约成功"}])
    result = submit(client, target(), plan(), FakeSolver(), tmp_path)
    assert result["verified"] and result["order_id"] == 42
    assert [fields["captcha"] for fields in client.posts] == ["a1b2", "c3d4"]


def test_business_denial_does_not_keep_submitting(tmp_path):
    client = FakeClient([{"status": 0, "info": "每天最多预约一次"}])
    result = submit(client, target(), plan(), FakeSolver(), tmp_path)
    assert result["state"] == "rejected" and not result["ok"]
    assert len(client.posts) == 1


def test_accepted_order_is_preserved_when_read_only_verification_fails(tmp_path):
    class ClientWithFailedRead(FakeClient):
        def find_existing(self, _):
            if self.accepted:
                raise BookingError("HTTP_FAILED", "query failed")
            return None
    client = ClientWithFailedRead([{"status": 1, "info": "预约成功"}])
    result = submit(client, target(), plan(), FakeSolver(), tmp_path)
    assert result["ok"] and result["state"] == "submitted" and not result["verified"]
    assert len(client.posts) == 1
    cached = json.loads(next(tmp_path.glob("*.json")).read_text(encoding="utf-8"))
    assert cached["state"] == "submitted"


def test_incomplete_submission_record_blocks_a_second_send(tmp_path):
    identity = "1234|" + json.dumps(target().__dict__, sort_keys=True)
    (tmp_path / (hashlib.sha256(identity.encode()).hexdigest() + ".json")).write_text("", encoding="utf-8")
    client = FakeClient([])
    with pytest.raises(BookingError) as error:
        submit(client, target(), plan(), FakeSolver(), tmp_path)
    assert error.value.code == "RESULT_UNCONFIRMED" and not client.posts


def test_cancel_during_preparation_prevents_the_final_order(tmp_path):
    cancelled = threading.Event()
    class CancellingClient(FakeClient):
        def captcha(self, ref):
            cancelled.set()
            return super().captcha(ref)
    client = CancellingClient([])
    with pytest.raises(BookingError) as error:
        submit(client, target(), plan(), FakeSolver(), tmp_path, cancelled=cancelled)
    assert error.value.code == "CANCELLED" and not client.posts


def test_same_request_key_cannot_silently_refer_to_another_date(tmp_path):
    first = FakeClient([{"status": 1, "info": "预约成功"}])
    submit(first, target(), plan(), FakeSolver(), tmp_path, "same-key")
    other = FakeClient([])
    with pytest.raises(BookingError) as error:
        submit(other, target(date="2026-10-14"), plan(), FakeSolver(), tmp_path, "same-key")
    assert error.value.code == "IDEMPOTENCY_CONFLICT" and not other.posts


def test_prepare_stops_at_server_precheck_and_never_creates_order():
    client = Client(EntryTicket({"uid": "1234"}, Path("fake")))
    calls = []
    def require(path, *_):
        calls.append(path)
        raise BookingError("API_REJECTED", "不可预约")
    client.intervals = lambda _, **kwargs: interval_data()
    client.require = require
    with pytest.raises(BookingError):
        client.prepare(target(), plan()["venue"])
    assert calls == ["/index/stadium/chooseVerify"]
    client.http.close()


def test_loopback_endpoint_cannot_book_by_get_or_without_key(monkeypatch):
    service = BookingService(key="unit-test-only", warm=False)
    submitted = []
    service.enqueue = lambda body: submitted.append(body) or {"ok": True, "job_id": "mock"}
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(service))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    key = {"X-Xmu-Local-Key": "unit-test-only"}
    try:
        assert requests.post(base + "/api/book", json={}, timeout=2).status_code == 403
        monkeypatch.setattr("core.booking.app.check_auth", lambda _: {"ok": False, "state": "needs_login"})
        assert requests.post(base + "/api/auth/check", json={}, timeout=2).status_code == 403
        auth = requests.post(base + "/api/auth/check", headers=key, json={}, timeout=2)
        assert auth.status_code == 200 and auth.json()["state"] == "needs_login"
        assert requests.get(base + "/api/book", headers=key, timeout=2).status_code == 405
        assert submitted == []
        result = requests.post(base + "/api/book", headers=key, json={"fake": True}, timeout=2)
        assert result.status_code == 202 and submitted == [{"fake": True}]
    finally:
        server.shutdown()
        server.server_close()
        service.cancelled.set()
        service.pool.shutdown(wait=True, cancel_futures=True)
