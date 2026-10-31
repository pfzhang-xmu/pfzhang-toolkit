import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('local_server_rebooking', Path(__file__).resolve().parents[1] / 'app/server.py')
server = importlib.util.module_from_spec(spec); spec.loader.exec_module(server)

class GuardErrorTests(unittest.TestCase):
    def test_booking_guard_error_is_reported_accurately(self):
        message = '已有预约尝试，请先核实订单，勿重复提交'
        def invoke(*args): raise ValueError(message)
        handler = object.__new__(server.Handler)
        handler.server = SimpleNamespace(server_port=1234, bridge=SimpleNamespace(invoke=invoke))
        handler.path = '/api/invoke'
        class Headers(dict):
            def get_content_type(self): return 'application/json'
        body = json.dumps({'command':'start_http_run'}).encode()
        handler.headers = Headers({'Host':'127.0.0.1:1234','Origin':'http://127.0.0.1:1234',
                                   'X-XMU-Bridge':'1','Content-Length':str(len(body))})
        handler.rfile = io.BytesIO(body)
        responses = []
        handler.send_json = lambda status, value: responses.append((status, value))
        handler.do_POST()
        self.assertEqual(responses, [(400, {'error':message})])
