import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('entry_qr', Path(__file__).resolve().parents[1] / 'app/entry_qr.py')
try:
    entry_qr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry_qr)
except FileNotFoundError:
    entry_qr = None

class EntryQRTests(unittest.TestCase):
    def test_active_order_generates_png(self):
        self.assertIsNotNone(entry_qr, 'entry_qr module is missing')
        uid = '1234'
        ticket = SimpleNamespace(uid=uid, fields={'uid':uid})
        account = hashlib.sha256(uid.encode()).hexdigest()
        fake = FakeClient(ticket)
        with patch.object(entry_qr, 'find_profile', return_value=ticket), patch.object(entry_qr, 'QRClient', return_value=fake):
            png = entry_qr.fetch_entry_qr('42', '71', account)
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertTrue(fake.closed)
        self.assertEqual(fake.requests[-1], ('/index/stadium/orderDetails', {'order_id':'42', 'order_num':''}))
        try:
            import zxingcpp
            from PIL import Image
            import io
        except ImportError:
            return
        self.assertEqual(zxingcpp.read_barcode(Image.open(io.BytesIO(png))).text, '71|synthetic-secret')

    def test_refuses_inactive_missing_credential_wrong_owner_or_duplicate(self):
        from core.booking.session import BookingError
        uid = '1234'; ticket = SimpleNamespace(uid=uid, fields={'uid':uid})
        account = hashlib.sha256(uid.encode()).hexdigest()
        for change in ('inactive_list', 'inactive_sub', 'inactive_qr', 'missing_qr', 'wrong_owner', 'duplicate'):
            with self.subTest(change=change):
                fake = FakeClient(ticket, change)
                with patch.object(entry_qr, 'find_profile', return_value=ticket), patch.object(entry_qr, 'QRClient', return_value=fake):
                    with self.assertRaises(BookingError) as err:
                        entry_qr.fetch_entry_qr('42', '71', account)
                self.assertNotIn('synthetic-secret', str(err.exception))
                self.assertTrue(fake.closed)

    def test_wrong_account_and_invalid_identifiers_never_create_client(self):
        from core.booking.session import BookingError
        ticket = SimpleNamespace(uid='1234', fields={})
        with patch.object(entry_qr, 'find_profile', return_value=ticket), patch.object(entry_qr, 'QRClient') as factory:
            for oid, did, account in [('42','71','a'*64), ('042','71','a'*64), ('42','71|x','a'*64)]:
                with self.assertRaises(BookingError): entry_qr.fetch_entry_qr(oid,did,account)
            factory.assert_not_called()

    def test_client_allowlist_rejects_even_if_cancel_enabled(self):
        from core.booking.session import BookingError
        client = entry_qr.QRClient(SimpleNamespace(uid='1234',fields={}))
        client.allow_cancel = True
        try:
            with self.assertRaises(BookingError): client.post('/index/stadium/cancelOrder', {})
        finally: client.http.close()

class FakeClient:
    def __init__(self, ticket, change=None):
        self.change=change
        self.ticket=ticket
        self.requests=[]
        self.closed=False
        self.http=SimpleNamespace(close=lambda: setattr(self, 'closed', True))
    def login(self): return {'account_checked':True}
    def require(self, path, fields, *args, **kwargs):
        self.requests.append((path, fields))
        if path.endswith('mySubscribe'):
            return [{'uid':'wrong' if self.change == 'wrong_owner' else '1234', 'order_id':'42', 'audit_status':'3' if self.change == 'inactive_list' else '1'}]
        if path.endswith('subDetails'):
            return {'uid':'1234', 'audit_status':'1', 'details':[{'id':'71','status':'3' if self.change == 'inactive_sub' else '1','date':'2099-01-01','interval_time':'10:00–11:00'}]}
        if path.endswith('orderDetails'):
            item = {'details_id':'71','status':'3' if self.change == 'inactive_qr' else '1'}
            return {'uid':'1234', 'audit_status':'1', 'qrcode':'' if self.change == 'missing_qr' else 'synthetic-secret', 'details':[item, item] if self.change == 'duplicate' else [item]}
        raise AssertionError('unexpected endpoint')

if __name__ == '__main__': unittest.main()
