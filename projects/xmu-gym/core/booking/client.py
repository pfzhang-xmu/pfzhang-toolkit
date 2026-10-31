"""Replicate the current H5 form protocol, with one-shot submission semantics."""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

import requests

from .session import BookingError, EntryTicket, ORIGIN
from .diagnostics import request_timing, api_label, record_exchange

API = ORIGIN + "/bdlp_h5_fitness_test/public/index.php"
PAGES = ORIGIN + "/bdlp_h5_fitness_test/view/stadium/"
ADD_ORDER_PATH = "/index/Stadium/addOrder"
RECONCILE_DELAYS = (1, 2, 4, 8, 15)
INTERVAL_RETRY_SECONDS = 2
ORDER_LOOKUP_SECONDS = 8.0
MAX_ORDER_PAGES = 3
# 2/4 are redeemed/unredeemed historical bookings, both evidence that a matching
# booking existed. 3 is cancelled and must never confirm the requested booking.
CONFIRMED_ORDER_STATES = {"1", "2", "4"}
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/144.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781 "
    "NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF "
    "miniProgram/wx81a2b2fa90759cb7"
)


@dataclass(frozen=True)
class Target:
    sport: str
    venue: str
    date: str
    time: str
    court: str | None = None
    end: str | None = None
    remark: str = ""
    court_offset: int = 0

    def __post_init__(self):
        if type(self.court_offset) is not int or not 0 <= self.court_offset <= 99:
            raise BookingError("INVALID_TARGET", "场地偏移必须为 0–99 的整数")
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.sport, self.venue, self.date, self.time)):
            raise BookingError("INVALID_TARGET", "运动、场馆、日期、时间必须是非空文本")
        if not isinstance(self.remark, str) or (self.court is not None and not isinstance(self.court, str)):
            raise BookingError("INVALID_TARGET", "场地和申请说明必须是文本")
        try:
            if date.fromisoformat(self.date).isoformat() != self.date:
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise BookingError("INVALID_TARGET", "预约日期须为 YYYY-MM-DD") from exc
        for value in (self.time, self.end):
            if value is not None and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                raise BookingError("INVALID_TARGET", "时段须使用 HH:MM")
        if not self.sport or not self.venue or len(self.remark) > 200:
            raise BookingError("INVALID_TARGET", "运动、场馆或申请说明不合法")


@dataclass(frozen=True)
class Venue:
    stadium_id: str
    venue_id: str
    category_id: str
    name: str
    sport: str
    user_range: str

    def fields(self) -> dict:
        return {key: getattr(self, key) for key in ("stadium_id", "venue_id", "category_id", "user_range")}

    def choose_url(self) -> str:
        return PAGES + "choose.html?" + urlencode({
            "id": "0", **self.fields(), "name": self.name, "project_name": self.sport,
        })


def records(value) -> list[dict]:
    if isinstance(value, dict):
        value = list(value.values())
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def same_name(left, right) -> bool:
    return unicodedata.normalize("NFKC", str(left)) == unicodedata.normalize("NFKC", str(right))


def rejection_code(info: str, status=None) -> str:
    # Only the full-quota phrase has been verified with a live addOrder rejection.
    # Other reasons require explicit server wording; do not infer deadlines from the clock.
    if "登录" in info or status == -1:
        return "SESSION_EXPIRED"
    if "每个账号每天最多只能预约" in info or "每天最多预约" in info:
        return "ACCOUNT_LIMIT"
    if "预约名额已满" in info:
        return "SLOT_OCCUPIED"
    if any(word in info for word in ("已截止", "超过预约截止", "已过预约时间")):
        return "BOOKING_CLOSED"
    if any(word in info for word in ("尚未开放", "未到预约时间", "不在预约时间")):
        return "NOT_OPEN"
    if any(word in info for word in ("场地已锁定", "场次已锁定")):
        return "SLOT_LOCKED"
    if "验证码" in info:
        return "CAPTCHA_REJECTED"
    return "API_REJECTED"


def cell_capacity(data: dict, cell: dict) -> int:
    capacity = int(cell["max_count"] if cell.get("max_count") is not None else data.get("personal_num") or 0)
    if data.get("personal_num") is not None:
        capacity = min(capacity, int(data["personal_num"]))
    return capacity


