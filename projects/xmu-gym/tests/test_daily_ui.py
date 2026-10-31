"""Browser tests for the reworked XMU Gym page: real bridge, isolated backend, never submits."""
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.daily import Store
from app.server import Bridge, make_server
from playwright.sync_api import sync_playwright

AVAILABILITY = {
    'ok': True,
    'state': 'availability_ready',
    'date': '2026-10-31',
    'sport': '健身房',
    'venue': '翔安校区健身房',
    'cached': False,
    'fetched_at': '2026-10-30T15:00:00+08:00',
    'slots': [
        {'start': '13:30', 'end': '15:00', 'cells': [
            {'court': '01', 'admin_locked': False, 'status': 'available', 'reason': '', 'remaining': 3, 'capacity': 5}]},
        {'start': '15:00', 'end': '16:30', 'cells': [
            {'court': '01', 'admin_locked': False, 'status': 'available', 'reason': '', 'remaining': 0, 'capacity': 5}]},
    ],
}


class Backend:
    """Read-only stand-in: auth succeeds, availability is canned, submitting is a hard failure."""
    def auth(self): return 'a' * 64
    def availability(self, args): return dict(AVAILABILITY, date=args['date'])
    def list_orders(self, account):
        return {'ok': True, 'orders': [], 'account_fingerprint': account,
                'cached': False, 'fetched_at': '2026-10-30T15:00:00+08:00'}
    def book(self, *args, **kwargs): raise AssertionError('must not submit')
    def cancel_order(self, *args, **kwargs): raise AssertionError('must not cancel')


class NoAuthBackend(Backend):
    def auth(self): raise ValueError('账号验证失败，请重新检查')


def serve(backend, store, cron_path):
    http = make_server(Bridge(backend, store, cron_path=cron_path))
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    return http, thread


class DailyUITest(unittest.TestCase):
    def test_settings_restores_saved_preferences_without_booking(self):
        with tempfile.TemporaryDirectory() as temp, sync_playwright() as p:
            store = Store(temp)
            task = {'sport': '健身房', 'venue': '翔安校区健身房',
                    'target_time_start': '13:30', 'target_time_end': '15:00',
                    'allow_time_fallback': True}
            store.save(task, 'a' * 64)
            http, thread = serve(Backend(), store, Path(temp) / 'jobs.json')
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{http.server_port}/')
                # 场馆在「预订」页
                self.assertEqual(page.locator('#sel_venue').input_value(), '翔安校区健身房')
                self.assertEqual(page.locator('#sel_sport').input_value(), '健身房')
                page.locator('[data-tab="settings"]').click()
                self.assertEqual(page.locator('#sel_start').input_value(), '13:30')
                self.assertEqual(page.locator('#sel_end').input_value(), '15:00')
                self.assertTrue(page.locator('#chk_fallback').is_checked())
                self.assertIn('翔安校区健身房', page.locator('#daily_venue_note').inner_text())
                self.assertEqual(errors, [])
                self.assertEqual(store.load()[0], task)
            finally:
                browser.close(); http.shutdown(); http.server_close(); thread.join()

    def test_saving_daily_settings_writes_store_and_never_schedules(self):
        with tempfile.TemporaryDirectory() as temp, sync_playwright() as p:
            cron_path = Path(temp) / 'jobs.json'
            store = Store(temp)
            http, thread = serve(Backend(), store, cron_path)
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                page.goto(f'http://127.0.0.1:{http.server_port}/')
                page.locator('#sel_sport').select_option('乒乓球')
                page.wait_for_timeout(300)
                page.locator('[data-tab="settings"]').click()
                page.locator('#sel_start').select_option('18:00')
                page.locator('#btn_save_daily').click()
                page.locator('.toast').get_by_text('每日设置已保存').wait_for(timeout=15000)
                task, account = store.load()
                self.assertEqual(task['sport'], '乒乓球')
                self.assertEqual(task['target_time_start'], '18:00')
                self.assertEqual(account, 'a' * 64)
                self.assertIn('已暂停', page.locator('#daily_state_note').inner_text())
                self.assertFalse(cron_path.exists())
            finally:
                browser.close(); http.shutdown(); http.server_close(); thread.join()

    def test_save_is_refused_when_account_is_not_verified(self):
        with tempfile.TemporaryDirectory() as temp, sync_playwright() as p:
            store = Store(temp)
            http, thread = serve(NoAuthBackend(), store, Path(temp) / 'jobs.json')
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                page.goto(f'http://127.0.0.1:{http.server_port}/')
                page.locator('[data-tab="settings"]').click()
                page.locator('#btn_save_daily').click()
                page.locator('.toast').get_by_text('先核验账号').wait_for(timeout=15000)
                self.assertFalse((Path(temp) / 'daily.json').exists())
            finally:
                browser.close(); http.shutdown(); http.server_close(); thread.join()

    def test_tomorrow_view_lists_real_quota_and_keeps_book_button_safe(self):
        with tempfile.TemporaryDirectory() as temp, sync_playwright() as p:
            store = Store(temp)
            http, thread = serve(Backend(), store, Path(temp) / 'jobs.json')
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                page.goto(f'http://127.0.0.1:{http.server_port}/')
                page.locator('.slot').first.wait_for(timeout=15000)
                self.assertEqual(page.locator('.slot').count(), 2)
                self.assertEqual(page.locator('.slot.is-full').count(), 1)
                page.locator('.slot:not(.is-full)').click()
                page.locator('#btn_book').click()
                page.locator('#dlg_confirm').wait_for(state='visible')
                self.assertIn('13:30–15:00', page.locator('#cf_time').inner_text())
                page.locator('#dlg_confirm button[value="cancel"]').click()
                self.assertEqual(page.locator('.toast').count(), 0)
            finally:
                browser.close(); http.shutdown(); http.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
