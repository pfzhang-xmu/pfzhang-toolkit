# -*- coding: utf-8 -*-
"""figure_router.py — 绘图三路确定性分发（决策层，不依赖 AI 判断）。

路由规则（与 skill_routing.md / paper-figure-routing 技能一致）：
  type=data        柱/线/散点等数据图 → matplotlib + figure_styles（ggsci NPG 配色）
  type=schematic   示意图/流程图/路线图 → figure_pptx（python-pptx → PowerPoint COM 导出，源 PPTX 存档）
  type=origin      Origin 风格数据图 → figure_origin（Origin COM，2019b）；探测失败明确报错并建议回退 data

规格（JSON）：
  {"type": "data|schematic|origin",
   "out": "输出路径(.png)",
   // data/origin: "kind": "bar|line|scatter", "xs": [...], "ys": [...] | "series": [[..],[..]],
   //               "xlabel","ylabel","title"
   // schematic:   "spec": {title, boxes, arrows, texts, footer}（见 figure_pptx.build_spec）
  }

CLI: python figure_router.py --json spec.json [--out out.png]
"""
import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _infer_type(spec):
    """无显式 type 时按内容推断。"""
    if spec.get("spec") and (spec["spec"].get("boxes") or spec["spec"].get("arrows")):
        return "schematic"
    if spec.get("xs") is not None or spec.get("ys") is not None or spec.get("series"):
        return "data"
    return "data"


def render_data(spec, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import figure_styles
    figure_styles.apply_mpl_style()
    NPG = figure_styles.NPG_ORDER
    kind = spec.get("kind", "scatter")
    xs = spec.get("xs")
    series = spec.get("series") or ([spec["ys"]] if spec.get("ys") else [])
    fig, ax = plt.subplots(figsize=(float(spec.get("width", 7.2)), float(spec.get("height", 3.8))), dpi=600)
    for i, ys in enumerate(series):
        c = NPG[i % len(NPG)]
        lbl = (spec.get("labels") or [None] * len(series))[i] if spec.get("labels") else None
        if kind == "bar":
            ax.bar(xs, ys, color=c, label=lbl, edgecolor="white", linewidth=0.6)
        elif kind == "line":
            ax.plot(xs, ys, color=c, label=lbl, lw=1.8)
        else:
            ax.scatter(xs, ys, color=c, label=lbl, s=28)
    if spec.get("xlabel"):
        ax.set_xlabel(spec["xlabel"], fontsize=10)
    if spec.get("ylabel"):
        ax.set_ylabel(spec["ylabel"], fontsize=10)
    if spec.get("title"):
        ax.set_title(spec["title"], fontsize=11)
    if spec.get("labels"):
        ax.legend(fontsize=8.5, frameon=False)
    ax.yaxis.grid(True, color="#DDDDDD", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    fig.savefig(out, dpi=600, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return {"ok": True, "engine": "matplotlib(NPG)", "file": str(out)}


def render_schematic(spec, out):
    import figure_pptx
    out = Path(out)
    tmp_pptx = out.with_suffix(".pptx")
    figure_pptx.build_spec(spec.get("spec") or {}, str(tmp_pptx))
    figure_pptx.export_png(str(tmp_pptx), str(out), width_px=int(spec.get("width_px", 4000)))
    # 源 PPTX 存档（导师/用户可二次编辑）
    archive = out.with_name(out.stem + "_source.pptx")
    if archive != tmp_pptx:
        import shutil
        shutil.copy2(tmp_pptx, archive)
    return {"ok": True, "engine": "pptx-route(python-pptx+PowerPoint COM)",
            "file": str(out), "source_pptx": str(tmp_pptx)}


def render_origin(spec, out):
    try:
        import figure_origin
    except Exception as e:
        return {"ok": False, "engine": "origin",
                "error": f"figure_origin 不可用: {e}；建议回退 type=data（matplotlib/NPG）"}
    try:
        bridge = figure_origin.OriginBridge()
    except Exception as e:
        return {"ok": False, "engine": "origin",
                "error": f"Origin 不可达: {e}；建议回退 type=data（matplotlib/NPG）或先启动 Origin"}
    ys = spec.get("ys") or (spec.get("series") or [[]])[0]
    r = bridge.plot_xy(spec.get("xs") or list(range(len(ys))), ys, str(out),
                       kind=spec.get("kind", "scatter"),
                       xlabel=spec.get("xlabel"), ylabel=spec.get("ylabel"))
    if r.get("ok"):
        r["engine"] = "origin(COM/LabTalk)"
    else:
        r["error"] = (r.get("error") or "Origin 出图失败") + "；可回退 type=data"
    return r


def route(spec):
    ftype = (spec.get("type") or _infer_type(spec)).strip().lower()
    out = spec.get("out")
    if not out:
        return {"ok": False, "error": "spec 缺 out（输出路径）"}
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    if ftype == "data":
        return render_data(spec, out)
    if ftype in ("schematic", "flow", "roadmap", "diagram"):
        return render_schematic(spec, out)
    if ftype == "origin":
        return render_origin(spec, out)
    return {"ok": False, "error": f"未知 type: {ftype}（支持 data/schematic/origin）"}


def main():
    ap = argparse.ArgumentParser(prog="figure_router", description="绘图三路确定性分发")
    ap.add_argument("--json", required=True, help="规格 JSON 文件")
    ap.add_argument("--out", help="输出路径（覆盖 spec.out）")
    args = ap.parse_args()
    spec = json.loads(Path(args.json).read_text(encoding="utf-8"))
    if args.out:
        spec["out"] = args.out
    r = route(spec)
    print(json.dumps(r, ensure_ascii=False))
    sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