def cell_reason(data: dict, cell: dict) -> str:
    if str(data.get("is_can_choose", 1)) == "0":
        code = rejection_code(str(data.get("is_can_choose_tips") or ""))
        return code if code != "API_REJECTED" else "NOT_OPEN"
    if cell.get("status") == "locked":
        return "SLOT_LOCKED"
    reason = rejection_code(str(cell.get("lock_reason") or ""))
    if reason != "API_REJECTED":
        return reason
    if cell.get("status") != "available" or cell_capacity(data, cell) <= 0:
        return "SLOT_UNAVAILABLE"
    if int(cell.get("selected") or 0) >= cell_capacity(data, cell):
        return "SLOT_OCCUPIED"
    return "AVAILABLE"


def pick_slot(data: dict, target: Target) -> dict:
    if str(data.get("is_can_choose", 1)) == "0":
        message = str(data.get("is_can_choose_tips") or "尚未开放预约")
        code = rejection_code(message)
        raise BookingError(code if code != "API_REJECTED" else "NOT_OPEN", message)
    failures = []
    if not any(str(block.get("date")) == target.date for block in data.get("time_slot_list") or []):
        raise BookingError("DATE_NOT_OPEN", "实时列表没有目标日期；该日期尚未发布或不在当前预约范围")
    for block in data.get("time_slot_list") or []:
        if str(block.get("date")) != target.date:
            continue
        time_range = str(block.get("time_range", ""))
        times = re.findall(r"\d{2}:\d{2}", time_range)
        start = str(block.get("start_time") or (times[0] if times else ""))[:5]
        end = str(block.get("end_time") or (times[1] if len(times) > 1 else ""))[:5]
        if start != target.time or (target.end and target.end != end):
            continue
        eligible = [cell for cell in block.get("slots") or [] if cell_reason(data, cell) == "AVAILABLE"
                    and cell.get("interval_id") and cell.get("column_id")]
        offset_cell = eligible[min(target.court_offset, len(eligible) - 1)] if eligible and not target.court else None
        for cell in block.get("slots") or []:
            area = str(cell.get("area_name") or cell.get("column_id", ""))
            if target.court and target.court != area:
                continue
            # 'available' is returned even for some full cells. Check both signals.
            # The live H5 has is_lock=1 on bookable cells too. Match its actual
            # getClass/choose logic, and let chooseVerify enforce server eligibility.
            reason = cell_reason(data, cell)
            if reason != "AVAILABLE":
                failures.append((reason, str(cell.get("lock_reason") or "")))
                continue
            if not cell.get("interval_id") or not cell.get("column_id"):
                continue
            if offset_cell is not None and cell is not offset_cell:
                continue
            try:
                raw_price = cell.get("price")
                if isinstance(raw_price, bool) or not isinstance(raw_price, (str, int, float)):
                    raise InvalidOperation
                if not re.fullmatch(r"\d+(?:\.\d+)?", str(raw_price)):
                    raise InvalidOperation
                price = Decimal(str(raw_price))
            except InvalidOperation as exc:
                raise BookingError("INVALID_PRICE", "服务端价格格式不合法") from exc
            if not price.is_finite() or price < 0:
                raise BookingError("INVALID_PRICE", "服务端价格格式不合法")
            if price != 0 and (target.sport, target.venue) != ("游泳馆", "翔安校区游泳馆"):
                raise BookingError("PAYMENT_REQUIRED", "该场次需要付费，不能按免费预约提交")
            day = date.fromisoformat(target.date)
            return {
                "date": target.date,
                "week": str(block.get("week") or day.isoweekday()),
                "week_msg": str(block.get("week_name") or "周" + "一二三四五六日"[day.weekday()]),
                "area_name": area,
                "interval_time": time_range,
                "interval_id": str(cell["interval_id"]),
                "area_id": str(cell["column_id"]),
                "price": str(raw_price),
            }
    if failures and len({code for code, _ in failures}) == 1:
        code = failures[0][0]
        labels = {"SLOT_OCCUPIED": "名额已满", "SLOT_LOCKED": "场地锁定", "SLOT_UNAVAILABLE": "场次不可预约",
                  "BOOKING_CLOSED": "预约已截止", "NOT_OPEN": "尚未开放"}
        raise BookingError(code, failures[0][1] or "目标没有可预约名额：" + labels.get(code, code))
    counts = {code: sum(item[0] == code for item in failures) for code, _ in failures}
    raise BookingError("NO_SLOT", "目标日期、时段、场地没有未满且可预约的场次",
                       details={"matching_cells": len(failures), "reason_counts": counts})


