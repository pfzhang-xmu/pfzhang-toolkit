import importlib.util
from pathlib import Path
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
TZ = timezone(timedelta(hours=8))


def load_wrapper():
    spec = importlib.util.spec_from_file_location('daily_prestart', ROOT / 'app/daily_prestart.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Clock:
    def __init__(self, now):
        self.now = now

    def read(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class PrestartTests(unittest.TestCase):
    def test_waits_until_seven_and_passes_frozen_date(self):
        wrapper = load_wrapper()
        clock = Clock(datetime(2026, 10, 28, 6, 58, tzinfo=TZ))
        launch = Mock(return_value=0)
        self.assertEqual(wrapper.run(clock.read, clock.sleep, launch), 0)
        self.assertEqual(clock.now.hour, 7)
        launch.assert_called_once_with('2026-10-28', None)

    def test_prewarm_runs_thirty_seconds_before_seven(self):
        wrapper = load_wrapper()
        clock = Clock(datetime(2026, 10, 28, 6, 58, tzinfo=TZ))
        marks = []

        def hook(run_date):
            marks.append(clock.now.strftime('%H:%M:%S'))
            return '/tmp/prewarm.json'

        launch = Mock(return_value=0)
        self.assertEqual(wrapper.run(clock.read, clock.sleep, launch, hook), 0)
        self.assertEqual(marks, ['06:59:30'])
        launch.assert_called_once_with('2026-10-28', '/tmp/prewarm.json')

    def test_prewarm_failure_still_launches(self):
        wrapper = load_wrapper()
        clock = Clock(datetime(2026, 10, 28, 6, 58, tzinfo=TZ))
        launch = Mock(return_value=0)
        self.assertEqual(wrapper.run(clock.read, clock.sleep, launch, lambda _: None), 0)
        launch.assert_called_once_with('2026-10-28', None)

    def test_prewarm_runs_only_once(self):
        wrapper = load_wrapper()
        clock = Clock(datetime(2026, 10, 28, 6, 58, tzinfo=TZ))
        calls = []
        launch = Mock(return_value=0)
        wrapper.run(clock.read, clock.sleep, launch,
                    lambda _: calls.append(1) or '/tmp/p.json')
        self.assertEqual(len(calls), 1)

    def test_rejects_late_early_and_midnight_jump_without_launch(self):
        wrapper = load_wrapper()
        # 窗口已放宽到 07:20，所以 07:20:00 之后才算迟到
        for now in [datetime(2026, 10, 28, 6, 57, 59, tzinfo=TZ),
                    datetime(2026, 10, 28, 7, 20, 0, 500000, tzinfo=TZ),
                    datetime(2026, 10, 29, 0, 1, tzinfo=TZ)]:
            launch = Mock()
            with self.assertRaises(ValueError):
                wrapper.run(lambda: now, Mock(), launch)
            launch.assert_not_called()
        times = iter([datetime(2026, 10, 28, 6, 58, tzinfo=TZ), datetime(2026, 10, 29, 7, tzinfo=TZ)])
        launch = Mock()
        with self.assertRaises(ValueError):
            wrapper.run(lambda: next(times), Mock(), launch)
        launch.assert_not_called()

    def test_launch_uses_venv_and_forwards_date(self):
        wrapper = load_wrapper()
        with unittest.mock.patch.object(wrapper.subprocess, 'run') as run:
            run.return_value.returncode = 3
            self.assertEqual(wrapper.launch('2026-10-28'), 3)
            run.assert_called_once_with([str(wrapper.PYTHON), '-m', 'app.daily', 'run',
                                         '--run-date', '2026-10-28'], check=False, cwd=str(ROOT))

    def test_launch_forwards_prewarm_file(self):
        wrapper = load_wrapper()
        with unittest.mock.patch.object(wrapper.subprocess, 'run') as run:
            run.return_value.returncode = 0
            wrapper.launch('2026-10-28', '/tmp/prewarm.json')
            run.assert_called_once_with([str(wrapper.PYTHON), '-m', 'app.daily', 'run',
                                         '--run-date', '2026-10-28',
                                         '--prewarm', '/tmp/prewarm.json'], check=False, cwd=str(ROOT))


if __name__ == '__main__':
    unittest.main()
