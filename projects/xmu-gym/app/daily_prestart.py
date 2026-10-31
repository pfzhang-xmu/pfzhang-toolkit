"""Safe 06:58 scheduler entry point; forwards daily.py stdout unchanged.

06:59:30 先做一次账号预热（只读登录核验），07:00:00 再正式执行，
省掉首轮登录的 1–3 秒 —— 这段时间正好是学校放号最拥堵的时刻。
预热失败不影响主流程。
"""
from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHINA = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
WINDOW_MINUTES = 20          # 与 daily.WINDOW_MINUTES 保持一致
PREWARM_LEAD_SECONDS = 30    # 07:00 前多少秒开始预热


def launch(run_date, prewarm_path=None):
    cmd = [str(PYTHON), '-m', 'app.daily', 'run', '--run-date', run_date]
    if prewarm_path:
        cmd += ['--prewarm', str(prewarm_path)]
    return subprocess.run(cmd, check=False, cwd=str(ROOT)).returncode


def prewarm(run_date):
    """返回预热文件路径；失败就返回 None，主流程照跑。"""
    handle, path = tempfile.mkstemp(prefix='.gym-prewarm-', suffix='.json')
    os.close(handle)
    os.unlink(path)
    try:
        done = subprocess.run(
            [str(PYTHON), '-m', 'app.daily', 'prewarm', '--out', path],
            check=False, timeout=40, capture_output=True, text=True, cwd=str(ROOT),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode == 0 and Path(path).is_file():
        return path
    if done.stderr:
        print('预热未生效：' + done.stderr.strip()[:120], flush=True)
    return None


def run(clock, sleep, execute, prewarm_hook=None):
    first = clock()
    if first.utcoffset() is None:
        raise ValueError('需要带时区的时间')
    first = first.astimezone(CHINA)
    run_date = first.date()
    start = first.replace(hour=7, minute=0, second=0, microsecond=0)
    if not start - timedelta(minutes=2) <= first <= start + timedelta(minutes=WINDOW_MINUTES):
        raise ValueError('启动时间不在 06:58–07:%02d:00 范围' % WINDOW_MINUTES)
    warm_at = start - timedelta(seconds=PREWARM_LEAD_SECONDS)
    warmed = None
    warmed_tried = False
    while True:
        now = clock()
        if now.utcoffset() is None:
            raise ValueError('需要带时区的时间')
        now = now.astimezone(CHINA)
        if (now.date() != run_date or now > start + timedelta(minutes=WINDOW_MINUTES)
                or now < start - timedelta(minutes=2)):
            raise ValueError('执行时间或日期已改变，停止预约')
        if now >= start:
            return execute(run_date.isoformat(), warmed)
        if prewarm_hook is not None and not warmed_tried and now >= warm_at:
            # 只尝试一次：预热失败也必须继续往下等，否则会在这里空转
            warmed_tried = True
            warmed = prewarm_hook(run_date.isoformat()) or None
            continue
        sleep(min((start - now).total_seconds(), 1.0))


def main():
    try:
        return run(lambda: datetime.now(CHINA), time.sleep, launch, prewarm)
    except Exception:
        print('今天的自动预约没有执行：启动时间或运行环境异常。'
              '这不是「已提交但未知」，可以到本机页面手动补一次。', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
