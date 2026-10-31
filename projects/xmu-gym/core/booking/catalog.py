"""Low-frequency read-only inventory. Snapshots are observations, not booking IDs."""
from __future__ import annotations

import json
import math
import re
import time
import unicodedata
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from .client import Client, PAGES, Venue, cell_reason, records
from .session import BookingError, discover_entry


class InventoryClient(Client):
    def __init__(self, ticket, interval=1.5):
        super().__init__(ticket)
        if not math.isfinite(interval) or interval < 1.5:
            raise BookingError("INVALID_INTERVAL", "采集请求间隔至少 1.5 秒")
        self.interval = interval
        self.last_request = None
        self.request_count = 0

    def post(self, path, fields, referer=None):
        # Also pace login calls. No concurrency, retries, captcha or order creation.
        if path not in {"/index/Index/checkLogin", "/index/stadium/mySubscribe",
                        "/index/Stadium/getStadiumList", "/index/Stadium/getStadiumDetails",
                        "/stadium/interval/getInterval"}:
            raise BookingError("READ_ONLY", "采集器只允许登录和目录查询接口")
        if self.last_request is not None:
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        self.request_count += 1
        return super().post(path, fields, referer)


def describe_intervals(data: dict) -> dict:
    courts = {}
    slots = []
    times = set()
    for block in data.get("time_slot_list") or []:
        found = re.findall(r"\d{2}:\d{2}", str(block.get("time_range", "")))
        start = str(block.get("start_time") or (found[0] if found else ""))[:5]
        end = str(block.get("end_time") or (found[1] if len(found) > 1 else ""))[:5]
        if start and end:
            times.add((start, end))
        cells = []
        for cell in block.get("slots") or []:
            area_id = str(cell.get("column_id") or "")
            name = str(cell.get("area_name") or area_id)
            capacity = int(cell["max_count"] if cell.get("max_count") is not None
                           else data.get("personal_num") or 0)
            if data.get("personal_num") is not None:
                capacity = min(capacity, int(data["personal_num"]))
            used = int(cell.get("selected") or 0)
            courts[area_id] = {"id": area_id, "name": name}
            cells.append({"court": name, "area_id": area_id, "capacity": capacity,
                          "selected": used, "remaining": max(0, capacity - used),
                          "status": cell.get("status"), "price": cell.get("price"),
                          "reason_code": cell_reason(data, cell),
                          "lock_reason": cell.get("lock_reason"), "is_lock": cell.get("is_lock"),
                          "available": cell_reason(data, cell) == "AVAILABLE"})
        slots.append({"date": str(block.get("date") or ""), "start": start, "end": end,
                      "court_count": len(cells), "cells": cells})
    return {"courts": list(courts.values()), "court_count": len(courts),
            "time_slots": [{"start": start, "end": end} for start, end in sorted(times)],
            "slots": slots, "is_can_choose": data.get("is_can_choose"),
            "open_tips": data.get("is_can_choose_tips"), "confirm_time": data.get("confirm_time")}


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_report(path: Path, snapshot: dict):
    labels = {"AVAILABLE": "可约", "SLOT_OCCUPIED": "已满", "SLOT_LOCKED": "锁定",
              "SLOT_UNAVAILABLE": "不可约（原因未明）", "NOT_OPEN": "未开放", "BOOKING_CLOSED": "已截止"}
    def clean(value):
        return str(value).replace("|", "／").replace("\n", " ").replace("\r", " ")
    lines = ["# 小程序场馆信息", "", f"采集时间：{snapshot['observed_at']}", "",
             f"本次 {snapshot.get('request_count', 0)} 次串行请求，起始间隔至少 {snapshot['request_interval_seconds']} 秒。",
             "覆盖当前账号学校目录返回的全部项目；不是校园全部体育设施清单。容量与状态均为快照，下单必须重新查询。",
             "", "| 项目 | 场馆 | 位置 | 场地/区域数 | 时段数 |", "| --- | --- | --- | ---: | ---: |"]
    for observation in snapshot["venues"]:
        venue = observation["venue"]
        location = observation.get("metadata", {}).get("location", "未提供")
        lines.append(f"| {clean(venue['sport'])} | {clean(venue['name'])} | {clean(location)} | {observation['court_count']} | {len(observation['time_slots'])} |")
    for observation in snapshot["venues"]:
        lines.extend(["", "## " + clean(observation["venue"]["name"]), "",
                      "场地/区域名称：" + "、".join(clean(court["name"]) for court in observation["courts"]), "",
                      "| 日期 | 时段 | 场地数 | 单场次容量 | 状态分布 |", "| --- | --- | ---: | --- | --- |"])
        for slot in observation["slots"]:
            counts = Counter(cell.get("reason_code", "UNKNOWN") for cell in slot["cells"])
            capacities = sorted({cell["capacity"] for cell in slot["cells"]})
            states = "；".join(f"{labels.get(code, code)} {count}" for code, count in counts.items())
            lines.append(f"| {slot['date']} | {slot['start']}–{slot['end']} | {slot['court_count']} | {','.join(map(str, capacities))} | {states} |")
        reasons = sorted({str(cell["lock_reason"]) for slot in observation["slots"] for cell in slot["cells"] if cell.get("lock_reason")})
        if reasons:
            lines.extend(["", "学校提供的锁定说明：" + "；".join(clean(reason) for reason in reasons)])
    lines.extend(["", "`is_lock` 不单独判断可约性。锁定看 `status=locked`，已满同时看容量和已约人数。",
                  "`unavailable` 没有明确说明时保留原因未明，不猜测为截止或被抢。", "",
                  "篮球、足球、排球未出现在本次账号返回的预约项目中，保留本地禁用占位。"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_structure(path: Path, snapshot: dict):
    """Selection templates never contain today's occupancy or lock policy."""
    structure = {"schema_version": 1, "observed_at": snapshot["observed_at"],
                 "purpose": "selection_templates", "complete": snapshot["complete"],
                 "schedule_is_permanent": False, "venues": []}
    lines = ["# 场馆、场地与时段资料", "", f"资料采集时间：{snapshot['observed_at']}", "",
             "用于选择目标场馆、场地和时段。时段来自响应窗口中的全部行，包括锁定和已满行；每日锁定不作为长期规则。",
             "本资料不保存余量和锁定状态，时段也不保证永久固定。预约日需重新查询目标日期并校验。", "",
             "| 运动 | 场馆 | 位置 | 场地/区域名称 | 已观察到的时段 |", "| --- | --- | --- | --- | --- |"]
    for item in snapshot["venues"]:
        venue = item["venue"]
        location = item.get("metadata", {}).get("location", "未提供")
        courts = [court["name"] for court in item["courts"]]
        structure["venues"].append({"sport": venue["sport"], "name": venue["name"], "location": location,
             "court_names": courts, "court_count": len(courts), "time_slots": item["time_slots"],
             "observed_dates": sorted({slot["date"] for slot in item["slots"]})})
        values = [venue["sport"], venue["name"], str(location), "、".join(courts),
                  "；".join(slot["start"] + "–" + slot["end"] for slot in item["time_slots"])]
        lines.append("| " + " | ".join(value.replace("|", "／").replace("\n", " ") for value in values) + " |")
    write_json(path, structure)
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_inventory(output: Path, profile=None, interval=1.5, sport=None, venue=None) -> dict:
    client = InventoryClient(discover_entry(profile), interval)
    snapshot = {"schema_version": 1, "observed_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                "complete": False, "request_interval_seconds": interval, "venues": [], "errors": []}
    try:
        client.login()
        catalog = client.require("/index/Stadium/getStadiumList", {})
        for stadium in records(catalog.get("stadium")):
            name = str(stadium.get("name") or "")
            if venue and name != venue:
                continue
            stadium_id = str(stadium["id"])
            try:
                detail = client.require("/index/Stadium/getStadiumDetails", {"id": stadium_id},
                                        PAGES + "detail.html?" + urlencode({"id": stadium_id}))
                area_ids = detail.get("user_range")
                if isinstance(area_ids, str):
                    area_ids = json.loads(area_ids)
                if not isinstance(area_ids, list) or not area_ids or any(not str(x).isdigit() for x in area_ids):
                    raise BookingError("VENUE_SCHEMA_CHANGED", "场地范围字段不合法")
                for project in records(detail.get("venue")):
                    project_name = str(project.get("name") or "")
                    if sport and project_name != sport:
                        continue
                    ids = {str(day["venue_id"]) for day in records(project.get("list")) if day.get("venue_id")}
                    if len(ids) != 1:
                        snapshot["errors"].append({"venue": name, "sport": project_name,
                                                   "code": "VENUE_SCHEMA_CHANGED"})
                        continue
                    resolved = Venue(stadium_id, ids.pop(), str(project["category_id"]), name,
                                     project_name, json.dumps(area_ids, separators=(",", ":")))
                    observation = describe_intervals(client.intervals(resolved))
                    metadata = {key: detail[key] for key in
                                ("name", "address", "location", "opening_hours", "appoint_time", "notice", "description", "tips", "introduction")
                                if key in detail}
                    snapshot["venues"].append({"venue": asdict(resolved), "metadata": metadata, **observation})
                    write_json(output, snapshot)
            except BookingError as exc:
                snapshot["errors"].append({"venue": name, "code": exc.code})
                # Stop on transport/login errors (including rate limits); never retry a scan.
                if exc.code in {"HTTP_FAILED", "SESSION_EXPIRED", "UNEXPECTED_REDIRECT", "INVALID_RESPONSE"}:
                    raise
        snapshot["complete"] = not snapshot["errors"]
    finally:
        client.http.close()
        snapshot["request_count"] = client.request_count
        write_json(output, snapshot)
    return snapshot


def update_catalog(path: Path, snapshot: dict) -> int:
    """Only merge observed matching entries. Empty/failed observations keep prior data."""
    catalog = json.loads(path.read_text(encoding="utf-8"))
    updated = 0
    for observation in snapshot["venues"]:
        identity = observation["venue"]
        if not observation["time_slots"] or not observation["courts"]:
            continue
        for sport in catalog["sports"]:
            if sport["ocr_label"] != identity["sport"]:
                continue
            for venue in sport["venues"]:
                if unicodedata.normalize("NFKC", venue["ocr_label"]) != unicodedata.normalize("NFKC", identity["name"]):
                    continue
                known_times = {(slot["start"], slot["end"]) for slot in venue["time_slots"]}
                known_times.update((slot["start"], slot["end"]) for slot in observation["time_slots"])
                venue.update(time_slots=[{"start": start, "end": end} for start, end in sorted(known_times)],
                             observed_time_slots=observation["time_slots"],
                             court_count_hint=observation["court_count"],
                             observed_at=snapshot["observed_at"],
                             courts=observation["courts"] if venue["has_courts"] else [])
                capacities = [cell["capacity"] for slot in observation["slots"] for cell in slot["cells"]]
                if capacities:
                    venue["capacity_hint"] = max(capacities)
                updated += 1
    write_json(path, catalog)
    return updated
