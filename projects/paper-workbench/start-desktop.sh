#!/bin/zsh
set -euo pipefail
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"
export PATH="/opt/homebrew/opt/openjdk/bin:/opt/homebrew/bin:$PATH"
exec "$BASE_DIR/.venv/bin/python" "$BASE_DIR/desktop.py"
