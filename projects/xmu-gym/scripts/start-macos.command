#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if [ ! -x ".venv/bin/python" ]; then
  echo "还没安装依赖：先运行 ./scripts/setup-macos.sh" >&2
  exit 1
fi
exec .venv/bin/python -m app.server "$@"
