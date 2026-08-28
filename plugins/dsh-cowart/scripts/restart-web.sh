#!/usr/bin/env bash
# dsh web 一键重启：先释放端口，再以简洁输出启动（避免 EADDRINUSE 大堆栈）。
#
# 用法：
#   ./scripts/restart-web.sh                 # 重启 3080
#   ./scripts/restart-web.sh 3090            # 指定端口
#
# 注意：会杀掉占用该端口的旧 dsh 实例（对话持久化，重启后会话可继续）。
set -euo pipefail

PORT="${1:-${COWART_WEB_PORT:-3080}}"

echo "◆ 释放端口 ${PORT} ..."
PIDS="$(lsof -ti tcp:"${PORT}" 2>/dev/null || true)"
if [ -n "${PIDS}" ]; then
  # shellcheck disable=SC2086
  kill ${PIDS} 2>/dev/null || true
  sleep 1
  PIDS="$(lsof -ti tcp:"${PORT}" 2>/dev/null || true)"
  if [ -n "${PIDS}" ]; then
    # shellcheck disable=SC2086
    kill -9 ${PIDS} 2>/dev/null || true
    sleep 1
  fi
fi

echo "◆ 启动 dsh web (http://127.0.0.1:${PORT}) ..."
# 沿用当前目录作为默认工作区；如需指定项目目录，先 cd 过去再执行本脚本。
exec npx @deepseek-ai/dsh web
