import sys

import pytest

from datetime import datetime, timezone
from core import adapter

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS lsof profile discovery")

def test_tomorrow_is_china_date_and_fixed_slot():
    target = adapter.daily_target(datetime(2026, 10, 28, 16, 30, tzinfo=timezone.utc))
    assert (target.date, target.sport, target.venue, target.time, target.end) == ("2026-10-30", "健身房", "翔安校区健身房", "13:30", "15:00")

def test_fallbacks_sorted_and_strictly_after_preferred():
    data = {'time_slot_list': [
        {'date':'2026-10-30', 'time_range': t} for t in
        ['16:30-18:00','14:00-15:30','15:00-16:30','15:00-16:30','13:30-15:00']
    ] + [{'date':'2026-10-29','time_range':'15:00-16:30'}]}
    target = adapter.daily_target(datetime(2026, 10, 28, 16, 30, tzinfo=timezone.utc))
    assert adapter.ordered_fallbacks(data, target) == [
        {'time':'15:00','end':'16:30'}, {'time':'16:30','end':'18:00'}]

def test_stale_unreadable_profile_is_never_read(tmp_path, monkeypatch):
    from types import SimpleNamespace
    root = tmp_path / 'profiles'
    active = root / 'webview_active'
    stale = root / 'webview_stale'
    for path in (active, stale):
        (path / 'Local Storage' / 'leveldb').mkdir(parents=True)
    monkeypatch.setattr('subprocess.run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=f'n{active}/Local Storage/leveldb/LOCK\n', stderr=''))
    def read(path):
        if path == stale:
            raise adapter.BookingError('SESSION_READ_FAILED', 'unreadable')
        return SimpleNamespace(profile=path, uid='1')
    monkeypatch.setattr(adapter, 'read_entry', read)
    assert adapter.find_profile(root).profile == active


def test_discovery_requires_exactly_one_usable_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    root = tmp_path / 'profiles'
    (root / 'webview_one' / 'Local Storage' / 'leveldb').mkdir(parents=True)
    (root / 'webview_two' / 'Local Storage' / 'leveldb').mkdir(parents=True)
    monkeypatch.setattr(adapter, 'read_entry', lambda path: SimpleNamespace(profile=path, uid='1'))
    import subprocess
    active = [root / 'webview_one', root / 'webview_two']
    def lsof(*args, **kwargs):
        return SimpleNamespace(returncode=0, stderr='', stdout=''.join(f'n{p}/Local Storage/leveldb/LOCK\n' for p in active))
    monkeypatch.setattr(subprocess, 'run', lsof)
    with pytest.raises(adapter.BookingError, match='multiple'):
        adapter.find_profile(root)
    active.pop()
    assert adapter.find_profile(root).profile.name == 'webview_one'

def test_partial_lsof_failure_never_selects_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest, subprocess
    profile = tmp_path / 'webview_active'
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stderr='', stdout=f'n{profile}/file\n'))
    monkeypatch.setattr(adapter, 'read_entry', lambda p: SimpleNamespace(profile=p, uid='1'))
    with pytest.raises(adapter.BookingError) as exc:
        adapter.find_profile(tmp_path)
    assert exc.value.code == 'PROFILE_DISCOVERY_FAILED'


def test_active_unreadable_refuses_even_with_usable_profile(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest, subprocess
    good, bad = tmp_path / 'webview_good', tmp_path / 'webview_bad'
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stderr='', stdout=f'n{good}/file\nn{bad}/file\n'))
    def read(path):
        if path == bad:
            raise adapter.BookingError('SESSION_READ_FAILED', 'private information')
        return SimpleNamespace(profile=path, uid='1')
    monkeypatch.setattr(adapter, 'read_entry', read)
    with pytest.raises(adapter.BookingError) as exc:
        adapter.find_profile(tmp_path)
    assert exc.value.code == 'SESSION_READ_FAILED'
    assert 'private information' not in str(exc.value)


