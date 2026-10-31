"""Worker timings and bounded, redacted upstream exchange evidence."""
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
import time


_current = ContextVar("http_diagnostics", default=None)
MAX_BODY_CHARS = 131072
_PRIVATE = re.compile(r"token|cookie|session|sessid|password|secret|sign|captcha|openid|unionid|authorization|credential|ticket|nonce|^package$|prepay|(?:^|_)(?:uid|uids|phone|mobile|email|user_?name|real_?name|student_id|student_num|card_id|idcard)(?:$|_)|^name$", re.I)


def redact(value, secrets=(), depth=0, public_names=False):
    if depth > 24:
        return "[depth limit]"
    if isinstance(value, dict):
        clean = {}
        for k, v in value.items():
            key = str(k)
            public = (public_names and key == "name") or (key == "verify_phone" and type(v) in (bool, int, str) and v in (True, False, 0, 1, "0", "1"))
            clean[key] = "[redacted]" if _PRIVATE.search(key) and not public else redact(v, secrets, depth + 1, public_names)
        return clean
    if isinstance(value, list):
        return [redact(v, secrets, depth + 1, public_names) for v in value]
    if isinstance(value, str):
        # Some APIs return JSON embedded inside a JSON string.
        try:
            nested = json.loads(value)
        except (ValueError, TypeError):
            nested = None
        if isinstance(nested, (dict, list)):
            clean = redact(nested, secrets, depth + 1, public_names)
            return value if clean == nested else json.dumps(clean, ensure_ascii=False)
        for secret in sorted(set(str(s) for s in secrets if s is not None and str(s)), key=len, reverse=True):
            value = value.replace(secret, "[redacted]")
        value = re.sub(r"https?://[^\s\"'<>]+", "[redacted URL]", value)
        value = re.sub(r"(?i)((?:token|cookie|session\w*|password|sign|captcha|uid|phone)\s*[=:]\s*)[^\s,;&<>]+", r"\1[redacted]", value)
        value = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[redacted phone]", value)
        if "@" in value:
            value = re.sub(r"[\w.+-]{1,254}@[\w.-]{1,254}\.[A-Za-z]{2,63}", "[redacted email]", value)
        return value
    return value


def record_exchange(path, fields, *, response=None, result=None, error=None, secrets=(), elapsed_ms=0, method="POST"):
    current = _current.get()
    if current is None:
        return
    # Unknown endpoints have no established privacy contract.
    api = api_label(path)
    event = {"api": api, "method": method, "elapsed_ms": elapsed_ms,
             "phase": current.phase, "sequence": current.exchange_count + 1}
    if api != "other":
        event["request"] = {key:"[redacted]" for key in fields} if api == "login" else redact(fields, secrets)
        if response is not None:
            event["http_status"] = response.status_code
            event["content_type"] = redact(getattr(response, "headers", {}).get("Content-Type", ""), secrets)
            if result is not None:
                body = redact(result, secrets, public_names=api in {"venue_list", "venue_detail", "intervals"})
            else:
                body = redact(getattr(response, "text", ""), secrets)
            serialized = json.dumps(body, ensure_ascii=False)
            event["response_chars"] = len(serialized)
            event["response_truncated"] = len(serialized) > MAX_BODY_CHARS
            event["response"] = serialized[:MAX_BODY_CHARS] if event["response_truncated"] else body
    if error:
        # Exception strings may contain signed URLs; only keep their category.
        event["error"] = error
    current.exchange_count += 1
    if current.exchange_sink:
        try:
            current.exchange_sink(event)
        except Exception:
            current.exchange_write_errors += 1
_APIS = {
    "/index/index/checklogin": "login",
    "/index/stadium/mysubscribe": "order_list",
    "/index/stadium/subdetails": "order_detail",
    "/index/stadium/getstadiumlist": "venue_list",
    "/index/stadium/getstadiumdetails": "venue_detail",
    "/stadium/interval/getinterval": "intervals",
    "/index/stadium/chooseverify": "eligibility",
    "/index/stadium/getvenueconfig": "venue_config",
    "/index/index/captcha": "captcha_image",
    "/index/stadium/addorder": "submit",
}


def api_label(path):
    return _APIS.get(path.lower(), "other")


class Diagnostics:
    def __init__(self, exchange_sink=None):
        self.started = self.phase_started = time.monotonic()
        self.phase = None
        self.phases = {}
        self.calls = {}
        self.exchange_sink = exchange_sink
        self.exchange_count = 0
        self.exchange_write_errors = 0

    def enter(self):
        return _current.set(self)

    def leave(self, token):
        _current.reset(token)

    def set_phase(self, phase):
        now = time.monotonic()
        if self.phase:
            self.phases[self.phase] = self.phases.get(self.phase, 0) + (now - self.phase_started)
        self.phase, self.phase_started = phase, now

    def snapshot(self):
        phases = dict(self.phases)
        if self.phase:
            phases[self.phase] = phases.get(self.phase, 0) + time.monotonic() - self.phase_started
        return {"worker_elapsed_ms": round((time.monotonic() - self.started) * 1000),
                "phase_ms": {key: round(value * 1000) for key, value in phases.items()},
                "http": self.calls, "exchange_count": self.exchange_count,
                "exchange_write_errors": self.exchange_write_errors}


def phase_changed(phase):
    current = _current.get()
    if current:
        current.set_phase(phase)


@contextmanager
def request_timing(path):
    current = _current.get()
    if current is None:
        yield
        return
    # Only fixed API labels and error categories; no URLs, IDs, parameters,
    # cookies, captcha values, server text or exception strings.
    key = api_label(path)
    entry = current.calls.setdefault(key, {"count": 0, "total_ms": 0, "max_ms": 0, "errors": {}})
    entry["count"] += 1
    started = time.monotonic()
    try:
        yield
    except Exception as exc:
        from .session import BookingError
        import requests
        cause = exc.__cause__ if isinstance(exc, BookingError) and exc.__cause__ else exc
        if isinstance(cause, requests.Timeout):
            reason = "timeout"
        elif isinstance(cause, requests.ConnectionError):
            reason = "connection"
        elif isinstance(cause, ValueError):
            reason = "invalid_json"
        elif isinstance(exc, BookingError):
            reason = exc.code if exc.code in {"SESSION_EXPIRED", "RATE_LIMITED", "SERVER_BUSY", "UNEXPECTED_REDIRECT", "INVALID_RESPONSE", "CAPTCHA_FETCH_FAILED"} else "protocol"
        else:
            reason = "transport"
        entry["errors"][reason] = entry["errors"].get(reason, 0) + 1
        raise
    finally:
        elapsed = round((time.monotonic() - started) * 1000)
        entry["total_ms"] += elapsed
        entry["max_ms"] = max(entry["max_ms"], elapsed)
