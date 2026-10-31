"""Local booking lifecycle gate; daily availability still comes from the server."""
from datetime import datetime, timedelta, timezone
from .session import BookingError

CHINA = timezone(timedelta(hours=8))

def check_target(target, now=None):
    now = (now or datetime.now(CHINA)).astimezone(CHINA)
    try:
        start = datetime.fromisoformat(f"{target.date}T{target.time}:00+08:00")
        end = datetime.fromisoformat(f"{target.date}T{target.end}:00+08:00")
    except (ValueError, TypeError):
        raise BookingError("INVALID_SLOT", "预约日期或时段无效", phase="local_eligibility")
    if (start.date() - now.date()).days not in (0, 1):
        raise BookingError("DATE_NOT_OPEN", "立即预约只支持今天或明天，其他日期请等待对应开抢时间", phase="local_eligibility")
    if now.hour < 7:
        raise BookingError("NOT_OPEN", "当前为定时预约时段，请安排今天 07:00 后执行", phase="local_eligibility")
    if end <= start:
        raise BookingError("INVALID_SLOT", "预约结束时间必须晚于开始时间", phase="local_eligibility")
    if start <= now:
        raise BookingError("BOOKING_CLOSED", "目标时段已经开始或已结束，请选择尚未开始的时段", phase="local_eligibility")
