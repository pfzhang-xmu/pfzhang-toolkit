"""Read only the XMU H5 login entry from WeChat's current user's cache.

No Cookie database unlock, DPAPI decryption, proxy, certificate or process hook.
The server's checkLogin issues the HTTP executor its own new PHPSESSID.
"""
from __future__ import annotations

import contextlib
import io
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ORIGIN = "https://xdty.xmu.edu.cn"


class BookingError(RuntimeError):
    def __init__(self, code: str, message: str, *, phase=None, server_status=None, details=None):
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.server_status = server_status
        self.details = details

    def public(self) -> dict:
        result = {"ok": False, "code": self.code, "message": str(self)}
        if self.phase:
            result["phase"] = self.phase
        if self.server_status is not None:
            result["server_status"] = self.server_status
        if self.details is not None:
            result["details"] = self.details
        if hasattr(self, "captcha_requested"):
            result["captcha_requested"] = self.captcha_requested
        if hasattr(self, "flow"):
            result["flow"] = self.flow
        if self.code in {"SESSION_EXPIRED", "NO_ENTRY", "INVALID_ENTRY"}:
            result.update(state="needs_login", recovery={
                "action": "reauthorize_in_desktop_wechat",
                "steps": ["在电脑微信打开厦大体育", "立即登录", "阅读并同意协议后授权登录",
                          "进入场馆预约", "重新检测登录状态后执行原日期任务"],
                "automatic_native_login": False})
        elif self.code == "NATIVE_TOKEN_REQUIRED":
            result.update(state="unsupported_auth", recovery={"action": "export_diagnostics",
                          "message": "当前登录票据类型尚未支持，请导出诊断；重复登录不能保证恢复旧 H5 模式"})
        return result


@dataclass(repr=False)
class EntryTicket:
    fields: dict[str, str] = field(repr=False)
    profile: Path = field(repr=False)

    @property
    def uid(self) -> str:
        return self.fields["uid"]


def latest_values(records) -> dict:
    """Respect the latest sequence including tombstones, never resurrect old keys."""
    latest = {}
    for record in records:
        key = record.script_key
        if key not in latest or record.leveldb_seq_number > latest[key].leveldb_seq_number:
            latest[key] = record
    return {key: r.value for key, r in latest.items() if r.is_live}


def parse_entry(value: str, profile: Path, new_api: str | None = None) -> EntryTicket:
    if new_api not in (None, "", "0", "false"):
        raise BookingError("NATIVE_TOKEN_REQUIRED", "该会话启用了原生 h5_token，不能使用旧 H5 换票流程")
    if value.startswith("http"):
        parsed = urlsplit(value)
        if f"{parsed.scheme}://{parsed.netloc}" != ORIGIN:
            raise BookingError("WRONG_ORIGIN", "登录入口不是厦大体育域名")
        value = parsed.query
    fields = {key: values[-1] for key, values in parse_qs(value.lstrip("?"), keep_blank_values=True).items()}
    if not fields.get("uid", "").isdigit() or fields.get("school_id") != "788":
        raise BookingError("INVALID_ENTRY", "缓存中的厦大体育账号/学校信息不完整")
    if not all(fields.get(key) for key in ("token", "sign", "timestamp", "nonce")):
        raise BookingError("INVALID_ENTRY", "缓存中没有完整的小程序登录票据，请重新进入场馆预约")
    fields.update(term_id="", id="")
    return EntryTicket(fields, profile)


def read_entry(profile: Path) -> EntryTicket:
    try:
        from ccl_chromium_reader import ccl_chromium_localstorage
    except ImportError as exc:
        raise BookingError("MISSING_READER", "未安装本地会话读取组件，请安装 requirements.txt") from exc
    try:
        # The parser may log unrelated malformed records. Suppress raw cache text.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with ccl_chromium_localstorage.LocalStoreDb(profile / "Local Storage" / "leveldb") as db:
                records = list(db.iter_records_for_storage_key(ORIGIN, include_deletions=True))
        values = latest_values(records)
    except Exception as exc:
        raise BookingError("SESSION_READ_FAILED", "未能读取该配置中的厦大体育登录入口") from exc
    if not values.get("hrefstr"):
        raise BookingError("NO_ENTRY", "请在已登录微信中打开厦大体育，进入一次场馆预约")
    return parse_entry(values["hrefstr"], profile, values.get("isNewAjaxRes"))


def discover_entry(profile: str | Path | None = None) -> EntryTicket:
    if profile:
        return read_entry(Path(profile).expanduser().resolve())
    root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    root = root / "Tencent" / "xwechat" / "radium" / "web" / "profiles"
    candidates = []
    failures = []
    for path in sorted(root.glob("webview_*")):
        try:
            candidates.append(read_entry(path))
        except BookingError as exc:
            failures.append(exc.code)
    if not candidates:
        if "NATIVE_TOKEN_REQUIRED" in failures:
            raise BookingError("NATIVE_TOKEN_REQUIRED", "该体育缓存使用原生新版 h5_token，不能使用旧 H5 票据流程")
        code = "MISSING_READER" if "MISSING_READER" in failures else "NO_ENTRY"
        raise BookingError(code, "没有找到可用的厦大体育登录缓存，请安装依赖并进入一次场馆预约")
    # Do not choose a different logged-in account merely because its file is newer.
    if len(candidates) != 1 or "NATIVE_TOKEN_REQUIRED" in failures:
        raise BookingError("AMBIGUOUS_PROFILE", "发现多个体育会话，请用 --profile 指定当前微信配置")
    return candidates[0]
