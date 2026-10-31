from pathlib import Path
from datetime import datetime
from types import SimpleNamespace

import pytest
from core.booking import orders
from core.booking.session import BookingError, EntryTicket


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(orders, "current_time", lambda: datetime.fromisoformat("2026-10-26T12:00:00+08:00"))


def order(oid=42, **values):
    return {"order_id": oid, "uid":"1234", "stadium_name":"示例体育馆", "project_name":"乒乓球", "audit_status":"1", **values}


def detail(**values):
    return {"uid":"1234", "audit_status":"1", "details":[{"id":"71", "date":"2026-10-27", "interval_time":"19:30–21:00", "area_name":"02", "status":"1", **values}]}


class Fake:
    def __init__(self, pages=None, details=None, failure=None, after=None):
        self.ticket=EntryTicket({"uid":"1234"},Path("fake"))
        self.pages=pages or {"1":[order()]}
        self.details=details or {"42":detail()}
        self.calls=[];self.failure=failure;self.after=after or detail(status="3")
        self.sent=False;self.allow_cancel=False
    def require(self,path,fields,*args,**kwargs):
        self.calls.append((path,fields))
        if path==orders.LIST:return self.pages.get(fields["p"],[])
        if path==orders.DETAIL:return self.after if self.sent else self.details[fields["order_id"]]
        assert path==orders.CANCEL and self.allow_cancel
        self.sent=True
        if self.failure:raise BookingError(self.failure,"模拟接口错误")
        return []


def test_filter_sort_and_pagination():
    first=[order(i,audit_status="2") for i in range(1,11)]
    fake=Fake({"1":first,"2":[order(),order(43,audit_status="3"),order(44)]},
              {"42":detail(),"44":detail(id="72",interval_time="07:00–08:00")})
    rows=orders.active_rows(fake)
    assert [r["details_id"] for r in rows]==["72","71"]
    assert len([c for c in fake.calls if c[0]==orders.DETAIL])==2


def test_login_first_page_is_reused_once_without_extra_request():
    client=orders.OrdersClient(EntryTicket({"uid":"1234"},Path("fake")))
    client.http.cookies.set("PHPSESSID","synthetic",domain="xdty.xmu.edu.cn",path="/")
    calls=[]
    def post(path,fields,*args,**kwargs):
        calls.append((path,fields))
        return {"status":1,"data":[order()] if path==orders.LIST else detail() if path==orders.DETAIL else []}
    client.post=post
    try:
        assert client.login()["account_checked"]
        assert len(orders.active_rows(client))==1
        assert sum(path==orders.LIST for path,_ in calls)==1
        orders.active_rows(client)
        assert sum(path==orders.LIST for path,_ in calls)==2,"the first page must not be reused across requests"
    finally:client.http.close()


def test_cancel_reads_only_the_target_detail_and_stops_paging_when_found():
    fake=Fake(pages={"1":[order(i) for i in range(1,11)],"2":[order()]},details={"42":detail()})
    assert orders.cancel(fake,"42","71")["state"]=="cancelled"
    assert [fields["p"] for path,fields in fake.calls if path==orders.LIST]==["1","2"]
    assert [fields["order_id"] for path,fields in fake.calls if path==orders.DETAIL]==["42","42"]
    assert sum(path==orders.CANCEL for path,_ in fake.calls)==1


@pytest.mark.parametrize("where",["list","detail","item"])
def test_inactive_not_displayed(where):
    fake=Fake(pages={"1":[order(audit_status="3" if where=="list" else "1")]},
              details={"42":{**detail(status="2" if where=="item" else "1"),"audit_status":"2" if where=="detail" else "1"}})
    assert not orders.active_rows(fake)


@pytest.mark.parametrize("missing",[None,"other"])
def test_wrong_or_missing_owner_rejected(missing):
    with pytest.raises(BookingError,match="账号"):
        orders.active_rows(Fake(pages={"1":[order(uid=missing)]}))


def test_duplicate_pages_not_treated_as_complete():
    batch=[order(i,audit_status="2") for i in range(1,11)]
    with pytest.raises(BookingError) as error:orders.active_rows(Fake({"1":batch,"2":batch}))
    assert error.value.code=="ORDER_LIST_INCOMPLETE"


@pytest.mark.parametrize("values",[{"id":None},{"details_id":"72"}])
def test_missing_or_conflicting_detail_id_disables_cancellation(values):
    row=orders.active_rows(Fake(details={"42":detail(**values)}))[0]
    assert not row["can_cancel"]


def test_exact_cancel_form_and_verified_once():
    fake=Fake()
    assert orders.cancel(fake,"42","71")=={"ok":True,"state":"cancelled"}
    assert [fields for path,fields in fake.calls if path==orders.CANCEL]==[
        {"order_id":"42","school_id":"788","order_num":"","details_id":"71"}]
    assert not fake.allow_cancel


