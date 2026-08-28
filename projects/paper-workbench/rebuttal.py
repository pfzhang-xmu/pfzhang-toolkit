# -*- coding: utf-8 -*-
"""rebuttal.py — 审稿意见回复草稿（point-by-point rebuttal）

定位:
- 与 simulate-reviewers（出审稿意见）互补：本模块负责"回意见"——把审稿
  意见（模拟审稿产物或真实审稿信）拆条，生成 point-by-point 回复草稿骨架；
- 「AI 产草稿、人定策略与语气」：--gen 仅经 web/ai_client.py 填 Response
  初稿并强制标注「草稿-待人定稿」；无任何自动应用路径（不改稿件/既有产物）；
- 输入三通道：
    1) --src 外部审稿信文件；
    2) stdin（管道输入，如 cat letter.txt | python rebuttal.py draft）；
    3) 默认项目通道：review/mock-reviews.md + review/tasks.md 问题清单表。

CLI:
    python rebuttal.py draft [--src FILE] [--dir PROJ] [--gen]
    python rebuttal.py reparse [--dir PROJ]

产物（只写新文件，不触碰既有产物）:
    review/rebuttal/items.json   人工可编辑中间态（拆条 + Response/Action 槽）
    review/rebuttal/draft.md     point-by-point 草稿 + 末尾修改建议清单表

退出码: 0=成功（含解析失败降级为全文嵌入）；1=输入缺失/参数错误。
纯标准库，零第三方依赖（--gen 通道复用仓库内 web/ai_client.py）。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

WORKBENCH_DIR = Path(__file__).resolve().parent

# ─────────────────────────── severity 关键词表 ───────────────────────────

MAJOR_KW = (
    "major concern", "major weakness", "major revision", "fundamental",
    "flaw", "flawed", "critical", "fatal", "serious", "reject",
    "significant weakness", "unacceptable", "not novel", "insufficient",
)
MINOR_KW = (
    "minor", "typo", "clarify", "clarification", "suggestion", "nitpick",
    "small issue", "optional", "cosmetic", "language", "formatting",
)

# ─────────────────────────── 章节别名 → 契约大纲映射 ───────────────────────────

# 规范词干 → 别名（中英）。契约大纲某节标题命中别名、且审稿意见文本也命中
# 同组别名时，该意见关联到对应段号（S1/S2/…）。
STEM_ALIASES = {
    "introduction": ("introduction", "intro", "引言", "绪论", "前言", "研究背景", "background"),
    "method": ("method", "methods", "materials", "方法", "材料与方法", "实验设计", "experimental design"),
    "result": ("result", "results", "结果", "实验结果", "findings"),
    "discussion": ("discussion", "讨论"),
    "conclusion": ("conclusion", "conclusions", "结论", "总结", "outlook", "展望", "future work"),
    "abstract": ("abstract", "摘要"),
    "safety": ("safety", "安全", "安全性", "监管", "regulatory"),
    "figure": ("figure", "figures", "图"),
    "table": ("table", "tables", "表"),
}

REVIEWER_HDR_RE = re.compile(
    r"^\s*#{1,6}\s*(Reviewer\s*\d+\b.*|审稿人\s*\d+.*|视角\s*\d+.*)$", re.I)
MAJORMINOR_RE = re.compile(
    r"^\s*#{0,6}\s*(Major|Minor)\s+(concerns?|issues?|comments?|points?|revision.*?)\s*[:：]?\s*$", re.I)
COMMENT_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:Comment|Point|意见|问题)\s*(\d+)\s*[:：.、]\s*(.*)$", re.I)
NUMDOT_RE = re.compile(r"^\s*(?:[-*•]\s*)?(\d+)[.、]\s+(\S.*)$")
PAREN_RE = re.compile(r"^\s*(?:[-*•]\s*)?[（(](\d+)[)）]\s*(\S.*)$")
MOCK_BULLET_RE = re.compile(r"^\s*-\s*(意见|认可|对应|问题|建议|结论)\s*[:：]\s*(.*)$")
SECTION_REF_RE = re.compile(r"(?:§|Sec(?:tion)?\.?\s*|第\s*)(\d+(?:\.\d+)*)(?:\s*[节章])?", re.I)
TASKS_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
TASKS_SEP_RE = re.compile(r"^\|[\s:\-|]+\|\s*$")
PROBLEM_HINT_RE = re.compile(r"P0|P1|P2|⚠|未达|缺失|问题|未关闭|残留|超|待补")

L10N = {
    "zh": {
        "title": "# 审稿意见回复草稿（point-by-point rebuttal）",
        "note": ("> 说明：Response/Action 均为「草稿-待人定稿」槽位；回复策略与语气由作者定夺。\n"
                 "> 人工编辑 review/rebuttal/items.json 后运行 `python rebuttal.py reparse` 重渲染本文件。\n"
                 "> 本草稿不自动应用到稿件，仅作为回信底稿。"),
        "point": "意见", "reviewer": "审稿人", "severity": "类型", "sections": "相关章节",
        "original": "审稿原文", "response": "Response", "action": "Action",
        "empty_slot": "_（待人定稿：请编辑 items.json 后运行 reparse）_",
        "ai_marker": "【草稿-待人定稿】",
        "gap_title": "## 修改建议清单",
        "th": "| # | 来源 | 类型 | Action 建议 | 相关章节 | 状态 |",
        "th_sep": "|---|------|------|------------|----------|------|",
        "status_open": "待处理",
    },
    "en": {
        "title": "# Rebuttal Draft (point-by-point response)",
        "note": ("> Note: Response/Action are draft slots pending author finalization; strategy and tone are up to the author.\n"
                 "> Edit review/rebuttal/items.json then run `python rebuttal.py reparse` to re-render this file.\n"
                 "> This draft is never auto-applied to the manuscript; it is a reply scaffold only."),
        "point": "Point", "reviewer": "Reviewer", "severity": "Type", "sections": "Sections",
        "original": "Original comment", "response": "Response", "action": "Action",
        "empty_slot": "_(pending author finalization: edit items.json then run reparse)_",
        "ai_marker": "[AI DRAFT - pending author finalization]",
        "gap_title": "## Action-item Summary",
        "th": "| # | Source | Type | Suggested action | Sections | Status |",
        "th_sep": "|---|--------|------|------------------|----------|--------|",
        "status_open": "open",
    },
}

SEV_LABEL = {"major": "major", "minor": "minor", "neutral": "neutral", "positive": "positive"}


# ─────────────────────────── 项目/输入定位 ───────────────────────────


def resolve_project(dir_arg):
    """--dir 优先；否则自 cwd 向上找 state.json；找不到返回 None。"""
    if dir_arg:
        p = Path(dir_arg).resolve()
        if (p / "state.json").exists():
            return p
        raise SystemExit(f"✗ 指定目录不是论文项目(缺 state.json): {p}")
    cur = Path.cwd().resolve()
    for d in [cur] + list(cur.parents):
        if (d / "state.json").exists():
            return d
    return None


def _read_lang(proj):
    if proj and (proj / "state.json").exists():
        try:
            return json.loads((proj / "state.json").read_text(encoding="utf-8")).get("lang", "zh") or "zh"
        except Exception:
            pass
    return "zh"


def load_contract_sections(proj):
    """读 draft/contract.md 的「章节大纲」表 → [(sid, title)]；不存在/解析失败返回 []。"""
    if not proj:
        return []
    p = proj / "draft" / "contract.md"
    if not p.exists():
        return []
    rows = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\|\s*(S\d+)\s*\|\s*([^|]+?)\s*\|", line)
            if m:
                rows.append((m.group(1), m.group(2).strip()))
    except Exception:
        return []
    # 去掉表头/占位行
    return [(sid, t) for sid, t in rows if t and "标题" not in t and "---" not in t]


# ─────────────────────────── parse_points ───────────────────────────


def classify_severity(text, preset=None):
    """severity 以关键词表为准；Major/Minor 分节仅在无关键词命中时作兜底。"""
    low = (text or "").lower()
    if any(k in low for k in MAJOR_KW):
        return "major"
    if any(k in low for k in MINOR_KW):
        return "minor"
    if preset in ("major", "minor"):
        return preset
    return "neutral"


def locate_sections(text, contract_sections):
    """显式 §N / Section N / 第N节 引用 + 章节别名→契约大纲映射；保序去重。"""
    secs = []
    for m in SECTION_REF_RE.finditer(text or ""):
        tag = "§" + m.group(1)
        if tag not in secs:
            secs.append(tag)
    low = (text or "").lower()
    for sid, title in contract_sections or []:
        tl = title.lower()
        for _stem, aliases in STEM_ALIASES.items():
            if not any(a in tl for a in aliases):
                continue
            for a in aliases:
                hit = (re.search(r"\b" + re.escape(a) + r"\b", low) if a.isascii()
                       else (a in low))
                if hit and sid not in secs:
                    secs.append(sid)
            break
    return secs


def _point_start(line):
    """识别条目起始行 → (label, body) 或 None。"""
    m = COMMENT_RE.match(line)
    if m:
        return f"Comment {m.group(1)}", m.group(2).strip()
    m = PAREN_RE.match(line)
    if m:
        return f"({m.group(1)})", m.group(2).strip()
    m = NUMDOT_RE.match(line)
    if m:
        body = m.group(2).strip()
        # 排除纯年份行等误报
        if not re.match(r"^\d{4}[.:：]?\s*$", body):
            return f"{m.group(1)}.", body
    return None


def parse_points(text, contract_sections=None):
    """把审稿信文本拆条 → [{id, reviewer, label, severity, text, sections}]。

    支持格式：Comment 1: / 1. / (1) / Major-Minor 分节 / 项目 mock-reviews.md
    的「视角 N + - 意见：」要点列表。解析不到任何条目时返回 []（由调用方降级）。
    """
    points = []
    reviewer, rev_idx = "", 0
    pending_sev = None
    open_pt = False  # 当前是否存在开放条目（普通标题后置假，防后续段落误并入）

    def _push(label, body):
        nonlocal open_pt
        open_pt = True
        points.append({"reviewer": reviewer, "label": label, "text": body.strip(),
                       "_sev_preset": pending_sev})

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = REVIEWER_HDR_RE.match(line)
        if m:
            rev_idx += 1
            reviewer = m.group(1).strip()
            pending_sev = None
            open_pt = False
            continue
        m = MAJORMINOR_RE.match(line)
        if m:
            pending_sev = "major" if m.group(1).lower() == "major" else "minor"
            continue
        if line.lstrip().startswith("#"):  # 其他标题：终止当前上下文
            pending_sev = None
            open_pt = False
            continue
        m = MOCK_BULLET_RE.match(line)
        if m:
            kind, body = m.group(1), m.group(2).strip()
            if kind in ("意见", "问题", "建议"):
                _push(kind, body)
            elif kind == "对应" and open_pt and points:
                points[-1]["text"] += f"（对应：{body}）"
            # 认可/结论 行不入条
            continue
        start = _point_start(line)
        if start:
            _push(start[0], start[1])
            continue
        stripped = line.strip()
        if stripped.startswith(("-", "*", "•")) and pending_sev:
            _push(pending_sev.capitalize(), stripped.lstrip("-*• ").strip())
            continue
        if open_pt and points and not stripped.startswith("|"):
            points[-1]["text"] += " " + stripped  # 续行并入上一条
        elif pending_sev and len(stripped) >= 20:
            _push(pending_sev.capitalize(), stripped)

    out = []
    for i, p in enumerate(points, 1):
        t = p["text"].strip()
        if not t:
            continue
        out.append({
            "id": f"P{i}",
            "reviewer": p["reviewer"],
            "label": p["label"],
            "severity": classify_severity(t, p.get("_sev_preset")),
            "text": t,
            "sections": locate_sections(t, contract_sections),
        })
    return out


def parse_tasks_tables(text):
    """tasks.md 问题清单表 → 条目列表（仅保留含问题信号的表行）。"""
    points, header_seen = [], False
    for line in (text or "").splitlines():
        if not TASKS_ROW_RE.match(line):
            header_seen = False
            continue
        if TASKS_SEP_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        joined = " | ".join(cells)
        if not header_seen:  # 每张表的第一行视为表头
            header_seen = True
            continue
        if not PROBLEM_HINT_RE.search(joined):
            continue
        points.append({
            "id": "", "reviewer": "tasks.md", "label": cells[0] if cells else "",
            "severity": classify_severity(joined), "text": joined, "sections": [],
        })
    return points


# ─────────────────────────── --gen：AI 草稿 ───────────────────────────


def _ai_draft_response(point, lang):
    """经 web/ai_client.py 生成 Response/Action 初稿；失败返回 (\"\", \"\")。"""
    try:
        sys.path.insert(0, str(WORKBENCH_DIR / "web"))
        import ai_client
    except Exception as e:
        print(f"[warn] ai_client 不可用，跳过 --gen: {e}", file=sys.stderr)
        return "", ""
    lang_desc = "英文" if lang == "en" else "中文"
    prompt = (
        "你是论文作者回复审稿人的助手。针对下面这条审稿意见起草 point-by-point 回复。\n"
        "要求：礼貌克制、具体可执行、不得虚构数据或实验；策略由作者后续定夺。\n"
        f"输出两段：第一段以 Response: 开头（正面回应），第二段以 Action: 开头（稿件修改动作）。语言：{lang_desc}。\n\n"
        f"审稿意见（类型 {point['severity']}，涉及章节 {'、'.join(point['sections']) or '未定位'}）：\n{point['text']}"
    )
    try:
        resp = str(ai_client.chat([{"role": "user", "content": prompt}]) or "")
    except Exception as e:
        print(f"[warn] AI 调用失败（{point['id']}）: {e}", file=sys.stderr)
        return "", ""
    rm = re.search(r"Response\s*[:：]\s*([\s\S]*?)(?=\n\s*Action\s*[:：]|\Z)", resp)
    am = re.search(r"Action\s*[:：]\s*([\s\S]*)", resp)
    return ((rm.group(1).strip() if rm else resp.strip()),
            (am.group(1).strip() if am else ""))


# ─────────────────────────── 渲染 ───────────────────────────


def render_draft(items, lang, source_desc):
    T = L10N.get(lang, L10N["zh"])
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        T["title"], "",
        f"> 生成: {now} | 来源: {source_desc} | 语言: {lang} | 工具: rebuttal.py",
        T["note"], "",
    ]
    for p in items:
        lines += [
            f"## {T['point']} {p['id']}"
            + (f" · {T['reviewer']}: {p['reviewer']}" if p.get("reviewer") else "")
            + (f" · {T['severity']}: {SEV_LABEL.get(p['severity'], p['severity'])}" if p.get("severity") else ""),
            "",
            f"**{T['sections']}**: {'、'.join(p['sections']) if p['sections'] else '-'}",
            "",
            f"> **{T['original']}**:",
        ]
        quote = ["> " + ln for ln in re.split(r"\n+", p["text"].strip()) if ln.strip()]
        lines += quote or ["> -"]
        lines += ["", f"**{T['response']}**:", ""]
        if p.get("response"):
            marker = T["ai_marker"] + "\n" if p.get("resp_via") == "ai" else ""
            lines += [marker + p["response"].strip()]
        else:
            lines.append(T["empty_slot"])
        lines += ["", f"**{T['action']}**:", ""]
        if p.get("action"):
            lines += ["- " + ln for ln in re.split(r"\n+", p["action"].strip()) if ln.strip()]
        else:
            lines.append(T["empty_slot"])
        lines += ["", "---", ""]
    # 末尾修改建议清单表（表格风格对齐 review/tasks.md：竖线表 + 状态列）
    lines += [T["gap_title"], "", T["th"], T["th_sep"]]
    for p in items:
        act = re.sub(r"\s+", " ", p.get("action") or "").strip() or "-"
        lines.append(f"| {p['id']} | {p.get('reviewer') or '-'} | {SEV_LABEL.get(p['severity'], '-')} "
                     f"| {act[:80]} | {'、'.join(p['sections']) or '-'} | {T['status_open']} |")
    return "\n".join(lines) + "\n"


