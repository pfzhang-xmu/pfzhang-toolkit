import importlib.util
from pathlib import Path
import threading, unittest, urllib.request, urllib.error, json
spec=importlib.util.spec_from_file_location('server',Path(__file__).resolve().parents[1]/'app/server.py')
server=importlib.util.module_from_spec(spec); spec.loader.exec_module(server)
class SecurityTests(unittest.TestCase):
    def test_untrusted_host_and_missing_origin_rejected(self):
        http=server.make_server(server.Bridge(object()),0); thread=threading.Thread(target=http.serve_forever,daemon=True); thread.start()
        try:
            url=f'http://127.0.0.1:{http.server_port}/api/invoke'
            for headers in ({'Content-Type':'application/json','X-XMU-Bridge':'1'}, {'Content-Type':'application/json','X-XMU-Bridge':'1','Origin':f'http://127.0.0.1:{http.server_port}','Host':'attacker.example'}):
                req=urllib.request.Request(url,data=b'{"command":"start_http_run"}',headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as exc: urllib.request.urlopen(req)
                self.assertEqual(exc.exception.code,403)
        finally: http.shutdown(); http.server_close(); thread.join()
    def test_pending_orders_are_not_falsely_reported_empty(self):
        with self.assertRaises(ValueError): server.Bridge(object()).invoke('list_pending_orders',{})
