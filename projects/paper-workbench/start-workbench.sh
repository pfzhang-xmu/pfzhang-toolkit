#!/bin/zsh
set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"
if [[ ! -x "$BASE_DIR/.venv/bin/python" ]]; then
  echo "未找到虚拟环境: $BASE_DIR/.venv"
  echo "请先运行: python3.12 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi
export PATH="/opt/homebrew/opt/openjdk/bin:/opt/homebrew/bin:$PATH"
exec "$BASE_DIR/.venv/bin/python" "$BASE_DIR/web/server.py" "${1:-8123}"
