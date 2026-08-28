# -*- coding: utf-8 -*-
"""Paper Workbench 打包脚本（PyInstaller）。

用法:
    python build_exe.py

产物: dist/PaperWorkbench/PaperWorkbench.exe（onedir 模式，含全部依赖）

说明:
- 入口 desktop.py（pywebview 桌面窗口 + 内置 HTTP 服务）
- 数据文件: web/index.html、templates/、checklists/
- 依赖: pypandoc-binary、pywebview、matplotlib、pandas、scipy、seaborn、
  openpyxl、bibtexparser、pyalex、arxiv
- 打包后 run_wb 走函数直调、templates/checklists 从 _MEIPASS 读取、
  .last-project 写用户目录（wb.py/server.py/desktop.py 已兼容 frozen）
"""
import shutil
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import PyInstaller.__main__
except ImportError:
    print("请先安装 pyinstaller: python -m pip install pyinstaller")
    sys.exit(1)

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

args = [
    str(ROOT / "desktop.py"),
    "--name=PaperWorkbench",
    "--windowed",
    "--clean",
    "--noconfirm",
    "--distpath", str(DIST),
    "--workpath", str(BUILD),
    "--specpath", str(ROOT),
    # 模块搜索路径（扁平目录结构）
    "--paths", str(ROOT),
    "--paths", str(WEB),
    # 数据文件
    "--add-data", f"{WEB / 'index.html'}{';' if sys.platform == 'win32' else ':'}web",
    "--add-data", f"{ROOT / 'templates'}{';' if sys.platform == 'win32' else ':'}templates",
    "--add-data", f"{ROOT / 'checklists'}{';' if sys.platform == 'win32' else ':'}checklists",
    # 显式模块
    "--hidden-import", "wb",
    "--hidden-import", "toolbox",
    "--hidden-import", "data2paper",
    "--hidden-import", "server",
    "--hidden-import", "ai_client",
    "--hidden-import", "charts",
    "--hidden-import", "pypandoc",
    "--hidden-import", "webview",
    # 大依赖收集
    "--collect-all", "pypandoc",
    "--collect-all", "webview",
    "--collect-all", "clr_loader",
    "--collect-all", "matplotlib",
    "--collect-all", "seaborn",
    "--collect-all", "pandas",
    "--collect-all", "scipy",
    "--collect-all", "openpyxl",
    "--collect-all", "bibtexparser",
    "--collect-all", "pyalex",
    "--collect-all", "arxiv",
]

print("PyInstaller args:")
for a in args:
    print("  ", a)
print("开始构建（大依赖，可能需要数分钟）...")
PyInstaller.__main__.run(args)

exe = DIST / "PaperWorkbench" / "PaperWorkbench.exe"
if exe.exists():
    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"\n✔ 构建成功: {exe} ({size_mb:.1f} MB)")
    print("  整个目录: " + str(DIST / "PaperWorkbench"))
else:
    print("\n✗ 构建失败: 未找到产物 " + str(exe))
    sys.exit(1)
