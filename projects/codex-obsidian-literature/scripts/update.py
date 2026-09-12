#!/usr/bin/env python3
"""Safely update framework files while preserving user literature data."""
from __future__ import annotations
import argparse, datetime, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=[Path('10_论文'),Path('20_知识'),Path('90_附件'),Path('.research_index')]
def run(cmd): return subprocess.run(cmd,cwd=ROOT,text=True)
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--target',default='main'); ap.add_argument('--remote',default='origin'); args=ap.parse_args()
 stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S'); backup=ROOT/'.backups'/stamp; backup.mkdir(parents=True)
 for rel in DATA:
  src=ROOT/rel
  if src.exists(): shutil.copytree(src,backup/rel,dirs_exist_ok=True)
 print(f'用户数据备份：{backup}')
 before=(ROOT/'VERSION').read_text().strip() if (ROOT/'VERSION').exists() else 'unknown'
 if run(['git','fetch',args.remote]).returncode: raise SystemExit('获取远端更新失败')
 if run(['git','pull','--ff-only',args.remote,args.target]).returncode:
  raise SystemExit('无法快进更新；用户数据备份仍保留，请手动处理框架冲突。')
 after=(ROOT/'VERSION').read_text().strip() if (ROOT/'VERSION').exists() else 'unknown'
 print(f'框架已更新：{before} -> {after}')
 run([sys.executable,str(ROOT/'scripts/doctor.py')])
 print('用户数据未修改；如需重建索引，请运行 research_search.py index。')
if __name__=='__main__': main()
