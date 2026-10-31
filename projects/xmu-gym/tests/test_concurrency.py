import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('booking_backend', Path(__file__).resolve().parents[1] / 'app/backend.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)

TASK = {'sport': '健身房', 'venue': '翔安', 'target_date': '2026-10-29', 'target_time_start': '13:30', 'target_time_end': '15:00'}

class ConcurrencyTests(unittest.TestCase):
    def test_invalid_running_fallback_journal_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'fallback-requests').mkdir()
            (root / 'fallback-requests' / 'old.json').write_text('{"state":"running","targets":[]}')
            with self.assertRaises(ValueError): backend.guard_fallback_journals(root, SimpleNamespace(sport='s', venue='v', date='d'))

    def test_existing_running_fallback_journal_blocks_matching_candidate(self):
        import json
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'fallback-requests').mkdir()
            (root / 'fallback-requests' / 'old.json').write_text(json.dumps({'state': 'running', 'targets': [{'sport': TASK['sport'], 'venue': TASK['venue'], 'date': TASK['target_date']}]}))
            target = SimpleNamespace(sport=TASK['sport'], venue=TASK['venue'], date=TASK['target_date'])
            with self.assertRaises(ValueError): backend.guard_fallback_journals(root, target)

    @unittest.skipIf(__import__("os").name == "nt", "POSIX select/pipe test")
    def test_other_process_waits_then_sees_persisted_attempt(self):
        import subprocess, sys, select, json
        with tempfile.TemporaryDirectory() as temp, patch.object(backend, 'BOOKING_ROOT', Path(temp)):
            script = '''import importlib.util, sys, json
from pathlib import Path
s = importlib.util.spec_from_file_location("b", sys.argv[1]); b = importlib.util.module_from_spec(s); s.loader.exec_module(b)
b.BOOKING_ROOT = Path(sys.argv[2]); task = json.loads(sys.argv[3])
print("ready", flush=True)
try:
    with b.booking_guard(task, "a"*64): print("acquired", flush=True)
except ValueError: print("blocked", flush=True)
'''
            with backend.booking_guard(TASK, 'a' * 64):
                child = subprocess.Popen([sys.executable, '-c', script, str(Path(__file__).resolve().parents[1] / 'app/backend.py'), temp, json.dumps(TASK)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(), 'ready')
                    self.assertFalse(select.select([child.stdout], [], [], 0.2)[0])
                except BaseException:
                    child.kill(); child.communicate(); raise
            stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertEqual(stdout.strip(), 'blocked')

    def test_unconfirmed_attempt_blocks_new_backend_instance(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(backend, 'BOOKING_ROOT', Path(temp), create=True), patch.object(backend, 'load_adapter') as load:
            mod = load.return_value
            mod.data_root.return_value = Path(temp)
            mod.find_profile.return_value = SimpleNamespace(uid='uid', profile='profile')
            mod.hashlib.sha256.return_value.hexdigest.return_value = 'a' * 64
            mod.check_auth.return_value = {'ok': True, 'account_checked': True, 'account_fingerprint': 'a' * 64}
            mod.Target.return_value = SimpleNamespace(sport='健身房', venue='翔安', date='2026-10-29')
            mod.run_candidates.side_effect = RuntimeError('response lost')
            with self.assertRaises(RuntimeError): backend.Backend().book(TASK, 'a' * 64)
            with self.assertRaises(ValueError): backend.Backend().book(TASK, 'a' * 64)
            self.assertEqual(mod.run_candidates.call_count, 1)
