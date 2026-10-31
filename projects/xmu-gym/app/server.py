"""Local-only HTTP bridge for the XMU Gym page.

The server binds 127.0.0.1, rejects cross-origin POSTs and never exposes the
booking core to the network.  All real side effects still go through
``app.backend.Backend``.
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from app import config


class PaymentRequired(Exception):
    code = "PAYMENT_REQUIRED"


def _scheduler_string(enabled: bool, has_task: bool) -> str:
    if enabled and has_task:
        return "enabled"
    if has_task:
        return "paused"
    return "not_configured"


class Bridge:
    def __init__(self, backend, daily_store=None, scheduler=None, demo=False, cron_path=None):
        self.backend = backend
        self.daily_store = daily_store
        self.scheduler = scheduler
        self.demo = bool(demo)
        self.lock = threading.RLock()
        self.cancel_lock = threading.Lock()
        self.task = None
        self.account = None
        self.account_label = ""
        self.running = False
        self.events = []
        self.prefs = {"favorite_sport_ids": [], "venue_slot_prefs": {}}
        self.initial_form = None
        try:
            if self.daily_store is None:
                from app.daily import Store
                self.daily_store = Store()
            preference, _ = self.daily_store.load()
            from uuid import uuid4
            self.initial_form = {key: preference[key] for key in (
                "sport", "venue", "target_time_start", "target_time_end", "allow_time_fallback")}
            self.initial_form.update(task_id=str(uuid4()), fire_at="")
        except (OSError, ValueError, KeyError, TypeError):
            self.initial_form = None

    # ---------------------------------------------------------------- helpers
    def _store(self):
        if self.daily_store is None:
            from app.daily import Store
            self.daily_store = Store()
        return self.daily_store

    def scheduler_status(self) -> str:
        if self.scheduler is not None:
            return self.scheduler.status_string()
        try:
            has_task = True
            try:
                self._store().load()
            except FileNotFoundError:
                has_task = False
            return _scheduler_string(bool(config.load().get("scheduler_enabled")), has_task)
        except (OSError, ValueError, KeyError, TypeError):
            return "unknown"

    def _scheduler_detail(self) -> dict:
        if self.scheduler is not None:
            return self.scheduler.status()
        return {"enabled": False, "state": "disabled", "next_run": None, "last": None}

    # ---------------------------------------------------------------- invoke
    def invoke(self, command, args):
        if command == "cancel_my_order":
            if not self.cancel_lock.acquire(blocking=False):
                raise ValueError("已有取消操作正在进行")
            try:
                return self._invoke(command, args)
            finally:
                self.cancel_lock.release()
        return self._invoke(command, args)

    def _invoke(self, command, args):
        with self.lock:
            if command == "get_scheduler_status":
                return self.scheduler_status()
            if command == "get_scheduler_detail":
                return self._scheduler_detail()
            if command == "set_scheduler_enabled":
                if type(args.get("enabled")) is not bool:
                    raise ValueError("每日任务开关无效")
                value = config.load()
                value["scheduler_enabled"] = args["enabled"]
                config.save(value)
                if self.scheduler is not None:
                    self.scheduler.set_enabled(args["enabled"])
                return {"enabled": args["enabled"], "scheduler_status": self.scheduler_status()}
            if command == "save_task":
                if self.running:
                    raise ValueError("运行期间不可修改预约")
                self.task = dict(args["task"])
                return self.task
            if command == "read_task_raw":
                return self.task if self.task is not None else self.initial_form
            if command == "get_daily_schedule":
                try:
                    task, _ = self._store().load()
                except FileNotFoundError:
                    return None
                status = self.scheduler_status()
                return {"task": task, "scheduler_installed": status == "enabled",
                        "scheduler_status": status, "fallback_policy": "later_only",
                        "scheduler": self._scheduler_detail()}
            if command == "save_daily_schedule":
                if self.running:
                    raise ValueError("运行期间不可修改预约")
                if not self.account:
                    raise ValueError("请先检查账号")
                if not self.task:
                    raise ValueError("请先保存预约选项")
                if self.backend.auth() != self.account:
                    self.account = None
                    raise ValueError("账号已改变，请重新检查")
                task = dict(self.task)
                if "allow_time_fallback" in args:
                    if type(args["allow_time_fallback"]) is not bool:
                        raise ValueError("备选时段配置无效")
                    task["allow_time_fallback"] = args["allow_time_fallback"]
                self._store().save(task, self.account)
                if type(args.get("enabled")) is bool:
                    value = config.load()
                    value["scheduler_enabled"] = args["enabled"]
                    config.save(value)
                    if self.scheduler is not None:
                        self.scheduler.set_enabled(args["enabled"])
                status = self.scheduler_status()
                return {"saved": True, "scheduler_installed": status == "enabled",
                        "scheduler_status": status, "fallback_policy": "later_only",
                        "scheduler": self._scheduler_detail()}
            if command == "get_shell_prefs":
                return self.prefs
            if command == "save_shell_prefs":
                self.prefs = dict(args["prefs"])
                return None
            if command == "get_app_info":
                return {
                    "demo": self.demo,
                    "platform": sys.platform,
                    "version": "0.1.0",
                    "state_root": str(config.STATE_ROOT),
                    "scheduler": self._scheduler_detail(),
                }
            if command == "get_live_status":
                return {"task": self.task,
                        "last_event": self.events[-1] if self.events else None,
                        "recent_events": self.events, "armed": False,
                        "running": self.running, "fire_at": None,
                        "scheduler": self._scheduler_detail()}
            if command == "list_recent_events":
                return list(self.events)
            if command == "list_profiles":
                return self.backend.profiles()
            if command == "select_profile":
                value = args.get("id")
                if not isinstance(value, str):
                    raise ValueError("微信配置标识无效")
                self.backend.select_profile(value)
                self.account = None
                self.account_label = ""
                return {"selected": value}
            if command == "get_my_orders":
                if not self.account:
                    raise ValueError("请先检查账号")
                result = self.backend.list_orders(self.account)
                if isinstance(result, dict):
                    result.setdefault("account_label", self.account_label)
                return result
            if command == "get_entry_qr":
                if not self.account:
                    raise ValueError("请先检查账号")
                if args.get("selected") is not True or args.get("accountFingerprint") != self.account:
                    raise ValueError("请选择预约并确认账号")
                png = self.backend.entry_qr(args.get("orderId"), args.get("detailsId"), self.account)
                if not isinstance(png, bytes) or not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("入场码不可用")
                import base64
                return {"png_base64": base64.b64encode(png).decode("ascii")}
            if command == "cancel_my_order":
                if not self.account:
                    raise ValueError("请先检查账号")
                if args.get("confirmed") is not True:
                    raise ValueError("请重新确认取消，必要时刷新页面")
                if args.get("accountFingerprint") != self.account:
                    return {"ok": False, "code": "ACCOUNT_CHANGED",
                            "message": "账号已改变，请刷新订单后重新确认"}
                return self.backend.cancel_order(self.account, args.get("orderId"), args.get("detailsId"))
            if command == "get_venue_availability":
                return self.backend.availability(args)
            if command == "start_http_run":
                if args.get("until") == "auth":
                    self.account = None
                    self.account_label = ""
                    self.account = self.backend.auth()
                    self.account_label = getattr(self.backend, "last_label", "") or ""
                    self.events.append({"ts": __import__("datetime").datetime.now().isoformat(),
                                        "task_id": (self.task or {}).get("task_id", ""),
                                        "result": "ok",
                                        "http": {"phase": "auth", "message": "账号已核验（真实服务器）"}})
                    return {"account_fingerprint": self.account, "account_label": self.account_label}
                if args.get("until") != "submit":
                    raise ValueError("不支持此操作")
                if self.demo:
                    raise ValueError("演示模式不会提交真实预约")
                if not self.account:
                    raise ValueError("请先检查账号")
                if not self.task:
                    raise ValueError("请先保存预约选项")
                if self.task.get("allow_time_fallback"):
                    raise ValueError("本地适配尚不支持备选时段，请关闭备选")
                self.running = True
                try:
                    result = self.backend.book(self.task, self.account)
                    if result.get("code") == "PAYMENT_REQUIRED":
                        raise PaymentRequired()
                    if not result.get("ok") or not result.get("verified"):
                        raise ValueError("预约未核实：" + str(result.get("code") or result.get("state")
                                                             or "请检查我的预约，勿重复提交"))
                    self.events.append({"ts": __import__("datetime").datetime.now().isoformat(),
                                        "task_id": self.task.get("task_id", ""), "action": "done",
                                        "result": "ok",
                                        "http": {"phase": "done", "message": "预约已核实", "verified": True}})
                    return {"ok": True, "verified": True}
                finally:
                    self.running = False
            if command == "set_run_badge":
                return None
            raise ValueError("本地适配不支持此功能：" + str(command))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        host = f"127.0.0.1:{self.server.server_port}"
        if (self.headers.get("Host") != host
                or self.headers.get("Origin") != "http://" + host
                or self.headers.get("X-XMU-Bridge") != "1"
                or self.path != "/api/invoke"
                or self.headers.get_content_type() != "application/json"):
            return self.send_json(403, {"error": "forbidden"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 65536:
                raise ValueError("invalid request size")
            value = json.loads(self.rfile.read(length))
            result = self.server.bridge.invoke(value["command"], value.get("args") or {})
            self.send_json(200, {"result": result})
        except Exception as exc:
            import re
            code = getattr(exc, "code", "")
            known = {
                "已有预约尝试，请先核实订单，勿重复提交", "请先检查账号", "请先保存预约选项",
                "本地适配尚不支持备选时段，请关闭备选", "运行期间不可修改预约",
                "账号验证失败，请检查微信登录", "账号已改变，请重新检查", "账号验证失败，请重新检查",
                "每日预约暂不支持指定场地", "场馆或项目无效", "时段无效", "备选时段配置无效",
                "请重新确认取消，必要时刷新页面", "已有取消操作正在进行",
                "已有取消尝试，请到小程序核对，勿重复取消", "取消预约标识无效",
                "微信配置标识无效", "每日任务开关无效", "演示模式不会提交真实预约",
            }
            if isinstance(exc, ValueError) and str(exc) in known:
                message = str(exc)
            elif code == "PAYMENT_REQUIRED":
                message = "预约可能需要支付，工具不会自动支付；请到官方小程序核对订单，勿重复提交"
            elif isinstance(code, str) and re.fullmatch(r"[A-Z_]{2,60}", code):
                message = f"{code}：操作失败；若提交结果不明，请先到微信检查我的预约，勿重试"
            else:
                message = "操作失败或结果未确认；如已提交，请到微信检查我的预约，勿重试"
            self.send_json(400, {"error": message})

    def do_GET(self):
        host = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != host:
            return self.send_json(403, {"error": "forbidden"})
        path = urlsplit(self.path).path
        root = config.WEB_ROOT.resolve()
        file = (root / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            return self.send_json(404, {"error": "not found"})
        content = file.read_bytes()
        mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
                ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png",
                ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
                ".ico": "image/x-icon", ".woff2": "font/woff2"}.get(file.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(content)


def make_server(bridge, port=0):
    http = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    http.bridge = bridge
    return http


def main(argv=None):
    import argparse
    import webbrowser
    from app import config as cfg
    from app.backend import Backend
    from app.daily import Store
    from app.scheduler import Scheduler
    from core.platform import KeepAwake

    parser = argparse.ArgumentParser(description="XMU Gym local page")
    parser.add_argument("--port", type=int, default=None, help="端口；默认读取 state/config.json（1420）")
    parser.add_argument("--demo", action="store_true", help="演示模式：假数据，不读微信，不提交")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-scheduler", action="store_true")
    parser.add_argument("--no-keepawake", action="store_true")
    args = parser.parse_args(argv)

    if args.demo:
        from app.demo import DemoBackend, DemoStore
        backend, store = DemoBackend(), DemoStore()
    else:
        backend, store = Backend(), Store()

    scheduler = None if args.no_scheduler else Scheduler(store=store)
    bridge = Bridge(backend, daily_store=store, scheduler=scheduler, demo=args.demo)
    port = args.port if args.port is not None else int(cfg.load().get("port") or cfg.DEFAULT_PORT)
    http = make_server(bridge, port)
    if scheduler is not None:
        scheduler.start()
    keep = KeepAwake()
    if not args.no_keepawake and cfg.load().get("keep_awake", True):
        keep.start()
    url = f"http://127.0.0.1:{http.server_port}/"
    print(f"XMU Gym：{url}  (Ctrl-C 停止)", flush=True)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if scheduler is not None:
            scheduler.stop()
        keep.stop()
        http.server_close()


if __name__ == "__main__":
    main()
