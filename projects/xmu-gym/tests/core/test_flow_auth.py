from pathlib import Path
from types import SimpleNamespace

import pytest

from core.booking import app
from core.booking.client import Client
from core.booking.session import BookingError, EntryTicket
from test_http_booking import target, plan, interval_data, FakeSolver


def ticket(token="old", uid="1234"):
    return EntryTicket({"uid": uid, "token": token}, Path("fake"))


def runtime(monkeypatch, tmp_path, data=None, precheck=None, config=None):
    calls = []
    class RuntimeClient(Client):
        def __init__(self, entry):
            self.ticket = entry
            self._interval_retry_used = False
            self.http = SimpleNamespace(close=lambda: calls.append("close"))
        def login(self):
            calls.append("login")
            return {"authenticated": True}
        def resolve_venue(self, _):
            return plan()["venue"]
        def intervals(self, _, **kwargs):
            calls.append("intervals")
            return data or interval_data()
        def require(self, path, *args):
            calls.append(path)
            if path.endswith("chooseVerify") and precheck:
                raise BookingError(precheck, "server denied", phase="server_precheck")
            return {"info": config or {}}
        def find_existing(self, _):
            calls.append("orders")
            return None
        def captcha(self, _):
            calls.append("captcha")
            return b"1"
        def post(self, *args):
            calls.append("addOrder")
            return {"status": 0, "info": "预约名额已满"}
    monkeypatch.setattr(app, "Client", RuntimeClient)
    monkeypatch.setattr(app, "discover_entry", lambda _: ticket())
    monkeypatch.setattr(app, "data_root", lambda: tmp_path)
    monkeypatch.setattr(app.time, "sleep", lambda _: None)
    return calls


@pytest.mark.parametrize("kind,code", [("locked", "SLOT_LOCKED"), ("unavailable", "SLOT_UNAVAILABLE"), ("full", "SLOT_OCCUPIED")])
def test_unbookable_cells_never_request_captcha_or_precheck(monkeypatch, tmp_path, kind, code):
    data = interval_data()
    cell = data["time_slot_list"][0]["slots"][1]
    if kind == "full":
        cell["selected"] = 1
    else:
        cell["status"] = kind
    calls = runtime(monkeypatch, tmp_path, data)
    with pytest.raises(BookingError) as error:
        app.run_target(target(), commit=True, solver=FakeSolver())
    result = error.value.public()
    assert result["code"] == code and result["phase"] == "checking_slot"
    assert result["captcha_requested"] is False
    assert "captcha" not in calls and "addOrder" not in calls
    assert not any(call.endswith("chooseVerify") for call in calls)


@pytest.mark.parametrize("code", ["SLOT_OCCUPIED", "BOOKING_CLOSED", "ACCOUNT_LIMIT", "SESSION_EXPIRED"])
def test_server_precheck_denial_never_fetches_config_or_captcha(monkeypatch, tmp_path, code):
    calls = runtime(monkeypatch, tmp_path, precheck=code)
    with pytest.raises(BookingError) as error:
        app.run_target(target(), commit=True, solver=FakeSolver())
    assert error.value.phase == "server_precheck" and not error.value.captcha_requested
    assert "captcha" not in calls and "addOrder" not in calls
    assert not any(call.endswith("getVenueConfig") for call in calls)


def test_config_denial_blocks_captcha(monkeypatch, tmp_path):
    calls = runtime(monkeypatch, tmp_path, config={"isCanAppoint": 0})
    with pytest.raises(BookingError) as error:
        app.run_target(target(), commit=True, solver=FakeSolver())
    assert error.value.phase == "checking_config" and "captcha" not in calls


def test_captcha_only_after_precheck_and_config(monkeypatch, tmp_path):
    calls = runtime(monkeypatch, tmp_path)
    result = app.run_target(target(), captcha_check=True, solver=FakeSolver())
    assert result["ok"] and result["captcha_requested"]
    assert calls.index("/index/stadium/chooseVerify") < calls.index("/index/Stadium/getVenueConfig") < calls.index("captcha")
    assert "addOrder" not in calls


def test_default_recovery_skips_locked_target_then_validates_next_before_captcha(monkeypatch, tmp_path):
    data = interval_data()
    cell = data["time_slot_list"][0]["slots"][1]
    data["time_slot_list"][0]["slots"].append({**cell, "column_id": "51", "area_name": "03"})
    cell["status"] = "locked"
    calls = runtime(monkeypatch, tmp_path, data)
    result = app.run_candidates(target(), fallbacks=[{"court": "03"}], captcha_check=True, solver=FakeSolver())
    assert result["ok"] and result["selected_candidate"] == 1
    assert result["attempts"][0]["code"] == "SLOT_LOCKED"
    assert result["attempts"][0]["captcha_requested"] is False
    assert calls.count("captcha") == 1 and "addOrder" not in calls


def test_unchanged_expired_ticket_is_not_retried(monkeypatch):
    calls = []
    client = SimpleNamespace(ticket=ticket(), login=lambda: expired(calls))
    monkeypatch.setattr(app, "discover_entry", lambda _: ticket())
    with pytest.raises(BookingError) as error:
        app.authenticate(client)
    assert len(calls) == 1
    assert error.value.public()["state"] == "needs_login"


def expired(calls):
    calls.append("login")
    raise BookingError("SESSION_EXPIRED", "登录信息失效，请返回并重新进入")


def test_changed_same_account_entry_can_refresh_once(monkeypatch):
    calls = []
    client = SimpleNamespace(ticket=ticket(), login=lambda: expired(calls), http=SimpleNamespace(close=lambda: None))
    replacement = SimpleNamespace(login=lambda: {"authenticated": True}, http=SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(app, "discover_entry", lambda _: ticket("new"))
    monkeypatch.setattr(app, "Client", lambda _: replacement)
    new_client, result = app.authenticate(client)
    assert new_client is replacement and result["entry_refreshed"] and len(calls) == 1


def test_entry_refresh_cannot_switch_accounts(monkeypatch):
    client = SimpleNamespace(ticket=ticket(), login=lambda: expired([]))
    monkeypatch.setattr(app, "discover_entry", lambda _: ticket("new", "5678"))
    with pytest.raises(BookingError) as error:
        app.authenticate(client)
    assert error.value.code == "ACCOUNT_CHANGED"


def test_missing_and_native_entries_have_distinct_readiness_states(monkeypatch):
    def missing(_):
        raise BookingError("NO_ENTRY", "missing")
    monkeypatch.setattr(app, "discover_entry", missing)
    assert app.check_auth()["state"] == "needs_login"
    assert BookingError("NATIVE_TOKEN_REQUIRED", "unsupported").public()["state"] == "unsupported_auth"


def test_unknown_submission_cannot_be_enabled_as_fallback():
    with pytest.raises(BookingError):
        app.fallback_policy(["SUBMIT_UNKNOWN"])
