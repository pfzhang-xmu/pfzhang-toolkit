"""Real backend: WeChat profile -> booking core. No UI, no scheduler."""
from datetime import datetime
import hashlib
import json
from contextlib import contextmanager
import os
import re
import secrets
from pathlib import Path

from app import config
from core import adapter
from core.locks import lock_exclusive, lock_nonblocking

ROOT = Path(__file__).resolve().parent.parent
STATE_ROOT = config.STATE_ROOT
BOOKING_ROOT = STATE_ROOT / 'bookings'
CANCEL_ROOT = STATE_ROOT / 'cancellations'
MAX_BOOKING_ATTEMPTS = 13  # first attempt + 12 bounded pre-submit retries


def _fsync_dir(path: Path) -> None:
    """Best-effort directory fsync; Windows cannot open directories this way."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass

@contextmanager
def cancellation_guard(account, oid, did):
    root = Path(CANCEL_ROOT)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir(): raise ValueError('取消记录目录无效')
    _chmod(root, 0o700)
    fd = os.open(root / 'cancel.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        try: lock_nonblocking(fd)
        except BlockingIOError: raise ValueError('已有取消操作正在进行') from None
        name = hashlib.sha256(json.dumps([account, oid, did], separators=(',', ':')).encode()).hexdigest() + '.json'
        try: marker = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError: raise ValueError('已有取消尝试，请到小程序核对，勿重复取消') from None
        with os.fdopen(marker, 'w') as out:
            out.write('{"state":"attempted"}')
            out.flush(); os.fsync(out.fileno())
        _fsync_dir(root)
        yield
    finally: os.close(fd)

@contextmanager
def booking_guard(task, account, rebooking_check=None):
    """Serialize UI writers across processes and retain an attempt on any outcome.

    The marker is intentionally conservative: it needs manual reconciliation even
    after a pre-submit failure. Never remove it without checking the real orders.
    """
    root = Path(BOOKING_ROOT)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('预约存储目录无效')
    _chmod(root, 0o700)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(root / 'booking.lock', flags, 0o600)
    try:
        lock_exclusive(fd)
        identity = json.dumps([account, task['sport'], task['venue'], task['target_date']], ensure_ascii=False, separators=(',', ':'))
        name = hashlib.sha256(identity.encode()).hexdigest() + '.json'
        generation = None
        try:
            marker = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        except FileExistsError:
            if rebooking_check is None:
                raise ValueError('已有预约尝试，请先核实订单，勿重复提交') from None
            rebooking_check()
            generation = secrets.token_hex(32)
            recovery = root / (name[:-5] + '.rebooking.' + generation + '.json')
            try:
                marker = os.open(recovery, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            except FileExistsError:
                raise ValueError('已有预约尝试，请先核实订单，勿重复提交') from None
        with os.fdopen(marker, 'w', encoding='utf-8') as out:
            json.dump({'state': 'attempted', 'account_fingerprint': account,
                       'sport': task['sport'], 'venue': task['venue'], 'date': task['target_date'],
                       'generation': generation}, out, ensure_ascii=False)
            out.flush()
            os.fsync(out.fileno())
        _fsync_dir(root)
        yield generation
    finally:
        os.close(fd)

def guard_fallback_journals(root, target):
    # Legacy batch journals omit account fingerprints, so matching unresolved
    # batches must conservatively block every account.
    for path in (Path(root) / 'fallback-requests').glob('*.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(value, dict) or not isinstance(value.get('targets'), list)
                    or not value['targets'] or not all(isinstance(t, dict) and all(isinstance(t.get(k), str) and t[k] for k in ('sport', 'venue', 'date')) for t in value['targets'])
                    or value.get('state') not in ('running', 'complete')):
                raise ValueError('invalid journal')
        except (OSError, ValueError, TypeError):
            raise ValueError('备选预约记录无法核实，请检查订单') from None
        if value['state'] == 'running' and any(all(t.get(k) == getattr(target, k) for k in ('sport', 'venue', 'date')) for t in value['targets']):
            raise ValueError('备选预约结果未确认，请检查订单')


def later_fallbacks(rows, earliest):
    valid = re.compile(r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    pairs = set()
    for row in rows:
        start, end = row.get('start'), row.get('end')
        if isinstance(start, str) and isinstance(end, str) and valid.fullmatch(start) and valid.fullmatch(end) and earliest <= start < end:
            pairs.add((start, end))
    return [{'time': start, 'end': end} for start, end in sorted(pairs)[:8]]

def load_adapter():
    """Kept as a function so tests can patch the adapter module."""
    return adapter

class Backend:
    def __init__(self):
        os.environ['XMU_GYM_DATA'] = str(STATE_ROOT)
        self.selected = config.selected_profile()
        self.last_label = ''

    def _find(self, adapter_module=None):
        module = adapter_module or load_adapter()
        if self.selected:
            return module.find_profile(selected=self.selected)
        return module.find_profile()

    @staticmethod
    def _digest(adapter_module, value):
        module = getattr(adapter_module, 'hashlib', None) or hashlib
        return module.sha256(value).hexdigest()
    def auth(self):
        adapter = load_adapter(); entry = self._find(adapter)
        self.last_label = adapter._label(entry)
        fingerprint = self._digest(adapter, entry.uid.encode())
        auth = adapter.check_auth(entry.profile)
        if not auth.get('ok'):
            code = auth.get('code')
            if code in {'HTTP_FAILED', 'SERVER_BUSY', 'RATE_LIMITED', 'ORDER_CHECK_TIMEOUT'}:
                raise adapter.BookingError(code, '账号验证暂时失败') from None
            raise ValueError('账号验证失败，请检查微信登录')
        if not auth.get('account_checked') or auth.get('account_fingerprint') != fingerprint:
            raise ValueError('账号验证失败，请检查微信登录')
        return fingerprint
    @contextmanager
    def order_client(self, account):
        adapter = load_adapter()
        from core.booking.orders import OrdersClient
        entry = self._find(adapter)
        if self._digest(adapter, entry.uid.encode()) != account:
            raise ValueError('账号已改变，请重新检查')
        client = OrdersClient(entry)
        try:
            if not client.login().get('account_checked'):
                raise ValueError('账号验证失败，请重新检查')
            yield client
        finally: client.http.close()

    def list_orders(self, account):
        with self.order_client(account) as client:
            from core.booking import orders
            rows = orders.active_rows(client)
            return {'ok':True, 'orders':rows, 'account_fingerprint':account,
                    'cached':False, 'fetched_at':orders.current_time().isoformat()}

    def cancel_order(self, account, oid, did):
        adapter = load_adapter()
        from core.booking import orders
        if not isinstance(oid, str) or not isinstance(did, str) or orders.identifier(oid) != oid or orders.identifier(did) != did:
            raise ValueError('取消预约标识无效')
        with self.order_client(account) as client:
            with cancellation_guard(account, oid, did):
                return orders.cancel(client, oid, did)

    def availability(self, args):
        adapter=load_adapter()
        from core.booking.availability import AvailabilityClient, snapshot
        entry=self._find(adapter); fingerprint=self._digest(adapter, entry.uid.encode())
        client=AvailabilityClient(entry)
        try:
            auth=client.login()
            if not auth.get('account_checked'): raise ValueError('账号未验证')
            target=adapter.Target(args['sport'],args['venue'],args['date'],'07:00')
            data=client.intervals(client.resolve_venue(target))
            result=snapshot(data,args['date'],datetime.now(adapter.CHINA).isoformat())
            published={str(block.get('date')) for block in data.get('time_slot_list', [])}
            published.update(str(day.get('date_int')) for day in data.get('date_list', []))
            state='availability_ready' if args['date'] in published else 'date_not_published'
            return {'ok':True,'state':state,'sport':args['sport'],'venue':args['venue'],
                    'cached':False,'account_fingerprint':fingerprint,**result}
        finally: client.http.close()
    def _prove_legacy_cancelled(self, task, account, current_generation=None):
        adapter = load_adapter()
        from core.booking import orders
        entry = self._find(adapter)
        if self._digest(adapter, entry.uid.encode()) != account:
            raise ValueError('账号已改变，请重新检查')
        auth = adapter.check_auth(entry.profile)
        if not auth.get('ok') or not auth.get('account_checked') or auth.get('account_fingerprint') != account:
            raise ValueError('账号验证失败，请重新检查')

        scope = {'sport': task['sport'], 'venue': task['venue'], 'date': task['target_date']}
        identity = json.dumps([account, task['sport'], task['venue'], task['target_date']], ensure_ascii=False, separators=(',', ':'))
        marker_path = Path(BOOKING_ROOT) / (self._digest(adapter, identity.encode()) + '.json')
        try:
            if marker_path.is_symlink(): raise ValueError()
            marker = json.loads(marker_path.read_text(encoding='utf-8'))
            if not isinstance(marker, dict) or marker.get('state') != 'attempted' or marker.get('account_fingerprint') != account or any(marker.get(k) != v for k, v in scope.items()): raise ValueError()
            root = Path(adapter.data_root())
            # A matching fallback batch cannot establish which single attempt was used.
            for path in (root / 'fallback-requests').glob('*.json'):
                if path.is_symlink(): raise ValueError()
                batch = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(batch, dict) or batch.get('state') not in ('running', 'complete') or not isinstance(batch.get('targets'), list) or not batch['targets'] or any(not isinstance(t, dict) or any(not isinstance(t.get(k), str) or not t[k] for k in scope) for t in batch['targets']): raise ValueError()
                if any(all(t[k] == v for k, v in scope.items()) for t in batch['targets']): raise ValueError()
            legacy_key = 'xmu-gym:' + self._digest(adapter, (scope['sport']+'|'+scope['venue']+'|'+scope['date']).encode())
            # Required journals must exist; allowed journals may exist because a
            # bounded retry can stop before or after writing its own journal.
            # Every matched record is re-validated below, so an optional earlier
            # attempt can never be treated as a successful booking on its own.
            required = {self._digest(adapter, (entry.uid+'|'+legacy_key).encode()) + '.json'}
            allowed = set(required)
            generations = set()
            prefix = marker_path.stem + '.rebooking'
            current_found = current_generation is None
            for path in marker_path.parent.glob(prefix + '*.json'):
                if path.is_symlink(): raise ValueError()
                value = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(value, dict) or value.get('state') != 'attempted' or value.get('account_fingerprint') != account or any(value.get(k) != v for k, v in scope.items()): raise ValueError()
                generation = value.get('generation')
                if not isinstance(generation, str) or not re.fullmatch(r'[0-9a-f]{64}', generation) or generation in generations: raise ValueError()
                if path.name not in (prefix + '.json', prefix + '.' + generation + '.json'): raise ValueError()
                generations.add(generation)
                if generation == current_generation:
                    current_found = True
                base_key = 'xmu-gym:rebooking:' + generation
                for index in range(MAX_BOOKING_ATTEMPTS):
                    key = base_key + (':attempt:' + str(index) if index else '')
                    allowed.add(self._digest(adapter, (entry.uid+'|'+key).encode()) + '.json')
                if generation != current_generation:
                    # Older generations must still be fully accounted for.
                    required.add(self._digest(adapter, (entry.uid+'|'+base_key).encode()) + '.json')
            if not current_found: raise ValueError()
            matches = {}
            for path in (root / 'requests').glob('*.json'):
                if path.is_symlink(): raise ValueError()
                record = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(record, dict) or not isinstance(record.get('target'), dict) or any(not isinstance(record['target'].get(k), str) or not record['target'][k] for k in scope): raise ValueError()
                if record.get('account_fingerprint') not in (None, account): continue
                if all(record['target'].get(k) == v for k, v in scope.items()): matches[path.name] = record
            if not required <= set(matches) or not set(matches) <= allowed: raise ValueError()
            targets = []
            seen_orders = set()
            for record in matches.values():
                if (record.get('state') == 'not_submitted' and record.get('account_fingerprint') == account
                        and 'submitted_target' not in record and isinstance(record.get('code'), str)
                        and record['code']):
                    continue
                if task.get('allow_time_fallback') is True:
                    raise ValueError()
                target = record.get('submitted_target')
                if record.get('account_fingerprint') != account or record.get('state') != 'submitted' or record.get('verified') is not True or not isinstance(target, dict) or any(target.get(k) != v for k, v in scope.items()): raise ValueError()
                oid = orders.identifier(record.get('order_id'))
                if not oid or oid in seen_orders or any(not isinstance(target.get(k), str) or not target[k] for k in ('time', 'end', 'court')): raise ValueError()
                seen_orders.add(oid)
                targets.append((oid, target))
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError('已有预约记录无法安全核实，请检查订单') from None
        with self.order_client(account) as client:
            for oid, target in targets:
                detail = orders.read_detail(client, oid)
                items = orders.collection(detail.get('details'))
                matching = [item for item in items if item.get('date') == target['date'] and item.get('area_name') == target['court'] and re.findall(r'\d{2}:\d{2}', str(item.get('interval_time', ''))) == [target['time'], target['end']]]
                if len(matching) != 1 or str(matching[0].get('status')) != '3' or not orders.detail_id(matching[0]):
                    raise ValueError('原预约取消状态无法确认')
            rows = orders.active_rows(client)
            if any(all(row.get(k) == v for k, v in scope.items()) for row in rows):
                raise ValueError('已有有效预约，请先核实订单')

    def book(self, task, account, retry=None, eligibility=None):
        # Keep the cross-process lock for the whole bounded retry sequence. Each
        # attempt gets a fresh journal key; unknown/submitted outcomes are never
        # eligible for retry (the caller enforces that policy).
        with booking_guard(task, account, lambda: self._prove_legacy_cancelled(task, account)) as generation:
            attempt = 0
            while True:
                if attempt >= MAX_BOOKING_ATTEMPTS:
                    raise ValueError('重试次数已达上限，请检查订单')
                try:
                    result = self._book_locked(task, account, generation, attempt, eligibility)
                except Exception as exc:
                    if retry is None or not retry(attempt, exc):
                        raise
                else:
                    if ((result.get('ok') and result.get('verified'))
                            or retry is None or not retry(attempt, result)):
                        return result
                attempt += 1

    def _book_locked(self, task, account, generation=None, attempt=0, eligibility=None):
        adapter=load_adapter(); entry=adapter.find_profile(selected=self.selected)
        fingerprint=self._digest(adapter, entry.uid.encode())
        if fingerprint != account: raise ValueError('账号已改变，请重新检查')
        auth=adapter.check_auth(entry.profile)
        if not auth.get('ok'):
            code = auth.get('code')
            if code in {'HTTP_FAILED', 'SERVER_BUSY', 'RATE_LIMITED', 'ORDER_CHECK_TIMEOUT'}:
                raise adapter.BookingError(code, '账号验证暂时失败') from None
            raise ValueError('账号验证失败，请重新检查')
        if not auth.get('account_checked') or auth.get('account_fingerprint') != account:
            raise ValueError('账号验证失败，请重新检查')
        target=adapter.Target(task['sport'],task['venue'],task['target_date'],task['target_time_start'],
                              end=task['target_time_end'],court=task.get('prefer_court') or None,
                              court_offset=task.get('court_offset',0))
        def before_commit(client, candidate):
            adapter.guard_unresolved(candidate,account,adapter.data_root())
            if generation is not None:
                self._prove_legacy_cancelled(task, account, generation)
        def fallbacks():
            fresh = self._find(adapter)
            if self._digest(adapter, fresh.uid.encode()) != account:
                raise ValueError('账号已改变，请重新检查')
            client = adapter.Client(fresh)
            try:
                auth = client.login()
                if not auth.get('account_checked'):
                    raise ValueError('账号验证失败，请重新检查')
                data = client.intervals(client.resolve_venue(target))
                rows = adapter.snapshot(data, target.date, '')['slots']
                return later_fallbacks(rows, task['target_time_end'])
            finally:
                client.http.close()
        guard_fallback_journals(adapter.data_root(), target)
        identity=self._digest(adapter, (target.sport+'|'+target.venue+'|'+target.date).encode())
        key = 'xmu-gym:rebooking:'+generation if generation else 'xmu-gym:'+identity
        if attempt:
            key += ':attempt:' + str(attempt)
        if generation and (Path(adapter.data_root()) / 'requests' / (self._digest(adapter, (entry.uid+'|'+key).encode())+'.json')).exists():
            raise ValueError('预约标识已使用，请检查订单')
        def gated_eligibility(candidate):
            adapter.check_target(candidate)
            if eligibility is not None:
                try:
                    eligibility(candidate)
                except ValueError:
                    raise adapter.BookingError('BOOKING_WINDOW_CLOSED', '每日预约时间窗口已关闭') from None
        return adapter.run_candidates(target, profile=entry.profile, commit=True, captcha_first=True,
              solver=adapter.CaptchaSolver(), watch_seconds=5, eligibility=gated_eligibility,
              idempotency_key=key, expected_account=account, before_commit=before_commit,
              fallbacks=fallbacks if task.get('allow_time_fallback') is True else None)

    def entry_qr(self, order_id, details_id, account):
        from app.entry_qr import fetch_entry_qr
        return fetch_entry_qr(order_id, details_id, account)

    def profiles(self):
        return load_adapter().list_profiles()

    def select_profile(self, value):
        config.set_selected_profile(value)
        self.selected = value
