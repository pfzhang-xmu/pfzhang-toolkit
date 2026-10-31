import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('daily_window_module', ROOT / 'app/daily.py')
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)
TASK = {'sport':'健身房','venue':'翔安校区健身房','target_time_start':'13:30','target_time_end':'15:00','allow_time_fallback':False}

class WindowTest(unittest.TestCase):
    def test_late_run_refuses_before_auth_or_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(temp); store.save(TASK, 'a'*64)
            backend = Mock()
            with self.assertRaises(ValueError):
                daily.run_daily(store, backend, datetime(2026, 10, 28, 7, 20, 1, tzinfo=daily.CHINA))
            backend.auth.assert_not_called()
            self.assertFalse(list(Path(temp).glob('attempt-*')))

    def test_window_boundaries_and_wrong_frozen_date(self):
        # 窗口在 2026-10-31 从 10 分钟放宽到 20 分钟，给 07:00 的拥堵留余量
        for minute, second, microsecond, allowed in [(0, 0, 0, True), (19, 59, 0, True), (20, 0, 0, True), (20, 0, 1, False)]:
            now = datetime(2026, 10, 28, 7, minute, second, microsecond, tzinfo=daily.CHINA)
            if allowed: self.assertEqual(daily.check_window(now).date().isoformat(), '2026-10-28')
            else:
                with self.assertRaises(ValueError): daily.check_window(now)
        with self.assertRaises(ValueError):
            daily.check_window(datetime(2026, 10, 29, 7, tzinfo=daily.CHINA), '2026-10-28')
        with self.assertRaises(ValueError): daily.check_window(datetime(2026, 10, 28, 7))

    def test_auth_delay_cannot_consume_marker_or_submit(self):
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(temp); store.save(TASK, 'a'*64)
            backend = Mock(); backend.auth.return_value = 'a'*64
            times = iter([datetime(2026, 10, 28, 7, 4, tzinfo=daily.CHINA), datetime(2026, 10, 28, 7, 21, tzinfo=daily.CHINA)])
            with self.assertRaises(ValueError): daily.run_daily(store, backend, clock=lambda: next(times))
            backend.book.assert_not_called()
            self.assertFalse(list(Path(temp).glob('attempt-*')))

    def test_date_jump_after_marker_blocks_booking_and_keeps_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            store = daily.Store(temp); store.save(TASK, 'a'*64)
            backend = Mock(); backend.auth.return_value = 'a'*64
            good = datetime(2026, 10, 28, 7, tzinfo=daily.CHINA)
            times = iter([good, good, datetime(2026, 10, 29, 7, tzinfo=daily.CHINA)])
            with self.assertRaises(ValueError): daily.run_daily(store, backend, clock=lambda: next(times))
            backend.book.assert_not_called()
            self.assertEqual(len(list(Path(temp).glob('attempt-*'))), 1)


class BackoffTest(unittest.TestCase):
    def test_first_seconds_are_short(self):
        # 07:00 一开场要能连打几枪，不能 2 秒起步、后面全是 30 秒
        self.assertEqual([daily._retry_delay(i) for i in range(5)], [1, 2, 4, 6, 8])
        self.assertEqual(daily._retry_delay(11), 30)
        self.assertEqual(daily._retry_delay(99), 30)

    def test_backoff_is_monotonic_and_fits_window(self):
        delays = [daily._retry_delay(i) for i in range(daily.MAX_RETRIES)]
        self.assertEqual(delays, sorted(delays))
        self.assertLess(sum(delays), daily.WINDOW_MINUTES * 60)


class PrewarmTest(unittest.TestCase):
    def setUp(self):
        self.account = 'a' * 64
        self.now = datetime(2026, 10, 29, 7, tzinfo=daily.CHINA)

    def _prewarm(self, fp=None, at=None):
        return {'fp': fp or self.account,
                'at': (at or (self.now - timedelta(seconds=30))).isoformat()}

    def test_matching_prewarm_is_usable(self):
        self.assertTrue(daily._usable_prewarm(self._prewarm(), self.account, self.now))

    def test_wrong_account_is_rejected(self):
        self.assertFalse(daily._usable_prewarm(self._prewarm(fp='b' * 64), self.account, self.now))

    def test_stale_future_or_other_day_is_rejected(self):
        old = self.now - timedelta(seconds=daily.PREWARM_TTL + 1)
        future = self.now + timedelta(seconds=30)
        yesterday = self.now - timedelta(days=1)
        for at in (old, future, yesterday):
            self.assertFalse(daily._usable_prewarm(self._prewarm(at=at), self.account, self.now))

    def test_garbage_is_rejected(self):
        for value in (None, {}, {'fp': self.account}, {'fp': self.account, 'at': 'nope'}):
            self.assertFalse(daily._usable_prewarm(value, self.account, self.now))

    def test_prewarm_skips_the_login_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            from datetime import timedelta as td
            store = daily.Store(temp); store.save(TASK, 'a'*64)
            backend = Mock(); backend.book.return_value = {'ok': True, 'verified': True,
                'order_id': 1, 'date': '2026-10-29', 'time': '13:30-15:00', 'court': '01'}
            backend.list_orders.return_value = {'ok': True, 'account_fingerprint': 'a'*64, 'orders': []}
            now = datetime(2026, 10, 28, 7, 0, tzinfo=daily.CHINA)
            prewarm = {'fp': 'a'*64, 'at': now.isoformat()}
            with patch.object(daily, '_notification', lambda *a: {'message': '', 'media_path': None}):
                daily.run_daily(store, backend, now, prewarm=prewarm)
            backend.auth.assert_not_called()
            backend.book.assert_called_once()
