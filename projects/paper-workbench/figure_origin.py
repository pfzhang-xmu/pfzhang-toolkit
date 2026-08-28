# -*- coding: utf-8 -*-
"""figure_origin.py — Origin COM 绘图桥（真实驱动 Origin 出图）。

经 Origin 2019b 实测验证的 LabTalk 语法封装：
  - 连接: Origin.Application（复用运行中实例，未运行则启动）
  - 数据: wcol(n)[i]=v 逐格写入任意数组
  - 绘图: plotxy iy:=(col(1),col(2)) plot:=<type>
      type 201=scatter, 202=line+symbol, 200=line
  - 样式: layer.x.title.text / layer.x.title.font.size / legend hide
  - 导出: expgraph type:=png|tiff filename:=... path:=...

对外接口:
  OriginBridge()           连接/复用 Origin
  bridge.plot_xy(series,...)  画 XY 图并导出
  CLI: python figure_origin.py --json spec.json --out fig.png

说明: 不依赖 originpro（其要求 Origin 2021+）；本桥直接用 COM+LabTalk，
兼容 Origin 2019b。适合 nature-figure 契约里"需要 Origin 风格/出版级"的图。
"""
from __future__ import annotations

import json
import os
import sys
import time

# 经实测的绘图类型
PLOT_TYPES = {
    "scatter": 201,
    "line": 200,
    "line+symbol": 202,
    "linesymbol": 202,
}


# 模块级单例：同一进程内只连接/拉起一次 Origin（避免实例堆积）。
# Origin 2019b 的 Dispatch 每次都拉新实例，且 GetActiveObject 复用不可靠，
# 故用进程内缓存保证：无论调用多少次，最多一个 Origin 实例。
_APP_CACHE = None


def _get_app():
    global _APP_CACHE
    if _APP_CACHE is not None:
        return _APP_CACHE
    try:
        import win32com.client as w
    except ImportError as e:
        raise RuntimeError("缺少 pywin32，无法驱动 Origin（pip install pywin32）") from e
    # 优先 GetActiveObject 复用外部已运行实例；无则 Dispatch 拉一次。
    try:
        _APP_CACHE = w.GetActiveObject("Origin.Application")
    except Exception:
        _APP_CACHE = w.Dispatch("Origin.Application")
    return _APP_CACHE


