#!/bin/bash
# 一键启动：Edge 远程调试 + CDP 代理
# 路径: ~/.claude/skills/xmu-literature-downloader/scripts/start_cdp.sh

SKILL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
EDGE="/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
PROXY_SCRIPT="$SKILL_DIR/scripts/cdp_proxy.mjs"
CDP_PORT=${CDP_BROWSER_PORT:-9223}
# 坑：之前 CDP_PORT 只是 shell 局部变量，子进程 node 读不到，
# 导致 cdp_proxy.mjs 只能靠默认 9222 去连，而 Edge 实际在 9223 → 代理"活着但连不上"。
# 这里必须 export，让代理知道浏览器真实端口。
export CDP_BROWSER_PORT=$CDP_PORT
PROXY_PORT=3456
export CDP_PROXY_PORT=$PROXY_PORT

# 1. 关闭已有的 Edge 实例（如果开了远程调试端口）
echo "=== 检查 Edge 进程 ==="
EXISTING=$(lsof -ti :$CDP_PORT 2>/dev/null)
if [ -n "$EXISTING" ]; then
    echo "端口 $CDP_PORT 已被占用 (PID: $EXISTING)"
    # 坑：必须带 --noproxy '*'，否则本机 http_proxy 注入会让这条 curl 走进代理，报出误导性的 Connection refused
    echo "验证是否有效：curl -s --noproxy '*' http://127.0.0.1:$CDP_PORT/json/version | head -1"
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

# 坑：若代理已在跑，再起一个会 EADDRINUSE 直接崩。
# 先探活，活着就复用，别重复启动。
if curl -s --noproxy '*' -m 3 "http://127.0.0.1:$PROXY_PORT/health" 2>/dev/null | grep -q '"ok"'; then
    echo "检测到已有代理在运行且健康 —— 直接复用，不再重复启动。"
    echo "（如需重启代理，先 kill 掉旧进程）"
    exit 0
fi

echo "使用完毕后 Ctrl+C 退出"
echo "=================================="
echo ""

node "$PROXY_SCRIPT"
