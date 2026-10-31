import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('backend', Path(__file__).resolve().parents[1]/'app/backend.py')
try:
    backend=importlib.util.module_from_spec(spec); spec.loader.exec_module(backend)
except FileNotFoundError: backend=None

class BackendTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = patch.object(backend, 'BOOKING_ROOT', Path(self.temp.name))
        root.start(); self.addCleanup(root.stop)
    def test_unpublished_date_is_not_marked_available(self):
        self.assertIsNotNone(backend, 'real backend missing')
        with patch.object(backend, 'load_adapter') as load, patch.dict('sys.modules', {'core.booking.availability': __import__('types').SimpleNamespace(AvailabilityClient=lambda entry: client, snapshot=lambda *args: {'date':'2026-10-29','slots':[]})}):
            from unittest.mock import MagicMock
            client=MagicMock(); client.login.return_value={'account_checked':True}; client.intervals.return_value={'time_slot_list':[], 'date_list':[]}
            load.return_value.find_profile.return_value.uid='uid'
            load.return_value.CHINA=__import__('datetime').timezone.utc
            result=backend.Backend().availability({'sport':'健身房','venue':'翔安校区健身房','date':'2026-10-29'})
            self.assertEqual(result['state'],'date_not_published')
    def test_booking_maps_selected_fields_to_upstream_target(self):
        self.assertIsNotNone(backend, 'real backend missing')
        task={'sport':'健身房','venue':'翔安校区健身房','target_date':'2026-10-29','target_time_start':'13:30','target_time_end':'15:00','prefer_court':None}
        with patch.object(backend, 'load_adapter') as load:
            from types import SimpleNamespace
            mod=load.return_value; mod.data_root.return_value=Path(self.temp.name); mod.Target.side_effect=lambda *a, **kw: SimpleNamespace(sport=a[0], venue=a[1], date=a[2])
            mod.find_profile.return_value.uid='uid'; mod.find_profile.return_value.profile='profile'
            mod.hashlib.sha256.return_value.hexdigest.return_value='a'*64
            mod.check_auth.return_value={'ok':True,'account_checked':True,'account_fingerprint':'a'*64}
            mod.run_candidates.return_value={'ok':True,'verified':True}
            result=backend.Backend().book(task,'a'*64)
            self.assertTrue(result['verified'])
            self.assertEqual(mod.Target.call_args.args,('健身房','翔安校区健身房','2026-10-29','13:30'))
            self.assertEqual(mod.Target.call_args.kwargs['end'],'15:00')
            self.assertTrue(mod.run_candidates.call_args.kwargs['commit'])
            self.assertEqual(mod.run_candidates.call_args.kwargs['expected_account'],'a'*64)
