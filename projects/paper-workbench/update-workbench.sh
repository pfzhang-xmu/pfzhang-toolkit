#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(git -C "$PROJECT_ROOT" rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then echo "请在 Git 工作区内运行。" >&2; exit 1; fi
cd "$REPO_ROOT"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$PROJECT_ROOT/backups/$STAMP"
mkdir -p "$BACKUP"
[[ -f "$PROJECT_ROOT/app_config.json" ]] && cp "$PROJECT_ROOT/app_config.json" "$BACKUP/app_config.json"
[[ -d "$PROJECT_ROOT/data" ]] && cp -R "$PROJECT_ROOT/data" "$BACKUP/data"
for f in .dsh_workbench_session .dsh_workbench_session.lock .dsh_workbench_pool.json .last-project; do [[ -e "$PROJECT_ROOT/$f" ]] && cp "$PROJECT_ROOT/$f" "$BACKUP/"; done
OLD="$(git rev-parse --short HEAD)"
git fetch --tags origin
git pull --ff-only
if [[ -x "$PROJECT_ROOT/.venv/bin/pip" ]]; then "$PROJECT_ROOT/.venv/bin/pip" install -r "$PROJECT_ROOT/requirements.txt"; fi
NEW="$(git rev-parse --short HEAD)"
printf "Paper Workbench 已更新: %s -> %s\n备份目录: %s\n" "$OLD" "$NEW" "$BACKUP"
