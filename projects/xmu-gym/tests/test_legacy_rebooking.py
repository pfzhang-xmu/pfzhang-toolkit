import hashlib
import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.booking import orders
spec = importlib.util.spec_from_file_location('rebook_backend', ROOT / 'app/backend.py')
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)

TASK = {'sport':'gym', 'venue':'venue', 'target_date':'2026-10-29', 'target_time_start':'13:30', 'target_time_end':'15:00'}
ACCOUNT = hashlib.sha256(b'uid').hexdigest()
TARGET = {'sport':'gym', 'venue':'venue', 'date':'2026-10-29', 'time':'13:30', 'end':'15:00', 'court':'02'}

@pytest.fixture
def setup(tmp_path):
    booking = tmp_path/'booking'; booking.mkdir()
    requests = tmp_path/'requests'; requests.mkdir()
    identity = json.dumps([ACCOUNT, 'gym', 'venue', '2026-10-29'], ensure_ascii=False, separators=(',', ':'))
    marker = booking/(hashlib.sha256(identity.encode()).hexdigest()+'.json')
    marker.write_text(json.dumps({'state':'attempted','account_fingerprint':ACCOUNT,'sport':'gym','venue':'venue','date':'2026-10-29'}))
    legacy = 'xmu-gym:' + hashlib.sha256(b'gym|venue|2026-10-29').hexdigest()
    path = requests/(hashlib.sha256(('uid|'+legacy).encode()).hexdigest()+'.json')
    journal = {'state':'submitted','verified':True,'account_fingerprint':ACCOUNT,'target':TARGET,'submitted_target':TARGET,'order_id':'42'}
    path.write_text(json.dumps(journal))
    detail = {'details':[{'id':'71','status':'3','date':'2026-10-29','interval_time':'13:30-15:00','area_name':'02'}]}
    mod = MagicMock(); mod.data_root.return_value=tmp_path
    mod.find_profile.return_value=SimpleNamespace(uid='uid',profile='profile'); mod.hashlib=hashlib
    mod.check_auth.return_value={'ok':True,'account_checked':True,'account_fingerprint':ACCOUNT}
    mod.Target.side_effect=lambda *a, **kw: SimpleNamespace(sport=a[0],venue=a[1],date=a[2])
    mod.run_candidates.return_value={'ok':True,'verified':True}
    client=SimpleNamespace()
    @contextmanager
    def order_client(account):
        assert account == ACCOUNT
        yield client
    instance=b.Backend()
    with patch.object(b,'BOOKING_ROOT',booking), patch.object(b,'load_adapter',return_value=mod), patch.object(instance,'order_client',side_effect=order_client), patch.object(orders,'read_detail',return_value=detail), patch.object(orders,'active_rows',return_value=[]):
        yield SimpleNamespace(instance=instance, mod=mod, path=path, marker=marker, journal=journal, detail=detail, root=tmp_path)

def test_cancelled_legacy_order_rebooks_with_fresh_key(setup):
    before=setup.path.read_bytes(); original=setup.marker.read_bytes()
    assert setup.instance.book(TASK, ACCOUNT)['ok']
    key=setup.mod.run_candidates.call_args.kwargs['idempotency_key']
    assert key != 'xmu-gym:'+hashlib.sha256(b'gym|venue|2026-10-29').hexdigest()
    assert setup.path.read_bytes()==before and setup.marker.read_bytes()==original
    with pytest.raises(ValueError): setup.instance.book(TASK, ACCOUNT)
    assert setup.mod.run_candidates.call_count == 1

@pytest.mark.parametrize('fallback', [False, True])
def test_verified_presubmit_journal_allows_fresh_generation(setup, fallback):
    setup.journal = {'state':'not_submitted', 'code':'PAYMENT_REQUIRED', 'account_fingerprint':ACCOUNT,
                     'target':TARGET}
    setup.path.write_text(json.dumps(setup.journal))
    original = setup.path.read_bytes(), setup.marker.read_bytes()
    task = dict(TASK, allow_time_fallback=fallback)
    def runner(*args, **kwargs):
        kwargs['before_commit'](None, SimpleNamespace(sport='gym',venue='venue',date='2026-10-29'))
        return {'ok': True}
    setup.mod.run_candidates.side_effect = runner
    assert setup.instance.book(task, ACCOUNT)['ok']
    assert setup.mod.run_candidates.call_args.kwargs['idempotency_key'].startswith('xmu-gym:rebooking:')
    assert (setup.path.read_bytes(), setup.marker.read_bytes()) == original
    with pytest.raises(ValueError): setup.instance.book(task, ACCOUNT)