# ─────────────────────────── 主流程 ───────────────────────────


def collect_input(args):
    """三通道取输入 → (text, source_desc)；无可用输入返回 (None, '')。"""
    if getattr(args, "src", None):
        p = Path(args.src)
        if not p.exists():
            raise SystemExit(f"✗ 审稿信文件不存在: {p}")
        return p.read_text(encoding="utf-8", errors="replace"), str(p)
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data, "stdin"
    return None, ""


def run_draft(args):
    proj = resolve_project(getattr(args, "dir", None))
    lang = _read_lang(proj)
    contract_sections = load_contract_sections(proj)

    text, source = collect_input(args)
    sources, tasks_points = [], []
    if text is None:
        # 默认项目通道：mock-reviews.md + tasks.md 问题清单表
        if not proj:
            print("✗ 无输入：请提供 --src 审稿信文件 / 管道输入，或在论文项目目录内运行", file=sys.stderr)
            sys.exit(1)
        mock = proj / "review" / "mock-reviews.md"
        tasks = proj / "review" / "tasks.md"
        if mock.exists():
            text = mock.read_text(encoding="utf-8", errors="replace")
            sources.append("review/mock-reviews.md")
        if tasks.exists():
            tasks_points = parse_tasks_tables(tasks.read_text(encoding="utf-8", errors="replace"))
            sources.append("review/tasks.md")
        if text is None and not tasks_points:
            print("✗ 项目内未找到 review/mock-reviews.md 或 review/tasks.md，请用 --src 指定审稿信", file=sys.stderr)
            sys.exit(1)
        text = text or ""
    else:
        sources = [source]
    source_desc = " + ".join(sources) if sources else source

    points = parse_points(text, contract_sections) if text.strip() else []
    degraded = False
    if not points and text.strip():
        # 解析失败降级：全文嵌入 + 空模板（退出码仍为 0）
        degraded = True
        points = [{"id": "P1", "reviewer": "", "label": "raw",
                   "severity": "neutral", "text": text.strip(),
                   "sections": []}]
    # tasks.md 问题行并入
    for tp in tasks_points:
        tp["id"] = f"P{len(points) + 1}"
        points.append(tp)

    if getattr(args, "gen", False):
        for p in points:
            resp, act = _ai_draft_response(p, lang)
            if resp:
                p["response"], p["action"], p["resp_via"] = resp, act, "ai"
    for p in points:
        p.setdefault("response", "")
        p.setdefault("action", "")
        p.setdefault("resp_via", "")

    out_dir = (proj / "review" / "rebuttal") if proj else Path("review") / "rebuttal"
    out_dir.mkdir(parents=True, exist_ok=True)
    items = {
        "schema": 1,
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "source": sources,
        "lang": lang,
        "degraded": degraded,
        "points": points,
    }
    (out_dir / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    (out_dir / "draft.md").write_text(render_draft(points, lang, source_desc), encoding="utf-8")

    sev_stat = {}
    for p in points:
        sev_stat[p["severity"]] = sev_stat.get(p["severity"], 0) + 1
    stat_txt = ", ".join(f"{k}={v}" for k, v in sorted(sev_stat.items()))
    print(f"✔ 拆条 {len(points)} 条（{stat_txt}）{'【降级:全文嵌入】' if degraded else ''}")
    print(f"  → {out_dir / 'items.json'}（人工可编辑：改 Response/Action 后 reparse）")
    print(f"  → {out_dir / 'draft.md'}")
    if degraded:
        print("  ⚠ 未识别出条目结构，已按全文嵌入处理（退出码 0）")


def run_reparse(args):
    proj = resolve_project(getattr(args, "dir", None))
    base = (proj / "review" / "rebuttal") if proj else Path("review") / "rebuttal"
    items_path = base / "items.json"
    if not items_path.exists():
        print(f"✗ 未找到 {items_path}，请先运行 draft 生成", file=sys.stderr)
        sys.exit(1)
    items = json.loads(items_path.read_text(encoding="utf-8"))
    points = items.get("points", [])
    lang = items.get("lang") or _read_lang(proj)
    source_desc = " + ".join(items.get("source") or []) or "(未知来源)"
    (base / "draft.md").write_text(render_draft(points, lang, source_desc), encoding="utf-8")
    print(f"✔ 已按人工编辑后的 items.json 重渲染 {len(points)} 条 → {base / 'draft.md'}")


def cmd_rebuttal(args):
    """供 wb.py 委托的入口：args 需含 action('draft'/'reparse')、src、gen、dir。"""
    act = getattr(args, "action", "draft") or "draft"
    if act == "draft":
        run_draft(args)
    elif act == "reparse":
        run_reparse(args)
    else:
        print(f"✗ 未知动作: {act}", file=sys.stderr)
        sys.exit(1)


def main():
    # GBK 控制台兼容：重定向/非 UTF-8 终端下避免 非GBK字符 抛 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser(prog="rebuttal",
                                 description="审稿意见回复草稿（point-by-point；AI 产草稿、人定策略与语气）")
    sub = ap.add_subparsers(dest="action", required=True)

    p = sub.add_parser("draft", help="拆解审稿意见 → items.json + draft.md")
    p.add_argument("--src", default=None, help="外部审稿信文件（缺省走 stdin / 项目默认通道）")
    p.add_argument("--dir", default=None, help="论文项目目录（默认自动查找）")
    p.add_argument("--gen", action="store_true",
                   help="经 web/ai_client.py 填 Response 草稿（强制标草稿-待人定稿，不自动应用）")

    p = sub.add_parser("reparse", help="读取人工编辑过的 items.json 重新渲染 draft.md")
    p.add_argument("--dir", default=None, help="论文项目目录（默认自动查找）")

    args = ap.parse_args()
    cmd_rebuttal(args)


if __name__ == "__main__":
    main()
