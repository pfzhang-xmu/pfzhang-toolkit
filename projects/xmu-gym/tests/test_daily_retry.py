import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('retry_backend', ROOT / 'app/backend.py')
backend = importlib.util.module_from_spec(spec); spec.loader.exec_module(backend)
spec = importlib.util.spec_from_file_location('retry_daily', ROOT / 'app/daily.py')
daily = importlib.util.module_from_spec(spec); spec.loader.exec_module(daily)

TASK = {'sport':'健身房','venue':'翔安校区健身房','target_date':'2026-10-31',
        'target_time_start':'13:30','target_time_end':'15:00','allow_time_fallback':True}
ACCOUNT = 'a' * 64


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = patch.object(backend, 'BOOKING_ROOT', Path(self.temp.name) / 'booking')
        self.root.start(); self.addCleanup(self.root.stop)
        self.mod = MagicMock()
        self.mod.data_root.return_value = Path(self.temp.name) / 'http'
        self.mod.find_profile.return_value = SimpleNamespace(uid='uid', profile='profile')
        self.mod.hashlib.sha256.return_value.hexdigest.return_value = ACCOUNT
        self.mod.check_auth.return_value = {'ok':True,'account_checked':True,'account_fingerprint':ACCOUNT}
        self.mod.Target.side_effect = lambda *a, **kw: SimpleNamespace(sport=a[0], venue=a[1], date=a[2])
        self.mod.BookingError = type('BookingError', (Exception,), {'__init__': lambda self, code, msg: (setattr(self,'code',code), Exception.__init__(self,msg)) and None})
        self.loader = patch.object(backend, 'load_adapter', return_value=self.mod)
        self.loader.start(); self.addCleanup(self.loader.stop)

    def test_transient_failure_retries_with_fresh_idempotency_key(self):
        self.mod.run_candidates.side_effect = [
            {'ok':False,'verified':False,'state':'failed','code':'HTTP_FAILED'},
            {'ok':True,'verified':True,'state':'submitted'},
        ]
        result = backend.Backend().book(
            TASK, ACCOUNT,
            retry=lambda attempt, outcome: daily._retryable(outcome) and attempt < 1)
        self.assertTrue(result['verified'])
        self.assertEqual(self.mod.run_candidates.call_count, 2)
        keys = [call.kwargs['idempotency_key'] for call in self.mod.run_candidates.call_args_list]
        self.assertNotEqual(keys[0], keys[1])
        self.assertTrue(keys[1].endswith(':attempt:1'))

    def test_full_daily_flow_retries_transient_failure_then_verifies_success(self):
        from datetime import datetime
        self.mod.run_candidates.side_effect = [
            {'ok':False,'verified':False,'state':'failed','code':'HTTP_FAILED'},
            {'ok':True,'verified':True,'state':'submitted'},
        ]
        store = daily.Store(Path(self.temp.name) / 'daily')
        store.save(TASK, ACCOUNT)
        result = daily.run_daily(store, backend.Backend(),
                                 datetime(2026, 10, 30, 7, tzinfo=daily.CHINA), sleep=lambda _: None)
        self.assertTrue(result['verified'])
        self.assertEqual(self.mod.run_candidates.call_count, 2)
        self.assertEqual(len(list((Path(self.temp.name) / 'daily').glob('attempt-*.json'))), 1)

    def test_unknown_result_never_retries(self):
        self.mod.run_candidates.return_value = {
            'ok':False,'verified':False,'state':'unknown','code':'SUBMIT_UNKNOWN'}
        result = backend.Backend().book(TASK, ACCOUNT, retry=lambda a, o: daily._retryable(o))
        self.assertEqual(result['code'], 'SUBMIT_UNKNOWN')
        self.assertEqual(self.mod.run_candidates.call_count, 1)

    def test_submitted_state_cannot_be_retried_even_with_transient_code(self):
        self.assertFalse(daily._retryable({'state':'submitted','code':'HTTP_FAILED'}))
        self.assertFalse(daily._retryable({'state':'unknown','code':'SERVER_BUSY'}))
        self.assertFalse(daily._retryable({'state':'not_submitted','code':'CAPTCHA_UNREADABLE'}))
        self.assertTrue(daily._retryable({'state':'failed','code':'HTTP_FAILED'}))
        self.assertTrue(daily._retryable({'state':'not_submitted','code':'CAPTCHA_FETCH_FAILED'}))

    def test_at_most_twelve_retries_after_the_first_attempt(self):
        self.mod.run_candidates.return_value = {
            'ok':False,'verified':False,'state':'failed','code':'HTTP_FAILED'}
        with self.assertRaisesRegex(ValueError, '重试次数'):
            backend.Backend().book(
                TASK, ACCOUNT, retry=lambda attempt, outcome: daily._retryable(outcome))
        self.assertEqual(self.mod.run_candidates.call_count, 13)
        keys = [call.kwargs['idempotency_key'] for call in self.mod.run_candidates.call_args_list]
        self.assertEqual(len(set(keys)), 13)

    def test_bare_exception_never_retries_booking(self):
        exc = self.mod.BookingError('HTTP_FAILED', 'transient')
        self.assertFalse(daily._retryable(exc))
        self.assertTrue(daily._retryable_auth(exc))
        self.assertEqual(daily.MAX_RETRIES, 12)
        self.assertEqual(backend.MAX_BOOKING_ATTEMPTS, daily.MAX_ATTEMPTS)

    def test_external_gate_is_composed_into_every_upstream_eligibility_check(self):
        self.mod.run_candidates.return_value = {'ok':True,'verified':True}
        backend.Backend().book(TASK, ACCOUNT, eligibility=lambda target: None)
        gate = self.mod.run_candidates.call_args.kwargs['eligibility']
        gate(SimpleNamespace())
        self.mod.check_target.assert_called_once()
        backend.Backend()._book_locked(
            TASK, ACCOUNT, attempt=1,
            eligibility=lambda target: (_ for _ in ()).throw(ValueError('late')))
        closing_gate = self.mod.run_candidates.call_args.kwargs['eligibility']
        with self.assertRaises(self.mod.BookingError) as ctx:
            closing_gate(SimpleNamespace())
        self.assertEqual(ctx.exception.code, 'BOOKING_WINDOW_CLOSED')
        self.assertEqual(self.mod.run_candidates.call_count, 2)


if __name__ == '__main__':
    unittest.main()
