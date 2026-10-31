"""Current user's active reservations and one-shot, verified cancellation."""
import hashlib
import re
from datetime import datetime
from urllib.parse import urlencode

from .availability import CHINA
from .client import Client, PAGES, records
from .session import BookingError, discover_entry

LIST = "/index/stadium/mySubscribe"
DETAIL = "/index/stadium/subDetails"
CANCEL = "/index/stadium/cancelOrder"


class OrdersClient(Client):
    allow_cancel = False

    def login(self):
        self._login_first_page = None
        self._capturing_login = True
        try:
            return super().login()
        finally:
            self._capturing_login = False

    def require(self, path, fields, *args, **kwargs):
        data = super().require(path, fields, *args, **kwargs)
        if getattr(self, "_capturing_login", False) and path == LIST and fields.get("p") == "1":
            self._login_first_page = data
        return data

    def post(self, path, *args, **kwargs):
        allowed = {"/index/Index/checkLogin", LIST, DETAIL}
        if self.allow_cancel:
            allowed.add(CANCEL)
        if path not in allowed:
            raise BookingError("ORDER_ACTION_FORBIDDEN", "订单操作不允许调用预约接口")
        return super().post(path, *args, **kwargs)


def identifier(value):
    text = str(value) if value is not None else ""
    return text if re.fullmatch(r"[1-9][0-9]{0,19}", text) else ""


def own(client, row, required=False):
    if (required and row.get("uid") is None) or (row.get("uid") is not None and str(row["uid"]) != client.ticket.uid):
        raise BookingError("ACCOUNT_MISMATCH", "预约账号不一致，请刷新后重试")


def read_detail(client, order_id):
    data = client.require(DETAIL, {"order_id": order_id, "uid": client.ticket.uid, "is_admin": "false"},
                          PAGES + "order.html?" + urlencode({"id": order_id}), timeout=(3, 6))
    if not isinstance(data, dict):
        raise BookingError("ORDER_SCHEMA_CHANGED", "预约详情结构异常")
    own(client, data)
    return data


def detail_id(item):
    first, second = identifier(item.get("details_id")), identifier(item.get("id"))
    if first and second and first != second:
        return ""
    return first or second


def collection(value):
    if not isinstance(value, (list, dict)):
        raise BookingError("ORDER_SCHEMA_CHANGED", "订单列表结构异常，请到小程序核对")
    rows = list(value.values()) if isinstance(value, dict) else value
    if any(not isinstance(row, dict) for row in rows):
        raise BookingError("ORDER_SCHEMA_CHANGED", "订单列表结构异常，请到小程序核对")
    return rows


def current_time():
    return datetime.now(CHINA)


def row_time(row, field):
    return datetime.fromisoformat(f"{row['date']}T{row[field]}:00+08:00")


def visible_rows(rows, now):
    return [{**row, "can_cancel": row["can_cancel"] and (row_time(row, "start") - now).total_seconds() > 3600}
            for row in rows if row_time(row, "end") > now]


def last_detail_end(detail):
    ends = []
    for item in collection(detail.get("details")):
        times = re.findall(r"\d{2}:\d{2}", str(item.get("interval_time", "")))
        try:
            if len(times) != 2:
                return None
            ends.append(row_time({"date": item["date"], "end": times[1]}, "end"))
        except (KeyError, ValueError, TypeError):
            return None
    return max(ends).isoformat() if ends else None


def active_rows(client, now=None, ended=None, only_order=None):
    now = now or current_time()
    ended = ended if ended is not None else {}
    seen, result = set(), []
    for page in range(1, 31):
        first = getattr(client, "_login_first_page", None) if page == 1 else None
        if first is not None:
            client._login_first_page = None
        batch = collection(first if first is not None else client.require(LIST, {"p": str(page)}, PAGES + "mine.html", timeout=(3, 6)))
        new = 0
        found = False
        for order in batch:
            own(client, order, required=True)
            oid = identifier(order.get("order_id"))
            if not oid:
                raise BookingError("ORDER_SCHEMA_CHANGED", "预约订单标识异常")
            if oid in seen:
                continue
            seen.add(oid)
            new += 1
            if only_order is not None and oid != only_order:
                continue
            found = True
            if str(order.get("audit_status")) != "1":
                continue
            try:
                if oid in ended and datetime.fromisoformat(ended[oid]) <= now:
                    continue
            except (ValueError, TypeError):
                ended.pop(oid, None)
            detail = read_detail(client, oid)
            if str(detail.get("audit_status", "1")) != "1":
                continue
            detail_rows = []
            for item in collection(detail.get("details")):
                if str(item.get("status", "1")) != "1":
                    continue
                times = re.findall(r"\d{2}:\d{2}", str(item.get("interval_time", "")))
                date = str(item.get("date", ""))
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or len(times) != 2 or times[1] <= times[0]:
                        raise ValueError()
                    datetime.strptime(date + " " + times[0], "%Y-%m-%d %H:%M")
                    datetime.strptime(date + " " + times[1], "%Y-%m-%d %H:%M")
                except ValueError:
                    raise BookingError("ORDER_SCHEMA_CHANGED", "预约时段不完整，请到小程序核对")
                did = detail_id(item)
                detail_rows.append({"order_id": oid, "details_id": did, "venue": str(order.get("stadium_name") or "场馆"),
                               "sport": str(order.get("project_name") or ""), "court": str(item.get("area_name") or ""),
                               "date": date, "start": times[0], "end": times[1], "can_cancel": bool(did) and str(item.get("status")) == "1"})
            cutoff = last_detail_end(detail)
            if cutoff:
                ended[oid] = cutoff
            else:
                ended.pop(oid, None)
            result.extend(visible_rows(detail_rows, now))
        if len(batch) < 10 or (only_order is not None and found):
            return sorted(result, key=lambda row: (row["date"], row["start"], row["end"], row["order_id"], row["details_id"]))
        if not new:
            break
    raise BookingError("ORDER_LIST_INCOMPLETE", "订单分页未完整读取，请稍后刷新")


