"""Read-only demo data for screenshots and for trying the page without WeChat."""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timedelta

import qrcode

from app import config

DEMO_ACCOUNT = hashlib.sha256(b"xmu-gym-demo").hexdigest()
DEMO_LABEL = "演示账号 ····0000"


def _tomorrow() -> str:
    return (datetime.now(config.CHINA).date() + timedelta(days=1)).isoformat()


class DemoStore:
    def __init__(self):
        self._task = {
            "sport": "健身房",
            "venue": "翔安校区健身房",
            "target_time_start": "13:30",
            "target_time_end": "15:00",
            "allow_time_fallback": True,
        }

    def load(self):
        return dict(self._task), DEMO_ACCOUNT

    def save(self, task, account):
        self._task = {key: task[key] for key in (
            "sport", "venue", "target_time_start", "target_time_end", "allow_time_fallback")}

    def mark(self, target_date):
        return None


class DemoBackend:
    last_label = DEMO_LABEL

    def auth(self):
        return DEMO_ACCOUNT

    def profiles(self):
        return [{"id": "webview_demo", "label": DEMO_LABEL, "ticket_age_hours": 0.2}]

    def select_profile(self, value):
        return None

    def availability(self, args):
        date = args.get("date") or _tomorrow()
        return {
            "ok": True,
            "state": "availability_ready",
            "date": date,
            "sport": args.get("sport") or "健身房",
            "venue": args.get("venue") or "翔安校区健身房",
            "cached": False,
            "fetched_at": datetime.now(config.CHINA).isoformat(),
            "account_fingerprint": DEMO_ACCOUNT,
            "slots": [
                {"start": "13:30", "end": "15:00", "cells": [
                    {"court": "01", "admin_locked": False, "status": "available",
                     "reason": "", "remaining": 3, "capacity": 5},
                    {"court": "02", "admin_locked": False, "status": "available",
                     "reason": "", "remaining": 1, "capacity": 5},
                ]},
                {"start": "15:00", "end": "16:30", "cells": [
                    {"court": "01", "admin_locked": False, "status": "unavailable",
                     "reason": "已满", "remaining": 0, "capacity": 5},
                ]},
                {"start": "16:30", "end": "18:00", "cells": [
                    {"court": "01", "admin_locked": False, "status": "available",
                     "reason": "", "remaining": 2, "capacity": 5},
                ]},
            ],
        }

    def list_orders(self, account):
        date = _tomorrow()
        return {
            "ok": True,
            "orders": [{
                "date": date,
                "start": "13:30",
                "end": "15:00",
                "sport": "健身房",
                "venue": "翔安校区健身房",
                "court": "01",
                "order_id": "10001",
                "details_id": "20001",
                "can_cancel": True,
            }],
            "account_fingerprint": account,
            "account_label": DEMO_LABEL,
            "cached": False,
            "fetched_at": datetime.now(config.CHINA).isoformat(),
        }

    def book(self, *args, **kwargs):
        raise ValueError("演示模式不会提交真实预约")

    def cancel_order(self, *args, **kwargs):
        raise ValueError("演示模式不会取消真实预约")

    def entry_qr(self, order_id, details_id, account):
        if account != DEMO_ACCOUNT:
            raise ValueError("账号已改变，请重新检查")
        image = qrcode.make(f"XMU-GYM-DEMO|{order_id}|{details_id}")
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