def confirm_url(venue: Venue, selected: dict) -> str:
    return PAGES + "confirm.html?" + urlencode({
        "stadium_id": venue.stadium_id, "venue_id": venue.venue_id,
        "category_id": venue.category_id, "project_name": venue.sport,
        "name": venue.name, "list": json.dumps([selected], ensure_ascii=False, separators=(",", ":")),
        "is_academy": "1", "academy_name": "", "mark": "", "price": selected["price"], "p_count": "0", "ids": "",
    })


def add_order_fields(venue: Venue, selected: dict, captcha: str, remark: str = "") -> dict:
    fields = {
        "stadium_id": venue.stadium_id, "venue_id": venue.venue_id,
        "stadium_name": venue.name, "project_name": venue.sport,
        "is_academy": "1", "academy_name": "", "mark": "", "uids": "",
        "captcha": captcha, "category_id": venue.category_id, "is_vip": "0", "pay_type": "1",
    }
    fields.update({f"details[0][{key}]": str(value) for key, value in selected.items()})
    if remark:
        fields["remark"] = remark
    return fields


def captcha_query(uid: str, r: str, timestamp: int) -> dict:
    source = {"r": r, "t": str(timestamp), "uid": uid}
    signature = "".join(key + source[key] for key in sorted(source)) + "Lptiyu_123!%$"
    # uid is bound by the session and signature; the browser does not send it in this URL.
    return {"r": r, "t": str(timestamp), "sign": hashlib.md5(signature.encode()).hexdigest()}


class CaptchaSolver:
    def __init__(self):
        self._engine = None
        self._lock = threading.Lock()
        self._inference_ready = False

    def warm(self):
        with self._lock:
            if self._engine is None:
                try:
                    from .captcha_ocr import CaptchaClassifier
                    self._engine = CaptchaClassifier()
                except ImportError as exc:
                    raise BookingError("MISSING_OCR", "未安装验证码识别组件") from exc

    def preload(self):
        self.warm()
        with self._lock:
            if not self._inference_ready:
                self._engine.warm_inference()
                self._inference_ready = True

    def solve(self, image: bytes) -> str:
        self.warm()
        with self._lock:
            value = self._engine.classification(image)
        code = "".join(char for char in str(value) if char.isascii() and char.isalnum()).lower()
        if len(code) != 4:
            raise BookingError("CAPTCHA_UNREADABLE", "验证码未识别成四位，需要换图")
        return code


