"""Read-only regression harness for the 2026-10-30 07:00 failure.

A fake server reproduces the observed sequence: login, login, then an 8s
order-lookup timeout. It must fail against the old implementation and pass
once pre-submit retries and bounded order lookup are fixed. No real network.
"""
import importlib.util
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT))


class SlowSchoolHandler(BaseHTTPRequestHandler):
    """Only read endpoints. addOrder must never be reached in the old path."""
    calls = []
    order_delay = 0.0
    active_order = False

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        self.rfile.read(length)
        path = self.path
        type(self).calls.append(path)
        if path.endswith('/addOrder'):
            self.send_response(500); self.end_headers(); self.wfile.write(b'{"status":0,"info":"must not submit"}')
            return
        if path.endswith('/checkLogin'):
            body = {'status': 1, 'data': {}}
        elif path.endswith('/mySubscribe'):
            # Enough historical rows to exercise the old detail fan-out.
            statuses = (1,) if type(self).active_order else (2, 3, 4)
            body = {'status': 1, 'data': [
                {'order_id': i, 'uid': 'u', 'stadium_name': '翔安校区健身房',
                 'project_name': '健身房', 'audit_status': status}
                for i, status in enumerate(statuses, 1)]}
        elif path.endswith('/subDetails'):
            if type(self).order_delay:
                import time
                time.sleep(type(self).order_delay)
            body = {'status': 1, 'data': {'uid': 'u', 'audit_status': 1, 'details': [
                {'id': 'd', 'status': '1',
                 'date': (datetime.now(timezone(timedelta(hours=8))).date() + timedelta(days=1)).isoformat(),
                 'interval_time': '13:30-15:00', 'area_name': 'A'}]}}

        else:
            body = {'status': 1, 'data': {}}
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        if path.endswith('/checkLogin'):
            self.send_header('Set-Cookie', 'PHPSESSID=test; Path=/')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class MorningFailureTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), SlowSchoolHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        SlowSchoolHandler.calls = []
        SlowSchoolHandler.order_delay = 0.0
        SlowSchoolHandler.active_order = False

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)

    def test_slow_historical_details_are_not_read_for_future_target(self):
        """Regression for 07:00:04–07:00:15: no detail fan-out, no timeout."""
        from core.booking import client as c
        entry = type('Entry', (), {'uid': 'u', 'profile': 'p', 'fields': {'x': '1'}})()
        cl = c.Client(entry)
        target = c.Target('健身房', '翔安校区健身房',
                          (datetime.now(timezone(timedelta(hours=8))).date() + timedelta(days=1)).isoformat(),
                          '13:30', end='15:00')
        with patch.object(c, 'ORIGIN', self.origin), patch.object(c, 'API', self.origin + '/api'), \
             patch.object(c, 'PAGES', self.origin + '/pages/'), \
             patch.object(c, 'ORDER_LOOKUP_SECONDS', 0.35), patch.object(c, 'MAX_ORDER_PAGES', 1), \
             patch.object(SlowSchoolHandler, 'order_delay', 0.2):
            cl.login()
            self.assertIsNone(cl.find_existing(target))
        cl.http.close()
        self.assertFalse(any(p.endswith('/subDetails') for p in SlowSchoolHandler.calls))
        self.assertFalse(any(p.endswith('/addOrder') for p in SlowSchoolHandler.calls))

    def test_active_matching_order_is_still_read_and_verified(self):
        from core.booking import client as c
        entry = type('Entry', (), {'uid': 'u', 'profile': 'p', 'fields': {'x': '1'}})()
        cl = c.Client(entry)
        target = c.Target('健身房', '翔安校区健身房',
                          (datetime.now(timezone(timedelta(hours=8))).date() + timedelta(days=1)).isoformat(),
                          '13:30', end='15:00')
        with patch.object(c, 'ORIGIN', self.origin), patch.object(c, 'API', self.origin + '/api'), \
             patch.object(c, 'PAGES', self.origin + '/pages/'), patch.object(c, 'MAX_ORDER_PAGES', 1), \
             patch.object(SlowSchoolHandler, 'active_order', True):
            cl.login()
            found = cl.find_existing(target)
            self.assertTrue(found['verified'])
        cl.http.close()
        details = [p for p in SlowSchoolHandler.calls if p.endswith('/subDetails')]
        self.assertEqual(len(details), 1, 'the active candidate must still be verified')


if __name__ == '__main__':
    unittest.main()
