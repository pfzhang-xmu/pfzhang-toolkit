import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('daily', Path(__file__).resolve().parents[1] / 'app/daily.py')
daily = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daily)


def booking_error(code, message):
    class Err(RuntimeError):
        def __init__(self, code, message):
            super().__init__(message)
            self.code = code
    return Err(code, message)


class FailureTextTests(unittest.TestCase):
    def test_login_phase_failure_is_reported_as_not_submitted(self):
        # 2026-10-31 真实故障：微信没在跑 → NO_ENTRY 'no usable profile'。
        # 这一步在提交之前，消息不能吓唬用户去核对订单。
        text = daily.failure_text(booking_error('NO_ENTRY', 'no usable profile'))
        self.assertIn('没提交', text)
        self.assertIn('厦大体育', text)
        self.assertNotIn('不确定', text)
        self.assertNotIn('先别重复提交', text)
        self.assertIn('不用去', text)

    def test_login_codes_never_look_uncertain(self):
        for code in ('SESSION_EXPIRED', 'AUTH_FAILED', 'PROFILE_DISCOVERY_FAILED', 'AMBIGUOUS_PROFILE'):
            with self.subTest(code=code):
                text = daily.failure_text(booking_error(code, 'boom'))
                self.assertIn('没提交', text)
                self.assertNotIn('不确定', text)

    def test_bare_no_entry_message_without_code_is_still_not_submitted(self):
        text = daily.failure_text(RuntimeError('no usable profile'))
        self.assertIn('没提交', text)

    def test_unknown_result_still_asks_for_manual_check(self):
        text = daily.failure_text(booking_error('RESULT_UNCONFIRMED', '预约结果未核实'))
        self.assertIn('未确认', text)
        self.assertIn('我的预约', text)

    def test_login_codes_are_reported_verbatim_on_stderr(self):
        self.assertEqual(daily._outcome_code(booking_error('NO_ENTRY', 'x')), 'NO_ENTRY')
        self.assertEqual(daily._outcome_code(booking_error('SESSION_EXPIRED', 'x')), 'SESSION_EXPIRED')


if __name__ == '__main__':
    unittest.main()
