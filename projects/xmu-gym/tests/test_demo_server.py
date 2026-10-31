"""End-to-end loopback smoke test for demo mode."""
from __future__ import annotations

import base64
import json
import threading
import urllib.error
import urllib.request

from app.demo import DEMO_ACCOUNT, DemoBackend, DemoStore
from app.server import Bridge, make_server


def _request(origin, payload):
    request = urllib.request.Request(
        origin + "/api/invoke",
        data=json.dumps(payload).encode(),
        headers={"Origin": origin, "Content-Type": "application/json", "X-XMU-Bridge": "1"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_demo_server_serves_page_and_read_only_data():
    bridge = Bridge(DemoBackend(), daily_store=DemoStore(), demo=True)
    http = make_server(bridge, 0)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{http.server_port}"
    try:
        with urllib.request.urlopen(origin + "/") as response:
            page = response.read().decode()
            assert response.status == 200
            assert "XMU <em>Gym</em>" in page

        status, body = _request(origin, {"command": "get_app_info"})
        assert status == 200 and body["result"]["demo"] is True

        status, body = _request(origin, {"command": "list_profiles"})
        assert status == 200 and body["result"][0]["label"].startswith("演示账号")

        status, body = _request(origin, {"command": "start_http_run", "args": {"until": "auth"}})
        assert status == 200 and body["result"]["account_fingerprint"] == DEMO_ACCOUNT

        status, body = _request(origin, {
            "command": "get_venue_availability",
            "args": {"date": "2026-11-01", "sport": "健身房", "venue": "翔安校区健身房"},
        })
        assert status == 200 and body["result"]["slots"]

        status, body = _request(origin, {
            "command": "get_entry_qr",
            "args": {"selected": True, "accountFingerprint": DEMO_ACCOUNT,
                     "orderId": "10001", "detailsId": "20001"},
        })
        assert status == 200
        assert base64.b64decode(body["result"]["png_base64"]).startswith(b"\x89PNG")

        status, body = _request(origin, {
            "command": "start_http_run",
            "args": {"until": "submit"},
        })
        assert status == 400 and "演示" in body["error"]
    finally:
        http.shutdown()
        http.server_close()
        thread.join()
