import importlib.util
from pathlib import Path
import threading
import unittest
import urllib.request
import urllib.error
import json
import io
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('local_server', Path(__file__).resolve().parents[1] / 'app/server.py')
try:
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
except FileNotFoundError:
    server = None

class LocalBridgeTests(unittest.TestCase):
    def test_payment_response_from_booking_is_not_success(self):
        backend = SimpleNamespace(book=lambda task, account: {'ok':False, 'state':'unknown', 'code':'PAYMENT_REQUIRED'})
        app = server.Bridge(backend=backend)
        app.account = 'fake'; app.task = {'task_id':'mock'}
        with self.assertRaises(Exception) as error:
            app.invoke('start_http_run', {'until':'submit'})
        self.assertEqual(getattr(error.exception, 'code', None), 'PAYMENT_REQUIRED')

    def test_booking_errors_are_sanitized_and_payment_is_definitive(self):
        class CodedError(Exception):
            def __init__(self, code):
                self.code = code
                super().__init__('SECRET-token-from-server')

        for code in ('PAYMENT_REQUIRED', 'SUBMIT_UNKNOWN', 'SECRET-token-from-server', ''):
            with self.subTest(code=code):
                class FailingBridge:
                    def invoke(self, command, args):
                        raise CodedError(code)

                handler = server.Handler.__new__(server.Handler)
                handler.server = SimpleNamespace(server_port=12345, bridge=FailingBridge())
                handler.path = '/api/invoke'
                payload = b'{"command":"start_http_run","args":{"until":"submit"}}'
                handler.headers = SimpleNamespace(get=lambda key, default=None: {
                    'Host': '127.0.0.1:12345', 'Origin': 'http://127.0.0.1:12345',
                    'X-XMU-Bridge': '1', 'Content-Length': str(len(payload)),
                }.get(key, default), get_content_type=lambda: 'application/json')
                handler.rfile = io.BytesIO(payload)
                response = []
                handler.send_json = lambda status, value: response.append((status, value))
                handler.do_POST()
                status, value = response[0]
                self.assertEqual(status, 400)
                message = value['error']
                self.assertNotIn('SECRET-token-from-server', message)
                if code == 'PAYMENT_REQUIRED':
                    self.assertIn('可能需要支付', message)
                    self.assertNotIn('未提交订单', message)
                    self.assertIn('官方小程序', message)
                    self.assertIn('勿重复', message)
                else:
                    self.assertIn('勿重试', message)
                    if code == 'SUBMIT_UNKNOWN':
                        self.assertIn(code, message)

    def test_rejects_cross_origin_before_dispatch(self):
        self.assertIsNotNone(server, 'local bridge server is not implemented')
        app = server.Bridge(backend=object())
        http = server.make_server(app, 0)
        thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
        try:
            url = f'http://127.0.0.1:{http.server_port}/api/invoke'
            req = urllib.request.Request(url, data=b'{"command":"start_http_run"}', headers={'Origin':'http://evil.example','Content-Type':'application/json'}, method='POST')
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(req)
            self.assertEqual(error.exception.code, 403)
        finally:
            http.shutdown(); http.server_close(); thread.join()

if __name__ == '__main__': unittest.main()
