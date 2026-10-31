"""Read-only dated availability snapshots; never used to authorize submission."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from .client import Client, Target, cell_capacity
from .session import BookingError, discover_entry
from .catalog import write_json

CHINA = timezone(timedelta(hours=8))


class AvailabilityClient(Client):
    def post(self, path, *args, **kwargs):
        if path not in {"/index/Index/checkLogin", "/index/stadium/mySubscribe",
                        "/index/Stadium/getStadiumList", "/index/Stadium/getStadiumDetails",
                        "/stadium/interval/getInterval"}:
            raise BookingError("READ_ONLY", "场次查询禁止预约操作")
        return super().post(path, *args, **kwargs)


def snapshot(data, date, fetched_at):
    rows = []
    for block in data.get("time_slot_list", []):
        if block.get("date") != date:
            continue
        import re
        times = re.findall(r"\d{2}:\d{2}", str(block.get("time_range", "")))
        start = str(block.get("start_time") or (times[0] if times else ""))[:5]
        end = str(block.get("end_time") or (times[1] if len(times)>1 else ""))[:5]
        if not start or not end:
            raise BookingError("SLOT_SCHEMA_CHANGED", "场次返回的时间不完整")
        cells=[]
        for cell in block.get("slots", []):
            capacity=cell_capacity(data,cell)
            used=int(cell.get("selected") or 0)
            cells.append({"court":str(cell.get("area_name") or cell.get("column_id") or "未命名场地"),
                          "admin_locked":cell.get("status")=="locked",
                          "status":str(cell.get("status") or "unknown"),
                          "reason":str(cell.get("lock_reason") or ""),
                          "remaining":max(0,capacity-used),"capacity":capacity})
        rows.append({"start":start,"end":end,"cells":cells})
    return {"date":date,"fetched_at":fetched_at,"slots":rows,
            "can_choose":str(data.get("is_can_choose",1))!="0",
            "notice":str(data.get("is_can_choose_tips") or "")}


def query(root, sport, venue, date, force=False, now=None, *, cancelled=None):
    now=now or datetime.now(CHINA)
    today=now.astimezone(CHINA).date()
    valid={today.isoformat(),(today+timedelta(days=1)).isoformat()}
    if date not in valid or not isinstance(sport,str) or not isinstance(venue,str) or not sport.strip() or not venue.strip():
        raise BookingError("INVALID_AVAILABILITY_TARGET", "只能查询今天或明天的场馆时段")
    ticket=discover_entry()
    account=hashlib.sha256(ticket.uid.encode()).hexdigest()
    key=hashlib.sha256(json.dumps([sport,venue],ensure_ascii=False).encode()).hexdigest()
    path=root/"availability-cache"/account/(key+".json")
    try:
        cache=json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cache,dict) or cache.get("schema_version")!=1 or cache.get("sport")!=sport or cache.get("venue")!=venue:
            cache={}
    except (OSError,ValueError,TypeError):
        cache={}
    cached_days=cache.get("days",{}) if isinstance(cache.get("days"),dict) else {}
    days={k:v for k,v in cached_days.items() if k in valid and isinstance(v,dict)
          and v.get("date")==k and isinstance(v.get("slots"),list) and isinstance(v.get("fetched_at"),str)}
    unpublished={}
    for day, stamp in (cache.get("unpublished",{}) if isinstance(cache.get("unpublished"),dict) else {}).items():
        try:
            age=(now-datetime.fromisoformat(stamp)).total_seconds()
            if day in valid and 0 <= age < 30: unpublished[day]=stamp
        except (ValueError,TypeError):
            continue

    def result(cached, cache_saved=True):
        common={"ok":True,"cached":cached,"cache_saved":cache_saved,"sport":sport,"venue":venue,
                "account_fingerprint":account}
        snapshots={day:{**common,"state":"availability_ready",**value} for day,value in days.items()}
        snapshots.update({day:{**common,"state":"date_not_published","date":day,"fetched_at":stamp,"slots":[]}
                          for day,stamp in unpublished.items()})
        return {**snapshots[date],"snapshots":snapshots}

    if not force and (date in days or date in unpublished):
        return result(True)
    client=AvailabilityClient(ticket)
    try:
        auth=client.login()
        if not auth.get("account_checked"):
            raise BookingError("HTTP_AUTH_UNVERIFIED", "场次查询尚未完成账号核验，请先检查登录")
        resolved=client.resolve_venue(Target(sport,venue,date,"07:00"))
        data=client.intervals(resolved,cancelled=cancelled)
        published={str(block.get("date")) for block in data.get("time_slot_list",[])}
        published.update(str(day.get("date_int")) for day in data.get("date_list",[]))
        fetched_at=now.astimezone(CHINA).isoformat()
        fresh={day:snapshot(data,day,fetched_at) for day in valid if day in published}
        days.update(fresh)
        # A successful refresh may show a previously published day was withdrawn.
        for day in valid-published:
            days.pop(day,None)
            unpublished[day]=fetched_at
        for day in published: unpublished.pop(day,None)
        try:
            write_json(path,{"schema_version":1,"sport":sport,"venue":venue,"days":days,"unpublished":unpublished})
            cache_saved=True
        except OSError:
            cache_saved=False
        return result(False,cache_saved)
    finally:
        client.http.close()