def cancel(client, oid, did):
    matching = [row for row in active_rows(client, only_order=oid) if row["details_id"] == did]
    if len(matching) != 1:
        raise BookingError("ORDER_NOT_CANCELLABLE", "该场次已变化或不允许取消，请刷新核对")
    # Re-evaluate after the fresh read, never authorize a mutation from a snapshot.
    if (row_time(matching[0], "start") - current_time()).total_seconds() <= 3600:
        raise BookingError("CANCEL_WINDOW_CLOSED", "开始前一小时内不能取消预约")
    if not matching[0]["can_cancel"]:
        raise BookingError("ORDER_NOT_CANCELLABLE", "该场次不允许取消，请刷新核对")
    client.allow_cancel = True
    uncertain = False
    try:
        client.require(CANCEL, {"order_id": oid, "school_id": "788", "order_num": "", "details_id": did},
                       PAGES + "order.html?" + urlencode({"id": oid}), timeout=(3, 8))
    except BookingError as error:
        if error.code not in {"HTTP_FAILED", "INVALID_RESPONSE", "SERVER_BUSY", "UNEXPECTED_REDIRECT"}:
            raise
        uncertain = True
    finally:
        client.allow_cancel = False
    try:
        detail = read_detail(client, oid)
        items = [item for item in records(detail.get("details")) if detail_id(item) == did]
        confirmed = str(detail.get("audit_status")) == "3" or (len(items) == 1 and str(items[0].get("status")) == "3")
        if confirmed:
            return {"ok": True, "state": "cancelled"}
    except BookingError:
        pass
    return {"ok": False, "state": "cancel_unconfirmed", "code": "CANCEL_UNCONFIRMED",
            "message": "取消结果尚未确认，请刷新或到小程序核对，勿重复取消", "response_uncertain": uncertain}


def execute(payload):
    cancellation = payload.get("action") == "cancel_order"
    oid, did = identifier(payload.get("order_id")), identifier(payload.get("details_id"))
    if cancellation and (not oid or not did or not re.fullmatch(r"[a-f0-9]{64}", str(payload.get("account_fingerprint", "")))):
        raise BookingError("INVALID_ORDER", "取消预约标识无效")
    ticket = discover_entry()
    account = hashlib.sha256(ticket.uid.encode()).hexdigest()
    if cancellation and payload["account_fingerprint"] != account:
        raise BookingError("ACCOUNT_CHANGED", "登录账号已变化，请刷新后操作")
    from .app import data_root
    from . import orders_cache
    path, cache = orders_cache.load(data_root(), account)
    now = current_time()
    snapshot = orders_cache.fresh(cache, now)
    if not cancellation and payload.get("force") is not True and snapshot is not None:
        try:
            rows = visible_rows(snapshot["orders"], now)
            return {"ok": True, "cached": True, "account_fingerprint": account,
                    "orders": rows, "fetched_at": snapshot["fetched_at"]}
        except (ValueError, KeyError, TypeError):
            pass
    if cancellation:
        cache.pop("snapshot", None)
        orders_cache.save(path, cache)
    client = OrdersClient(ticket)
    try:
        if not client.login().get("account_checked"):
            raise BookingError("HTTP_AUTH_UNVERIFIED", "尚未完成订单账号核验")
        if cancellation:
            result = cancel(client, oid, did)
            if result.get("state") == "cancelled" and snapshot is not None:
                # Patch only the verified cancellation; other rows keep their original age.
                cache["snapshot"] = {**snapshot, "orders": [row for row in snapshot["orders"]
                    if (row["order_id"], row["details_id"]) != (oid, did)]}
                orders_cache.save(path, cache)
            return result
        rows = visible_rows(active_rows(client, now=now, ended=cache["ended"]), current_time())
        stamp = current_time().isoformat()
        cache["snapshot"] = {"orders": rows, "fetched_at": stamp}
        saved = orders_cache.save(path, cache)
        return {"ok": True, "cached": False, "cache_saved": saved, "account_fingerprint": account,
                "orders": rows, "fetched_at": stamp}
    finally:
        client.http.close()