@pytest.mark.parametrize("failure",["HTTP_FAILED","INVALID_RESPONSE","SERVER_BUSY"])
def test_uncertain_send_only_reads_back_never_replays(failure):
    fake=Fake(failure=failure)
    assert orders.cancel(fake,"42","71")["ok"]
    assert sum(path==orders.CANCEL for path,_ in fake.calls)==1


def test_unconfirmed_does_not_claim_success():
    fake=Fake(failure="HTTP_FAILED",after=detail())
    assert orders.cancel(fake,"42","71")["state"]=="cancel_unconfirmed"


def test_rejected_cancel_is_not_retried():
    fake=Fake(failure="API_REJECTED")
    with pytest.raises(BookingError):orders.cancel(fake,"42","71")
    assert sum(path==orders.CANCEL for path,_ in fake.calls)==1


def test_wrong_detail_does_not_send():
    fake=Fake()
    with pytest.raises(BookingError):orders.cancel(fake,"42","99")
    assert not fake.sent


def test_account_change_before_login(monkeypatch):
    monkeypatch.setattr(orders,"discover_entry",lambda:EntryTicket({"uid":"1234"},Path("fake")))
    monkeypatch.setattr(orders,"OrdersClient",lambda _:pytest.fail("must not login with changed account"))
    with pytest.raises(BookingError) as error:
        orders.execute({"action":"cancel_order","order_id":"42","details_id":"71","account_fingerprint":"a"*64})
    assert error.value.code=="ACCOUNT_CHANGED"


def test_endpoint_allowlist():
    client=orders.OrdersClient(EntryTicket({"uid":"1234"},Path("fake")))
    for path in [orders.CANCEL,"/index/Stadium/addOrder","/index/index/captcha"]:
        with pytest.raises(BookingError):client.post(path,{})
    client.http.close()


def test_missing_status_disables_cancel_even_with_valid_id():
    data=detail()
    del data["details"][0]["status"]
    assert not orders.active_rows(Fake(details={"42":data}))[0]["can_cancel"]


@pytest.mark.parametrize("data", [None, "unexpected", [1]])
def test_malformed_list_cannot_be_reported_as_empty(data):
    fake=Fake()
    fake.pages={"1":data}
    with pytest.raises(BookingError) as error:orders.active_rows(fake)
    assert error.value.code=="ORDER_SCHEMA_CHANGED"


@pytest.mark.parametrize("stamp,allowed,visible", [
    ("2026-10-27T18:29:59+08:00", True, True),
    ("2026-10-27T18:30:00+08:00", False, True),
    ("2026-10-27T21:00:00+08:00", False, False),
])
def test_cancel_hour_boundary_and_end_filter(stamp,allowed,visible):
    rows=orders.active_rows(Fake(),now=datetime.fromisoformat(stamp))
    assert bool(rows)==visible
    if rows:assert rows[0]["can_cancel"] is allowed


def test_cancel_rechecks_hour_boundary_after_read(monkeypatch):
    times=iter([datetime.fromisoformat("2026-10-27T18:29:59+08:00"),datetime.fromisoformat("2026-10-27T18:30:00+08:00")])
    monkeypatch.setattr(orders,"current_time",lambda:next(times))
    fake=Fake()
    with pytest.raises(BookingError) as error:orders.cancel(fake,"42","71")
    assert error.value.code=="CANCEL_WINDOW_CLOSED"
    assert not fake.sent


@pytest.fixture
def cache_env(monkeypatch,tmp_path):
    from core.booking import app
    clock=[datetime.fromisoformat("2026-10-26T12:00:00+08:00")]
    uid=["1234"]
    instances=[]
    class CacheFake(Fake):
        def __init__(self,ticket):
            super().__init__()
            self.ticket=ticket
            self.pages={"1":[order(uid=ticket.uid)]}
            self.details={"42":{**detail(),"uid":ticket.uid}}
            self.http=SimpleNamespace(close=lambda:None)
            instances.append(self)
        def login(self):return {"account_checked":True}
    monkeypatch.setattr(app,"data_root",lambda:tmp_path)
    monkeypatch.setattr(orders,"current_time",lambda:clock[0])
    monkeypatch.setattr(orders,"discover_entry",lambda:EntryTicket({"uid":uid[0]},Path("fake")))
    monkeypatch.setattr(orders,"OrdersClient",CacheFake)
    return clock,uid,instances,tmp_path


def test_cache_is_persistent_ttl_scoped_and_manual_bypasses(cache_env):
    clock,uid,instances,root=cache_env
    first=orders.execute({"action":"my_orders"})
    assert first["ok"] and not first["cached"]
    assert orders.execute({"action":"my_orders"})["cached"]
    assert len(instances)==1,"fresh disk snapshot must not create/login a Client"
    assert not orders.execute({"action":"my_orders","force":True})["cached"]
    assert len(instances)==2
    clock[0]=datetime.fromisoformat("2026-10-26T12:30:00+08:00")
    assert not orders.execute({"action":"my_orders"})["cached"]
    uid[0]="9999"
    changed=orders.execute({"action":"my_orders"})
    assert not changed["cached"] and changed["account_fingerprint"]!=first["account_fingerprint"]
    assert len(list((root/"orders-cache").glob("*.json")))==2
    text=next((root/"orders-cache").glob("*.json")).read_text(encoding="utf-8")
    assert '"uid"' not in text and 'Cookie' not in text


