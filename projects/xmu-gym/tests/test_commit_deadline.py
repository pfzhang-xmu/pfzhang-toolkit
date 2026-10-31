import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.booking.client import Target, submit
from core.booking.session import BookingError


def test_deadline_gate_runs_after_captcha_and_immediately_before_final_send(tmp_path):
    client = MagicMock()
    client.ticket.uid = 'uid'
    client.find_existing.return_value = None
    client.captcha.return_value = b'png'
    solver = MagicMock(); solver.solve.return_value = '1234'
    target = Target('健身房', '翔安校区健身房', '2026-10-31', '13:30', end='15:00')
    plan = {'referer': 'https://example.invalid/confirm', 'selected': {'area_name': 'A'}}
    eligibility = MagicMock(side_effect=[None, BookingError('BOOKING_WINDOW_CLOSED', 'late')])
    with pytest.raises(BookingError) as exc:
        submit(client, target, plan, solver, tmp_path, 'deadline-test', eligibility=eligibility)
    assert exc.value.code == 'BOOKING_WINDOW_CLOSED'
    client.post.assert_not_called()
    journals = list(tmp_path.glob('*.json'))
    assert len(journals) == 1
    record = json.loads(journals[0].read_text())
    assert record['state'] == 'not_submitted'
    assert record['code'] == 'BOOKING_WINDOW_CLOSED'
    assert eligibility.call_count == 2