def test_no_active_profile_ignores_other_roots(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest, subprocess
    root = tmp_path / 'profiles'
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stderr='', stdout=f'n{root}2/webview_other/file\n'))
    monkeypatch.setattr(adapter, 'read_entry', lambda p: pytest.fail('must not read'))
    with pytest.raises(adapter.BookingError) as exc:
        adapter.find_profile(root)
    assert exc.value.code == 'NO_ENTRY'


def _disk_profile(root, name, monkeypatch):
    from types import SimpleNamespace
    import subprocess
    profile = root / name
    (profile / 'Local Storage' / 'leveldb').mkdir(parents=True)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stderr='', stdout=''))
    return profile


def test_disk_fallback_when_wechat_is_not_running(tmp_path, monkeypatch):
    # 2026-10-31 真实故障：微信在 06:40 没在跑，lsof 一条配置都看不到。
    # 票据仍在磁盘上的 Local Storage 里，可以直接用来换票。
    from types import SimpleNamespace
    root = tmp_path / 'profiles'
    profile = _disk_profile(root, 'webview_cached', monkeypatch)
    monkeypatch.setattr(adapter, 'read_entry', lambda path: SimpleNamespace(profile=path, uid='1'))
    assert adapter.find_profile(root).profile == profile


def test_disk_fallback_skips_unreadable_sibling(tmp_path, monkeypatch):
    from types import SimpleNamespace
    root = tmp_path / 'profiles'
    good = _disk_profile(root, 'webview_good', monkeypatch)
    bad = root / 'webview_bad'
    (bad / 'Local Storage' / 'leveldb').mkdir(parents=True)
    def read(path):
        if path == bad:
            raise adapter.BookingError('SESSION_READ_FAILED', 'unreadable')
        return SimpleNamespace(profile=path, uid='1')
    monkeypatch.setattr(adapter, 'read_entry', read)
    assert adapter.find_profile(root).profile == good


