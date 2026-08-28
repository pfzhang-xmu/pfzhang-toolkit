#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper Workbench 桌面版启动器。

用 pywebview 把本地 Web 工作台包成独立桌面窗口。
依赖: pywebview (pip install pywebview)
"""
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
SERVER = BASE / "web" / "server.py"


def find_free_port(preferred=8123):
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return preferred


def wait_server(url, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    try:
        import webview
    except Exception as e:
        print("未安装 pywebview，请先运行: python -m pip install pywebview")
        print(f"错误: {e}")
        sys.exit(1)

    port = find_free_port(8123)
    url = f"http://127.0.0.1:{port}"
    frozen = getattr(sys, "frozen", False)
    proc = None
    if frozen:
        # 打包后：无独立 python 解释器，线程内直接启动 server
        import threading
        sys.path.insert(0, str(Path(getattr(sys, "_MEIPASS", Path(__file__).parent))))
        import server as ws
        ws.PORT = port

        def serve():
            srv = ws.ThreadingHTTPServer(("127.0.0.1", port), ws.Handler)
            ws.srv = srv
            srv.serve_forever()

        threading.Thread(target=serve, daemon=True).start()
    else:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [sys.executable, str(SERVER), str(port)],
            cwd=str(BASE / "web"),
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    try:
        if not wait_server(url):
            print("工作台服务启动失败，请检查端口/防火墙。")
            if proc:
                proc.terminate()
            sys.exit(1)
        webview.create_window(
            "Paper Workbench 论文工作台",
            url,
            width=1440,
            height=900,
            min_size=(1100, 700),
            background_color="#0f1420",
        )
        webview.start()
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()


if __name__ == "__main__":
    main()
