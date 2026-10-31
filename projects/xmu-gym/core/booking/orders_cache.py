"""Account-scoped, normalized order snapshots and completed-order detail index."""
import json
from datetime import datetime, timedelta, timezone
from .catalog import write_json

TTL_SECONDS = 1800


def ttl_seconds(now):
    beijing = now.astimezone(timezone(timedelta(hours=8)))
    minute = beijing.hour * 60 + beijing.minute
    return 300 if 420 <= minute < 435 else TTL_SECONDS


def load(root, account):
    path = root / "orders-cache" / (account + ".json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or data.get("account") != account:
            raise ValueError()
        ended = data.get("ended", {})
        if not isinstance(ended, dict):
            raise ValueError()
        data["ended"] = {str(k): v for k, v in ended.items() if str(k).isdigit() and isinstance(v, str)}
        return path, data
    except (OSError, ValueError, TypeError):
        return path, {"version": 1, "account": account, "ended": {}}


def fresh(data, now):
    try:
        snapshot = data["snapshot"]
        stamp = datetime.fromisoformat(snapshot["fetched_at"])
        age = (now - stamp).total_seconds()
        if not 0 <= age < ttl_seconds(now) or not isinstance(snapshot["orders"], list):
            return None
        fields = ("order_id", "details_id", "venue", "sport", "court", "date", "start", "end")
        if any(not isinstance(row, dict) or any(not isinstance(row.get(k), str) for k in fields)
               or not isinstance(row.get("can_cancel"), bool) for row in snapshot["orders"]):
            return None
        return snapshot
    except (KeyError, ValueError, TypeError):
        return None


def save(path, data):
    data["ended"] = dict(sorted(data["ended"].items(), key=lambda row: row[1], reverse=True)[:2048])
    try:
        write_json(path, data)
        return True
    except OSError:
        return False
