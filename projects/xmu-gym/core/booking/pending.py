"""Reconcile one durable submission, with no path into booking code."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import re

from .client import Client, Target
from .catalog import write_json
from .session import BookingError, discover_entry


def load_record(root, request_ref):
    if not isinstance(request_ref, str) or not re.fullmatch(r"[0-9a-f]{64}", request_ref):
        raise BookingError("INVALID_REQUEST_REF", "原请求标识不合法")
    directory = root / "requests"
    path = directory / (request_ref + ".json")
    if path.resolve().parent != directory.resolve():
        raise BookingError("INVALID_REQUEST_REF", "原请求记录路径不合法")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BookingError("REQUEST_RECORD_UNAVAILABLE", "原请求记录缺失或损坏，请在小程序核对") from exc
    if not isinstance(record, dict) or record.get("state") not in {"unknown", "started", "submitted"}:
        raise BookingError("REQUEST_NOT_PENDING", "该记录没有待确认的预约提交")
    account = record.get("account_fingerprint")
    actual = record.get("submitted_target")
    if not isinstance(account, str) or not re.fullmatch(r"[0-9a-f]{64}", account) or not isinstance(actual, dict):
        raise BookingError("SUBMISSION_SNAPSHOT_MISSING", "旧记录缺少原账号或实际提交目标，请在小程序核对")
    try:
        target = Target(**actual)
    except (TypeError, BookingError) as exc:
        raise BookingError("SUBMISSION_SNAPSHOT_MISSING", "原提交场次快照不完整，请在小程序核对") from exc
    if not target.court or not target.court.strip() or not target.end:
        raise BookingError("SUBMISSION_SNAPSHOT_MISSING", "原记录缺少实际场地或完整时段，请在小程序核对")
    return path, record, account, target


def recheck(root, request_ref, cancelled, progress):
    path, record, account, target = load_record(root, request_ref)
    def emit(phase):
        progress({"phase":phase,"request_ref":request_ref,"target":asdict(target),"read_only":True})
    emit("reading_entry")
    if cancelled.is_set():
        raise BookingError("CANCELLED", "已停止再次查单，原预约结果仍待确认")
    try:
        entry = discover_entry()
    except BookingError as exc:
        exc.details={**(exc.details or {}),"original_account":account,"target":asdict(target),"request_ref":request_ref}
        raise
    if hashlib.sha256(entry.uid.encode()).hexdigest() != account:
        raise BookingError("ACCOUNT_CHANGED", "当前账号与原提交账号不一致，未查询其他账号")
    client = Client(entry)
    try:
        emit("authenticating")
        auth = client.login()
        if not auth.get("account_checked"):
            raise BookingError("HTTP_AUTH_UNVERIFIED", "无法完成原提交账号核验，原预约仍待确认")
        if cancelled.is_set():
            raise BookingError("CANCELLED", "已停止再次查单，原预约结果仍待确认")
        emit("rechecking_orders")
        existing = client.find_existing(target)
        checked_at = datetime.now(timezone.utc).isoformat()
        # Re-read before replacing so no concurrent/local modification can change
        # the account or actual target underneath a successful lookup.
        _, latest, latest_account, latest_target = load_record(root, request_ref)
        if latest_account != account or latest_target != target:
            raise BookingError("SUBMISSION_SNAPSHOT_CHANGED", "原提交记录已变化，未回写查询结果")
        latest.update(last_recheck_at=checked_at)
        found = bool(existing and existing.get("verified") is True and existing.get("order_id") is not None)
        if found:
            latest.update(ok=True, state="submitted", reconciled=True, **existing)
            latest.pop("code", None)
            latest["message"] = "再次查单已确认原预约，未重复下单"
        write_json(path, latest)
        return {"ok":found,"state":"order_found" if found else "unknown",
                "code":"http_order_rechecked" if found else "RESULT_UNCONFIRMED",
                "message":"再次查单已确认原预约，未重复下单" if found else "仍未查到匹配预约，原结果保持待确认，未重复下单",
                "request_ref":request_ref,"target":asdict(target),"account_fingerprint":account,
                "checked_at":checked_at,"read_only":True,"booking_submitted":False,
                **(existing if found else {"verified":False})}
    except BookingError as exc:
        exc.details = {**(exc.details or {}),"original_account":account,"target":asdict(target),"request_ref":request_ref}
        raise
    finally:
        client.http.close()