def test_disk_fallback_still_refuses_two_usable_accounts(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    root = tmp_path / 'profiles'
    _disk_profile(root, 'webview_a', monkeypatch)
    _disk_profile(root, 'webview_b', monkeypatch)
    monkeypatch.setattr(adapter, 'read_entry', lambda path: SimpleNamespace(profile=path, uid=path.name))
    with pytest.raises(adapter.BookingError) as exc:
        adapter.find_profile(root)
    assert exc.value.code == 'AMBIGUOUS_PROFILE'


def test_disk_fallback_reports_no_entry_when_nothing_readable(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pytest
    root = tmp_path / 'profiles'
    _disk_profile(root, 'webview_empty', monkeypatch)
    def read(path):
        raise adapter.BookingError('NO_ENTRY', 'please open the mini program')
    monkeypatch.setattr(adapter, 'read_entry', read)
    with pytest.raises(adapter.BookingError) as exc:
        adapter.find_profile(root)
    assert exc.value.code == 'NO_ENTRY'


def test_active_webview_without_xmu_entry_falls_back_to_disk(tmp_path, monkeypatch):
    # 微信开着、但当前 webview 是别的小程序（配置里没有厦大体育）：
    # 旧代码会直接 NO_ENTRY 放弃，现在应该继续用磁盘上的票据。
    from types import SimpleNamespace
    import subprocess
    root = tmp_path / 'profiles'
    other = root / 'webview_other_miniprogram'
    cached = root / 'webview_cached'
    for path in (other, cached):
        (path / 'Local Storage' / 'leveldb').mkdir(parents=True)
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(
        returncode=0, stderr='', stdout=f'n{other}/Local Storage/leveldb/LOCK\n'))
    def read(path):
        if path == other:
            raise adapter.BookingError('NO_ENTRY', 'not the xmu mini program')
        return SimpleNamespace(profile=path, uid='1')
    monkeypatch.setattr(adapter, 'read_entry', read)
    assert adapter.find_profile(root).profile == cached


def test_ticket_age_hours_reads_seconds_and_milliseconds():
    from types import SimpleNamespace
    from datetime import datetime, timezone
    now = datetime.now(adapter.CHINA)
    seconds = str(int(now.timestamp()) - 7200)
    assert 1.9 < adapter.ticket_age_hours(SimpleNamespace(fields={'timestamp': seconds})) < 2.1
    assert 1.9 < adapter.ticket_age_hours(SimpleNamespace(fields={'timestamp': seconds + '000'})) < 2.1
    assert adapter.ticket_age_hours(SimpleNamespace(fields={})) is None


def test_booking_dispatch_pins_identity_and_daily_key(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import hashlib
    ticket = SimpleNamespace(uid='123', profile=tmp_path)
    fingerprint = hashlib.sha256(b'123').hexdigest()
    monkeypatch.setattr(adapter, 'find_profile', lambda: ticket)
    monkeypatch.setattr(adapter, 'check_auth', lambda profile: {'ok':True,'account_checked':True,'account_fingerprint':fingerprint})
    calls = []
    monkeypatch.setattr(adapter, 'run_candidates', lambda target, **kw: calls.append((target,kw)) or {'ok':True,'state':'submitted'})
    result = adapter.execute_booking(datetime(2026,10,28,7,0,tzinfo=adapter.CHINA), fingerprint)
    target, options = calls[0]
    assert result['ok'] and target.date == '2026-10-29'
    assert options['profile'] == tmp_path and options['expected_account'] == fingerprint
    assert options['idempotency_key'] == 'xiang-an-gym:2026-10-29'
    assert options['commit'] is True and callable(options['fallbacks']) and callable(options['before_commit'])

def test_cli_does_not_leak_exception_text(monkeypatch, capsys):
    def fail(*args):
        raise RuntimeError('secret-token-value')
    monkeypatch.setattr(adapter, 'execute_booking', fail)
    assert adapter.main(['book','--account','a'*64]) == 1
    captured = capsys.readouterr()
    assert 'secret-token-value' not in captured.out + captured.err
    assert 'INTERNAL_ERROR' in captured.out

def test_unexpected_executor_failure_is_unknown(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import hashlib, pytest
    fingerprint = hashlib.sha256(b'1').hexdigest()
    monkeypatch.setattr(adapter, 'find_profile', lambda: SimpleNamespace(uid='1',profile=tmp_path))
    monkeypatch.setattr(adapter, 'check_auth', lambda p: {'ok':True,'account_checked':True,'account_fingerprint':fingerprint})
    def fail(*args, **kwargs): raise RuntimeError('failure after potential send')
    monkeypatch.setattr(adapter, 'run_candidates', fail)
    with pytest.raises(adapter.BookingError) as exc:
        adapter.execute_booking(datetime(2026,10,28,7,tzinfo=adapter.CHINA), fingerprint)
    assert exc.value.code == 'SUBMIT_UNKNOWN'

def test_guard_blocks_even_if_prior_uncertain_order_now_found(tmp_path, monkeypatch):
    import json, pytest
    requests = tmp_path / 'requests'
    requests.mkdir()
    target = adapter.daily_target(datetime(2026,10,28,7,tzinfo=adapter.CHINA))
    (requests / 'prior.json').write_text(json.dumps({'account_fingerprint':'a', 'state':'unknown', 'target':{'sport':target.sport,'venue':target.venue,'date':target.date}}))
    with pytest.raises(adapter.BookingError) as exc:
        adapter.guard_unresolved(target, 'a', tmp_path)
    assert exc.value.code == 'RESULT_UNCONFIRMED'

def test_account_mismatch_never_reaches_auth_or_booking(monkeypatch, tmp_path):
    from types import SimpleNamespace
    import pytest
    monkeypatch.setattr(adapter, 'find_profile', lambda: SimpleNamespace(uid='1',profile=tmp_path))
    monkeypatch.setattr(adapter, 'check_auth', lambda *args: pytest.fail('auth must not run'))
    monkeypatch.setattr(adapter, 'run_candidates', lambda *args, **kw: pytest.fail('booking must not run'))
    with pytest.raises(adapter.BookingError) as exc:
        adapter.execute_booking(datetime(2026,10,28,7,tzinfo=adapter.CHINA), '0'*64)
    assert exc.value.code == 'ACCOUNT_CHANGED'