@pytest.mark.parametrize('mutation', ['unknown', 'unverified', 'missing_target', 'wrong_account', 'other_journal', 'unresolved_journal', 'fallback', 'active', 'duplicate_detail', 'wrong_status', 'wrong_court', 'bad_marker', 'auth'])
def test_ambiguous_proof_never_submits(setup, mutation):
    if mutation == 'unknown': setup.journal['state'] = 'unknown'
    if mutation == 'unverified': setup.journal['verified'] = False
    if mutation == 'missing_target': setup.journal.pop('submitted_target')
    if mutation == 'wrong_account': setup.journal['account_fingerprint'] = 'b'*64
    if mutation in ('other_journal', 'unresolved_journal'):
        extra = dict(setup.journal)
        if mutation == 'unresolved_journal': extra['state'] = 'unknown'
        (setup.path.parent/'extra.json').write_text(json.dumps(extra))
    if mutation == 'fallback':
        directory=setup.root/'fallback-requests'; directory.mkdir()
        (directory/'batch.json').write_text(json.dumps({'state':'complete','targets':[TARGET]}))
    if mutation == 'active': orders.active_rows.return_value = [{'sport':'gym','venue':'venue','date':'2026-10-29'}]
    if mutation == 'duplicate_detail': setup.detail['details'].append(dict(setup.detail['details'][0]))
    if mutation == 'wrong_status': setup.detail['details'][0]['status'] = '1'
    if mutation == 'wrong_court': setup.detail['details'][0]['area_name'] = '03'
    if mutation == 'bad_marker': setup.marker.write_text('{}')
    if mutation == 'auth': setup.mod.check_auth.return_value = {'ok':False}
    setup.path.write_text(json.dumps(setup.journal))
    with pytest.raises(ValueError): setup.instance.book(TASK, ACCOUNT)
    setup.mod.run_candidates.assert_not_called()

def test_cancellation_is_rechecked_before_commit_and_unknown_stops_retry(setup):
    def runner(*args, **kwargs):
        setup.detail['details'][0]['status'] = '1'
        kwargs['before_commit'](None, SimpleNamespace(sport='gym',venue='venue',date='2026-10-29'))
    setup.mod.run_candidates.side_effect = runner
    with pytest.raises(ValueError): setup.instance.book(TASK, ACCOUNT)
    with pytest.raises(ValueError): setup.instance.book(TASK, ACCOUNT)
    assert setup.mod.run_candidates.call_count == 1

