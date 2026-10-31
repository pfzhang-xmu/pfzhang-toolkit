"""CLI and a loopback HTTP endpoint; all requests use the user's current account."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from .client import CaptchaSolver, Client, Target, pick_slot, public_plan, submit
from .session import BookingError, discover_entry


def data_root() -> Path:
    if os.environ.get("XMU_GYM_DATA"):
        return Path(os.environ["XMU_GYM_DATA"]) / "http-booking"
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "xmu-gym" / "http-booking"


def parse_fire_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except (TypeError, ValueError) as exc:
        raise BookingError("INVALID_FIRE_AT", "触发时间必须含时区，例如 2026-10-14T07:00:00+08:00") from exc


def wait_until(deadline: datetime | None, cancelled=None):
    while deadline:
        if cancelled and cancelled.is_set():
            raise BookingError("CANCELLED", "定时请求已取消")
        remaining = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining <= 0:
            return
        duration = min(remaining, 0.05 if remaining < 1 else 0.5)
        if cancelled:
            cancelled.wait(duration)
        else:
            time.sleep(duration)


def make_target(payload: dict) -> Target:
    allowed = {"sport", "venue", "date", "time", "court", "end", "remark", "court_offset"}
    try:
        return Target(**{key: value for key, value in payload.items() if key in allowed})
    except (TypeError, ValueError) as exc:
        raise BookingError("INVALID_TARGET", "请提供 sport、venue、date 和 time") from exc


def fallback_targets(target: Target, values) -> list[Target]:
    if values is None:
        return [target]
    if not isinstance(values, list) or len(values) > 8:
        raise BookingError("INVALID_FALLBACKS", "fallbacks 必须是最多 8 项的列表")
    targets = [target]
    for value in values:
        if not isinstance(value, dict) or not value or set(value) - {"time", "end", "court"}:
            raise BookingError("INVALID_FALLBACKS", "备选只允许 time、end、court；场馆和日期保持一致")
        fields = asdict(target)
        if "time" in value and "end" not in value:
            fields["end"] = None
        fields.update(value)
        candidate = Target(**fields)
        if candidate not in targets:
            targets.append(candidate)
    return targets


DEFAULT_FALLBACK_ON = ("SLOT_OCCUPIED", "SLOT_LOCKED", "SLOT_UNAVAILABLE", "BOOKING_CLOSED", "NO_SLOT")


def fallback_policy(values=None) -> tuple[str, ...]:
    allowed = set(DEFAULT_FALLBACK_ON) | {"NOT_OPEN"}
    if values is None:
        return tuple(sorted(DEFAULT_FALLBACK_ON))
    if not isinstance(values, list) or any(not isinstance(code, str) or code not in allowed for code in values):
        raise BookingError("INVALID_FALLBACK_POLICY", "fallback_on 只接受场次级不可约原因，不能包含账号或提交未知错误")
    return tuple(sorted(set(values)))


def run_candidates(target: Target, profile=None, *, fallbacks=None, fallback_on=None, **options) -> dict:
    policy = fallback_policy(fallback_on)
    initial_result = None
    if callable(fallbacks):
        # Desktop discovers alternative times only after a definite primary rejection.
        # The primary attempt must not be replayed after building the batch journal.
        try:
            initial_result = run_target(target, profile, **options)
        except BookingError as exc:
            if exc.code in {"SUBMIT_UNKNOWN", "RESULT_UNCONFIRMED"}:
                raise
            initial_result = {"state": "failed", **exc.public()}
        if initial_result.get("ok") or initial_result.get("code") not in policy:
            return initial_result
        if options.get("progress"):
            options["progress"]({"phase":"candidate_rejected", "candidate_index":0,
                                 "target":asdict(target), "code":initial_result.get("code"),
                                 "message":initial_result.get("message", "首选场次不可预约")})
        if options.get("cancelled") and options["cancelled"].is_set():
            raise BookingError("CANCELLED", "任务已取消，未查询备选时段")
        fallbacks = fallbacks()
    targets = fallback_targets(target, fallbacks)
    if len(targets) == 1:
        return initial_result if initial_result is not None else run_target(target, profile, **options)
    commit = options.get("commit", False)
    account = hashlib.sha256(discover_entry(profile).uid.encode()).hexdigest()
    if options.get("expected_account") and account != options["expected_account"]:
        raise BookingError("ACCOUNT_CHANGED", "等待期间账号已改变")
    options["expected_account"] = account
    definition = [asdict(item) for item in targets]
    identity = account + "|" + str(options.get("idempotency_key") or json.dumps({"targets": definition, "fallback_on": policy}, sort_keys=True))
    batch_id = hashlib.sha256(identity.encode()).hexdigest()
    journal = None
    if commit:
        root = data_root() / "fallback-requests"
        root.mkdir(parents=True, exist_ok=True)
        path = root / (batch_id + ".json")
        try:
            journal = path.open("x", encoding="utf-8")
        except FileExistsError:
            try:
                prior = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise BookingError("RESULT_UNCONFIRMED", "备选任务已有未完成记录，请查单") from exc
            if prior.get("targets") != definition or prior.get("fallback_on") != list(policy):
                raise BookingError("IDEMPOTENCY_CONFLICT", "请求标识对应了不同备选列表")
            if prior.get("state") == "complete":
                return prior["result"]
            raise BookingError("RESULT_UNCONFIRMED", "备选任务执行结果尚未确认，未继续下单")

    def save(state, result=None):
        if journal:
            journal.seek(0)
            journal.truncate()
            json.dump({"targets": definition, "fallback_on": policy, "state": state, "result": result}, journal, ensure_ascii=False)
            journal.flush()
            os.fsync(journal.fileno())

    attempts = []
    try:
        save("running")
        for index, candidate in enumerate(targets):
            cancelled = options.get("cancelled")
            if cancelled and cancelled.is_set():
                raise BookingError("CANCELLED", "备选任务已取消")
            if index:
                if cancelled:
                    if cancelled.wait(1.5):
                        raise BookingError("CANCELLED", "备选任务已取消")
                else:
                    time.sleep(1.5)
            attempt_options = {**options,
                               "idempotency_key": batch_id + f"-{index}"}
            if options.get("progress") and not (index == 0 and initial_result is not None):
                options["progress"]({"phase": "selecting_candidate", "candidate_index": index,
                                     "target": asdict(candidate)})
            try:
                result = initial_result if index == 0 and initial_result is not None else run_target(candidate, profile, **attempt_options)
            except BookingError as exc:
                if exc.code in {"SUBMIT_UNKNOWN", "RESULT_UNCONFIRMED"}:
                    raise  # Keep journal running to block replay across the entire candidate list.
                result = {"state": "failed", **exc.public()}
            attempts.append({"target": asdict(candidate), "state": result.get("state"),
                             "code": result.get("code"), "message": result.get("message"),
                             "phase": result.get("phase"), "captcha_requested": result.get("captcha_requested", False)})
            result = {**result, "attempts": attempts.copy(), "selected_candidate": index}
            if not result.get("ok") and options.get("progress") and not (index == 0 and initial_result is not None):
                options["progress"]({"phase": "candidate_rejected", "candidate_index": index,
                                     "target": asdict(candidate), "code": result.get("code"),
                                     "message": result.get("message", "候选场次不可预约")})
            # Only caller-configured slot failures advance; global/account errors stop.
            if result.get("ok") or result.get("code") not in policy:
                save("complete", result)
                return result
        result.update(code="FALLBACKS_EXHAUSTED", message="所有指定候选场次均不可约，详见 attempts")
        save("complete", result)
        return result
    finally:
        if journal:
            journal.close()


def authenticate(client: Client, profile=None):
    try:
        return client, client.login()
    except BookingError as exc:
        if exc.code != "SESSION_EXPIRED":
            raise
        # Only retry when this same account's local entry has actually changed.
        fresh = discover_entry(profile or client.ticket.profile)
        if fresh.uid != client.ticket.uid:
            raise BookingError("ACCOUNT_CHANGED", "刷新期间账号已改变，未继续原任务") from exc
        if fresh.fields == client.ticket.fields:
            raise exc
        replacement = Client(fresh)
        client.http.close()
        try:
            result = replacement.login()
            result["entry_refreshed"] = True
            return replacement, result
        except BaseException:
            replacement.http.close()
            raise


def check_auth(profile=None) -> dict:
    client = None
    try:
        client = Client(discover_entry(profile))
        client, result = authenticate(client, profile)
        return {"ok": True, "state": "ready", "auth_mode": "legacy_h5", "ttl_verified": False,
                "account_fingerprint": hashlib.sha256(client.ticket.uid.encode()).hexdigest(), **result}
    except BookingError as exc:
        result = exc.public()
        if client:
            result["account_fingerprint"] = hashlib.sha256(client.ticket.uid.encode()).hexdigest()
        return result
    finally:
        if client:
            client.http.close()


def run_target(target: Target, profile=None, *, commit=False, captcha_check=False,
               solver=None, watch_seconds=0, idempotency_key=None, expected_account=None, cancelled=None,
               progress=None, eligibility=None, captcha_first=False, before_commit=None, request_context=None) -> dict:
    phase = "reading_entry"
    flow = []
    captcha_requested = False
    def emit(value, **detail):
        nonlocal phase, captcha_requested
        phase = value
        if value == "captcha":
            captcha_requested = True
        flow.append(value)
        if progress:
            progress({"phase": value, "target": asdict(target), **detail})
    if eligibility:
        emit("local_eligibility")
        eligibility(target)
    emit("reading_entry")
    try:
        ticket = discover_entry(profile)
        fingerprint = hashlib.sha256(ticket.uid.encode()).hexdigest()
        if expected_account and fingerprint != expected_account:
            raise BookingError("ACCOUNT_CHANGED", "等待期间微信账号发生了变化，未执行另一账号的预约")
    except BookingError as exc:
        exc.phase = "reading_entry"
        exc.captcha_requested = False
        exc.flow = flow
        raise
    client = Client(ticket)
    try:
        emit("authenticating")
        client, authentication = authenticate(client, profile)
        if commit and before_commit:
            emit("checking_orders")
            before_commit(client, target)
        if commit:
            emit("checking_orders")
            existing = client.find_existing(target)
            if existing:
                return {"ok": True, "state": "already_reserved", "flow": flow, "captcha_requested": False, **existing}
        if commit and captcha_first:
            def prepare_fresh():
                if eligibility:
                    eligibility(target)
                emit("resolving_venue")
                venue = client.resolve_venue(target)
                deadline = time.monotonic() + min(max(float(watch_seconds), 0), 60)
                while True:
                    if cancelled and cancelled.is_set():
                        raise BookingError("CANCELLED", "任务已取消，未提交")
                    if eligibility:
                        eligibility(target)
                    emit("querying_slots")
                    data = client.intervals(venue, cancelled=cancelled, progress=emit)
                    try:
                        plan = client.prepare(target, venue=venue, progress=emit, slot_data=data)
                        break
                    except BookingError as exc:
                        if exc.code not in ("DATE_NOT_OPEN", "NOT_OPEN") or time.monotonic() >= deadline:
                            raise
                        emit("waiting_open")
                        time.sleep(1)
                emit("slot_ready", court=plan["selected"].get("area_name"), message="识别后实时场次与预约资格校验通过")
                if eligibility:
                    eligibility(target)
                return plan
            emit("warming")
            result = submit(client, target, None, solver or CaptchaSolver(),
                            data_root() / "requests", idempotency_key, cancelled, progress=emit,
                            eligibility=eligibility, prepare_after_captcha=prepare_fresh, progress_detail=emit, request_context=request_context)
            return {**result, "flow": flow, "captcha_requested": captcha_requested,
                    "captcha_first": True}
        emit("resolving_venue")
        venue = client.resolve_venue(target)
        deadline = time.monotonic() + min(max(float(watch_seconds), 0), 60)
        while True:
            if cancelled and cancelled.is_set():
                raise BookingError("CANCELLED", "请求服务已停止")
            emit("querying_slots")
            if eligibility:
                eligibility(target)
            data = client.intervals(venue, cancelled=cancelled, progress=emit)
            try:
                emit("checking_slot")
                pick_slot(data, target)
                break
            except BookingError as exc:
                if exc.code not in ("DATE_NOT_OPEN", "NOT_OPEN") or time.monotonic() >= deadline:
                    raise
                emit("waiting_open")
                time.sleep(1)
        plan = client.prepare(target, venue=venue, progress=emit, cancelled=cancelled)
        emit("slot_ready", court=plan["selected"].get("area_name"), message="场次与预约资格校验通过")
        if eligibility:
            eligibility(target)
        if commit:
            result = submit(client, target, plan, solver or CaptchaSolver(),
                            data_root() / "requests", idempotency_key, cancelled, progress=emit, eligibility=eligibility, progress_detail=emit, request_context=request_context)
            return {**result, "flow": flow, "captcha_requested": captcha_requested}
        result = public_plan(plan, target)
        result.update(authentication)
        if captcha_check:
            if cancelled and cancelled.is_set():
                raise BookingError("CANCELLED", "任务已取消，未获取验证码")
            emit("captcha")
            solver = solver or CaptchaSolver()
            for attempt in range(3):
                image = client.captcha(plan["referer"])
                try:
                    code = solver.solve(image)
                    break
                except BookingError as exc:
                    if exc.code != "CAPTCHA_UNREADABLE" or attempt == 2:
                        raise
            result.update(captcha_downloaded=True, captcha_recognized=len(code) == 4,
                          captcha_bytes=len(image), add_order_sent=False)
        return {**result, "flow": flow, "captcha_requested": captcha_requested}
    except BookingError as exc:
        exc.phase = exc.phase or phase
        exc.captcha_requested = captcha_requested
        exc.flow = flow
        raise
    finally:
        client.http.close()


class BookingService:
    def __init__(self, profile=None, key=None, warm=True):
        self.profile = profile
        self.key = key or secrets.token_urlsafe(32)
        self.solver = CaptchaSolver()
        if warm:
            self.solver.warm()
        self.jobs = {}
        self.job_lock = threading.Lock()
        self.booking_lock = threading.Lock()
        self.cancelled = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=2)

    def enqueue(self, payload: dict) -> dict:
        target = make_target(payload)
        fallbacks = payload.get("fallbacks")
        fallback_targets(target, fallbacks)
        policy = payload.get("fallback_on")
        fallback_policy(policy)
        fire_at = parse_fire_at(payload.get("fire_at"))
        # Freeze the intended account as well as the absolute target date.
        ticket = discover_entry(self.profile)
        account = hashlib.sha256(ticket.uid.encode()).hexdigest()
        job_id = uuid4().hex
        with self.job_lock:
            if len(self.jobs) >= 64:
                raise BookingError("QUEUE_FULL", "当前进程的任务数量已满，请重启服务后继续")
            self.jobs[job_id] = {"ok": True, "state": "waiting" if fire_at else "queued", "job_id": job_id}

        def execute():
            try:
                wait_until(fire_at, self.cancelled)
                with self.booking_lock:
                    if self.cancelled.is_set():
                        raise BookingError("CANCELLED", "请求服务已停止")
                    with self.job_lock:
                        self.jobs[job_id]["state"] = "running"
                    def progress(value):
                        with self.job_lock:
                            self.jobs[job_id].update(value)
                    result = run_candidates(target, self.profile, fallbacks=fallbacks, fallback_on=policy, commit=True, solver=self.solver,
                        captcha_first=payload.get("captcha_first", True),
                        watch_seconds=payload.get("watch_seconds", 30),
                        idempotency_key=payload.get("idempotency_key"), expected_account=account,
                        cancelled=self.cancelled, progress=progress)
            except BookingError as exc:
                result = {"state": "failed", **exc.public()}
            except Exception:
                result = {"ok": False, "state": "failed", "code": "INTERNAL_ERROR", "message": "任务执行异常，未重放提交请求"}
            with self.job_lock:
                self.jobs[job_id] = {"job_id": job_id, **result}

        self.pool.submit(execute)
        return self.jobs[job_id].copy()


def handler_for(service: BookingService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log authentication headers or user request bodies.

        def send_json(self, status: int, value: dict):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            supplied = self.headers.get("X-Xmu-Local-Key", "")
            if not hmac.compare_digest(supplied.encode(), service.key.encode()):
                self.send_json(403, {"ok": False, "code": "LOCAL_KEY_REQUIRED"})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path.startswith("/api/jobs/"):
                with service.job_lock:
                    job = service.jobs.get(self.path.removeprefix("/api/jobs/"))
                    job = job.copy() if job else None
                self.send_json(200 if job else 404, job or {"ok": False, "code": "JOB_NOT_FOUND"})
            elif self.path == "/api/status":
                self.send_json(200, {"ok": True, "state": "ready", "booking_method": "POST",
                                     "auth_status": "not_checked", "auth_check": "POST /api/auth/check"})
            else:
                self.send_json(405, {"ok": False, "code": "POST_REQUIRED"})

        def do_POST(self):
            if not self.authorized():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16_384:
                    raise BookingError("INVALID_BODY", "请求体为空或过大")
                if self.headers.get_content_type() != "application/json":
                    raise BookingError("INVALID_BODY", "请使用 application/json")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise BookingError("INVALID_BODY", "请求体必须是 JSON 对象")
                if self.path == "/api/prepare":
                    with service.booking_lock:
                        result = run_candidates(make_target(payload), service.profile, fallbacks=payload.get("fallbacks"), fallback_on=payload.get("fallback_on"),
                            captcha_check=bool(payload.get("captcha_check")), solver=service.solver)
                    self.send_json(200, result)
                elif self.path == "/api/auth/check":
                    with service.booking_lock:
                        result = check_auth(service.profile)
                    self.send_json(200, result)
                elif self.path == "/api/book":
                    self.send_json(202, service.enqueue(payload))
                else:
                    self.send_json(404, {"ok": False, "code": "NOT_FOUND"})
            except BookingError as exc:
                self.send_json(400, exc.public())
            except (ValueError, TypeError):
                self.send_json(400, {"ok": False, "code": "INVALID_BODY"})
            except Exception:
                self.send_json(500, {"ok": False, "code": "INTERNAL_ERROR"})
    return Handler


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="厦大体育 HTTP 预约与自动入口准备")
    parser.add_argument("command", choices=("check", "refresh", "prepare", "book", "serve", "catalog"))
    parser.add_argument("--profile")
    parser.add_argument("--sport")
    parser.add_argument("--venue")
    parser.add_argument("--date")
    parser.add_argument("--time")
    parser.add_argument("--end")
    parser.add_argument("--court")
    parser.add_argument("--remark", default="")
    parser.add_argument("--captcha-check", action="store_true")
    parser.add_argument("--fire-at")
    parser.add_argument("--watch-seconds", type=float, default=30)
    parser.add_argument("--idempotency-key")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--request-interval", type=float, default=1.5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--update-catalog", type=Path)
    parser.add_argument("--fallbacks-json", type=Path, help="备选列表 JSON 文件，最多 8 项")
    parser.add_argument("--fallback-on", help="允许换场的原因码，以逗号分隔")
    args = parser.parse_args()
    try:
        if args.command == "refresh":
            from .recovery import recover_auth
            result = recover_auth(progress=lambda value: print(json.dumps({"progress": value}, ensure_ascii=False), flush=True))
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result.get("ok") else 1
        if args.command == "catalog":
            from .catalog import collect_inventory, update_catalog, write_report, write_structure
            output = args.output or data_root() / "catalog.json"
            snapshot = collect_inventory(output, args.profile, args.request_interval, args.sport, args.venue)
            write_report(output.with_suffix(".md"), snapshot)
            write_structure(output.with_name(output.stem + "-structure.json"), snapshot)
            updated = update_catalog(args.update_catalog, snapshot) if args.update_catalog else 0
            print(json.dumps({"ok": snapshot["complete"], "output": str(output.resolve()),
                              "venues": len(snapshot["venues"]), "requests": snapshot["request_count"],
                              "updated": updated, "errors": snapshot["errors"]}, ensure_ascii=False))
            return 0 if snapshot["complete"] else 1
        if args.command == "serve":
            service = BookingService(args.profile)
            root = data_root()
            root.mkdir(parents=True, exist_ok=True)
            key_file = root / "control.key"
            server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(service))
            key_file.write_text(service.key, encoding="utf-8")
            os.chmod(key_file, 0o600)
            print(json.dumps({"ok": True, "state": "ready", "url": f"http://127.0.0.1:{server.server_port}",
                              "key_file": str(key_file), "submit": "POST /api/book",
                              "preview": "POST /api/prepare"}, ensure_ascii=False), flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
                service.cancelled.set()
                service.pool.shutdown(wait=False, cancel_futures=True)
            return 0
        if args.command == "check":
            result = check_auth(args.profile)
        else:
            target = make_target({key: getattr(args, key) for key in ("sport", "venue", "date", "time", "end", "court", "remark")})
            deadline = parse_fire_at(args.fire_at)
            solver = CaptchaSolver()
            expected_account = None
            if args.command == "book":
                solver.warm()  # Warm model before the seven o'clock deadline.
            if deadline:
                expected_account = hashlib.sha256(discover_entry(args.profile).uid.encode()).hexdigest()
                wait_until(deadline)
            fallbacks = json.loads(args.fallbacks_json.read_text(encoding="utf-8")) if args.fallbacks_json else None
            result = run_candidates(target, args.profile, fallbacks=fallbacks,
                fallback_on=args.fallback_on.split(",") if args.fallback_on else None, commit=args.command == "book",
                captcha_check=args.captcha_check, solver=solver, watch_seconds=args.watch_seconds,
                idempotency_key=args.idempotency_key, expected_account=expected_account)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    except BookingError as exc:
        print(json.dumps(exc.public(), ensure_ascii=False))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
