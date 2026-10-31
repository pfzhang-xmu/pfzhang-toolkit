import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest


@pytest.fixture(autouse=True)
def no_real_reconciliation_delays(monkeypatch):
    # Exercise every polling attempt without sleeping in offline regression tests.
    monkeypatch.setattr("core.booking.client.RECONCILE_DELAYS", (0, 0, 0, 0, 0))
