import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from app.daily import Store
spec = importlib.util.spec_from_file_location('daily_server', root/'app/server.py')
server=importlib.util.module_from_spec(spec); spec.loader.exec_module(server)

class DailyBridgeTests(unittest.TestCase):
    def test_missing_and_corrupt_configuration_leave_form_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            for contents in (None, '{broken', 'null', '{}'):
                if contents is not None: (Path(temp)/'daily.json').write_text(contents)
                app = server.Bridge(Mock(), daily_store=store, cron_path=Path(temp)/'jobs.json')
                self.assertIsNone(app.invoke('read_task_raw', {}))
                self.assertIsNone(app.invoke('get_live_status', {})['task'])

    def test_restart_seeds_form_without_creating_live_task_or_leaking_account(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            task = {'sport':'健身房','venue':'翔安校区健身房','target_time_start':'13:30','target_time_end':'15:00','allow_time_fallback':True}
            store.save(task, 'a'*64)
            app = server.Bridge(Mock(), daily_store=store, cron_path=Path(temp)/'jobs.json')
            raw = app.invoke('read_task_raw', {})
            for key, value in task.items(): self.assertEqual(raw[key], value)
            self.assertTrue(raw['task_id'])
            self.assertNotIn('target_date', raw)
            self.assertNotIn('account', raw)
            self.assertNotIn('a'*64, str(raw))
            self.assertIsNone(app.invoke('get_live_status', {})['task'])
            self.assertIsNone(app.account)

    def test_configuration_requires_verified_account_and_persists_without_arming(self):
        with tempfile.TemporaryDirectory() as temp:
            backend=Mock(); backend.auth.return_value='a'*64
            app=server.Bridge(backend, daily_store=Store(Path(temp)), cron_path=Path(temp)/'jobs.json')
            task={'sport':'健身房','venue':'翔安校区健身房','target_time_start':'13:30','target_time_end':'15:00','allow_time_fallback':True}
            app.invoke('save_task', {'task':task})
            with self.assertRaisesRegex(ValueError,'账号'): app.invoke('save_daily_schedule', {})
            app.invoke('start_http_run', {'until':'auth'})
            result=app.invoke('save_daily_schedule', {})
            self.assertFalse(result['scheduler_installed'])
            self.assertEqual(result['fallback_policy'], 'later_only')
            self.assertEqual(Store(Path(temp)).load()[1], 'a'*64)
            recovered = app.invoke('get_daily_schedule', {})
            self.assertEqual(recovered['task']['target_time_start'], '13:30')
            self.assertNotIn('account', recovered)
            self.assertFalse(app.invoke('get_live_status', {})['armed'])
            backend.book.assert_not_called()
