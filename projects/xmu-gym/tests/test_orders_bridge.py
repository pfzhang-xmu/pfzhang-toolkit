import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec=importlib.util.spec_from_file_location('orders_server',Path(__file__).resolve().parents[1]/'app/server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)

class OrdersBridgeTests(unittest.TestCase):
    def test_http_frontend_payload_and_response_are_forwarded_without_network_mutation(self):
        import json, threading, urllib.request, urllib.error
        backend=Mock()
        backend.cancel_order.return_value={'ok':False,'state':'cancel_unconfirmed','code':'CANCEL_UNCONFIRMED','message':'请到小程序核对'}
        bridge=server.Bridge(backend); bridge.account='a'*64
        http=server.make_server(bridge)
        thread=threading.Thread(target=http.serve_forever,daemon=True); thread.start()
        origin=f'http://127.0.0.1:{http.server_port}'
        def invoke(args):
            request=urllib.request.Request(origin+'/api/invoke', data=json.dumps({'command':'cancel_my_order','args':args}).encode(), headers={'Origin':origin,'Content-Type':'application/json','X-XMU-Bridge':'1'})
            try:
                with urllib.request.urlopen(request) as response: return response.status, json.load(response)
            except urllib.error.HTTPError as response: return response.code, json.load(response)
        args={'accountFingerprint':'a'*64,'orderId':'42','detailsId':'71','confirmed':True}
        try:
            status, body=invoke(args)
            self.assertEqual(status,200)
            self.assertEqual(body['result'],backend.cancel_order.return_value)
            backend.cancel_order.assert_called_once_with('a'*64,'42','71')
            backend.cancel_order.reset_mock()
            status, body=invoke({**args,'accountFingerprint':'b'*64})
            self.assertEqual((status,body['result']['code']),(200,'ACCOUNT_CHANGED'))
            status, body=invoke({k:v for k,v in args.items() if k!='confirmed'})
            self.assertEqual(status,400)
            self.assertIn('请重新确认取消',body['error'])
            backend.cancel_order.assert_not_called()
        finally:
            http.shutdown(); http.server_close(); thread.join()

    def test_cancel_reports_confirmation_and_account_mismatch_separately(self):
        backend=Mock()
        bridge=server.Bridge(backend); bridge.account='a'*64
        args={'accountFingerprint':'a'*64,'orderId':'42','detailsId':'71'}
        with self.assertRaisesRegex(ValueError, '请重新确认取消'):
            bridge.invoke('cancel_my_order', args)
        result=bridge.invoke('cancel_my_order', {**args,'confirmed':True,'accountFingerprint':'b'*64})
        self.assertEqual(result['code'], 'ACCOUNT_CHANGED')
        self.assertFalse(result['ok'])
        backend.cancel_order.assert_not_called()

    def test_concurrent_cancel_rejected(self):
        import threading
        entered=threading.Event(); release=threading.Event()
        backend=Mock(); backend.auth.return_value='a'*64
        def slow(*args):
            entered.set(); release.wait(3); return {'ok':True,'state':'cancelled'}
        backend.cancel_order.side_effect=slow
        bridge=server.Bridge(backend); bridge.account='a'*64
        args={'accountFingerprint':'a'*64,'orderId':'42','detailsId':'71','confirmed':True}
        thread=threading.Thread(target=lambda:bridge.invoke('cancel_my_order',args));thread.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaises(ValueError): bridge.invoke('cancel_my_order',args)
        finally: release.set();thread.join()
        self.assertEqual(backend.cancel_order.call_count,1)

    def test_orders_require_checked_account_and_cancel_confirmation(self):
        backend=Mock(); backend.auth.return_value='a'*64
        backend.list_orders.return_value={'ok':True,'orders':[],'account_fingerprint':'a'*64}
        bridge=server.Bridge(backend)
        with self.assertRaises(ValueError): bridge.invoke('get_my_orders',{})
        bridge.invoke('start_http_run',{'until':'auth'})
        self.assertTrue(bridge.invoke('get_my_orders',{})['ok'])
        with self.assertRaises(ValueError): bridge.invoke('cancel_my_order',{'accountFingerprint':'a'*64,'orderId':'42','detailsId':'71'})
        backend.cancel_order.assert_not_called()
        backend.cancel_order.return_value={'ok':True,'state':'cancelled'}
        self.assertEqual(bridge.invoke('cancel_my_order',{'accountFingerprint':'a'*64,'orderId':'42','detailsId':'71','confirmed':True})['state'],'cancelled')