def test_end_filter_uses_cache_without_querying(cache_env):
    clock,_,instances,_=cache_env
    clock[0]=datetime.fromisoformat("2026-10-27T20:59:59+08:00")
    assert len(orders.execute({"action":"my_orders"})["orders"])==1
    clock[0]=datetime.fromisoformat("2026-10-27T21:00:00+08:00")
    result=orders.execute({"action":"my_orders"})
    assert result["cached"] and not result["orders"] and len(instances)==1


def test_completed_order_detail_index_skips_known_ended_details(cache_env):
    clock,_,instances,_=cache_env
    orders.execute({"action":"my_orders"})
    clock[0]=datetime.fromisoformat("2026-10-27T21:00:00+08:00")
    result=orders.execute({"action":"my_orders","force":True})
    assert not result["orders"]
    assert not any(path==orders.DETAIL for path,_ in instances[-1].calls)
    assert any(path==orders.LIST for path,_ in instances[-1].calls),"still discover new bookings"


def test_cancel_revalidates_then_patches_only_confirmed_detail(cache_env):
    _,_,instances,_=cache_env
    result=orders.execute({"action":"my_orders"})
    orders.execute({"action":"cancel_order","account_fingerprint":result["account_fingerprint"],"order_id":"42","details_id":"71"})
    assert len(instances)==2
    assert any(path==orders.DETAIL for path,_ in instances[-1].calls)
    assert any(path==orders.CANCEL for path,_ in instances[-1].calls)
    after=orders.execute({"action":"my_orders"})
    assert after["cached"] and after["orders"]==[]
    assert after["fetched_at"]==result["fetched_at"],"cancellation must not renew unrelated snapshot freshness"


def test_corrupt_snapshot_falls_back_to_network(cache_env):
    *_,root=cache_env
    orders.execute({"action":"my_orders"})
    path=next((root/"orders-cache").glob("*.json"))
    path.write_text("broken",encoding="utf-8")
    assert not orders.execute({"action":"my_orders"})["cached"]


def test_unconfirmed_cancel_keeps_snapshot_invalid(cache_env):
    _,_,instances,_=cache_env
    result=orders.execute({"action":"my_orders"})
    original=orders.cancel
    try:
        orders.cancel=lambda *args:{"ok":False,"state":"cancel_unconfirmed"}
        orders.execute({"action":"cancel_order","account_fingerprint":result["account_fingerprint"],"order_id":"42","details_id":"71"})
    finally:orders.cancel=original
    assert not orders.execute({"action":"my_orders"})["cached"]


def test_confirmed_cancel_keeps_other_rows_and_original_freshness(cache_env):
    from core.booking import orders_cache
    _,_,_,root=cache_env
    result=orders.execute({"action":"my_orders"})
    path,data=orders_cache.load(root,result["account_fingerprint"])
    other={**data["snapshot"]["orders"][0],"order_id":"99","details_id":"98"}
    data["snapshot"]["orders"].append(other);orders_cache.save(path,data)
    orders.execute({"action":"cancel_order","account_fingerprint":result["account_fingerprint"],"order_id":"42","details_id":"71"})
    after=orders.execute({"action":"my_orders"})
    assert after["cached"] and [row["order_id"] for row in after["orders"]]==["99"]
    assert after["fetched_at"]==result["fetched_at"]


def test_order_cache_morning_window_uses_beijing_and_original_age(cache_env):
    clock, _, instances, _ = cache_env
    clock[0] = datetime.fromisoformat("2026-10-26T06:59:00+08:00")
    orders.execute({"action":"my_orders"})
    clock[0] = datetime.fromisoformat("2026-10-25T23:03:59+00:00")
    assert orders.execute({"action":"my_orders"})["cached"]
    clock[0] = datetime.fromisoformat("2026-10-25T23:04:00+00:00")
    assert not orders.execute({"action":"my_orders"})["cached"]
    assert len(instances) == 2
    clock[0] = datetime.fromisoformat("2026-10-26T07:15:00+08:00")
    assert orders.execute({"action":"my_orders"})["cached"]


def test_empty_orders_are_cached_for_normal_window(cache_env):
    clock, _, instances, _ = cache_env
    clock[0] = datetime.fromisoformat("2026-10-27T22:00:00+08:00")
    assert not orders.execute({"action":"my_orders"})["orders"]
    clock[0] = datetime.fromisoformat("2026-10-27T22:29:59+08:00")
    assert orders.execute({"action":"my_orders"})["cached"]
    assert len(instances) == 1