class Client:
    def _diagnostic_secrets(self, fields=None):
        private = r"token|sign|nonce|uid|card_id|student_num|captcha|password|phone"
        return [value for key, value in {**self.ticket.fields, **(fields or {})}.items()
                if re.search(private, key, re.I)] + [
                    cookie.value for cookie in getattr(self.http, "cookies", [])
                    if re.search(r"sess|token|auth", cookie.name, re.I)]

    def __init__(self, ticket: EntryTicket, http=None):
        self.ticket = ticket
        self.http = http or requests.Session()
        self._interval_retry_used = False
        self.http.headers.update({
            "User-Agent": USER_AGENT, "Origin": ORIGIN, "Referer": PAGES + "home.html",
            "Accept": "*/*", "X-Requested-With": "XMLHttpRequest",
        })

    def post(self, path: str, fields: dict, referer: str | None = None, *, timeout=None) -> dict:
        with request_timing(path):
            return self._post(path, fields, referer, timeout=timeout)

    def _post(self, path: str, fields: dict, referer: str | None = None, *, timeout=None) -> dict:
        started = time.monotonic()
        response = result = None
        error = None
        secrets = self._diagnostic_secrets(fields)
        try:
            response = self.http.post(API + path, data=fields,
                                      headers={"Referer": referer or PAGES + "home.html"},
                                      timeout=timeout or (8, 20), allow_redirects=False)
            if response.status_code == 401:
                raise BookingError("SESSION_EXPIRED", "学校接口要求重新登录")
            if response.status_code == 429:
                raise BookingError("RATE_LIMITED", "学校接口限制了请求频率，请稍后再试")
            if response.status_code >= 500:
                raise BookingError("SERVER_BUSY", "学校服务暂时异常，请稍后再试")
            response.raise_for_status()
            if response.is_redirect:
                raise BookingError("UNEXPECTED_REDIRECT", "接口发生了重定向，未继续发送会话")
            result = response.json()
        except (requests.RequestException, ValueError) as exc:
            error = "timeout" if isinstance(exc, requests.Timeout) else "connection" if isinstance(exc, requests.ConnectionError) else "invalid_json" if isinstance(exc, ValueError) else "http_error"
            # Do not expose request objects, cookies or signed login fields.
            raise BookingError("HTTP_FAILED", "体育接口请求失败或响应不是 JSON") from exc
        except BookingError as exc:
            error = exc.code
            raise
        finally:
            secrets += self._diagnostic_secrets(fields)
            record_exchange(path, fields, response=response, result=result, error=error,
                            secrets=secrets, elapsed_ms=round((time.monotonic()-started)*1000))
        if not isinstance(result, dict):
            raise BookingError("INVALID_RESPONSE", "体育接口响应结构不符合预期")
        return result

    def require(self, path: str, fields: dict, referer: str | None = None, *, timeout=None):
        result = self.post(path, fields, referer, timeout=timeout) if timeout else self.post(path, fields, referer)
        if result.get("status") != 1:
            info = str(result.get("info") or "体育接口拒绝请求")
            code = rejection_code(info, result.get("status"))
            phases = {"/index/Index/checkLogin": "authenticating",
                      "/index/stadium/chooseVerify": "server_precheck",
                      "/index/Stadium/getVenueConfig": "checking_config",
                      "/stadium/interval/getInterval": "querying_slots"}
            raise BookingError(code, info, phase=phases.get(path), server_status=result.get("status"),
                               details={"api":api_label(path)})
        return result.get("data")

    def login(self):
        self.require("/index/Index/checkLogin", self.ticket.fields)
        if not any(cookie.name == "PHPSESSID" for cookie in self.http.cookies):
            raise BookingError("NO_SESSION_COOKIE", "换票成功但没有取得 PHPSESSID")
        self.http.cookies.set("COOKIE_IS_APP_UI", "0", domain="xdty.xmu.edu.cn", path="/")
        self.http.cookies.set("login_type", "4", domain="xdty.xmu.edu.cn", path="/")
        orders = records(self.require("/index/stadium/mySubscribe", {"p": "1"}, PAGES + "mine.html"))
        identities = {str(order["uid"]) for order in orders if order.get("uid")}
        if identities and identities != {self.ticket.uid}:
            raise BookingError("ACCOUNT_MISMATCH", "服务端账号与本机登录票据不一致")
        return {"authenticated": True, "account_checked": bool(identities),
                "existing_orders_read": True, "existing_order_count": len(orders)}

    def resolve_venue(self, target: Target) -> Venue:
        catalog = self.require("/index/Stadium/getStadiumList", {})
        stadiums = [item for item in records(catalog.get("stadium")) if same_name(item.get("name"), target.venue)]
        if len(stadiums) != 1:
            raise BookingError("VENUE_NOT_FOUND", "服务器目录没有唯一匹配的目标场馆")
        stadium_id = str(stadiums[0]["id"])
        detail = self.require("/index/Stadium/getStadiumDetails", {"id": stadium_id},
                              PAGES + "detail.html?" + urlencode({"id": stadium_id}))
        sports = [item for item in records(detail.get("venue")) if same_name(item.get("name"), target.sport)]
        if not same_name(detail.get("name"), target.venue) or len(sports) != 1:
            raise BookingError("SPORT_MISMATCH", "场馆详情与目标运动不匹配")
        sport = sports[0]
        venue_ids = {str(day["venue_id"]) for day in records(sport.get("list")) if day.get("venue_id")}
        if len(venue_ids) != 1:
            raise BookingError("VENUE_SCHEMA_CHANGED", "日期列表的项目 ID 不唯一，未猜测参数")
        raw_range = detail.get("user_range")
        try:
            area_ids = json.loads(raw_range) if isinstance(raw_range, str) else raw_range
            if not isinstance(area_ids, list) or not area_ids or any(not str(x).isdigit() for x in area_ids):
                raise ValueError()
        except (ValueError, TypeError) as exc:
            raise BookingError("VENUE_SCHEMA_CHANGED", "场地范围字段不合法") from exc
        return Venue(stadium_id, venue_ids.pop(), str(sport["category_id"]), str(detail["name"]),
                     str(sport["name"]), json.dumps(area_ids, separators=(",", ":")))

    def intervals(self, venue: Venue, *, cancelled=None, progress=None) -> dict:
        def read():
            if cancelled and cancelled.is_set():
                raise BookingError("CANCELLED", "任务已取消，未继续查询场次")
            return self.require("/stadium/interval/getInterval", venue.fields(), venue.choose_url())

        try:
            data = read()
        except BookingError as exc:
            # A narrow read-only recovery, never a retry of require/post/submit.
            # Keep the budget across open-date and captcha loops in this session.
            if (self._interval_retry_used or exc.code != "API_REJECTED"
                    or type(exc.server_status) is not int or exc.server_status != 0
                    or str(exc).strip() != "参数错误"):
                raise
            self._interval_retry_used = True
            if progress:
                progress("waiting_slot_retry")
            if cancelled:
                if cancelled.wait(INTERVAL_RETRY_SECONDS):
                    raise BookingError("CANCELLED", "任务已取消，未重试场次查询") from exc
            else:
                time.sleep(INTERVAL_RETRY_SECONDS)
            if progress:
                progress("querying_slots")
            data = read()
        if not isinstance(data, dict) or not isinstance(data.get("time_slot_list"), list):
            raise BookingError("SLOT_SCHEMA_CHANGED", "场次接口没有返回有效列表")
        return data

    def prepare(self, target: Target, venue: Venue | None = None, progress=None, slot_data=None, *, cancelled=None) -> dict:
        emit = progress or (lambda _: None)
        if venue is None:
            emit("resolving_venue")
            venue = self.resolve_venue(target)
        if slot_data is None:
            emit("querying_slots")
            slot_data = self.intervals(venue, cancelled=cancelled, progress=progress)
        emit("checking_slot")
        selected = pick_slot(slot_data, target)
        emit("server_precheck")
        self.require("/index/stadium/chooseVerify", {
            "stadium_id": venue.stadium_id, "venue_id": venue.venue_id,
            "selected": json.dumps([selected], ensure_ascii=False, separators=(",", ":")),
            "is_academy": "1", "ids": "",
        }, venue.choose_url())
        ref = confirm_url(venue, selected)
        emit("checking_config")
        config = self.require("/index/Stadium/getVenueConfig", {
            "stadium_id": venue.stadium_id, "venue_id": venue.venue_id, "category_id": venue.category_id,
        }, ref)
        info = config.get("info") or {}
        if info.get("isCanAppoint", 1) in (0, False, "0"):
            raise BookingError("NOT_OPEN", str(info.get("isCanAppointMsg") or "不允许预约"))
        if info.get("needRemark") in (1, True, "1") and not target.remark:
            raise BookingError("REMARK_REQUIRED", "该场馆需要申请说明")
        if info.get("verify_phone") in (1, True, "1") and not info.get("phone"):
            raise BookingError("PHONE_REQUIRED", "该账号需要先在小程序绑定手机号")
        return {"venue": venue, "selected": selected, "referer": ref}

    def captcha(self, referer: str) -> bytes:
        with request_timing("/index/index/captcha"):
            return self._captcha(referer)

    def _captcha(self, referer: str) -> bytes:
        query = captcha_query(self.ticket.uid, str(random.random()), int(time.time()))
        started = time.monotonic()
        response = None
        error = None
        try:
            response = self.http.get(API + "/index/index/captcha", params=query,
                                     headers={"Referer": referer}, timeout=(8, 20), allow_redirects=False)
            response.raise_for_status()
            if not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise BookingError("CAPTCHA_FETCH_FAILED", "验证码签名或会话验证失败，没有返回 PNG")
        except requests.RequestException as exc:
            error = "timeout" if isinstance(exc, requests.Timeout) else "http_error"
            raise BookingError("CAPTCHA_FETCH_FAILED", "验证码请求失败") from exc
        except BookingError as exc:
            error = exc.code
            raise
        finally:
            record_exchange("/index/index/captcha", {}, response=response,
                            result={"image_received":True} if not error else None,
                            error=error, secrets=[*self._diagnostic_secrets(), *query.values()],
                            elapsed_ms=round((time.monotonic()-started)*1000), method="GET")
        return response.content

    def find_existing(self, target: Target) -> dict | None:
        deadline = time.monotonic() + ORDER_LOOKUP_SECONDS
        active_only = (target.sport, target.venue) == ("游泳馆", "翔安校区游泳馆")
        # A future booking cannot already be redeemed (2) or unredeemed history (4).
        # Do not fan out into every historical gym order at the 07:00 rush: only
        # currently active orders can match a future target. Keep legacy behaviour
        # for today/past lookups, which are also used to prove old bookings existed.
        china_today = datetime.now(timezone(timedelta(hours=8))).date()
        future_target = date.fromisoformat(target.date) > china_today
        list_states = {"1"} if active_only or future_target else CONFIRMED_ORDER_STATES
        def read(path, fields, referer):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BookingError("ORDER_CHECK_TIMEOUT", "本轮查单时间已用完，不能据此认定预约不存在")
            return self.require(path, fields, referer, timeout=(min(2, remaining), min(4, remaining)))
        seen = set()
        for page in range(1, MAX_ORDER_PAGES + 1):
            orders = records(read("/index/stadium/mySubscribe", {"p": str(page)}, PAGES + "mine.html"))
            new_rows = 0
            for order in orders:
                if order.get("uid") is not None and str(order["uid"]) != self.ticket.uid:
                    raise BookingError("ACCOUNT_MISMATCH", "查单账号与原提交账号不一致")
                order_id = order.get("order_id")
                if order_id is None or str(order_id) in seen:
                    continue
                seen.add(str(order_id)); new_rows += 1
                if not same_name(order.get("stadium_name"), target.venue) or not same_name(order.get("project_name"), target.sport):
                    continue
                if str(order.get("audit_status")) not in list_states:
                    continue
                detail = read("/index/stadium/subDetails", {
                    "order_id": str(order_id), "uid": self.ticket.uid, "is_admin": "false",
                }, PAGES + "order.html?" + urlencode({"id": order_id}))
                if detail.get("uid") is not None and str(detail["uid"]) != self.ticket.uid:
                    raise BookingError("ACCOUNT_MISMATCH", "预约详情账号与原提交账号不一致")
                if active_only and str(detail.get("audit_status")) != "1":
                    continue
                if detail.get("audit_status") is not None and str(detail["audit_status"]) not in CONFIRMED_ORDER_STATES:
                    continue
                for item in records(detail.get("details")):
                    if active_only and str(item.get("status")) != "1":
                        continue
                    if item.get("status") is not None and str(item["status"]) not in CONFIRMED_ORDER_STATES:
                        continue
                    times = re.findall(r"\d{2}:\d{2}", str(item.get("interval_time", "")))
                    if item.get("date") != target.date or not times or times[0] != target.time:
                        continue
                    if target.end and (len(times) < 2 or times[1] != target.end):
                        continue
                    if target.court and not same_name(item.get("area_name"), target.court):
                        continue
                    return {"order_id": order_id, "date": item["date"],
                            "time": item["interval_time"], "court": item.get("area_name"), "verified": True}
            if len(orders) < 10 or not new_rows:
                break
        return None