class OriginBridge:
    """通过 COM 驱动 Origin。所有 LabTalk 均经 2019b 实测。进程内单实例。"""

    def __init__(self):
        try:
            self.app = _get_app()
        except Exception as e:
            raise RuntimeError(f"无法连接 Origin（请确认已安装并注册 COM）: {e}") from e

    def lt(self, cmd: str) -> bool:
        """执行一条/一串 LabTalk，返回 Execute 的布尔结果。"""
        try:
            return bool(self.app.Execute(cmd))
        except Exception:
            return False

    def _new_book(self):
        return self.lt("newbook;")

    def _fill_columns(self, xs, yss, xlabel=None, ylabel=None):
        """写 X 列 + 若干 Y 列（逐格写入任意数组）。
        xlabel/ylabel 写入列 Long Name，Origin 绘图时自动用作轴标题（已验证可靠）。"""
        cmds = []
        n = len(xs)
        if xlabel:
            cmds.append(f'wcol(1)[L]$ = "{xlabel}"')
        for i, x in enumerate(xs, start=1):
            cmds.append(f"wcol(1)[{i}]={x}")
        for ci, ys in enumerate(yss, start=2):
            if ylabel and ci == 2:
                cmds.append(f'wcol({ci})[L]$ = "{ylabel}"')
            for i, y in enumerate(ys[:n], start=1):
                cmds.append(f"wcol({ci})[{i}]={y}")
        return self.lt(";".join(cmds) + ";")

    def plot_xy(self, xs, yss, out_path, kind="scatter",
                xlabel=None, ylabel=None, title_font=20, tick_font=18,
                legend=False, fmt=None, width=None, height=None):
        """画 XY 图并导出。

        xs: 一维数组（X 轴）
        yss: 一个或多个一维数组（每个一条曲线）
        out_path: 导出文件完整路径（扩展名决定格式，或显式 fmt）
        """
        if isinstance(yss[0], (int, float)):
            yss = [yss]  # 单条曲线
        if not self._new_book():
            return {"ok": False, "error": "newbook 失败"}
        time.sleep(0.2)
        if not self._fill_columns(xs, yss, xlabel, ylabel):
            return {"ok": False, "error": "数据写入失败"}
        ptype = PLOT_TYPES.get(kind, 201)
        ncols = 1 + len(yss)
        cols = ",".join(f"col({i})" for i in range(1, ncols + 1))
        if not self.lt(f"plotxy iy:=({cols}) plot:={ptype};"):
            return {"ok": False, "error": f"plotxy 失败 (type={ptype})"}
        time.sleep(0.4)
        # 轴标题已通过列 Long Name 写入（Origin 绘图时自动用作轴标题，实测可靠）。
        # 注意：2019b 中 `legend hide` 会被渲染成 "hide" 文本污染图，故不使用；图例保留默认。
        time.sleep(0.2)
        # 导出
        d, fn = os.path.split(out_path)
        name, ext = os.path.splitext(fn)
        gtype = (fmt or ext.lstrip(".").lower() or "png")
        if gtype not in ("png", "tiff", "tif", "jpg", "jpeg", "svg", "pdf", "emf"):
            gtype = "png"
        if gtype == "tif":
            gtype = "tiff"
        exp = f'expgraph type:={gtype} filename:="{name}" path:="{d}";'
        if width and height:
            exp = f'expgraph type:={gtype} filename:="{name}" path:="{d}" width:={width} height:={height};'
        ok = self.lt(exp)
        time.sleep(0.6)
        real = os.path.join(d, name + "." + (gtype if gtype != "jpeg" else "jpg"))
        exists = os.path.exists(real)
        return {"ok": ok and exists, "file": real if exists else None,
                "error": None if (ok and exists) else "导出失败或文件未生成"}


def render_from_spec(spec: dict) -> dict:
    """从 JSON 规格渲染。spec: {xs, ys|series, out, kind, xlabel, ylabel, ...}"""
    b = OriginBridge()
    xs = spec.get("xs") or spec.get("x") or []
    ys = spec.get("ys") or spec.get("y") or spec.get("series") or []
    out = spec.get("out") or spec.get("output")
    if not xs or not ys or not out:
        return {"ok": False, "error": "spec 缺少 xs/ys/out"}
    return b.plot_xy(xs, ys, out,
                     kind=spec.get("kind", "scatter"),
                     xlabel=spec.get("xlabel"), ylabel=spec.get("ylabel"),
                     title_font=int(spec.get("title_font", 20)),
                     tick_font=int(spec.get("tick_font", 18)),
                     legend=bool(spec.get("legend", False)),
                     fmt=spec.get("fmt"),
                     width=spec.get("width"), height=spec.get("height"))


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="figure_origin", description="Origin COM 绘图桥")
    ap.add_argument("--json", help="规格 JSON 文件")
    ap.add_argument("--out", help="输出文件（覆盖 spec.out）")
    ap.add_argument("--selftest", action="store_true", help="内置自检（sin 曲线）")
    args = ap.parse_args()

    if args.selftest:
        import math
        out = args.out or os.path.join(os.getcwd(), "figure_origin_selftest.png")
        xs = [round(i * 0.3, 2) for i in range(1, 21)]
        ys = [round(math.sin(x), 4) for x in xs]
        r = OriginBridge().plot_xy(xs, ys, out, kind="line+symbol",
                                   xlabel="x", ylabel="sin(x)")
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0 if r.get("ok") else 1)

    if args.json:
        spec = json.loads(Path_read(args.json))
        if args.out:
            spec["out"] = args.out
        r = render_from_spec(spec)
        print(json.dumps(r, ensure_ascii=False))
        sys.exit(0 if r.get("ok") else 1)
    ap.print_help()


def Path_read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


if __name__ == "__main__":
    main()
