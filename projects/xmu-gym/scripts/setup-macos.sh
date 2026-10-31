#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3.12}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "找不到 $PY。先安装 Python 3.12（brew install python@3.12）。" >&2
  exit 1
fi

"$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

if [ ! -d "$HOME/Library/Containers/com.tencent.xinWeChat/Data/Documents/app_data/radium/web/profiles" ]; then
  echo "提醒：还没找到微信的 webview 目录。先在电脑微信打开「厦大体育」小程序并进入一次场馆预约。"
fi

echo
echo "安装完成。启动：./scripts/start-macos.command"
echo "演示模式：./scripts/start-macos.command --demo --port 1431"
