#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper Workbench 数据图表模块。
优先使用 matplotlib 生成 PNG;未安装时自动降级为纯标准库 SVG。
"""
import base64
import csv
import io
import json
import re
import statistics
import time
from pathlib import Path

CHART_TYPES = ("bar", "line", "scatter", "pie", "box", "violin", "hist", "area", "heatmap")

# ─────────── 图表规范常量（D2）：与 templates/reference.docx 的 Times New Roman 正文对齐 ───────────
CHART_FONT_FAMILY_MPL = "serif"                          # matplotlib 字体族
CHART_FONT_SERIF = ["Times New Roman", "DejaVu Serif", "Microsoft YaHei", "SimHei"]  # serif 字体优先列表（尾部中文字体为 CJK 字形回退兜底）
CHART_FONT_FAMILY_SVG = "Times New Roman, serif"         # SVG font-family
CHART_FONT_SIZE_MIN = 10        # 字号下限：任何图表文字不得小于该值
CHART_FONT_SIZE_TITLE = 12      # 图表标题字号
CHART_LINE_WIDTH = 1.5          # 统一线宽
CHART_SAVE_PAD_INCHES = 0.2     # 保存留白，防标签/图例被裁切
# 色盲安全默认色板（Okabe-Ito）
CHART_PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#F0E442", "#56B4E9"]


def _as_number(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_csv(text):
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return [], []
    header = [h.lstrip("\ufeff").strip() for h in rows[0]]
    if len(set(header)) == len(header) and all(h for h in header):
        columns = header
        data_rows = rows[1:]
    else:
        columns = [f"col{i + 1}" for i in range(len(rows[0]))]
        data_rows = rows
    dict_rows = []
    for r in data_rows:
        if len(r) < len(columns):
            r = r + [""] * (len(columns) - len(r))
        dict_rows.append({c: (r[i].strip() if i < len(r) else "") for i, c in enumerate(columns)})
    return columns, dict_rows


def parse_json(text):
    obj = json.loads(text)
    if isinstance(obj, dict):
        if "columns" in obj and "rows" in obj:
            columns = [str(c) for c in obj["columns"]]
            rows = obj["rows"]
            dict_rows = []
            for r in rows:
                if isinstance(r, dict):
                    dict_rows.append({str(k): v for k, v in r.items()})
                else:
                    dict_rows.append({columns[i]: (r[i] if i < len(r) else "") for i in range(len(columns))})
            return columns, dict_rows
        # {col: [values...]}
        columns = [str(k) for k in obj.keys()]
        vals = [obj[k] if isinstance(obj[k], list) else [obj[k]] for k in obj.keys()]
        n = max((len(v) for v in vals), default=0)
        dict_rows = []
        for i in range(n):
            dict_rows.append({columns[j]: (vals[j][i] if i < len(vals[j]) else "") for j in range(len(columns))})
        return columns, dict_rows
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        columns = []
        for r in obj:
            for k in r.keys():
                if str(k) not in columns:
                    columns.append(str(k))
        dict_rows = [{str(k): v for k, v in r.items()} for r in obj]
        return columns, dict_rows
    raise ValueError("JSON 需为对象数组、列/行对象或 {列名: 值数组}")


def parse_data(filename, content):
    name = (filename or "data.csv").lower()
    if name.endswith(".json"):
        return parse_json(content)
    return parse_csv(content)


def numeric_stats(rows, columns):
    stats = {}
    for c in columns:
        nums = [_as_number(r.get(c)) for r in rows]
        nums = [x for x in nums if x is not None]
        if nums:
            stats[c] = {
                "count": len(nums),
                "mean": round(statistics.mean(nums), 4),
                "min": round(min(nums), 4),
                "max": round(max(nums), 4),
                "stdev": round(statistics.stdev(nums), 4) if len(nums) > 1 else 0.0,
            }
    return stats


def _pick_columns(columns, x_col, y_col, chart_type):
    x = x_col if x_col in columns else (columns[0] if columns else None)
    if chart_type == "pie":
        y = y_col if y_col in columns else (columns[-1] if len(columns) > 1 else columns[0])
    else:
        y = y_col if y_col in columns else (columns[-1] if len(columns) > 1 else columns[0])
    return x, y


def _data_url_bytes(raw, mime):
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _save_svg(svg_text, out_dir, stem, chart_type):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{stem}_{chart_type}_{int(time.time())}.svg"
    path = out_dir / fname
    path.write_text(svg_text, encoding="utf-8")
    raw = path.read_bytes()
    return {
        "format": "svg",
        "file": str(path),
        "rel": f"data/charts/{fname}",
        "data_url": _data_url_bytes(raw, "image/svg+xml"),
    }


def _svg_escape(v):
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _svg_chart(rows, columns, chart_type, x_col, y_col, title):
    """零依赖 SVG 图表生成,供 matplotlib 缺失时降级。"""
    # 高级类型在零依赖降级时映射到基础类型
    if chart_type in ("box", "violin", "hist", "heatmap"):
        chart_type = "bar"
    elif chart_type == "area":
        chart_type = "line"
    x, y = _pick_columns(columns, x_col, y_col, chart_type)
    labels = [_svg_escape(r.get(x, "")) for r in rows]
    nums = [_as_number(r.get(y)) for r in rows]
    pairs = [(labels[i], nums[i]) for i in range(len(rows)) if nums[i] is not None]
    if not pairs:
        raise ValueError("没有可用于绘图的数值数据")
    width, height = 640, 420
    pad_l, pad_b, pad_t, pad_r = 60, 60, 40, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    vals = [p[1] for p in pairs]
    vmin, vmax = min(vals), max(vals)
    if vmin == vmax:
        vmin, vmax = vmin - 1, vmax + 1
    title = _svg_escape(title or f"{y} by {x}")

    def px(pair, i, n):
        lx, val = pair
        if chart_type == "pie":
            return None
        if n <= 1:
            xp = pad_l + plot_w / 2
        else:
            xp = pad_l + (i / (n - 1)) * plot_w
        yp = pad_t + plot_h - (val - vmin) / (vmax - vmin) * plot_h
        return xp, yp

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"'
             f' font-family="{CHART_FONT_FAMILY_SVG}">']
    parts.append(f'<text x="{width/2}" y="24" text-anchor="middle" font-size="{CHART_FONT_SIZE_TITLE}" fill="#222">{title}</text>')
    # 网格与 Y 轴
    for gi in range(5):
        gy = pad_t + plot_h - (gi / 4) * plot_h
        gv = vmin + (gi / 4) * (vmax - vmin)
        parts.append(f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width-pad_r}" y2="{gy:.1f}" stroke="#e5e5e5"/>')
        parts.append(f'<text x="{pad_l-8}" y="{gy+4:.1f}" text-anchor="end" font-size="{CHART_FONT_SIZE_MIN}" fill="#666">{gv:.2f}</text>')
    parts.append(f'<line x1="{pad_l}" y1="{pad_t+plot_h}" x2="{width-pad_r}" y2="{pad_t+plot_h}" stroke="#333"/>')
    parts.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t+plot_h}" stroke="#333"/>')

    n = len(pairs)
    if chart_type == "pie":
        total = sum(p[1] for p in pairs)
        cx, cy, rad = width / 2, height / 2 + 10, min(plot_w, plot_h) / 2 - 10
        start = 0.0
        colors = ["#4f8cff", "#3ecf8e", "#ffb454", "#f472b6", "#a78bfa", "#34d399", "#f87171", "#60a5fa"]
        for i, (lab, val) in enumerate(pairs):
            angle = val / total * 360.0
            large = 1 if angle > 180 else 0
            x1 = cx + rad * __import__("math").cos(__import__("math").radians(start))
            y1 = cy + rad * __import__("math").sin(__import__("math").radians(start))
            x2 = cx + rad * __import__("math").cos(__import__("math").radians(start + angle))
            y2 = cy + rad * __import__("math").sin(__import__("math").radians(start + angle))
            color = colors[i % len(colors)]
            parts.append(f'<path d="M {cx:.1f} {cy:.1f} L {x1:.1f} {y1:.1f} A {rad:.1f} {rad:.1f} 0 {large} 1 {x2:.1f} {y2:.1f} Z" fill="{color}" stroke="white" stroke-width="1"/>')
            start += angle
        # 图例
        lx, ly = width - pad_r - 140, pad_t + 10
        for i, (lab, val) in enumerate(pairs):
            color = colors[i % len(colors)]
            parts.append(f'<rect x="{lx}" y="{ly}" width="12" height="12" fill="{color}"/>')
            parts.append(f'<text x="{lx+18}" y="{ly+11}" font-size="{CHART_FONT_SIZE_MIN}" fill="#333">{_svg_escape(lab)[:18]}</text>')
            ly += 18
    elif chart_type == "line":
        pts = " ".join(f"{xp:.1f},{yp:.1f}" for i, (_, _v) in enumerate(pairs) for xp, yp in [px(pairs[i], i, n)])
        parts.append(f'<polyline points="{pts}" fill="none" stroke="#4f8cff" stroke-width="{CHART_LINE_WIDTH}"/>')
        for i, (lab, val) in enumerate(pairs):
            xp, yp = px(pairs[i], i, n)
            parts.append(f'<circle cx="{xp:.1f}" cy="{yp:.1f}" r="3" fill="#4f8cff"/>')
    elif chart_type == "scatter":
        for i, (lab, val) in enumerate(pairs):
            xp, yp = px(pairs[i], i, n)
            parts.append(f'<circle cx="{xp:.1f}" cy="{yp:.1f}" r="5" fill="#3ecf8e" opacity="0.8"/>')
    else:  # bar
        slot = plot_w / max(n, 1)
        bw = min(slot * 0.6, 60)
        for i, (lab, val) in enumerate(pairs):
            xp, _ = px(pairs[i], i, n)
            hgt = (val - vmin) / (vmax - vmin) * plot_h
            x0 = xp - bw / 2
            y0 = pad_t + plot_h - hgt
            parts.append(f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{hgt:.1f}" fill="#4f8cff"/>')
    # X 轴标签
    if chart_type != "pie":
        for i, (lab, val) in enumerate(pairs):
            xp, _ = px(pairs[i], i, n)
            parts.append(f'<text x="{xp:.1f}" y="{height-pad_b+18}" text-anchor="middle" font-size="{CHART_FONT_SIZE_MIN}" fill="#666">{lab[:12]}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def generate_chart(filename, content, chart_type="bar", x_col=None, y_col=None, title="", out_dir=None):
    """生成图表,返回 dict。优先 PNG(matplotlib),缺失时 SVG。"""
    if chart_type not in CHART_TYPES:
        raise ValueError(f"chart_type 必须是 {', '.join(CHART_TYPES)}")
    columns, rows = parse_data(filename, content)
    if not columns or not rows:
        raise ValueError("数据为空或无法解析")
    stats = numeric_stats(rows, columns)
    x, y = _pick_columns(columns, x_col, y_col, chart_type)
    stem = Path(filename or "data").stem
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:40] or "chart"
    out_dir = Path(out_dir) if out_dir else None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams.update({
            "font.family": CHART_FONT_FAMILY_MPL,
            "font.serif": list(CHART_FONT_SERIF),
            "svg.fonttype": "none",
            "font.size": CHART_FONT_SIZE_MIN,
            "axes.spines.right": False,
            "axes.spines.top": False,
            "axes.linewidth": 0.8,
            "legend.frameon": False,
            "axes.unicode_minus": False,
        })
        PALETTE = list(CHART_PALETTE)

        fig, ax = plt.subplots(figsize=(8, 5), dpi=300)
        if chart_type == "pie":
            labels = [str(r.get(x, "")) for r in rows]
            vals = [_as_number(r.get(y)) for r in rows]
            pairs = [(l, v) for l, v in zip(labels, vals) if v is not None]
            if not pairs:
                raise ValueError("没有可用于饼图的数值数据")
            ax.pie([p[1] for p in pairs], labels=[p[0] for p in pairs],
                   autopct="%1.1f%%", colors=PALETTE, startangle=90,
                   wedgeprops={"edgecolor": "white", "linewidth": 1})
            ax.set_title(title or f"{y} 分布")
        elif chart_type == "heatmap":
            import numpy as np
            numeric_cols = [c for c in columns if any(_as_number(r.get(c)) is not None for r in rows)]
            if len(numeric_cols) < 2:
                raise ValueError("热力图至少需要 2 个数值列")
            matrix = np.array([[float(r[c]) if _as_number(r.get(c)) is not None else float("nan") for c in numeric_cols] for r in rows])
            matrix = matrix[:, ~np.isnan(matrix).all(axis=0)]
            if matrix.shape[1] < 2:
                raise ValueError("热力图数值列不足")
            corr = np.corrcoef(matrix.T)
            im = ax.imshow(corr, cmap="coolwarm", vmin=-1, vmax=1, aspect="auto")
            ax.set_xticks(range(len(numeric_cols[:matrix.shape[1]])))
            ax.set_yticks(range(len(numeric_cols[:matrix.shape[1]])))
            ax.set_xticklabels(numeric_cols[:matrix.shape[1]], rotation=45, ha="right")
            ax.set_yticklabels(numeric_cols[:matrix.shape[1]])
            for i in range(corr.shape[0]):
                for j in range(corr.shape[1]):
                    ax.text(j, i, f"{corr[i, j]:.2f}", ha="center", va="center",
                            fontsize=CHART_FONT_SIZE_MIN, color="white" if abs(corr[i, j]) > 0.5 else "#222")
            fig.colorbar(im, ax=ax, shrink=0.8)
            ax.set_title(title or "数值列相关性热力图")
        else:
            xs = [r.get(x, "") for r in rows]
            ys = [_as_number(r.get(y)) for r in rows]
            pairs = [(a, b) for a, b in zip(xs, ys) if b is not None]
            if not pairs:
                raise ValueError("没有可用于绘图的数值数据")
            if chart_type == "bar":
                groups = {}
                for a, b in pairs:
                    groups.setdefault(str(a), []).append(b)
                cats = list(groups.keys())
                means = [statistics.mean(groups[c]) for c in cats]
                sds = [statistics.stdev(groups[c]) if len(groups[c]) > 1 else 0.0 for c in cats]
                xpos = list(range(len(cats)))
                ax.bar(xpos, means, yerr=sds, capsize=5, color=PALETTE[0],
                       alpha=0.85, edgecolor="white", linewidth=0.5, error_kw={"elinewidth": 1.2, "capthick": 1.2})
                for i, c in enumerate(cats):
                    vals = groups[c]
                    jitter = [i + (j - (len(vals) - 1) / 2) * 0.08 for j in range(len(vals))]
                    ax.scatter(jitter, vals, s=14, color="#272727", alpha=0.55, zorder=3)
                    ax.text(i, means[i] + sds[i] + (max(means) - min(means)) * 0.05,
                            f"n={len(vals)}", ha="center", va="bottom", fontsize=CHART_FONT_SIZE_MIN, color="#555")
                ax.set_xticks(xpos)
                ax.set_xticklabels([c[:12] for c in cats], rotation=30, ha="right")
            elif chart_type == "line":
                groups = {}
                for a, b in pairs:
                    groups.setdefault(str(a), []).append(b)
                cats = list(groups.keys())
                if all(len(v) == 1 for v in groups.values()):
                    vals = [groups[c][0] for c in cats]
                    ax.plot(range(len(cats)), vals, marker="o", markersize=4,
                            linewidth=CHART_LINE_WIDTH, color=PALETTE[0])
                else:
                    means = [statistics.mean(groups[c]) for c in cats]
                    sems = [statistics.stdev(groups[c]) / (len(groups[c]) ** 0.5)
                            if len(groups[c]) > 1 else 0.0 for c in cats]
                    xpos = range(len(cats))
                    ax.plot(xpos, means, marker="o", markersize=4, linewidth=CHART_LINE_WIDTH,
                            color=PALETTE[0], label="mean")
                    ax.fill_between(xpos,
                                    [m - s for m, s in zip(means, sems)],
                                    [m + s for m, s in zip(means, sems)],
                                    color=PALETTE[0], alpha=0.15, label="±SEM")
                    ax.legend(loc="best", fontsize=CHART_FONT_SIZE_MIN)
                ax.set_xticks(range(len(cats)))
                ax.set_xticklabels([c[:12] for c in cats], rotation=30, ha="right")
            elif chart_type == "scatter":
                xnums = [_as_number(p[0]) if _as_number(p[0]) is not None else i for i, p in enumerate(pairs)]
                yvals = [p[1] for p in pairs]
                ax.scatter(xnums, yvals, s=22, color=PALETTE[2],
                           edgecolor="white", linewidth=0.5, alpha=0.8)
                if len(xnums) >= 3 and len(set(xnums)) > 1:
                    import numpy as np
                    arr_x = np.array(xnums, dtype=float)
                    arr_y = np.array(yvals, dtype=float)
                    mask = ~(np.isnan(arr_x) | np.isnan(arr_y))
                    if mask.sum() >= 3:
                        sx, sy = arr_x[mask], arr_y[mask]
                        slope, intercept = np.polyfit(sx, sy, 1)
                        r = np.corrcoef(sx, sy)[0, 1]
                        x_fit = np.array([sx.min(), sx.max()])
                        y_fit = slope * x_fit + intercept
                        ax.plot(x_fit, y_fit, color="#D55E00", linewidth=CHART_LINE_WIDTH,
                                linestyle="--", label=f"R²={r**2:.3f}")
                        ax.legend(loc="best", fontsize=CHART_FONT_SIZE_MIN)
            elif chart_type == "box":
                groups = {}
                for a, b in pairs:
                    groups.setdefault(str(a), []).append(b)
                cats = list(groups.keys())
                ax.boxplot([groups[c] for c in cats],
                           patch_artist=True, showmeans=True,
                           medianprops={"color": "#272727", "linewidth": 1.2},
                           meanprops={"marker": "D", "markerfacecolor": "#D55E00",
                                      "markeredgecolor": "white", "markersize": 5},
                           boxprops={"facecolor": PALETTE[0], "alpha": 0.7},
                           whiskerprops={"color": "#272727"}, capprops={"color": "#272727"})
                ax.set_xticks(range(1, len(cats) + 1))
                ax.set_xticklabels([c[:12] for c in cats], rotation=30, ha="right")
                ymax = max(max(groups[c]) for c in cats)
                for i, c in enumerate(cats):
                    ax.text(i + 1, ymax * 1.02, f"n={len(groups[c])}",
                            ha="center", va="bottom", fontsize=CHART_FONT_SIZE_MIN, color="#555")
            elif chart_type == "violin":
                try:
                    import seaborn as sns
                    sns.violinplot(x=[str(p[0]) for p in pairs], y=[p[1] for p in pairs],
                                   hue=[str(p[0]) for p in pairs], ax=ax,
                                   palette=PALETTE, inner="quart", linewidth=0.8, legend=False)
                except Exception:
                    groups = {}
                    for a, b in pairs:
                        groups.setdefault(str(a), []).append(b)
                    cats = list(groups.keys())
                    parts = ax.violinplot([groups[c] for c in cats], showmeans=True, showmedians=True)
                    for pc in parts["bodies"]:
                        pc.set_facecolor(PALETTE[0])
                        pc.set_alpha(0.7)
                    ax.set_xticks(range(1, len(cats) + 1))
                    ax.set_xticklabels([c[:12] for c in cats], rotation=30, ha="right")
            elif chart_type == "hist":
                vals = [p[1] for p in pairs]
                ax.hist(vals, bins="auto", color=PALETTE[0], edgecolor="white", alpha=0.85)
                ax.set_xlabel(str(y))
                ax.set_ylabel("频数")
            elif chart_type == "area":
                groups = {}
                for a, b in pairs:
                    groups.setdefault(str(a), []).append(b)
                cats = list(groups.keys())
                if all(len(v) == 1 for v in groups.values()):
                    vals = [groups[c][0] for c in cats]
                else:
                    vals = [statistics.mean(groups[c]) for c in cats]
                xpos = list(range(len(cats)))
                ax.plot(xpos, vals, color=PALETTE[0], linewidth=CHART_LINE_WIDTH)
                ax.fill_between(xpos, vals, color=PALETTE[0], alpha=0.2)
                if not all(len(v) == 1 for v in groups.values()):
                    sems = [statistics.stdev(groups[c]) / (len(groups[c]) ** 0.5)
                            if len(groups[c]) > 1 else 0.0 for c in cats]
                    ax.fill_between(xpos,
                                    [v - s for v, s in zip(vals, sems)],
                                    [v + s for v, s in zip(vals, sems)],
                                    color=PALETTE[0], alpha=0.12)
                ax.set_xticks(xpos)
                ax.set_xticklabels([c[:12] for c in cats], rotation=30, ha="right")
            if chart_type not in ("hist",):
                ax.set_xlabel(str(x))
                ax.set_ylabel(str(y))
            ax.set_title(title or f"{y} by {x}")
            ax.grid(True, linestyle="--", alpha=0.3)
            ax.margins(0.04)
        fig.tight_layout()
        png_buf = io.BytesIO()
        fig.savefig(png_buf, format="png", dpi=300, bbox_inches="tight",
                    pad_inches=CHART_SAVE_PAD_INCHES)
        pdf_buf = io.BytesIO()
        fig.savefig(pdf_buf, format="pdf", bbox_inches="tight",
                    pad_inches=CHART_SAVE_PAD_INCHES)
        plt.close(fig)
        png_raw = png_buf.getvalue()
        pdf_raw = pdf_buf.getvalue()
        if out_dir is not None:
            out_dir = Path(out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            png_fname = f"{stem}_{chart_type}_{ts}.png"
            pdf_fname = f"{stem}_{chart_type}_{ts}.pdf"
            (out_dir / png_fname).write_bytes(png_raw)
            (out_dir / pdf_fname).write_bytes(pdf_raw)
            rel = f"data/charts/{png_fname}"
            pdf_rel = f"data/charts/{pdf_fname}"
        else:
            png_fname = f"{stem}_{chart_type}_{int(time.time())}.png"
            pdf_fname = png_fname.replace(".png", ".pdf")
            rel = f"data/charts/{png_fname}"
            pdf_rel = f"data/charts/{pdf_fname}"
        return {
            "format": "png",
            "file": str(out_dir / png_fname) if out_dir else png_fname,
            "pdf_file": str(out_dir / pdf_fname) if out_dir else pdf_fname,
            "rel": rel,
            "pdf_rel": pdf_rel,
            "data_url": _data_url_bytes(png_raw, "image/png"),
            "stats": stats,
            "columns": columns,
            "x": x,
            "y": y,
        }
    except ImportError:
        svg_text = _svg_chart(rows, columns, chart_type, x_col, y_col, title)
        if out_dir is not None:
            res = _save_svg(svg_text, out_dir, stem, chart_type)
        else:
            raw = svg_text.encode("utf-8")
            res = {
                "format": "svg",
                "file": f"{stem}_{chart_type}.svg",
                "rel": f"data/charts/{stem}_{chart_type}.svg",
                "data_url": _data_url_bytes(raw, "image/svg+xml"),
            }
        res["stats"] = stats
        res["columns"] = columns
        res["x"] = x
        res["y"] = y
        return res

# ───────────── 图表混合路由（任务 38）：路线判定 / 分发 / 降级链 ─────────────
# route 取值: data(data/ 数据自动绘图) / imagegen(AI 生图) / text(结构化文字图注)
# 判定优先级: 「绘制」列手动覆盖 > 数据来源命中 > 关键视觉生图 > 文字图注

def _import_data2paper():
    """函数级导入，规避 data2paper(`from web.charts import ...`)与本模块的循环依赖。"""
    try:
        import data2paper
        return data2paper
    except ImportError:
        import sys
        if str(Path(__file__).resolve().parent.parent) not in sys.path:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import data2paper
        return data2paper


def _import_imagegen():
    """函数级导入 imagegen_bridge（兼容 web. 包导入与平铺导入两种上下文）。"""
    try:
        from web import imagegen_bridge
        return imagegen_bridge
    except Exception:
        import imagegen_bridge
        return imagegen_bridge


def _rec_fig_id(rec):
    return (rec.get("编号") or "").strip()


def _rec_type(rec):
    return (rec.get("类型") or rec.get("类型(图/表)") or "").strip()


def _is_figure_rec(rec):
    """仅处理『图』行（表行由 data2paper.generate_planned_tables 负责）。"""
    return _rec_fig_id(rec).startswith("图") and "图" in _rec_type(rec)


_MANUAL_ROUTE_MAP = (
    # 全等匹配（strip 后）：「自动判定」等非显式取值一律归入自动判定路径
    (("自动绘图",), "data"),
    (("生图",), "imagegen"),
    (("文字图注",), "text"),
)


def _manual_route(val):
    v = (val or "").strip()
    if not v:
        return None
    for keys, route in _MANUAL_ROUTE_MAP:
        if v in keys:
            return route
    return None


_KEY_VISUAL_PLACEHOLDERS = {"无", "暂无", "-", "—", "N/A", "n/a", "none", "None", "空"}


def _clean_key_visual(v):
    """关键视觉占位符过滤：占位值（无/暂无/-/—/N/A/none/空 等）与纯符号一律视为空。"""
    s = (v or "").strip()
    if not s or s in _KEY_VISUAL_PLACEHOLDERS:
        return ""
    if all(not ch.isalnum() for ch in s):  # 纯符号（如 —— / …）
        return ""
    return s


def judge_figure_route(rec, project):
    """单张图路线判定 → (route, reason)。

    优先级: 「绘制」列手动覆盖 > 数据来源命中 data/ 文件 > 关键视觉非空走 imagegen > text。
    imagegen 未配置时 route 仍为 imagegen，reason 注明「未配置，将降级」，降级在生成期执行。
    """
    d2p = _import_data2paper()
    data_dir = Path(project) / "data"
    source = (rec.get("数据来源") or "").strip()
    key_visual = (rec.get("关键视觉") or "").strip()
    data_file = None
    if source and data_dir.exists():
        data_file = d2p.find_data_file(data_dir, source)

    manual = _manual_route(rec.get("绘制"))
    if manual == "data":
        if data_file is not None:
            return ("data", "手动覆盖：自动绘图（数据文件命中 %s）" % data_file.name)
        # 手动指定自动绘图但数据文件缺失 → 继续走自动判定
    elif manual == "imagegen":
        if _import_imagegen().is_image_gen_configured():
            return ("imagegen", "手动覆盖：AI 生图")
        return ("imagegen", "手动覆盖：AI 生图；imagegen 未配置，将降级")
    elif manual == "text":
        return ("text", "手动覆盖：文字图注")

    # 自动判定的关键视觉先过滤占位值（手动显式「生图」覆盖已在上方返回，不受此过滤影响）
    key_visual = _clean_key_visual(key_visual)

    if source and data_file is not None:
        return ("data", "数据来源命中文件 %s" % data_file.name)
    if source and data_file is None and not key_visual:
        return ("text", "数据来源『%s』在 data/ 下未匹配到文件，且无关键视觉" % source)
    if key_visual:
        note = "" if _import_imagegen().is_image_gen_configured() else "；imagegen 未配置，将降级"
        if source and data_file is None:
            return ("imagegen", "数据来源未找到匹配文件，改按关键视觉生图" + note)
        return ("imagegen", "无数据源，关键视觉非空" + note)
    return ("text", "无数据源且无关键视觉，使用结构化文字图注")


def _chart_stem_match(p_stem, stem):
    """产物文件名严格分段匹配：数据源 stem 完全相等，或等于产物命名
    `<stem>_<chart_type>_<ts>` 的首段；防止 exp1 误命中 exp10_*、图1 误命中 图10_*。"""
    if not stem:
        return False
    return p_stem == stem or p_stem.split("_")[0] == stem


def _artifact_exists(rec, project):
    """产物状态检查：同时扫 data/figures（imagegen 产物）与 data/charts（data 路线产物）。"""
    project = Path(project)
    key_visual = (rec.get("关键视觉") or "").strip()
    if key_visual:
        igb = _import_imagegen()
        key = igb.cache_key_for(key_visual)
        figures_dir = project / "data" / "figures"
        if igb.find_cached_figure_image(figures_dir, key) is not None:
            return True
    source = (rec.get("数据来源") or "").strip()
    data_dir = project / "data"
    if source and data_dir.exists():
        data_file = _import_data2paper().find_data_file(data_dir, source)
        if data_file is not None:
            charts_dir = project / "data" / "charts"
            if charts_dir.exists():
                stem = data_file.stem
                for p in charts_dir.iterdir():
                    if p.is_file() and _chart_stem_match(p.stem, stem):
                        return True
    return False


def judge_figure_routes(project):
    """全量路线判定 → list[dict]，每项含 编号/内容/route/reason/产物状态(exists|missing)。"""
    plan = _import_data2paper().parse_figures_plan(project)
    out = []
    for rec in plan:
        if not _is_figure_rec(rec):
            continue
        route, reason = judge_figure_route(rec, project)
        out.append({
            "编号": _rec_fig_id(rec),
            "内容": (rec.get("内容") or "").strip(),
            "route": route,
            "reason": reason,
            "产物状态": "exists" if _artifact_exists(rec, project) else "missing",
        })
    return out


def figure_caption_block(rec):
    """结构化文字图注 md 块（text 路线兜底产物）：标题句 + 目的/结构元素/读者收获。"""
    fig_id = _rec_fig_id(rec) or "图?"
    num = fig_id[1:].strip() if fig_id.startswith("图") else fig_id
    content = (rec.get("内容") or "").strip()
    claims = (rec.get("本图回答的 Claims") or "").strip()
    section = (rec.get("对应章节") or "").strip()
    head = re.split(r"[。；]|\.(?=\s|$)", content, maxsplit=1)[0]
    head = head.split("(")[0].split("（")[0].strip()
    title = head or (content[:40] if content else "（标题待定）")
    lines = ["**Figure %s. %s**" % (num, title), ""]
    lines.append("- 目的: %s" % (content or "（内容待定）"))
    lines.append("- 结构元素: 按 figures.md 规划组织面板与标注；回答的 Claims: %s" % (claims or "（未填写）"))
    gain = "读者可由本图直接确认: %s" % (claims or content or "该图承载的核心结论")
    if section:
        gain += "（对应章节: %s）" % section
    lines.append("- 读者收获: %s" % gain)
    lines.append("")
    lines.append("> 注: 本条目为结构化文字图注（无匹配数据源且未启用 AI 生图时的兜底产物）。")
    return "\n".join(lines)


def generate_figure(rec, project, out_dir=None):
    """按 route 分发生成单张图，返回 {fig_id, route, ok, file/rel/text, fallback_reason}。

    imagegen 失败降级链: 有数据源且命中文件 → data 近似图；否则 → text 文字图注。
    产物只落 data/charts/（data 路线）或 data/figures/（imagegen 路线）。
    """
    d2p = _import_data2paper()
    project = Path(project)
    fig_id = _rec_fig_id(rec)
    route, _reason = judge_figure_route(rec, project)
    result = {"fig_id": fig_id, "route": route, "ok": False, "fallback_reason": ""}

    def _finish_data():
        source = (rec.get("数据来源") or "").strip()
        data_file = d2p.find_data_file(project / "data", source) if source else None
        if data_file is None:
            raise ValueError("数据文件未找到: %s" % (source or "(空)"))
        columns, rows = d2p.read_table(data_file)
        numeric = d2p.numeric_columns(columns, rows)
        if not numeric:
            raise ValueError("数据文件无数值列: %s" % data_file.name)
        x = next((c for c in columns if c not in numeric), columns[0])
        y = d2p._pick_value_column(rec.get("内容", ""), numeric)
        chart_type = d2p.chart_type_from_requirement(rec.get("期刊规范要求", "") or "")
        target_dir = Path(out_dir) if out_dir else project / "data" / "charts"
        target_dir.mkdir(parents=True, exist_ok=True)
        content = data_file.read_text(encoding="utf-8-sig", errors="replace")
        res = generate_chart(data_file.name, content, chart_type, x, y,
                             "%s: %s" % (fig_id, (rec.get("内容", "") or "").strip()), target_dir)
        result.update(ok=True, file=res.get("file"), rel=res.get("rel"))

    def _finish_imagegen():
        igb = _import_imagegen()
        key_visual = (rec.get("关键视觉") or "").strip()
        cache_key = igb.cache_key_for(key_visual)
        target_dir = Path(out_dir) if out_dir else project / "data" / "figures"
        res = igb.generate_figure_image(key_visual, target_dir, cache_key)
        fpath = Path(res["file"])
        try:
            rel = fpath.relative_to(project).as_posix()
        except ValueError:
            rel = fpath.name
        result.update(ok=True, file=str(fpath), rel=rel, cached=bool(res.get("cached")))

    def _fallback_text(reason):
        result["route"] = "text"
        result["text"] = figure_caption_block(rec)
        result["ok"] = True
        result["fallback_reason"] = reason

    if route == "data":
        try:
            _finish_data()
        except Exception as e:
            _fallback_text("data 路线失败(%s)，降级为文字图注" % e)
    elif route == "imagegen":
        try:
            _finish_imagegen()
        except Exception as e:
            source = (rec.get("数据来源") or "").strip()
            data_file = d2p.find_data_file(project / "data", source) if source else None
            if data_file is not None:
                try:
                    result["route"] = "data"
                    _finish_data()
                    result["fallback_reason"] = "imagegen 失败(%s)，降级为 data 近似图" % e
                except Exception as e2:
                    _fallback_text("imagegen 失败(%s)；data 近似图亦失败(%s)，降级为文字图注" % (e, e2))
            else:
                _fallback_text("imagegen 失败(%s)，降级为文字图注" % e)
    else:
        result["text"] = figure_caption_block(rec)
        result["ok"] = True
    return result


def generate_all_figures(project, mode="auto", ids=None):
    """按 framework/figures.md 规划批量生成图。

    mode=auto: 跳过已有产物（route=data/imagegen 且产物 exists）；mode=all: 强制重生成。
    ids: 可选，仅处理指定编号（如 ["图1", "图3"]）。
    只写 data/charts/ 与 data/figures/，不触碰 manuscript/main.md（回填是后续任务）。
    返回 {total, generated, skipped, fallback, results:[...]}。
    """
    plan = _import_data2paper().parse_figures_plan(project)
    wanted = None
    if ids:
        wanted = set(str(i).strip() for i in ids if str(i).strip())
    results = []
    generated = skipped = fallback = 0
    for rec in plan:
        if not _is_figure_rec(rec):
            continue
        fig_id = _rec_fig_id(rec)
        if wanted is not None and fig_id not in wanted:
            continue
        if mode == "auto" and _artifact_exists(rec, project):
            skipped += 1
            results.append({"fig_id": fig_id, "route": None, "ok": True, "skipped": True,
                            "fallback_reason": "产物已存在（mode=auto 跳过）"})
            continue
        if mode == "all":
            # 强制重绘：imagegen 路线先删已有缓存文件，使本次必然重新生成
            route_chk, _ = judge_figure_route(rec, project)
            if route_chk == "imagegen":
                key_visual = (rec.get("关键视觉") or "").strip()
                if key_visual:
                    igb = _import_imagegen()
                    igb.clear_cached_figure_image(project / "data" / "figures",
                                                  igb.cache_key_for(key_visual))
        try:
            res = generate_figure(rec, project)
        except Exception as e:
            res = {"fig_id": fig_id, "route": None, "ok": False,
                   "fallback_reason": "生成异常: %s" % e}
        if res.get("ok"):
            generated += 1
        if res.get("fallback_reason") and "产物已存在" not in res["fallback_reason"]:
            fallback += 1
        results.append(res)
    return {"total": len(results), "generated": generated, "skipped": skipped,
            "fallback": fallback, "results": results}
