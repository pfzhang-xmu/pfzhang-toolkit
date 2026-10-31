import base64
import importlib.util
import json
import sys
import threading
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('qr_bridge_server', ROOT / 'app/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def test_selected_qr_http_is_private_and_account_checked():
    backend = Mock()
    bridge = server.Bridge(backend)
    bridge.account = 'a' * 64
    http = server.make_server(bridge)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    origin = f'http://127.0.0.1:{http.server_port}'
    png = b'\x89PNG\r\n\x1a\nmock-image'
    args = {'accountFingerprint': bridge.account, 'orderId': '42', 'detailsId': '71', 'selected': True}
    def request(payload, headers=None):
        req = urllib.request.Request(origin + '/api/invoke', data=json.dumps(payload).encode(), headers=headers or {'Origin': origin, 'Content-Type': 'application/json', 'X-XMU-Bridge': '1'})
        try:
            return urllib.request.urlopen(req)
        except urllib.error.HTTPError as error:
            return error
    try:
        backend.entry_qr.return_value = png
        with request({'command':'get_entry_qr', 'args':args}) as response:
            assert response.status == 200
            assert response.headers['Cache-Control'] == 'no-store'
            assert base64.b64decode(json.load(response)['result']['png_base64']) == png
        backend.entry_qr.assert_called_once_with('42', '71', 'a' * 64)
        for invalid in ({**args, 'selected': False}, {**args, 'accountFingerprint': 'b' * 64}):
            with request({'command':'get_entry_qr', 'args':invalid}) as response:
                assert response.status == 400
        with request({'command':'get_entry_qr', 'args':args}, {'Origin':'http://evil.example', 'Content-Type':'application/json', 'X-XMU-Bridge':'1'}) as response:
            assert response.status == 403
        backend.cancel_order.assert_not_called()
        backend.book.assert_not_called()
    finally:
        http.shutdown(); http.server_close(); thread.join()
