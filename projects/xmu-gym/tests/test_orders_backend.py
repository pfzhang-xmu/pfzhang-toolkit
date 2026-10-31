import importlib.util
from pathlib import Path
import tempfile, unittest
from unittest.mock import patch
from types import SimpleNamespace
import hashlib
spec=importlib.util.spec_from_file_location('orders_backend',Path(__file__).resolve().parents[1]/'app/backend.py')
backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)

class OrdersTests(unittest.TestCase):
    def test_guard_rejects_other_concurrent_operation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(backend,'CANCEL_ROOT',Path(tmp)):
            with backend.cancellation_guard('a'*64,'42','71'):
                with self.assertRaises(ValueError):
                    with backend.cancellation_guard('a'*64,'99','22'):
                        self.fail('concurrent cancellation entered')
            with self.assertRaises(ValueError):
                with backend.cancellation_guard('a'*64,'42','71'):
                    self.fail('duplicate cancellation entered')

    def test_read_and_cancel_are_account_bound_and_duplicate_blocked(self):
        backend.load_adapter()  # return the adapter module
        from core.booking import orders
        entry=SimpleNamespace(uid='1234')
        account=hashlib.sha256(b'1234').hexdigest()
        adapter=SimpleNamespace(find_profile=lambda:entry)
        clients=[]
        class Fake:
            def __init__(self,ticket): self.http=SimpleNamespace(close=lambda:None);clients.append(self)
            def login(self): return {'account_checked':True}
        with tempfile.TemporaryDirectory() as tmp, patch.object(backend,'CANCEL_ROOT',Path(tmp)), patch.object(backend,'load_adapter',return_value=adapter), patch.object(orders,'OrdersClient',Fake), patch.object(orders,'active_rows',return_value=[{'order_id':'42'}]), patch.object(orders,'cancel',return_value={'ok':False,'state':'cancel_unconfirmed'}) as cancel:
            b=backend.Backend()
            self.assertEqual(b.list_orders(account)['orders'],[{'order_id':'42'}])
            with self.assertRaises(ValueError): b.list_orders('a'*64)
            self.assertEqual(b.cancel_order(account,'42','71')['state'],'cancel_unconfirmed')
            with self.assertRaises(ValueError): b.cancel_order(account,'42','71')
            self.assertEqual(cancel.call_count,1)
