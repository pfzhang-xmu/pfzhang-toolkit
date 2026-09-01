#!/bin/bash
# 一键启动：Edge 远程调试 + CDP 代理
# 路径: ~/.claude/skills/xmu-literature-downloader/scripts/start_cdp.sh

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EDGE="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
PROXY_SCRIPT="$SKILL_DIR/scripts/cdp_proxy.mjs"
CDP_PORT=9222
PROXY_PORT=3456

# 1. 关闭已有的 Edge 实例（如果开了远程调试端口）
echo "=== 检查 Edge 进程 ==="
EXISTING=$(lsof -ti :$CDP_PORT 2>/dev/null)
if [ -n "$EXISTING" ]; then
    echo "端口 $CDP_PORT 已被占用 (PID: $EXISTING)，尝试复用已有 Edge"
else
    echo "启动 Edge 并开启远程调试 (端口 $CDP_PORT)..."
    "$EDGE" \
        --remote-debugging-port=$CDP_PORT \
        --no-first-run \
        --no-default-browser-check \
        --user-data-dir="$HOME/.xmu-literature-edge-profile" &
    sleep 3
fi

# 2. 启动 CDP 代理
echo ""
echo "=== 启动 CDP 代理 (端口 $PROXY_PORT) ==="
echo "Edge CDP: http://127.0.0.1:$CDP_PORT"
echo "Proxy:    http://127.0.0.1:$PROXY_PORT"
echo ""
echo "使用完毕后 Ctrl+C 退出"
echo "=================================="
echo ""

node "$PROXY_SCRIPT"