def public_plan(plan: dict, target: Target) -> dict:
    return {"ok": True, "state": "prepared", "target": asdict(target),
            "selected": plan["selected"], "method": "POST", "url": API + ADD_ORDER_PATH,
            "fields": add_order_fields(plan["venue"], plan["selected"], "<运行时验证码>", target.remark)}


def submitted_target(target: Target, plan: dict) -> Target:
    """Freeze the actual selected court/end, even when the user's goal was auto."""
    selected = plan["selected"]
    times = re.findall(r"\d{2}:\d{2}", str(selected.get("interval_time", "")))
    return replace(target, court=str(selected.get("area_name") or target.court or "") or None,
                   end=times[1] if len(times) >= 2 else target.end)


def reconcile_submission(client, target: Target, cancelled=None, progress=None, progress_detail=None):
    """Read-only bounded reconciliation. Absence is never proof of rejection."""
    def emit(phase, **detail):
        if progress_detail:
            progress_detail(phase, **detail)
        elif progress:
            progress(phase)
    summary = {"checks_attempted": 0, "successful_reads": 0, "stopped_by_user": False}
    emit("submission_uncertain")
    for attempt, delay in enumerate(RECONCILE_DELAYS, 1):
        detail = {"check_attempt": attempt, "check_total": len(RECONCILE_DELAYS)}
        emit("waiting_order_sync", **detail, retry_after_seconds=delay)
        if cancelled is not None:
            if cancelled.wait(delay):
                summary["stopped_by_user"] = True
                break
        else:
            time.sleep(delay)
        emit("rechecking_orders", **detail)
        summary["checks_attempted"] += 1
        try:
            existing = client.find_existing(target)
            summary["successful_reads"] += 1
            if existing and existing.get("verified") is True and existing.get("order_id") is not None:
                emit("order_reconciled", **detail)
                return existing, summary
        except BookingError as exc:
            summary["last_error"] = exc.code
            if exc.code in {"ACCOUNT_MISMATCH", "ACCOUNT_CHANGED", "SESSION_EXPIRED", "RATE_LIMITED"}:
                break
        emit("order_check_retry", **detail)
    emit("order_unconfirmed")
    return None, summary