def test_concurrent_rebookings_submit_at_most_once(setup):
    import threading
    entered, release = threading.Event(), threading.Event()
    outcomes = []
    def runner(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return {'ok':True}
    def run():
        try: outcomes.append(setup.instance.book(TASK, ACCOUNT))
        except Exception as exc: outcomes.append(exc)
    setup.mod.run_candidates.side_effect = runner
    first = threading.Thread(target=run); second = threading.Thread(target=run)
    first.start()
    assert entered.wait(5)
    second.start()
    release.set()
    first.join(5); second.join(5)
    assert not first.is_alive() and not second.is_alive()
    assert setup.mod.run_candidates.call_count == 1
    assert sum(isinstance(item, ValueError) for item in outcomes) == 1


def test_independent_rebookings_use_distinct_keys(setup):
    import shutil
    second = setup.root/'second-booking'
    shutil.copytree(setup.marker.parent, second)
    setup.instance.book(TASK, ACCOUNT)
    first = setup.mod.run_candidates.call_args.kwargs['idempotency_key']
    with patch.object(b, 'BOOKING_ROOT', second):
        setup.instance.book(TASK, ACCOUNT)
    second_key = setup.mod.run_candidates.call_args.kwargs['idempotency_key']
    assert first != second_key


def test_lost_response_keeps_rebooking_marker(setup):
    setup.mod.run_candidates.side_effect = RuntimeError('lost response')
    with pytest.raises(RuntimeError): setup.instance.book(TASK, ACCOUNT)
    with pytest.raises(ValueError): setup.instance.book(TASK, ACCOUNT)
    assert setup.mod.run_candidates.call_count == 1


def test_current_generation_not_submitted_journal_allows_internal_retry(setup):
    keys = []
    def runner(*args, **kwargs):
        key = kwargs['idempotency_key']
        keys.append(key)
        kwargs['before_commit'](None, SimpleNamespace(sport='gym', venue='venue', date='2026-10-29'))
        if len(keys) == 1:
            record = {'state':'not_submitted', 'code':'CAPTCHA_FETCH_FAILED',
                      'account_fingerprint':ACCOUNT, 'target':TARGET}
            path = setup.path.parent / (hashlib.sha256(('uid|' + key).encode()).hexdigest() + '.json')
            path.write_text(json.dumps(record))
            return {'ok':False, 'verified':False, 'state':'failed', 'code':'HTTP_FAILED'}
        return {'ok':True, 'verified':True}
    setup.mod.run_candidates.side_effect = runner
    result = setup.instance.book(
        TASK, ACCOUNT,
        retry=lambda attempt, outcome: attempt < 1 and outcome.get('code') == 'HTTP_FAILED')
    assert result['ok'] and result['verified']
    assert len(keys) == 2 and keys[0] != keys[1] and keys[1].endswith(':attempt:1')
    # The old generation's not_submitted journal is preserved, not deleted.
    journal = json.loads((setup.path.parent / (hashlib.sha256(('uid|' + keys[0]).encode()).hexdigest() + '.json')).read_text())
    assert journal['state'] == 'not_submitted'


def test_current_generation_retry_without_any_journal_is_not_blocked(setup):
    keys = []
    def runner(*args, **kwargs):
        keys.append(kwargs['idempotency_key'])
        kwargs['before_commit'](None, SimpleNamespace(sport='gym', venue='venue', date='2026-10-29'))
        if len(keys) == 1:
            # A transient failure before submit opens its journal must not make
            # the next attempt require a nonexistent journal.
            return {'ok':False, 'verified':False, 'state':'failed', 'code':'HTTP_FAILED'}
        return {'ok':True, 'verified':True}
    setup.mod.run_candidates.side_effect = runner
    result = setup.instance.book(
        TASK, ACCOUNT,
        retry=lambda attempt, outcome: attempt < 1 and outcome.get('code') == 'HTTP_FAILED')
    assert result['ok'] and len(keys) == 2


def test_previous_generation_attempt_journal_is_allowed_but_revalidated(setup):
    marker, base = install_legacy_recovery(setup)
    generation = 'a' * 64
    attempt_key = 'xmu-gym:rebooking:' + generation + ':attempt:1'
    attempt = base.parent / (hashlib.sha256(('uid|' + attempt_key).encode()).hexdigest() + '.json')
    attempt.write_text(json.dumps({'state':'not_submitted', 'code':'CAPTCHA_FETCH_FAILED',
                                   'account_fingerprint':ACCOUNT, 'target':TARGET}))
    setup.mod.run_candidates.return_value = {'ok': True, 'verified': True}
    assert setup.instance.book(TASK, ACCOUNT)['ok']
    assert attempt.exists() and base.exists()


def test_two_cancelled_generations_allow_another_fresh_attempt(setup):
    keys = []
    def runner(*args, **kwargs):
        key = kwargs['idempotency_key']
        keys.append(key)
        kwargs['before_commit'](None, SimpleNamespace(sport='gym', venue='venue', date='2026-10-29'))
        journal = dict(setup.journal, order_id=str(43 + len(keys)))
        path = setup.path.parent / (hashlib.sha256(('uid|' + key).encode()).hexdigest() + '.json')
        path.write_text(json.dumps(journal))
        return {'ok': True}
    setup.mod.run_candidates.side_effect = runner
    assert setup.instance.book(TASK, ACCOUNT)['ok']
    assert setup.instance.book(TASK, ACCOUNT)['ok']
    assert len(set(keys)) == 2
    assert setup.path.exists()


def install_legacy_recovery(setup, state='submitted'):
    generation = 'a' * 64
    marker = setup.marker.with_name(setup.marker.stem + '.rebooking.json')
    marker.write_text(json.dumps({'state':'attempted', 'account_fingerprint':ACCOUNT, 'sport':'gym', 'venue':'venue', 'date':'2026-10-29', 'generation':generation}))
    key = 'xmu-gym:rebooking:' + generation
    journal = dict(setup.journal, state=state, order_id='43')
    path = setup.path.parent / (hashlib.sha256(('uid|' + key).encode()).hexdigest() + '.json')
    path.write_text(json.dumps(journal))
    return marker, path

def test_legacy_recovery_marker_and_journal_are_preserved(setup):
    marker, journal = install_legacy_recovery(setup)
    prior = marker.read_bytes(), journal.read_bytes()
    assert setup.instance.book(TASK, ACCOUNT)['ok']
    assert (marker.read_bytes(), journal.read_bytes()) == prior

@pytest.mark.parametrize('problem', ['unknown', 'missing', 'active', 'mismatch', 'duplicate_order'])
def test_bad_second_generation_blocks_all_submissions(setup, problem):
    marker, path = install_legacy_recovery(setup, 'unknown' if problem == 'unknown' else 'submitted')
    if problem == 'missing': path.unlink()
    if problem == 'active':
        orders.read_detail.side_effect = lambda client, oid: {'details':[dict(setup.detail['details'][0], status='1' if str(oid) == '43' else '3')]}
    if problem in ('mismatch', 'duplicate_order'):
        value = json.loads(path.read_text())
        if problem == 'mismatch': value['submitted_target'] = dict(TARGET, court='different')
        else: value['order_id'] = '42'
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError): setup.instance.book(TASK, ACCOUNT)
    setup.mod.run_candidates.assert_not_called()

