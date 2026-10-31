import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location('daily', Path(__file__).resolve().parents[1]/'app/daily.py')
try:
    daily = importlib.util.module_from_spec(spec); spec.loader.exec_module(daily)
except FileNotFoundError:
    daily = None

TASK = {'sport':'健身房','venue':'翔安校区健身房','target_date':'2026-01-01', 'target_time_start':'13:30','target_time_end':'15:00','allow_time_fallback':False}

class DailyTests(unittest.TestCase):
    def test_transient_auth_timeout_retries_before_marker_and_books_once(self):
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(temp); store.save(TASK, 'a'*64)
            backend = Mock()
            def auth():
                self.assertEqual(list(Path(temp).glob('attempt-*')), [])
                if backend.auth.call_count == 1:
                    raise TimeoutError('secret-token')
                return 'a'*64
            backend.auth.side_effect = auth
            backend.book.return_value = {'ok': True, 'verified': True}
            result = daily.run_daily(store, backend, datetime(2026, 10, 28, 23, tzinfo=timezone.utc))
            self.assertTrue(result['verified'])
            self.assertEqual(backend.auth.call_count, 2)
            self.assertEqual(backend.book.call_count, 1)

    def test_auth_diagnostics_are_allowlisted_and_do_not_leak_secrets(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(temp); store.save(TASK, 'a'*64)
            backend = Mock(); backend.auth.side_effect = TimeoutError('secret-token')
            output = io.StringIO()
            with contextlib.redirect_stderr(output), self.assertRaises(TimeoutError):
                daily.run_daily(store, backend, datetime(2026, 10, 28, 23, tzinfo=timezone.utc), sleep=lambda _: None)
            self.assertEqual(backend.auth.call_count, daily.MAX_ATTEMPTS)
            self.assertIn('phase=auth', output.getvalue())
            self.assertIn('code=TIMEOUT', output.getvalue())
            self.assertNotIn('secret-token', output.getvalue())
            self.assertFalse(list(Path(temp).glob('attempt-*')))
            backend.book.assert_not_called()

    def test_nontransient_auth_and_booking_errors_have_safe_diagnostics(self):
        import contextlib
        import io
        now = datetime(2026, 10, 28, 23, tzinfo=timezone.utc)
        for phase in ('auth', 'book'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temp:
                store = daily.Store(temp); store.save(TASK, 'a'*64)
                backend = Mock(); backend.auth.return_value = 'a'*64
                getattr(backend, phase).side_effect = RuntimeError('secret-token')
                output = io.StringIO()
                with contextlib.redirect_stderr(output), self.assertRaises(RuntimeError):
                    daily.run_daily(store, backend, now)
                self.assertIn('phase=' + phase + ' code=OTHER', output.getvalue())
                self.assertNotIn('secret-token', output.getvalue())
                self.assertEqual(backend.auth.call_count, 1)
                self.assertEqual(backend.book.call_count, int(phase == 'book'))
                self.assertEqual(len(list(Path(temp).glob('attempt-*'))), int(phase == 'book'))

    def test_failed_booking_result_logs_only_safe_code(self):
        import contextlib, io
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(temp); store.save(TASK, 'a'*64)
            backend = Mock(); backend.auth.return_value = 'a'*64
            backend.book.return_value = {'ok': False, 'code': 'SERVER_BUSY', 'message': 'secret-token'}
            output = io.StringIO()
            with contextlib.redirect_stderr(output), self.assertRaises(ValueError):
                daily.run_daily(store, backend, datetime(2026, 10, 28, 23, tzinfo=timezone.utc))
            self.assertIn('phase=book code=SERVER_BUSY', output.getvalue())
            self.assertNotIn('secret-token', output.getvalue())
            self.assertEqual(backend.book.call_count, 1)

    def test_timeout_retry_stops_when_window_closes(self):
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(temp); store.save(TASK, 'a'*64)
            backend = Mock(); backend.auth.side_effect = TimeoutError('secret-token')
            times = iter([datetime(2026, 10, 28, 7, 4, tzinfo=daily.CHINA),
                          datetime(2026, 10, 28, 7, 21, tzinfo=daily.CHINA)])
            with self.assertRaises(ValueError):
                daily.run_daily(store, backend, clock=lambda: next(times))
            self.assertEqual(backend.auth.call_count, 1)
            backend.book.assert_not_called()
            self.assertFalse(list(Path(temp).glob('attempt-*')))

    def test_daily_execution_persists_preferences_pins_account_and_runs_once(self):
        self.assertIsNotNone(daily, 'daily scheduling implementation missing')
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(Path(temp))
            store.save(TASK, 'a'*64)
            backend = Mock(); backend.auth.return_value = 'a'*64
            backend.book.return_value = {'ok':True, 'verified':True}
            now = datetime(2026, 10, 28, 23, tzinfo=timezone.utc)
            self.assertTrue(daily.run_daily(store, backend, now)['verified'])
            actual, account = backend.book.call_args.args
            self.assertEqual(actual['target_date'], '2026-10-30')
            self.assertEqual(account, 'a'*64)
            with self.assertRaises(ValueError): daily.run_daily(store, backend, now)
            self.assertEqual(backend.book.call_count, 1)
            if os.name != 'nt':
                self.assertEqual((Path(temp)/'daily.json').stat().st_mode & 0o777, 0o600)

    def test_failed_or_unknown_submission_blocks_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(Path(temp)); store.save(TASK, 'a'*64)
            backend = Mock(); backend.auth.return_value = 'a'*64
            backend.book.side_effect = RuntimeError('unknown')
            now = datetime(2026, 10, 27, 23, tzinfo=timezone.utc)
            with self.assertRaises(RuntimeError): daily.run_daily(store, backend, now)
            with self.assertRaises(ValueError): daily.run_daily(store, backend, now)
            self.assertEqual(backend.book.call_count, 1)

    def test_before_beijing_seven_does_not_consume_daily_attempt(self):
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(Path(temp)); store.save(TASK, 'a'*64)
            backend = Mock(); backend.auth.return_value = 'a'*64
            now = datetime(2026, 10, 28, 22, 59, tzinfo=timezone.utc)
            with self.assertRaises(ValueError): daily.run_daily(store, backend, now)
            backend.auth.assert_not_called()
            backend.book.assert_not_called()
            self.assertEqual(list(Path(temp).glob('attempt-*')), [])

    def test_changed_account_cannot_submit(self):
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(Path(temp)); store.save(TASK, 'a'*64)
            backend = Mock(); backend.auth.return_value = 'b'*64
            with self.assertRaisesRegex(ValueError, '账号'): daily.run_daily(store, backend, datetime(2026, 10, 27, 23, tzinfo=timezone.utc))
            backend.book.assert_not_called()