def submit(client: Client, target: Target, plan: dict | None, solver: CaptchaSolver,
           state_dir: Path, idempotency_key: str | None = None, cancelled=None, progress=None, eligibility=None,
           prepare_after_captcha=None, progress_detail=None, request_context=None) -> dict:
    emit = progress or (lambda _: None)
    identity = client.ticket.uid + "|" + (idempotency_key or json.dumps(asdict(target), sort_keys=True))
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / (hashlib.sha256(identity.encode()).hexdigest() + ".json")
    emit("checking_orders")
    prior = client.find_existing(target)
    if prior:
        return {"ok": True, "state": "already_reserved", **prior}
    solver.warm()
    try:
        handle = path.open("x", encoding="utf-8")
    except FileExistsError:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BookingError("RESULT_UNCONFIRMED", "已有提交记录尚未完整写入，未重放请求") from exc
        if cached.get("target") and cached["target"] != asdict(target):
            raise BookingError("IDEMPOTENCY_CONFLICT", "同一请求标识对应了不同预约目标，请使用新的标识")
        if cached.get("state") in ("submitted", "rejected", "not_submitted", "needs_login", "unsupported_auth"):
            return cached
        raise BookingError("RESULT_UNCONFIRMED", "该请求已经发送或执行结果未知，禁止重放；请先核对预约记录")

    sent_target = None
    def save(value):
        value["request_ref"] = path.stem
        if request_context:
            value.update({key:request_context[key] for key in ("source_run_id","source_task_id") if request_context.get(key)})
        value["account_fingerprint"] = hashlib.sha256(client.ticket.uid.encode()).hexdigest()
        if sent_target is not None:
            value["submitted_target"] = asdict(sent_target)
        handle.seek(0)
        handle.truncate()
        json.dump(value, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())

    def resolve_uncertain():
        existing, summary = reconcile_submission(client, sent_target, cancelled, emit, progress_detail)
        if existing:
            result = {"ok": True, "state": "submitted", "reconciled": True,
                      "message": "提交响应未确认，已自动查到匹配预约并确认成功", "target": asdict(target),
                      "reconciliation": summary, **existing}
            save(result)
            return result
        message = "多轮查单仍未确认预约结果，已保留待确认记录，未重复提交"
        if summary.get("last_error") == "SESSION_EXPIRED":
            message = "提交结果待确认，查单登录已失效；请恢复原账号登录后核对预约，未重复提交"
        elif summary.get("last_error") in {"ACCOUNT_MISMATCH", "ACCOUNT_CHANGED"}:
            message = "查单账号与提交账号不一致，已停止查询，提交结果仍待确认，未重复提交"
        if summary["stopped_by_user"]:
            message = "已停止自动查单，本次提交结果仍待确认，未重复提交"
        save({"ok": False, "state": "unknown", "code": "SUBMIT_UNKNOWN", "message": message,
              "target": asdict(target), "reconciliation": summary})
        raise BookingError("SUBMIT_UNKNOWN", message, phase="order_unconfirmed",
                           details={"reconciliation": summary, "submitted_target": asdict(sent_target)})

    with handle:
        save({"ok": False, "state": "started", "target": asdict(target)})
        for attempt in range(3):
            if eligibility:
                try:
                    eligibility(target)
                except BookingError as exc:
                    save({"ok": False, "state": "not_submitted", "code": exc.code, "target": asdict(target)})
                    raise
            if cancelled and cancelled.is_set():
                save({"ok": False, "state": "not_submitted", "code": "CANCELLED", "target": asdict(target)})
                raise BookingError("CANCELLED", "任务已取消，未获取验证码或提交")
            try:
                emit("captcha")
                # No selected slot is needed to request the session's captcha.
                # Keep this client/session through the subsequent fresh selection and send.
                referer = PAGES + "confirm.html" if prepare_after_captcha else plan["referer"]
                code = solver.solve(client.captcha(referer))
                emit("captcha_recognized")
            except BookingError as exc:
                if exc.code == "CAPTCHA_UNREADABLE" and attempt < 2:
                    emit("captcha_retry")
                    continue
                save({"ok": False, "state": "not_submitted", "code": exc.code, "target": asdict(target)})
                raise
            if prepare_after_captcha:
                try:
                    if cancelled and cancelled.is_set():
                        raise BookingError("CANCELLED", "任务已取消，未查询场地或提交")
                    plan = prepare_after_captcha()
                except BookingError as exc:
                    save({"ok": False, "state": "not_submitted", "code": exc.code, "target": asdict(target)})
                    raise
            if cancelled and cancelled.is_set():
                save({"ok": False, "state": "not_submitted", "code": "CANCELLED", "target": asdict(target)})
                raise BookingError("CANCELLED", "请求服务已停止，未发送最终预约")
            # Persist BEFORE sending: a crash/timeout is never a reason to blindly replay.
            if eligibility:
                try:
                    eligibility(target)
                except BookingError as exc:
                    save({"ok": False, "state": "not_submitted", "code": exc.code, "target": asdict(target)})
                    raise
            sent_target = submitted_target(target, plan)
            save({"ok": False, "state": "unknown", "target": asdict(target)})
            try:
                if progress_detail:
                    progress_detail("submitting",request_ref=path.stem,submitted_target=asdict(sent_target))
                else:
                    emit("submitting")
                response = client.post(ADD_ORDER_PATH,
                    add_order_fields(plan["venue"], plan["selected"], code, target.remark), plan["referer"])
            except BookingError:
                return resolve_uncertain()
            if response.get("status") == 1:
                data = response.get("data")
                if isinstance(data, dict) and data.get("package"):
                    result = {"ok": False, "state": "unknown", "code": "PAYMENT_REQUIRED",
                              "verified": False, "target": asdict(target),
                              "message": "服务端要求支付，未自动支付；预约尚未确认，请到官方小程序核对"}
                    save(result)
                    return result
                result = {"ok": True, "state": "submitted", "verified": False,
                          "message": str(response.get("info") or "服务端已接受预约"), "target": asdict(target)}
                paid = Decimal(plan["selected"]["price"]) != 0
                if paid:
                    result.update(ok=False, state="unknown", code="RESULT_UNCONFIRMED",
                                  message="服务端已接受请求，但尚未查到有效预约；请核对，勿重复提交")
                # Record server acceptance even when subsequent read-only verification fails.
                save(result)
                emit("order_accepted")
                emit("verifying_order")
                for delay in (0, 0.5, 1.0):
                    time.sleep(delay)
                    try:
                        existing = client.find_existing(sent_target)
                    except BookingError:
                        break
                    if existing and existing.get("verified") is True and existing.get("order_id") is not None:
                        result.update(existing)
                        if paid:
                            result.update(ok=True, state="submitted", message="已查到有效预约")
                            result.pop("code", None)
                        break
                save(result)
                return result
            info = str(response.get("info") or "服务端拒绝预约")
            reason = rejection_code(info, response.get("status"))
            # Authentication/quota/slot failures take priority over incidental
            # captcha wording. Only an explicit captcha denial permits a resend.
            captcha_failure = response.get("status") == 0 and reason == "CAPTCHA_REJECTED" and any(word in info for word in ("错误", "不正确", "过期", "失效"))
            if captcha_failure and attempt < 2:
                save({"ok": False, "state": "captcha_rejected", "target": asdict(target)})
                emit("captcha_retry")
                continue
            if response.get("status") not in (0, -1):
                return resolve_uncertain()
            rejection = BookingError(reason, info,
                                     phase="submitting", server_status=response.get("status"))
            result = {"state": "rejected", **rejection.public(), "target": asdict(target)}
            save(result)
            return result
    raise BookingError("CAPTCHA_UNREADABLE", "验证码识别次数已用完")
