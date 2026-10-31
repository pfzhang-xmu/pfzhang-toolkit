import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('backend', Path(__file__).resolve().parents[1]/'app/backend.py')
backend=importlib.util.module_from_spec(spec); spec.loader.exec_module(backend)

class FallbackTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = patch.object(backend, 'BOOKING_ROOT', Path(self.temp.name))
        root.start(); self.addCleanup(root.stop)
    def test_later_candidates_only_sorted_deduped(self):
        rows = [{'start':'12:00','end':'13:00'}, {'start':'16:00','end':'17:00'}, {'start':'15:00','end':'16:00'}, {'start':'15:00','end':'16:00'}, {'start':'14:00','end':'15:30'}, {'start':'bogus','end':'xxx'}]
        self.assertEqual(backend.later_fallbacks(rows, '15:00'), [{'time':'15:00','end':'16:00'}, {'time':'16:00','end':'17:00'}])

    def test_fallback_is_lazy_and_account_checked(self):
        task={'sport':'健身房','venue':'翔安校区健身房','target_date':'2026-10-29','target_time_start':'13:30','target_time_end':'15:00','allow_time_fallback':True}
        with patch.object(backend, 'load_adapter') as load:
            mod=load.return_value; mod.data_root.return_value=Path(self.temp.name)
            mod.find_profile.return_value=SimpleNamespace(uid='uid',profile='profile')
            mod.hashlib.sha256.return_value.hexdigest.return_value='a'*64
            mod.check_auth.return_value={'ok':True,'account_checked':True,'account_fingerprint':'a'*64}
            mod.Target.return_value=SimpleNamespace(sport='健身房',venue='翔安校区健身房',date='2026-10-29')
            mod.run_candidates.return_value={'ok':True,'verified':True}
            backend.Backend().book(task,'a'*64)
            self.assertTrue(callable(mod.run_candidates.call_args.kwargs['fallbacks']))
            callback = mod.run_candidates.call_args.kwargs['fallbacks']
            # Identity changed before discovery: do not even open a client.
            mod.find_profile.return_value.uid = 'other'
            mod.hashlib.sha256.side_effect = lambda value: SimpleNamespace(
                hexdigest=lambda: 'b' * 64 if value == b'other' else 'a' * 64)
            with self.assertRaisesRegex(ValueError, '账号'):
                callback()
            mod.Client.assert_not_called()
