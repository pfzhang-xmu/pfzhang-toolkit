from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from core.booking import availability as av
from core.booking.session import BookingError, EntryTicket

NOW=datetime(2026,10,23,10,tzinfo=av.CHINA)

def payload():
    return {"personal_num":95,"is_can_choose":1,"date_list":[{"date_int":"2026-10-23"},{"date_int":"2026-10-24"}],
            "time_slot_list":[{"date":day,"start_time":"16:30","end_time":"18:00","slots":[
                {"column_id":"67","area_name":"健身房","max_count":95,"selected":95,"is_lock":1,"status":"available"},
                {"column_id":"68","area_name":"场地2","max_count":1,"selected":0,"status":"locked","lock_reason":"课程使用"},
                {"column_id":"69","area_name":"场地3","max_count":1,"selected":0,"is_lock":1,"status":"available"}
            ]} for day in ("2026-10-23","2026-10-24")]}

@pytest.fixture
def runtime(monkeypatch):
    calls=[]
    identity={"uid":"test-account"}
    monkeypatch.setattr(av,"discover_entry",lambda:EntryTicket(identity.copy(),Path("fake")))
    class Fake:
        def __init__(self,ticket): self.http=SimpleNamespace(close=lambda:calls.append("close"))
        def login(self): calls.append("login"); return {"account_checked":True}
        def resolve_venue(self,target): calls.append("venue"); return "venue"
        def intervals(self,venue,**kwargs): calls.append("intervals"); return payload()
    monkeypatch.setattr(av,"AvailabilityClient",Fake)
    return calls,identity

def test_full_and_is_lock_are_not_admin_lock():
    result=av.snapshot(payload(),"2026-10-23",NOW.isoformat())
    cells=result["slots"][0]["cells"]
    assert cells[0]["remaining"]==0 and not cells[0]["admin_locked"]
    assert cells[1]["admin_locked"] and cells[1]["reason"]=="课程使用"
    assert cells[2]["remaining"]==1 and not cells[2]["admin_locked"]


def test_unavailable_status_keeps_full_and_empty_counts_separate():
    data=payload()
    data["personal_num"]=1
    for block in data["time_slot_list"]:
        for cell in block["slots"]:
            if cell["status"]!="locked":
                cell["status"]="unavailable"
                cell["max_count"]=1
                cell["selected"]=1 if cell["selected"] else 0
    result=av.snapshot(data,"2026-10-23",NOW.isoformat())
    full,locked,empty=result["slots"][0]["cells"]
    assert full["status"]==empty["status"]=="unavailable"
    assert full["remaining"]==0 and full["capacity"]==1 and not full["admin_locked"]
    assert empty["remaining"]==1 and not empty["admin_locked"]
    assert locked["admin_locked"]

def test_fetch_caches_both_dates_and_tomorrow_is_reused_after_midnight(tmp_path,runtime):
    calls,_=runtime
    first=av.query(tmp_path,"健身房","场馆","2026-10-23",now=NOW)
    assert first["ok"] and not first["cached"]
    assert set(first["snapshots"])=={"2026-10-23","2026-10-24"}
    assert all(value["account_fingerprint"]==first["account_fingerprint"] for value in first["snapshots"].values())
    count=len(calls)
    tomorrow=av.query(tmp_path,"健身房","场馆","2026-10-24",now=NOW)
    crossed=av.query(tmp_path,"健身房","场馆","2026-10-24",now=NOW+timedelta(days=1))
    assert tomorrow["cached"] and crossed["cached"] and len(calls)==count
    av.query(tmp_path,"健身房","场馆","2026-10-24",force=True,now=NOW)
    assert calls.count("intervals")==2

def test_account_and_venue_cache_are_isolated(tmp_path,runtime):
    calls,identity=runtime
    av.query(tmp_path,"健身房","场馆","2026-10-23",now=NOW)
    identity["uid"]="other-account"
    assert not av.query(tmp_path,"健身房","场馆","2026-10-23",now=NOW)["cached"]
    assert not av.query(tmp_path,"健身房","其他场馆","2026-10-23",now=NOW)["cached"]
    assert calls.count("intervals")==3

def test_out_of_range_never_reads_credentials(tmp_path,monkeypatch):
    monkeypatch.setattr(av,"discover_entry",lambda:pytest.fail("invalid date read credentials"))
    with pytest.raises(BookingError) as error: av.query(tmp_path,"运动","场馆","2026-10-25",now=NOW)
    assert error.value.code=="INVALID_AVAILABILITY_TARGET"

def test_missing_date_has_short_negative_cache_and_force_bypasses(tmp_path,runtime,monkeypatch):
    original=payload
    def today_only():
        data=original(); data["date_list"]=[{"date_int":"2026-10-23"}]
        data["time_slot_list"]=data["time_slot_list"][:1]
        return data
    monkeypatch.setattr(__import__(__name__,fromlist=["payload"]),"payload",today_only)
    for _ in range(2):
        assert av.query(tmp_path,"运动","场馆","2026-10-24",now=NOW)["state"]=="date_not_published"
    assert runtime[0].count("intervals")==1
    av.query(tmp_path,"运动","场馆","2026-10-24",force=True,now=NOW)
    assert runtime[0].count("intervals")==2
    av.query(tmp_path,"运动","场馆","2026-10-24",now=NOW+timedelta(seconds=31))
    assert runtime[0].count("intervals")==3

@pytest.mark.parametrize("path",["/index/Stadium/addOrder","/index/stadium/chooseVerify","/index/index/captcha"])
def test_readonly_client_rejects_mutations_before_network(path):
    client=av.AvailabilityClient(EntryTicket({"uid":"fake"},Path("fake")))
    try:
        with pytest.raises(BookingError) as error: client.post(path,{})
        assert error.value.code=="READ_ONLY"
    finally: client.http.close()

@pytest.mark.parametrize("corruption",[[], None, {"schema_version":1,"sport":"运动","venue":"场馆","days":[]}])
def test_corrupted_cache_is_replaced_with_live_data(tmp_path,runtime,corruption):
    av.query(tmp_path,"运动","场馆","2026-10-23",now=NOW)
    path=next((tmp_path/"availability-cache").rglob("*.json"))
    path.write_text(json.dumps(corruption),encoding="utf-8")
    assert not av.query(tmp_path,"运动","场馆","2026-10-23",now=NOW)["cached"]
