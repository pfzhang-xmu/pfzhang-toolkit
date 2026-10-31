"""Persist one daily policy and execute it once per Beijing target date.

The built-in scheduler invokes `python -m app.daily run` at 06:58 China time.
A durable attempt marker deliberately prevents automatic retries after any failure.
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

from app import config

CHINA = config.CHINA
DEFAULT_ROOT = config.STATE_ROOT
IMAGE_ROOT = None
IMAGE_TTL = 48 * 60 * 60


def _save_qr(png):
    if not isinstance(png, bytes) or not png.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('二维码图片无效')
    root = Path(IMAGE_ROOT) if IMAGE_ROOT else config.outbox()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise ValueError('二维码目录无效')
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    # Only our own old regular files; never follow links or remove a fresh image.
    cutoff = time.time() - IMAGE_TTL
    for path in root.glob('gym-entry-*.png'):
        try:
            if not path.is_symlink() and path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
    fd, name = tempfile.mkstemp(prefix='gym-entry-', suffix='.png', dir=root)
    try:
        try:
            os.fchmod(fd, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(fd, 'wb') as out:
            out.write(png)
        return name
    except BaseException:
        try: os.unlink(name)
        except OSError: pass
        raise


def _weekday_label(iso):
    try:
        day = datetime.strptime(str(iso), '%Y-%m-%d')
    except (ValueError, TypeError):
        return str(iso)
    return '%d月%d日 %s' % (day.month, day.day, '周一周二周三周四周五周六周日'[day.weekday() * 2:][:2])


def _notification(task, account, result, backend):
    # The booking remains successful even if any subsequent read/QR step fails.
    def safe(value):
        return re.sub(r'[\x00-\x1f\x7f]+', ' ', str(value))[:120]
    times = re.findall(r'\d{2}:\d{2}', result.get('time') or '')
    court = result.get('court')
    window = (times[0] + '–' + times[1]) if len(times) == 2 else (
        safe(task['target_time_start']) + '–' + safe(task['target_time_end']))
    lines = ['预约成功 · 已核实',
             '日期：' + _weekday_label(task['target_date']),
             '时段：' + window,
             '场馆：' + safe(task['venue'])]
    if isinstance(court, str) and court:
        lines.append('场地：' + safe(court) + (' 号场地' if court.isdigit() else ''))
    lines += ['', '入场码在下一张图，进馆直接出示。', '取消只能在开场前一小时之前，过期撤不了。']
    message = '\n'.join(lines)
    output = {'ok': True, 'verified': True, 'target_date': task['target_date'], 'message': message}
    try:
        oid = str(result.get('order_id') or '')
        if not re.fullmatch(r'[1-9][0-9]{0,19}', oid) or result.get('date') != task['target_date'] or len(times) != 2 or not isinstance(court, str) or not court:
            raise ValueError('订单信息不完整')
        listing = backend.list_orders(account)
        if listing.get('ok') is not True or listing.get('account_fingerprint') != account or not isinstance(listing.get('orders'), list):
            raise ValueError('订单列表无法核实')
        expected = {'order_id': oid, 'sport': task['sport'], 'venue': task['venue'], 'date': task['target_date'], 'start': times[0], 'end': times[1], 'court': court}
        rows = [row for row in listing['orders'] if isinstance(row, dict) and all(row.get(k) == v for k, v in expected.items())]
        if len(rows) != 1 or not isinstance(rows[0].get('details_id'), str) or not re.fullmatch(r'[1-9][0-9]{0,19}', rows[0]['details_id']):
            raise ValueError('订单详情不唯一')
        from app.entry_qr import fetch_entry_qr
        output['media_path'] = _save_qr(fetch_entry_qr(oid, rows[0]['details_id'], account))
    except Exception:
        output['message'] += '\n入场二维码暂不可用，请在小程序查看；勿重复预约。'
    return output


def validate(task, account):
    if not isinstance(task, dict) or not isinstance(account, str) or not re.fullmatch(r'[0-9a-f]{64}', account):
        raise ValueError('账号或预约配置无效')
    for key in ('sport', 'venue'):
        if not isinstance(task.get(key), str) or not task[key].strip():
            raise ValueError('场馆或项目无效')
    for key in ('target_time_start', 'target_time_end'):
        if not isinstance(task.get(key), str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', task[key]):
            raise ValueError('时段无效')
    if task['target_time_start'] >= task['target_time_end']:
        raise ValueError('时段无效')
    if type(task.get('allow_time_fallback')) is not bool:
        raise ValueError('备选时段配置无效')
    if task.get('prefer_court') not in (None, '') or task.get('court_offset', 0) not in (0, None):
        raise ValueError('每日预约暂不支持指定场地')
    return {key: task.get(key) for key in ('sport','venue','target_time_start','target_time_end','allow_time_fallback')}


class Store:
    def __init__(self, root=None):
        self.root = Path(root or config.STATE_ROOT)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError('每日预约存储目录无效')
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    def save(self, task, account):
        task = validate(task, account)
        data = json.dumps({'task': task, 'account': account}, ensure_ascii=False).encode()
        import tempfile
        fd, name = tempfile.mkstemp(prefix='.daily-', dir=self.root)
        try:
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):
                pass
            with os.fdopen(fd, 'wb') as out:
                out.write(data); out.flush(); os.fsync(out.fileno())
            os.replace(name, self.root / 'daily.json')
        finally:
            if os.path.exists(name): os.unlink(name)

    def load(self):
        path = self.root / 'daily.json'
        if path.is_symlink(): raise ValueError('每日预约配置无效')
        value = json.loads(path.read_text(encoding='utf-8'))
        return validate(value['task'], value['account']), value['account']

    def mark(self, day):
        path = self.root / ('attempt-' + day + '.json')
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            raise ValueError('当天已有预约尝试；请检查我的预约，勿重复提交')
        with os.fdopen(fd, 'w') as out:
            json.dump({'target_date': day, 'state': 'started'}, out)
            out.flush(); os.fsync(out.fileno())


WINDOW_MINUTES = 20
MAX_RETRIES = 12
MAX_ATTEMPTS = MAX_RETRIES + 1
TRANSIENT_CODES = frozenset({
    'TIMEOUT', 'HTTP_FAILED', 'SERVER_BUSY', 'RATE_LIMITED', 'ORDER_CHECK_TIMEOUT',
    'CAPTCHA_FETCH_FAILED',
})
SAFE_CODES = TRANSIENT_CODES | frozenset({
    'SESSION_EXPIRED', 'SUBMIT_UNKNOWN', 'RESULT_UNCONFIRMED', 'CAPTCHA_REJECTED',
    'CAPTCHA_UNREADABLE', 'DATE_NOT_OPEN', 'NOT_OPEN', 'FALLBACKS_EXHAUSTED', 'SLOT_OCCUPIED',
    'SLOT_LOCKED', 'SLOT_UNAVAILABLE', 'BOOKING_CLOSED', 'NO_SLOT',
    'BOOKING_WINDOW_CLOSED', 'ACCOUNT_CHANGED', 'ACCOUNT_MISMATCH',
    # 登录态类：都在最终提交之前，失败可以确定「一单都没发出去」
    'NO_ENTRY', 'INVALID_ENTRY', 'WRONG_ORIGIN', 'MISSING_READER', 'PROFILE_DISCOVERY_FAILED',
    'AMBIGUOUS_PROFILE', 'AUTH_FAILED', 'NATIVE_TOKEN_REQUIRED', 'NO_SESSION_COOKIE',
})

LOGIN_CODES = frozenset({
    'NO_ENTRY', 'INVALID_ENTRY', 'WRONG_ORIGIN', 'MISSING_READER', 'PROFILE_DISCOVERY_FAILED',
    'AMBIGUOUS_PROFILE', 'AUTH_FAILED', 'SESSION_EXPIRED', 'NATIVE_TOKEN_REQUIRED',
    'NO_SESSION_COOKIE',
})


PREWARM_TTL = 180        # 06:59:30 预热，07:00 用，最多容忍 3 分钟


def _usable_prewarm(prewarm, account, now):
    """06:59 预热出来的账号指纹，只有「同账号 + 同一天 + 3 分钟内」才敢用。"""
    if not isinstance(prewarm, dict) or prewarm.get('fp') != account:
        return False
    try:
        at = datetime.fromisoformat(prewarm['at'])
    except (KeyError, TypeError, ValueError):
        return False
    if at.utcoffset() is None:
        return False
    at = at.astimezone(CHINA)
    if at.date() != now.date():
        return False
    age = (now - at).total_seconds()
    return 0 <= age <= PREWARM_TTL


def _outcome_code(outcome):
    if isinstance(outcome, TimeoutError):
        return 'TIMEOUT'
    code = outcome.get('code') if isinstance(outcome, dict) else getattr(outcome, 'code', None)
    return code if isinstance(code, str) and code in SAFE_CODES else 'OTHER'


def _retryable_auth(outcome):
    # Authentication is always before the daily attempt marker and any addOrder.
    return _outcome_code(outcome) in TRANSIENT_CODES


def _retryable(outcome):
    # A bare exception does not prove where the booking flow stopped. Booking
    # retries require a structured pre-submit result from run_candidates.
    if not isinstance(outcome, dict):
        return False
    code = _outcome_code(outcome)
    if code not in TRANSIENT_CODES:
        return False
    # Unknown, submitted, rejected or already-reserved results always stop.
    return outcome.get('state') in ('failed', 'not_submitted')


def _retry_delay(attempt):
    # 07:00 刚放号时最堵，头一分钟多打几枪，后面再拉长间隔
    return (1, 2, 4, 6, 8, 10, 12, 15, 18, 20, 25, 30)[min(attempt, 11)]


def check_window(now, expected_date=None):
    if now.utcoffset() is None: raise ValueError('需要带时区的时间')
    now = now.astimezone(CHINA)
    if expected_date is not None and now.date().isoformat() != expected_date:
        raise ValueError('执行日期已改变，停止预约')
    start = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if not start <= now <= start + timedelta(minutes=WINDOW_MINUTES):
        raise ValueError('每日预约仅可在北京时间 07:00–07:%02d:00 执行' % WINDOW_MINUTES)
    return now


def run_daily(store, backend, now=None, expected_date=None, clock=None, sleep=None, prewarm=None):
    clock = clock or ((lambda: now) if now is not None else (lambda: datetime.now(CHINA)))
    sleep = sleep or time.sleep
    started = check_window(clock(), expected_date)
    run_date = started.date().isoformat()
    task, account = store.load()
    if _usable_prewarm(prewarm, account, started):
        # 06:59:30 已经核验过，这里省掉一次登录；真正提交前 book() 还会再核一次账号
        authenticated = account
        print('daily prewarm=hit', file=sys.stderr)
    else:
        for attempt in range(MAX_ATTEMPTS):
            try:
                authenticated = backend.auth()
                break
            except Exception as exc:
                code = _outcome_code(exc)
                if not _retryable_auth(exc) or attempt == MAX_ATTEMPTS - 1:
                    print('daily phase=auth code=' + code, file=sys.stderr)
                    raise
                print('daily phase=auth code=' + code + ' retry=' + str(attempt + 1), file=sys.stderr)
                check_window(clock(), run_date)
                sleep(_retry_delay(attempt))
                check_window(clock(), run_date)
    if authenticated != account: raise ValueError('账号已改变，请重新检查')
    check_window(clock(), run_date)
    task['target_date'] = (started.date() + timedelta(days=1)).isoformat()
    store.mark(task['target_date'])
    check_window(clock(), run_date)

    def retry_book(attempt, outcome):
        code = _outcome_code(outcome)
        if not _retryable(outcome) or attempt >= MAX_ATTEMPTS - 1:
            return False
        print('daily phase=book code=' + code + ' retry=' + str(attempt + 1), file=sys.stderr)
        check_window(clock(), run_date)
        sleep(_retry_delay(attempt))
        check_window(clock(), run_date)
        return True

    def final_commit_gate(_target):
        # Called again by the school API immediately before every addOrder send.
        check_window(clock(), run_date)

    try:
        result = backend.book(task, account, retry=retry_book, eligibility=final_commit_gate)
    except Exception as exc:
        print('daily phase=book code=' + _outcome_code(exc), file=sys.stderr)
        raise
    if not result.get('ok') or not result.get('verified'):
        print('daily phase=book code=' + _outcome_code(result), file=sys.stderr)
        raise ValueError('预约结果未核实；请检查我的预约，勿重复提交')
    return _notification(task, account, result, backend)


def failure_text(exc):
    """失败也要说人话：先讲到底是「没提交」还是「不知道」，再讲下一步。"""
    text = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(exc))[:160] or exc.__class__.__name__
    code = getattr(exc, 'code', '')
    if not isinstance(code, str):
        code = ''
    # 登录态读不到 = 流程停在第一枪，一单都没发出去。别让用户以为要核对订单。
    if code in LOGIN_CODES or 'no usable profile' in text:
        return ('\n'.join([
            '今天这次没提交 · 登录态读不到',
            '',
            '自动预约在核验登录这一步就停了，没有发出任何预约请求，',
            '不用去「我的预约」翻，也不会重复下单。',
            '',
            '请在电脑微信里打开一次「厦大体育」小程序（能进到场馆列表就行），',
            '再到本机页面手动补一次。',
            '',
            '技术原因：' + text,
        ]))
    uncertain = any(key in text for key in ('未核实', '未确认', 'UNCONFIRMED', 'UNKNOWN'))
    if uncertain:
        return ('\n'.join([
            '预约未确认 · 先别重复提交',
            '提交可能已经发出，但没能在服务器上核实到。',
            '',
            '打开微信小程序「我的预约」看一眼：',
            '· 有这一笔 → 就是订上了；',
            '· 没有 → 今天手动补一次。',
            '',
            '技术原因：' + text,
        ]))
    not_submitted = any(key in text for key in ('WINDOW_CLOSED', 'NOT_SUBMITTED', '未提交', '未放号'))
    if not_submitted:
        return ('\n'.join([
            '今天这次没提交 · 可以手动补',
            '',
            '技术原因：' + text,
            '',
            '这一单没有发出去，不会重复下单，现在到本机页面补一次就行。',
        ]))
    return ('\n'.join([
        '今天这次自动预约没成',
        '',
        '技术原因：' + text,
        '',
        '没能在服务器上核实，请按「不确定」处理：',
        '先到小程序「我的预约」看一眼，有这一笔就不要重复提交；没有就手动补一次。',
    ]))


def cmd_prewarm(out_path):
    """06:59:30 先把账号核验做掉，07:00 就能直接查单提交。成功时一句话都不打印。"""
    from app.backend import Backend
    task, account = Store().load()
    stamp = datetime.now(CHINA)
    fingerprint = Backend().auth()
    if fingerprint != account:
        print('预热失败：登录账号与绑定账号不一致', file=sys.stderr)
        return 1
    payload = json.dumps({'fp': fingerprint, 'at': stamp.isoformat()}).encode()
    target = Path(out_path)
    fd, name = tempfile.mkstemp(prefix='.prewarm-', dir=str(target.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='每日预约执行入口；不会安装调度器')
    parser.add_argument('action', choices=['run', 'prewarm'])
    parser.add_argument('--run-date', help='冻结的北京时间执行日期 YYYY-MM-DD')
    parser.add_argument('--prewarm', help='06:59 预热的账号指纹文件')
    parser.add_argument('--out', help='prewarm 输出路径')
    args = parser.parse_args(argv)
    if args.action == 'prewarm':
        if not args.out:
            parser.error('prewarm 需要 --out')
        try:
            return cmd_prewarm(args.out)
        except Exception as exc:
            print('预热失败：%s' % str(exc)[:120], file=sys.stderr)
            return 1
    from app.backend import Backend
    prewarm = None
    if args.prewarm:
        try:
            prewarm = json.loads(Path(args.prewarm).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            prewarm = None
    try:
        result = run_daily(Store(), Backend(), expected_date=args.run_date, prewarm=prewarm)
        print(result['message'])
        if result.get('media_path'):
            print('MEDIA:' + result['media_path'])
        return 0
    except Exception as exc:
        print(failure_text(exc))
        return 1

if __name__ == '__main__': sys.exit(main())
