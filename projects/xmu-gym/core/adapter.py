"""WeChat profile discovery and the one-shot daily booking adapter."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import platform as platform_layer
from core.booking.app import check_auth, data_root, run_candidates
from core.booking.availability import snapshot
from core.booking.client import CaptchaSolver, Client, Target
from core.booking.eligibility import check_target
from core.booking.session import BookingError, read_entry

CHINA = timezone(timedelta(hours=8))


def daily_target(now):
    if now.utcoffset() is None:
        raise ValueError("timezone required")
    day = (now.astimezone(CHINA).date() + timedelta(days=1)).isoformat()
    return Target("健身房", "翔安校区健身房", day, "13:30", end="15:00")


def ordered_fallbacks(data, target):
    rows = snapshot(data, target.date, "")["slots"]
    times = {(row["start"], row["end"]) for row in rows
             if row["start"] >= "15:00" and row["end"] > row["start"]}
    return [{"time": start, "end": end} for start, end in sorted(times)[:8]]


def _read_entries(paths, strict):
    entries = []
    for path in sorted(paths):
        try:
            entries.append(read_entry(path))
        except BookingError as exc:
            # A profile without the XMU mini program is simply not a candidate.
            if exc.code in ("NO_ENTRY", "INVALID_ENTRY", "WRONG_ORIGIN"):
                continue
            # An active profile that cannot be read is ambiguous: stop instead
            # of silently choosing another account.
            if strict:
                raise BookingError(exc.code, "Unable to read active profile") from None
    return entries


def _root(root=None) -> Path:
    return Path(root or platform_layer.wechat_profiles_root()).expanduser().resolve()


def _active(root: Path) -> set[Path]:
    try:
        return platform_layer.active_profiles(root)
    except RuntimeError:
        raise BookingError("PROFILE_DISCOVERY_FAILED", "Unable to identify active profiles") from None


def _all_entries(root: Path):
    active = _read_entries(_active(root), strict=True)
    if active:
        return active
    # WeChat may be closed while the ticket is still on disk (observed: about
    # 60 hours of validity).  Fall back to a disk scan instead of giving up.
    on_disk = {path for path in root.glob("webview_*") if path.is_dir()}
    return _read_entries(on_disk, strict=False)


def _match_selected(root: Path, selected: str):
    value = selected.strip()
    if not value:
        return None
    # A directory name or an absolute path is the most direct choice.
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        candidate = candidate.resolve()
    elif value.startswith("webview_"):
        candidate = (root / value).resolve()
    else:
        candidate = None
    if candidate is not None:
        try:
            candidate.relative_to(root)
        except ValueError:
            raise BookingError("PROFILE_NOT_FOUND", "所选微信配置不在当前微信目录中") from None
        if candidate.parent != root or not candidate.name.startswith("webview_"):
            raise BookingError("PROFILE_NOT_FOUND", "所选微信配置无效")
        try:
            return read_entry(candidate)
        except BookingError as exc:
            raise BookingError(exc.code, "所选微信配置读不到厦大体育登录态") from None
    # Otherwise treat the selection as an account fingerprint.
    if re.fullmatch(r"[0-9a-f]{64}", value):
        for entry in _all_entries(root):
            if hashlib.sha256(entry.uid.encode()).hexdigest() == value:
                return entry
        raise BookingError("PROFILE_NOT_FOUND", "所选账号已不在微信配置中，请重新选择")
    raise BookingError("PROFILE_NOT_FOUND", "微信配置标识无效")


def find_profile(root=None, selected=None):
    root = _root(root)
    if selected:
        entry = _match_selected(root, selected)
        if entry is not None:
            return entry
    entries = _all_entries(root)
    if len(entries) != 1:
        raise BookingError("AMBIGUOUS_PROFILE" if entries else "NO_ENTRY",
                           "multiple usable profiles" if entries else "no usable profile")
    return entries[0]


def _label(entry) -> str:
    uid = str(entry.uid)
    return "账号 ····" + uid[-4:] if len(uid) >= 4 else "账号 ····"


def list_profiles(root=None):
    """Return selectable profiles without exposing the full WeChat UID."""
    root = _root(root)
    rows = []
    for entry in _all_entries(root):
        rows.append({
            "id": entry.profile.name,
            "label": _label(entry),
            "ticket_age_hours": round(ticket_age_hours(entry) or 0, 1),
        })
    rows.sort(key=lambda row: row["label"])
    return rows


def ticket_age_hours(entry):
    """Hours since the ticket was issued; None when the timestamp is unusable."""
    try:
        stamp = int(entry.fields.get("timestamp") or 0)
    except (TypeError, ValueError, AttributeError):
        return None
    if stamp > 10 ** 12:
        stamp //= 1000
    if stamp <= 0:
        return None
    return (datetime.now(CHINA) - datetime.fromtimestamp(stamp, CHINA)).total_seconds() / 3600


def guard_unresolved(target, account, root):
    for path in (root / "requests").glob("*.json"):
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(previous, dict):
                raise ValueError("invalid journal")
        except (OSError, ValueError):
            raise BookingError("RESULT_UNCONFIRMED", "Unreadable journal; check orders")
        if previous.get("account_fingerprint") not in (None, account):
            continue
        original = previous.get("target") or {}
        if not isinstance(original, dict):
            raise BookingError("RESULT_UNCONFIRMED", "Invalid journal; check orders")
        if any(original.get(field) != getattr(target, field) for field in ("sport", "venue", "date")):
            continue
        if previous.get("state") in ("unknown", "started") or (
                previous.get("state") == "submitted" and not previous.get("verified")):
            raise BookingError("RESULT_UNCONFIRMED", "Prior order is unresolved; check orders")


def execute_booking(now, expected_account, selected=None):
    entry = find_profile(selected=selected) if selected else find_profile()
    account = hashlib.sha256(entry.uid.encode()).hexdigest()
    if account != expected_account:
        raise BookingError("ACCOUNT_CHANGED", "Account does not match the pinned fingerprint")
    auth = check_auth(entry.profile)
    if not auth.get("ok") or not auth.get("account_checked") or auth.get("account_fingerprint") != account:
        raise BookingError("AUTH_FAILED", "Account authentication could not be verified")
    target = daily_target(now)

    def fallbacks():
        fresh = read_entry(entry.profile)
        if hashlib.sha256(fresh.uid.encode()).hexdigest() != account:
            raise BookingError("ACCOUNT_CHANGED", "Account changed before fallback lookup")
        client = Client(fresh)
        try:
            auth_value = client.login()
            if not auth_value.get("account_checked"):
                raise BookingError("AUTH_FAILED", "Fallback account not verified")
            return ordered_fallbacks(client.intervals(client.resolve_venue(target)), target)
        finally:
            client.http.close()

    def before_commit(client, candidate):
        guard_unresolved(candidate, account, data_root())

    try:
        return run_candidates(
            target, profile=entry.profile, fallbacks=fallbacks,
            commit=True, captcha_first=True, solver=CaptchaSolver(),
            watch_seconds=5, eligibility=check_target,
            idempotency_key="xiang-an-gym:" + target.date,
            expected_account=account, before_commit=before_commit,
        )
    except BookingError:
        raise
    except Exception as exc:
        raise BookingError("SUBMIT_UNKNOWN", "Executor interrupted; check orders before retrying") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="XMU Gym adapter (no scheduler)")
    parser.add_argument("action", choices=["check", "book"])
    parser.add_argument("--account", help="SHA-256 account fingerprint returned by check")
    args = parser.parse_args(argv)
    if args.action == "book" and not re.fullmatch(r"[0-9a-f]{64}", args.account or ""):
        parser.error("book requires --account with the fingerprint from check")
    os.environ.setdefault("XMU_GYM_DATA", str(Path(__file__).resolve().parent.parent / "state"))
    try:
        if args.action == "check":
            entry = find_profile()
            account = hashlib.sha256(entry.uid.encode()).hexdigest()
            auth = check_auth(entry.profile)
            if not auth.get("ok") or not auth.get("account_checked") or auth.get("account_fingerprint") != account:
                raise BookingError("AUTH_FAILED", "Authentication failed")
            result = {
                "ok": True,
                "state": "ready",
                "account_fingerprint": account,
                "account_label": _label(entry),
                "ticket_age_hours": round(ticket_age_hours(entry) or 0, 1),
            }
        else:
            value = execute_booking(datetime.now(CHINA), args.account)
            result = {key: value[key] for key in ("ok", "state", "code", "verified", "request_ref") if key in value}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("ok") else 1
    except Exception as exc:
        code = exc.code if isinstance(exc, BookingError) and re.fullmatch(r"[A-Z_]+", exc.code) else "INTERNAL_ERROR"
        print(json.dumps({"ok": False, "code": code,
                          "state": "unknown" if code in ("SUBMIT_UNKNOWN", "RESULT_UNCONFIRMED") else "failed"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
