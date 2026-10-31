"""Command line for XMU Gym.

This is the same backend the page uses; it exists for agents and for users who
prefer a terminal.  Booking and cancellation require ``--yes`` on purpose.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta

from app import config


def _backend():
    from app.backend import Backend
    return Backend()


def _today():
    return datetime.now(config.CHINA).date()


def parse_day(value: str) -> str:
    value = (value or "").strip().lower()
    if value in ("today", "今天"):
        return _today().isoformat()
    if value in ("tomorrow", "明天"):
        return (_today() + timedelta(days=1)).isoformat()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise SystemExit("日期要写 today、tomorrow 或 YYYY-MM-DD")


def _task(date, start, end, sport, venue, fallback=False):
    return {
        "task_id": str(uuid.uuid4()),
        "sport": sport or "健身房",
        "venue": venue or "翔安校区健身房",
        "target_date": date,
        "target_time_start": start,
        "target_time_end": end,
        "allow_time_fallback": bool(fallback),
        "time_fallback_order": "up_then_down",
        "executor": "http",
        "fire_at": datetime.now(config.CHINA).isoformat(),
    }


def cmd_auth(args):
    backend = _backend()
    account = backend.auth()
    print(json.dumps({"ok": True, "account_fingerprint": account[:8] + "…",
                      "account_label": getattr(backend, "last_label", "")}, ensure_ascii=False))
    return 0


def cmd_profiles(args):
    rows = _backend().profiles()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("没有找到可用的微信配置。先在电脑微信打开「厦大体育」并进入一次场馆预约。")
        return 1
    for row in rows:
        print(f"{row['id']}\t{row['label']}\t票据 {row['ticket_age_hours']} 小时")
    return 0


def cmd_slots(args):
    date = parse_day(args.date)
    backend = _backend()
    result = backend.availability({"date": date, "sport": args.sport or "健身房",
                                   "venue": args.venue or "翔安校区健身房"})
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"{result.get('venue')} · {date} · {result.get('state')}")
    for slot in result.get("slots") or []:
        cells = slot.get("cells") or []
        free = sum(int(cell.get("remaining") or 0) for cell in cells)
        state = "可约" if free else "已满"
        print(f"  {slot.get('start')}–{slot.get('end')}  {state}  余 {free}")
    return 0


def cmd_book(args):
    if not args.yes:
        raise SystemExit("预约会真实提交，确认后加 --yes")
    date = parse_day(args.date)
    backend = _backend()
    account = backend.auth()
    task = _task(date, args.start, args.end, args.sport, args.venue, args.fallback)
    result = backend.book(task, account)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not (result.get("ok") and result.get("verified")):
        print("结果未核实：先到微信「我的预约」核对，不要重复提交。", file=sys.stderr)
        return 1
    return 0


def _orders(backend, account):
    result = backend.list_orders(account)
    return result.get("orders") or []


def _pick_order(rows, ref):
    if ref == "next":
        if not rows:
            raise SystemExit("当前没有可操作的预约")
        return rows[0]
    if ":" in ref:
        oid, did = ref.split(":", 1)
    else:
        oid, did = ref, None
    for row in rows:
        if str(row.get("order_id")) == oid and (did is None or str(row.get("details_id")) == did):
            return row
    raise SystemExit("找不到这笔预约；先跑 `python -m app.cli orders`")


def cmd_orders(args):
    backend = _backend()
    rows = _orders(backend, backend.auth())
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("没有进行中的预约。")
        return 0
    for row in rows:
        print(f"{row.get('date')} {row.get('start')}–{row.get('end')}  "
              f"{row.get('venue')} {row.get('court')}  {row.get('order_id')}:{row.get('details_id')}")
    return 0


def cmd_cancel(args):
    if not args.yes:
        raise SystemExit("取消会真实提交，确认后加 --yes")
    backend = _backend()
    account = backend.auth()
    row = _pick_order(_orders(backend, account), args.order)
    result = backend.cancel_order(account, str(row["order_id"]), str(row["details_id"]))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def cmd_daily(args):
    from app.daily import Store
    store = Store()
    if args.action == "show":
        try:
            task, account = store.load()
        except FileNotFoundError:
            print("还没有每日任务。先跑 `python -m app.cli daily set --start 13:30 --end 15:00`。")
            return 1
        print(json.dumps({"task": task, "account": "…" + account[-4:],
                          "enabled": bool(config.load().get("scheduler_enabled"))},
                         ensure_ascii=False, indent=2))
        return 0
    if args.action == "set":
        backend = _backend()
        account = backend.auth()
        task = _task(parse_day(args.date or "tomorrow"), args.start, args.end,
                     args.sport, args.venue, args.fallback)
        store.save(task, account)
        print("每日任务已保存；用 `daily resume` 或页面开关启用。")
        return 0
    value = config.load()
    value["scheduler_enabled"] = args.action == "resume"
    config.save(value)
    print("每日任务已" + ("启用" if value["scheduler_enabled"] else "暂停") + "。")
    return 0


def cmd_qr(args):
    backend = _backend()
    account = backend.auth()
    row = _pick_order(_orders(backend, account), args.order)
    png = backend.entry_qr(str(row["order_id"]), str(row["details_id"]), account)
    root = config.outbox()
    path = root / f"entry-{row['order_id']}.png"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(png)
    print(path)
    print("入场码是凭证：不要发到群里，也不要用完还留在共享目录。", file=sys.stderr)
    return 0


def cmd_scheduler(args):
    from app.scheduler import Scheduler
    scheduler = Scheduler()
    if args.action == "status":
        print(json.dumps(scheduler.status(), ensure_ascii=False, indent=2))
    else:
        scheduler.set_enabled(args.action == "enable")
        print("每日任务已" + ("启用" if args.action == "enable" else "暂停") + "。")
    return 0


def cmd_notify(args):
    from app.notify import latest_message, send_webhook
    message = latest_message()
    if not message:
        print("还没有每日任务结果。")
        return 1
    print(message)
    url = args.webhook or (config.load().get("notify") or {}).get("webhook_url")
    if url:
        send_webhook(url, message)
        print("已发送 webhook。", file=sys.stderr)
    return 0


def cmd_info(args):
    print("规则（来自学校接口和本地闸门）：")
    print("- 每天 07:00 放次日场次；00:00–06:59 不能提交当天预约。")
    print("- 只能约今天/明天，且时段还没开始。")
    print("- 结果不明（SUBMIT_UNKNOWN / 未核实）先查「我的预约」，不要重试。")
    print("- 入场码是凭证，不写日志、不发群。")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="xmu-gym", description="XMU Gym CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("auth", help="核验微信登录态")
    p.set_defaults(func=cmd_auth)

    p = sub.add_parser("profiles", help="列出可选的微信配置")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("slots", help="查场次")
    p.add_argument("--date", default="tomorrow")
    p.add_argument("--sport")
    p.add_argument("--venue")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_slots)

    p = sub.add_parser("book", help="提交预约（需要 --yes）")
    p.add_argument("--date", required=True)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--sport")
    p.add_argument("--venue")
    p.add_argument("--fallback", action="store_true", help="首选满了只往后找")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_book)

    p = sub.add_parser("orders", help="我的预约")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_orders)

    p = sub.add_parser("cancel", help="取消预约（需要 --yes）")
    p.add_argument("--order", required=True, help="next、order_id 或 order_id:details_id")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_cancel)

    p = sub.add_parser("daily", help="每日任务")
    p.add_argument("action", choices=["show", "set", "pause", "resume"])
    p.add_argument("--date", default="tomorrow")
    p.add_argument("--start", default="13:30")
    p.add_argument("--end", default="15:00")
    p.add_argument("--sport")
    p.add_argument("--venue")
    p.add_argument("--fallback", action="store_true")
    p.set_defaults(func=cmd_daily)

    p = sub.add_parser("qr", help="生成入场码 PNG")
    p.add_argument("--order", default="next")
    p.set_defaults(func=cmd_qr)

    p = sub.add_parser("scheduler", help="每日任务开关")
    p.add_argument("action", choices=["status", "enable", "disable"])
    p.set_defaults(func=cmd_scheduler)

    p = sub.add_parser("notify", help="打印/发送最近一次每日结果")
    p.add_argument("--webhook")
    p.set_defaults(func=cmd_notify)

    p = sub.add_parser("info", help="打印真实规则")
    p.set_defaults(func=cmd_info)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ValueError, OSError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