def test_generation_key_collision_never_replays_existing_journal(setup):
    marker, path = install_legacy_recovery(setup)
    with patch.object(b.secrets, 'token_hex', return_value='a'*64):
        with pytest.raises(ValueError): setup.instance.book(TASK, ACCOUNT)
    setup.mod.run_candidates.assert_not_called()


@pytest.mark.skipif(__import__("os").name == "nt", reason="fork-only process test")
def test_rebooking_processes_share_lock_and_only_one_submits(setup):
    import multiprocessing
    ctx = multiprocessing.get_context('fork')
    entered, release = ctx.Event(), ctx.Event()
    results = ctx.Queue()
    def runner(*args, **kwargs):
        results.put('submitted')
        entered.set()
        if not release.wait(5): raise RuntimeError('timeout')
        return {'ok':True}
    setup.mod.run_candidates.side_effect = runner
    def run():
        try: setup.instance.book(TASK, ACCOUNT)
        except ValueError: results.put('blocked')
    first, second = ctx.Process(target=run), ctx.Process(target=run)
    first.start()
    assert entered.wait(5)
    second.start()
    release.set()
    first.join(5); second.join(5)
    assert first.exitcode == second.exitcode == 0
    assert sorted([results.get(timeout=2), results.get(timeout=2)]) == ['blocked', 'submitted']


def test_generation_key_dangling_journal_symlink_never_replayed(setup):
    generation = 'b'*64
    key = 'xmu-gym:rebooking:' + generation
    path = setup.path.parent / (hashlib.sha256(('uid|' + key).encode()).hexdigest() + '.json')
    path.symlink_to(setup.root / 'missing')
    with patch.object(b.secrets, 'token_hex', return_value=generation):
        with pytest.raises(ValueError): setup.instance.book(TASK, ACCOUNT)
    setup.mod.run_candidates.assert_not_called()
