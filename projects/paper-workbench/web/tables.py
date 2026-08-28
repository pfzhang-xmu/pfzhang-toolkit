#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper Workbench 科研表格模块。
支持三线表生成，导出 LaTeX (booktabs)、CSV、Markdown。
"""
import csv
import io
import json
import statistics
import time
from pathlib import Path

from charts import parse_data, numeric_stats, _as_number

TABLE_TYPES = ("desc", "comparison", "summary", "grouped")


def _fmt_num(v, digits=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        if abs(v) >= 10:
            return f"{v:.1f}"
        return f"{v:.{digits}f}"
    return str(v)


def _fmt_mean_sd(vals, digits=2):
    if not vals:
        return "—"
    m = statistics.mean(vals)
    s = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return f"{_fmt_num(m, digits)} ± {_fmt_num(s, digits)}"


def generate_desc_table(columns, rows, group_col=None):
    """描述统计表：均值±SD，n，中位数，范围。"""
    if group_col and group_col in columns:
        groups = {}
        for r in rows:
            g = str(r.get(group_col, "")).strip() or "NA"
            groups.setdefault(g, []).append(r)
        cats = sorted(groups.keys())
        numeric = [c for c in columns if c != group_col and
                   any(_as_number(r.get(c)) is not None for r in rows)]
        if not numeric:
            raise ValueError("无数值列可统计")
        header = [group_col, "指标", "n", "均值±SD", "中位数", "范围"]
        data_rows = []
        for g in cats:
            for nc in numeric:
                vals = [_as_number(r.get(nc)) for r in groups[g]]
                vals = [v for v in vals if v is not None]
                if vals:
                    data_rows.append([
                        g, nc, str(len(vals)),
                        _fmt_mean_sd(vals),
                        _fmt_num(statistics.median(vals)),
                        f"{_fmt_num(min(vals))}–{_fmt_num(max(vals))}",
                    ])
        return {"header": header, "rows": data_rows, "type": "desc"}
    else:
        numeric = [c for c in columns if
                   any(_as_number(r.get(c)) is not None for r in rows)]
        if not numeric:
            raise ValueError("无数值列可统计")
        header = ["指标", "n", "均值±SD", "中位数", "最小", "最大"]
        data_rows = []
        for nc in numeric:
            vals = [_as_number(r.get(nc)) for r in rows]
            vals = [v for v in vals if v is not None]
            if vals:
                data_rows.append([
                    nc, str(len(vals)),
                    _fmt_mean_sd(vals),
                    _fmt_num(statistics.median(vals)),
                    _fmt_num(min(vals)),
                    _fmt_num(max(vals)),
                ])
        return {"header": header, "rows": data_rows, "type": "desc"}


def generate_comparison_table(columns, rows, group_col=None, metric_cols=None):
    """对比表：按分组对比各指标的均值±SD。每行一个指标，每列一个分组。"""
    if not group_col or group_col not in columns:
        group_col = next((c for c in columns if
                          not any(_as_number(r.get(c)) is not None for r in rows[:5])), columns[0])
    groups = {}
    for r in rows:
        g = str(r.get(group_col, "")).strip() or "NA"
        groups.setdefault(g, []).append(r)
    cats = sorted(groups.keys())
    if metric_cols is None:
        metric_cols = [c for c in columns if c != group_col and
                       any(_as_number(r.get(c)) is not None for r in rows)]
    if not metric_cols:
        raise ValueError("无对比指标列")
    header = ["指标"] + [f"{c} (n={len(groups[c])})" for c in cats]
    data_rows = []
    for mc in metric_cols:
        row = [mc]
        for g in cats:
            vals = [_as_number(r.get(mc)) for r in groups[g]]
            vals = [v for v in vals if v is not None]
            row.append(_fmt_mean_sd(vals) if vals else "—")
        data_rows.append(row)
    return {"header": header, "rows": data_rows, "type": "comparison"}


def generate_summary_table(columns, rows, max_rows=20):
    """摘要表：直接展示原始数据的关键列，适合综述列举文献/方法。"""
    str_cols = [c for c in columns if
                not any(_as_number(r.get(c)) is not None for r in rows[:10])]
    num_cols = [c for c in columns if c not in str_cols]
    show_cols = str_cols[:3] + num_cols[:5]
    if not show_cols:
        show_cols = columns[:6]
    header = show_cols
    data_rows = []
    for r in rows[:max_rows]:
        data_rows.append([_fmt_cell(r.get(c, "")) for c in show_cols])
    return {"header": header, "rows": data_rows, "type": "summary"}


def _fmt_cell(v):
    if v is None or v == "":
        return "—"
    s = str(v).strip()
    if s.isdigit() and len(s) == 4:
        return s
    n = _as_number(s)
    if n is not None and "." in s:
        return _fmt_num(n)
    if n is not None and len(s) <= 3:
        return _fmt_num(n)
    return s


def generate_grouped_table(columns, rows, group_col=None):
    """分组汇总表：每组一行，列出各数值列的均值±SD。"""
    if not group_col or group_col not in columns:
        group_col = next((c for c in columns if
                          not any(_as_number(r.get(c)) is not None for r in rows[:5])), None)
        if not group_col:
            return generate_desc_table(columns, rows)
    groups = {}
    for r in rows:
        g = str(r.get(group_col, "")).strip() or "NA"
        groups.setdefault(g, []).append(r)
    cats = sorted(groups.keys())
    numeric = [c for c in columns if c != group_col and
               any(_as_number(r.get(c)) is not None for r in rows)]
    if not numeric:
        raise ValueError("无数值列可汇总")
    header = [group_col, "n"] + [f"{c} (均值±SD)" for c in numeric]
    data_rows = []
    for g in cats:
        vals_all = groups[g]
        row = [g, str(len(vals_all))]
        for nc in numeric:
            vals = [_as_number(r.get(nc)) for r in vals_all]
            vals = [v for v in vals if v is not None]
            row.append(_fmt_mean_sd(vals) if vals else "—")
        data_rows.append(row)
    return {"header": header, "rows": data_rows, "type": "grouped"}


def generate_table(filename, content, table_type="desc", group_col=None,
                   metric_cols=None, title="", out_dir=None):
    """生成科研表格，返回 dict（含预览数据和导出文件路径）。"""
    if table_type not in TABLE_TYPES:
        raise ValueError(f"table_type 必须是 {', '.join(TABLE_TYPES)}")
    columns, rows = parse_data(filename, content)
    if not columns or not rows:
        raise ValueError("数据为空或无法解析")

    stats = numeric_stats(rows, columns)

    if table_type == "desc":
        tbl = generate_desc_table(columns, rows, group_col)
    elif table_type == "comparison":
        tbl = generate_comparison_table(columns, rows, group_col, metric_cols)
    elif table_type == "summary":
        tbl = generate_summary_table(columns, rows)
    elif table_type == "grouped":
        tbl = generate_grouped_table(columns, rows, group_col)
    else:
        raise ValueError(f"未知表格类型: {table_type}")

    tbl["title"] = title
    tbl["source_columns"] = columns
    tbl["row_count"] = len(rows)

    stem = Path(filename or "data").stem
    stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:40] or "table"
    ts = int(time.time())

    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        csv_fname = f"{stem}_{table_type}_{ts}.csv"
        tex_fname = f"{stem}_{table_type}_{ts}.tex"
        md_fname = f"{stem}_{table_type}_{ts}.md"

        csv_path = out_dir / csv_fname
        tex_path = out_dir / tex_fname
        md_path = out_dir / md_fname

        csv_path.write_text(export_csv(tbl), encoding="utf-8-sig")
        tex_path.write_text(export_latex(tbl), encoding="utf-8")
        md_path.write_text(export_markdown(tbl), encoding="utf-8")

        tbl["csv_file"] = str(csv_path)
        tbl["tex_file"] = str(tex_path)
        tbl["md_file"] = str(md_path)
        tbl["csv_rel"] = f"data/tables/{csv_fname}"
        tbl["tex_rel"] = f"data/tables/{tex_fname}"
        tbl["md_rel"] = f"data/tables/{md_fname}"
    else:
        tbl["csv_file"] = None
        tbl["tex_file"] = None
        tbl["md_file"] = None
        tbl["csv_rel"] = None
        tbl["tex_rel"] = None
        tbl["md_rel"] = None

    tbl["csv_content"] = export_csv(tbl)
    tbl["latex_content"] = export_latex(tbl)
    tbl["markdown_content"] = export_markdown(tbl)
    tbl["html_content"] = export_html(tbl)
    tbl["stats"] = stats
    return tbl


def export_csv(tbl):
    """导出 CSV（含表头，UTF-8-BOM 兼容 Excel）。"""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(tbl["header"])
    for row in tbl["rows"]:
        w.writerow(row)
    return buf.getvalue()


def export_latex(tbl):
    """导出 LaTeX 三线表（booktabs 风格，期刊标准格式）。"""
    ncols = len(tbl["header"])
    col_spec = _latex_col_spec(tbl)
    lines = []
    lines.append(r"\begin{table}[htbp]")
    lines.append(r"\centering")
    title = tbl.get("title", "").strip()
    if title:
        lines.append(rf"\caption{{{_latex_escape(title)}}}")
        label = title.lower().replace(" ", "-")[:30]
        lines.append(rf"\label{{tab:{_latex_escape(label)}}}")
    lines.append(rf"\begin{{tabular}}{{{col_spec}}}")
    lines.append(r"\toprule")
    lines.append(" & ".join(_latex_escape(h) for h in tbl["header"]) + r" \\")
    lines.append(r"\midrule")
    for row in tbl["rows"]:
        lines.append(" & ".join(_latex_escape(str(c)) for c in row) + r" \\")
    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")
    if tbl.get("type") == "desc":
        lines.append(r"\footnotesize{注: 数据以均值$\pm$标准差表示; n 为样本量。}")
    elif tbl.get("type") == "comparison":
        lines.append(r"\footnotesize{注: 各组数据以均值$\pm$标准差表示。}")
    lines.append(r"\end{table}")
    return "\n".join(lines)


def _latex_col_spec(tbl):
    """根据列数和数据类型生成 LaTeX 列对齐格式。"""
    ncols = len(tbl["header"])
    if tbl.get("type") == "summary":
        return "l" + "l" * (ncols - 1) if ncols > 1 else "l"
    if ncols <= 1:
        return "l"
    return "l" + "c" * (ncols - 1)


def _latex_escape(s):
    """转义 LaTeX 特殊字符。"""
    s = str(s)
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"),
        ("%", r"\%"),
        ("$", r"\$"),
        ("#", r"\#"),
        ("_", r"\_"),
        ("{", r"\{"),
        ("}", r"\}"),
        ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
        ("±", r"$\pm$"),
        ("—", "---"),
        ("–", "--"),
        ("×", r"$\times$"),
    ]
    for old, new in replacements:
        s = s.replace(old, new)
    return s


def export_markdown(tbl):
    """导出 Markdown 表格。"""
    lines = []
    title = tbl.get("title", "").strip()
    if title:
        lines.append(f"**{title}**\n")
    lines.append("| " + " | ".join(str(h) for h in tbl["header"]) + " |")
    lines.append("|" + "|".join("---" for _ in tbl["header"]) + "|")
    for row in tbl["rows"]:
        lines.append("| " + " | ".join(str(c) for c in row) + " |")
    if tbl.get("type") == "desc":
        lines.append("\n*注: 数据以均值±SD表示; n 为样本量。*")
    elif tbl.get("type") == "comparison":
        lines.append("\n*注: 各组数据以均值±SD表示。*")
    return "\n".join(lines)


def export_html(tbl):
    """导出 HTML 三线表（用于前端预览）。"""
    lines = []
    title = tbl.get("title", "").strip()
    if title:
        lines.append(f'<div class="tbl-title">{_html_escape(title)}</div>')
    lines.append('<table class="sci-table">')
    lines.append("<thead><tr>")
    for h in tbl["header"]:
        lines.append(f"<th>{_html_escape(h)}</th>")
    lines.append("</tr></thead><tbody>")
    for row in tbl["rows"]:
        lines.append("<tr>")
        for c in row:
            lines.append(f"<td>{_html_escape(str(c))}</td>")
        lines.append("</tr>")
    lines.append("</tbody></table>")
    if tbl.get("type") == "desc":
        lines.append('<div class="tbl-note">注: 数据以均值±SD表示; n 为样本量。</div>')
    elif tbl.get("type") == "comparison":
        lines.append('<div class="tbl-note">注: 各组数据以均值±SD表示。</div>')
    return "\n".join(lines)


def _html_escape(s):
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))
