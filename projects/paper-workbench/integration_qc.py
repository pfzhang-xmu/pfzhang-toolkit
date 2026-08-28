# -*- coding: utf-8 -*-
"""integration_qc.py — 整合质检模块（阶段4，并行子代理产物汇总后的机械质检）。

设计原则：
- 机械性质检全部走确定性代码（纯字符串/正则/启发式），禁止调用任何 LLM/ai_client；
  主控 agent 只阅读本模块输出的摘要做裁决。
- 三个核心函数均为「文本进、结构出」的纯函数，可单测：
    qc_references    按正文首现顺序对引用编号做确定性重编号（不碰图表编号）
    qc_terminology   以契约术语表为唯一权威，输出归一化报告（默认只报告不改写）
    qc_transitions   包装 staged_gen._assemble_logic_check，输出 P0/P1/P2 结构化清单，
                     并为 P2 衔接缺口标注具体相邻段位置（供定点修补，不整段重写）
- 默认只返回结果不写盘；仅在 apply/--apply-refs 时才改写文件（先备份）。
- 仅标准库依赖；对 toolbox / staged_gen 只做只读 import（失败时降级，不致命）。

CLI:
    python integration_qc.py <proj> [--apply-refs] [--json]
"""
from __future__ import annotations

import argparse
import bisect
import datetime
import json
import re
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ─────────────────────────── 通用小工具 ───────────────────────────

# 参考文献章节切分（与 toolbox.used_refs 保持一致的口径）
_REFS_SPLIT = re.compile(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", re.I)

# 引用组内部：纯数字/区间，逗号分隔。满足此式才认定为引用组，
# 从而天然排除 "(14,165 ORFs)"、"(CBS 513.88)"、"(~34 Mb)" 等噪声。
_PURE_NUMS = re.compile(
    r"^\d{1,3}(?:\s*[-–—]{1,2}\s*\d{1,3})?"
    r"(?:\s*[,，]\s*\d{1,3}(?:\s*[-–—]{1,2}\s*\d{1,3})?)*$")

_NUM_TOKEN = re.compile(r"(\d{1,3})(?:\s*[-–—]{1,2}\s*(\d{1,3}))?")


def _line_starts(text):
    """返回每行起始偏移列表，配合 bisect 把字符偏移换算成 1 基行号。"""
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _line_no(starts, pos):
    return bisect.bisect_right(starts, pos)


def _code_fence_spans(text):
    """返回 ``` 围栏代码块的 (start, end) 偏移区间列表（围栏内不参与质检）。"""
    spans, in_fence, start = [], False, 0
    for m in re.finditer(r"^[ \t]*```[^\n]*$", text, re.M):
        if not in_fence:
            in_fence, start = True, m.start()
        else:
            spans.append((start, m.end()))
            in_fence = False
    if in_fence:  # 未闭合围栏：到文末都算代码块
        spans.append((start, len(text)))
    return spans


def _in_spans(spans, pos):
    return any(s <= pos < e for s, e in spans)


def _expand_token(tok):
    """把 '4' 或 '4–6' 展开为整数列表（区间展开参与首现排序）。"""
    m = _NUM_TOKEN.fullmatch(tok.strip())
    if not m:
        return []
    a = int(m.group(1))
    if m.group(2) is None:
        return [a]
    b = int(m.group(2))
    if b < a or b - a > 500:  # 防御异常区间
        return []
    return list(range(a, b + 1))


def _compress_nums(nums):
    """把升序去重后的编号列表压缩回 '2, 5–7' 形式（连续段用 en-dash 区间）。"""
    nums = sorted(set(nums))
    if not nums:
        return ""
    parts, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}–{nums[j]}")
        i = j + 1
    return ", ".join(parts)


def _parse_table_rows(md):
    """解析 Markdown 表格数据行（跳过表头与分隔行）。本地实现，避免耦合外部模块改动。"""
    rows = []
    for ln in md.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # 分隔行
        rows.append(cells)
    return rows[1:] if len(rows) > 1 else []  # 第一行视为表头


def _load_refs_pool(proj):
    """读 framework/references.md 候选池 → (条目数, 说明)。复用 toolbox.parse_bibtex（含标准库降级）。"""
    refs_path = Path(proj) / "framework" / "references.md"
    if not refs_path.exists():
        return 0, "无 framework/references.md，跳过未引用编号计算"
    text = refs_path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"```bibtex\s*(.*?)```", text, re.S)
    try:
        sys.path.insert(0, str(ENGINE))
        import toolbox
        entries = toolbox.parse_bibtex(m.group(1) if m else text)
        if entries and isinstance(entries[0], dict) and "error" in entries[0]:
            return 0, f"references.md 解析失败: {entries[0]['error']}"
        return len(entries), ""
    except Exception as exc:  # toolbox 不可用时不影响主流程
        return 0, f"toolbox 不可用({exc})，跳过未引用编号计算"


# ─────────────────────────── ① 引用重编号 ───────────────────────────

def _scan_citation_groups(body, fence_spans):
    """扫描正文引用组，返回 [{start, end, inner, nums, style}]（按出现顺序）。

    - [1]、[2,3]、[4–6] 方括号形式：≥1 个编号即收；
    - (1, 2, 4–6) 圆括号形式：≥2 个编号才收（单数字圆括号歧义大，保守跳过）；
    - 内部必须是纯数字/区间（_PURE_NUMS），Figure/Table、菌株号、度量值自动被排除；
    - 排除围栏代码块内、markdown 链接 [1](url)、图片 ![..] 的情况。
    """
    groups = []
    for style, pat in (("square", re.compile(r"\[([^\[\]\n]{1,160})\]")),
                       ("paren", re.compile(r"\(([^\(\)\n]{1,160})\)"))):
        for m in pat.finditer(body):
            if _in_spans(fence_spans, m.start()):
                continue
            inner = m.group(1).strip()
            if not _PURE_NUMS.match(inner):
                continue
            toks = [t.strip() for t in re.split(r"[,，]", inner) if t.strip()]
            if not toks:
                continue
            if style == "paren" and len(toks) == 1 and not re.search(r"[-–—]", toks[0]):
                # 圆括号单数字 (N): 与门禁 _extract_cited_numbers 口径对齐也收录,
                # 但用「后随句读/位于句末或文末」启发式压低误伤（年份/编号列表等噪声）;
                # 配合 _rewrite_refs_section 的正文残留安全阀, 即使误收也不会误删文献条目。
                nxt = body[m.end():m.end() + 1]
                if nxt and nxt not in ".!?。，、；;:：！？" and nxt != "\n":
                    continue
            if style == "square":
                prev = body[m.start() - 1] if m.start() > 0 else ""
                if prev in "[!":          # 图片 / 嵌套
                    continue
                nxt = body[m.end():m.end() + 1]
                if nxt == "(":            # [1](url) 是链接，不是引用
                    continue
            nums = []
            for t in toks:
                nums.extend(_expand_token(t))
            if not nums:
                continue
            groups.append({"start": m.start(), "end": m.end(), "inner": inner,
                           "nums": nums, "style": style})
    groups.sort(key=lambda g: g["start"])
    return groups


def _rewrite_refs_section(refs_text, mapping, cited, body=""):
    """按 mapping 重排参考文献章节的编号列表（未引用条目删除 = 收窄文献池）。

    保守策略：仅当能识别出编号列表行且编号无重复时才重排，
    否则返回 (None, 说明) 原样保留。
    安全阀：待删编号若在正文（含未被引用组扫描收录的形式）仍出现，
    则放弃删除该条目并记 note（宁可多留, 不可误删）。
    """
    lines = refs_text.splitlines()
    entries = {}  # 旧号 → 行内其余内容
    idx_of = {}   # 旧号 → 行下标
    for i, ln in enumerate(lines):
        m = re.match(r"^\s*(\d{1,3})[.、)]\s?(.*)$", ln)
        if m:
            n = int(m.group(1))
            if n in entries:  # 编号重复，放弃重排
                return None, "参考文献编号有重复，未重排参考文献章节"
            entries[n] = m.group(2)
            idx_of[n] = i
    if not entries:
        return None, "参考文献章节未识别到编号列表，未重排"
    inv = {new: old for old, new in mapping.items()}
    top = max(mapping.values()) if mapping else 0
    if any(k not in entries for k in inv.values()):
        return None, "参考文献列表与正文引用编号不完全对应，未重排"
    new_lines = [ln for i, ln in enumerate(lines) if i not in set(idx_of.values())]
    insert_at = min(idx_of.values())
    rebuilt = []
    for new_num in range(1, top + 1):
        old = inv.get(new_num)
        if old is None:
            return None, "映射存在缺口，未重排参考文献章节"
        rebuilt.append(f"{new_num}. {entries[old]}")
    dropped = sorted(set(entries) - cited)
    # 安全阀: 待删编号若仍以类引用形式出现在正文（含未收录形式, 如句内括号）,
    # 放弃删除该条目并记 note——避免把正文实际引用的条目删光。
    kept_by_body = []
    if body:
        for n in list(dropped):
            pat = re.compile(r"[\[\(][^\[\]\(\)\n]{0,80}(?<![0-9])%d(?![0-9])[^\[\]\(\)\n]{0,80}[\]\)]" % n)
            if pat.search(body):
                kept_by_body.append(n)
    if kept_by_body:
        for n in kept_by_body:
            rebuilt.append(f"{n}. {entries[n]}")
        dropped = [n for n in dropped if n not in kept_by_body]
    out = new_lines[:insert_at] + rebuilt + new_lines[insert_at:]
    note = f"参考文献章节已按新编号重排；删除未引用条目 {dropped}" if dropped else "参考文献章节已按新编号重排"
    if kept_by_body:
        note += f"；编号 {kept_by_body} 未被收录为引用组但正文仍出现, 已保留原条目（编号可能与新序列冲突, 请人工核对）"
    return "\n".join(out), note


def qc_references(proj=None, md_text=None, apply=False):
    """引用编号确定性重编号（纯字符串映射，不涉及任何 LLM）。

    按正文首次出现顺序重排引用编号；区间展开后参与首现排序；
    Figure/Table 编号不在此处理（引用组只匹配纯数字方括号/圆括号）。

    参数:
        proj   项目目录（提供时读取 manuscript/main.md；apply 时写回并备份）
        md_text 直接给定全文（优先于 proj）
        apply  True 才写盘（仅对 proj 模式有效）
    返回:
        {ok, mapping{旧→新}, text(重编号后全文), changed,
         cited(首现顺序编号列表), uncited(候选池未引用编号列表或 None),
         pool_total, notes[], written(写盘路径或空)}
    """
    notes = []
    text = md_text
    main_md = None
    if proj:
        # 有 proj 时 main_md 恒指向 <proj>/manuscript/main.md（修复: 此前只在 md_text is None
        # 分支赋值, 而 run_qc 同时传 proj 与 md_text → --apply-refs 永不写盘, 静默失效）
        main_md = Path(proj) / "manuscript" / "main.md"
    if text is None:
        if main_md is None:
            return {"ok": False, "error": "需要 proj 或 md_text 之一"}
        if not main_md.exists():
            return {"ok": False, "error": f"缺少 {main_md}"}
        text = main_md.read_text(encoding="utf-8", errors="replace")

    # 切出正文与参考文献章节，只在正文里重编号
    parts = _REFS_SPLIT.split(text, maxsplit=1)
    body = parts[0]
    refs_sep = ""
    refs_sec = ""
    if len(parts) > 1:
        m = _REFS_SPLIT.search(text)
        refs_sep = text[len(body):len(text) - len(parts[1])]
        refs_sec = parts[1]

    fence_spans = _code_fence_spans(body)
    groups = _scan_citation_groups(body, fence_spans)

    # 首现顺序 → 旧号→新号 映射
    order = []
    for g in groups:
        for n in g["nums"]:
            if n not in order:
                order.append(n)
    mapping = {old: i + 1 for i, old in enumerate(order)}
    cited = set(order)

    # 改写正文中的引用组（原地重建，无编号碰撞问题）
    changed = 0
    out, cursor = [], 0
    for g in groups:
        new_nums = [mapping[n] for n in g["nums"] if n in mapping]
        new_inner = _compress_nums(new_nums)
        if not new_inner:
            continue
        o, c = ("[", "]") if g["style"] == "square" else ("(", ")")
        new_group = f"{o}{new_inner}{c}"
        if new_group != body[g["start"]:g["end"]]:
            changed += 1
        out.append(body[cursor:g["start"]])
        out.append(new_group)
        cursor = g["end"]
    out.append(body[cursor:])
    new_body = "".join(out)

    # 参考文献章节同步重排（收窄候选池的正文侧联动）
    if refs_sec:
        rebuilt, refs_note = _rewrite_refs_section(refs_sec, mapping, cited, body=body)
        if rebuilt is not None:
            refs_sec = rebuilt
        notes.append(refs_note)
    new_text = new_body + (refs_sep + refs_sec if refs_sep else "")

    # 候选池差集 → 未引用编号列表（复用 toolbox used-refs 的解析口径）
    uncited, pool_total = None, 0
    if proj:
        pool_total, why = _load_refs_pool(proj)
        if why:
            notes.append(why)
        elif pool_total:
            uncited = [i for i in range(1, pool_total + 1) if i not in cited]
            over = sorted(n for n in cited if n > pool_total)
            if over:
                notes.append(f"正文引用编号超出候选池条目数({pool_total}): {over}")
    if not order:
        notes.append("正文未发现任何引用组")

    result = {"ok": True, "mapping": mapping, "text": new_text, "changed": changed,
              "cited": order, "uncited": uncited, "pool_total": pool_total,
              "notes": notes, "written": ""}

    if apply and proj and main_md is not None and new_text != text:
        bak = main_md.with_name(f"main.md.bak-{datetime.date.today():%Y%m%d}-qc-refs")
        if not bak.exists():
            bak.write_text(text, encoding="utf-8")
        main_md.write_text(new_text, encoding="utf-8")
        result["written"] = str(main_md)
        notes.append(f"已写回 {main_md}（旧版备份: {bak.name}）")
        # 台账留痕: 追加 {"tool": "integration_qc", ...}（格式同 runner._ledger_append）;
        # "integration_qc" 已入 workbench_mcp._POST_TOOLS, process_audit 不误报 bypassed_toolchain
        try:
            sys.path.insert(0, str(ENGINE))
            import runner
            runner._ledger_append({"tool": "integration_qc", "action": "apply-refs",
                                   "proj": str(proj), "written": str(main_md),
                                   "changed": changed, "mapping_size": len(mapping)})
        except Exception:
            pass
    return result


# ─────────────────────────── ② 术语归一 ───────────────────────────

def _parse_glossary(glossary_md):
    """解析契约术语表（| 术语 | 规约 | 两列表格）→ [(术语, 规约)]。跳过占位与空行。"""
    rows = []
    for cells in _parse_table_rows(glossary_md or ""):
        if len(cells) < 2:
            continue
        term, spec = cells[0].strip().strip("`*"), cells[1].strip()
        if not term or not spec or "【填写】" in term or "【填写】" in spec:
            continue
        rows.append((term, spec))
    return rows


def _sub_outside_guard(text, pattern, term, fence_spans, refs_begin):
    """只在「非代码围栏 且 参考文献章节之前」区间做替换（与变体计数口径一致）。

    等于 term 本身的匹配原样跳过；围栏内/参考文献章节内的匹配不动。
    """
    out, cursor = [], 0
    for m in pattern.finditer(text):
        if _in_spans(fence_spans, m.start()) or m.start() >= refs_begin:
            continue
        if m.group(0) == term:
            continue
        out.append(text[cursor:m.start()])
        out.append(term)
        cursor = m.end()
    out.append(text[cursor:])
    return "".join(out)


def qc_terminology(contract_glossary, md_text, apply=False):
    """术语一致性检查：契约术语表是唯一权威。

    默认只报告不改写；apply=True 时只做「保守替换」——仅把术语的内部空白
    变体（如多余空格）归一为规约拼写，不改动任何语义性内容，并返回差异。

    返回: {ok, rows: [{term, spec, count, lines, suspect, whitespace_variants}],
           applied, diffs, notes, text(仅 apply 时返回改写后文本)}
    """
    notes = []
    terms = _parse_glossary(contract_glossary)
    if not terms:
        return {"ok": True, "rows": [], "applied": 0, "diffs": [],
                "notes": ["术语表为空或无可解析行（占位未填）"], "text": None}

    rows, diffs, applied = [], [], 0
    for term, spec in terms:
        # apply 可能改写文本（空白变体归一）, 每个术语循环开头刷新偏移/围栏区间,
        # 保证命中计数与替换范围口径一致（此前 spans 只算一次, 替换却全文生效）
        starts = _line_starts(md_text)
        fence_spans = _code_fence_spans(md_text)
        refs_m = _REFS_SPLIT.search(md_text)
        refs_begin = refs_m.start() if refs_m else len(md_text)
        guard_l, guard_r = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
        exact = re.compile(guard_l + re.escape(term) + guard_r)
        hits, suspect = [], []
        for m in exact.finditer(md_text):
            ln = _line_no(starts, m.start())
            hits.append(ln)
            reason = ("代码块内" if _in_spans(fence_spans, m.start())
                      else ("参考文献章节内" if m.start() >= refs_begin else ""))
            if reason:
                suspect.append({"line": ln, "reason": reason})
        # 空白变体（词元相同但空白不同）——这是 apply 唯一允许改写的对象
        variant_re = None
        if re.search(r"\s", term) and len(term.split()) > 1:
            variant_re = re.compile(
                guard_l + r"\s+".join(re.escape(p) for p in term.split()) + guard_r)
        vcount = 0
        if variant_re:
            for m in variant_re.finditer(md_text):
                frag = m.group(0)
                if frag != term and not _in_spans(fence_spans, m.start()) and m.start() < refs_begin:
                    vcount += 1
                    if apply:
                        diffs.append({"line": _line_no(starts, m.start()),
                                      "before": frag, "after": term})
        if apply and variant_re and vcount:
            # 只在非围栏/参考文献章节之前的区间替换（与上方计数口径一致）
            md_text = _sub_outside_guard(md_text, variant_re, term, fence_spans, refs_begin)
            applied += vcount
        rows.append({"term": term, "spec": spec, "count": len(hits),
                     "lines": hits[:50], "suspect": suspect,
                     "whitespace_variants": vcount})
    if not any(r["count"] for r in rows):
        notes.append("正文中未发现任何术语表词条——请人工确认术语是否改用别名")
    return {"ok": True, "rows": rows, "applied": applied, "diffs": diffs,
            "notes": notes, "text": md_text if apply else None}


# ─────────────────────────── ③ 段间衔接与逻辑清单 ───────────────────────────

_CONNECTIVES = ("however", "therefore", "thus", "in addition", "furthermore", "moreover",
                "consequently", "nevertheless", "specifically", "finally", "first", "second",
                "为此", "因此", "此外", "进一步", "总之", "但是", "然而", "同时")


def _paragraphs_with_lines(text):
    """把正文切成段落并记录起始行号（跳过标题/表格/图片/代码块；标题视为段落屏障）。"""
    paras, cur, cur_line, in_fence = [], [], None, False
    barrier_after = [False]  # 上一段之后是否隔着小节标题

    def _flush():
        nonlocal cur, cur_line
        if cur:
            paras.append({"text": "\n".join(cur), "start_line": cur_line,
                          "_barrier": barrier_after[0]})
            barrier_after[0] = False
        cur, cur_line = [], None

    for idx, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            _flush()
            continue
        if in_fence or not s:
            _flush()
            continue
        if s.startswith(("#", "|", "!")):
            _flush()
            if s.startswith("#"):
                barrier_after[0] = True
            continue
        if cur_line is None:
            cur_line = idx
        cur.append(s)
    _flush()
    return paras


def _weak_transition(prev_text, next_text):
    """与 staged_gen._assemble_logic_check 同口径的衔接启发式（单对判断）。"""
    first = re.split(r"(?<=[.!?。！？])\s", next_text)[0].lower()
    if not first:
        return False
    prev_words = set(re.findall(r"[a-z]{5,}", prev_text.lower()))
    if any(c in first for c in _CONNECTIVES):
        return False
    return not (prev_words & set(re.findall(r"[a-z]{5,}", first)))


def qc_transitions(md_text):
    """整合逻辑质检：包装 staged_gen._assemble_logic_check 的输出为结构化清单。

    返回: {ok, P0:[矛盾], P1:[过度宣称], P2:{count, gaps:[定点位置]},
           upstream_available, notes}
    P2.gaps 每项给出相邻两段的行号与开头摘录，供主控做定点修补（不整段重写）。
    """
    P0, P1, notes = [], [], []
    upstream = False
    try:
        sys.path.insert(0, str(ENGINE))
        import staged_gen
        upstream = True
        for w in staged_gen._assemble_logic_check(md_text):
            sev = w.get("severity", "")
            item = {"type": w.get("type", ""), "msg": w.get("msg", "")}
            if sev == "P0" or item["type"] == "cross_section_contradiction":
                P0.append(item)
            elif sev == "P1" or item["type"] == "overstatement":
                P1.append(item)
            # P2 由下方定点扫描给出更细的位置信息，这里不重复收录
    except Exception as exc:
        notes.append(f"staged_gen._assemble_logic_check 不可用({exc})，仅做定点衔接扫描")

    # 定点衔接扫描：为每个弱过渡标注相邻段位置
    body = _REFS_SPLIT.split(md_text, maxsplit=1)[0]
    paras = _paragraphs_with_lines(body)
    gaps = []
    for i in range(1, len(paras)):
        prev_p, next_p = paras[i - 1], paras[i]
        if next_p.get("_barrier"):
            continue  # 隔着小节标题的段落对不算段内衔接缺口
        if _weak_transition(prev_p["text"], next_p["text"]):
            gaps.append({
                "prev_para_line": prev_p["start_line"],
                "next_para_line": next_p["start_line"],
                "prev_head": prev_p["text"][:60].replace("\n", " "),
                "next_head": next_p["text"][:60].replace("\n", " "),
                "hint": "在下一段首句补衔接词或复现上段关键词（定点修补，勿整段重写）",
            })
    return {"ok": True, "P0": P0, "P1": P1,
            "P2": {"count": len(gaps), "gaps": gaps},
            "upstream_available": upstream, "notes": notes}


# ─────────────────────────── ④ 统一入口与 CLI ───────────────────────────

def _extract_glossary_section(contract_text):
    """从契约全文提取「术语表」小节原文（本地正则，避免耦合 staged_gen 的解析变动）。"""
    m = re.search(r"##\s*(?:\d+[.．、]\s*)?术语表\s*\n([\s\S]*?)(?=\n##\s|\Z)", contract_text)
    if m:
        return m.group(1).strip()
    try:  # 兜底：用 staged_gen.parse_contract
        sys.path.insert(0, str(ENGINE))
        import staged_gen
        return staged_gen.parse_contract(contract_text).get("glossary", "")
    except Exception:
        return ""


def run_qc(proj, apply_refs=False):
    """整合质检统一入口：读取拼装稿与契约，跑三项机械质检，产出汇总 report。

    返回: {ok, report(dict, JSON 可序列化), summary(人类可读摘要), json_path}
    report 会写入 manuscript/qc_report.json，供 smoke_test 与主控调用。
    """
    proj = Path(proj)
    main_md = proj / "manuscript" / "main.md"
    if not main_md.exists():
        return {"ok": False, "error": f"缺少 {main_md}", "report": {}, "summary": "", "json_path": ""}
    md_text = main_md.read_text(encoding="utf-8", errors="replace")

    glossary = ""
    contract_p = proj / "draft" / "contract.md"
    if contract_p.exists():
        glossary = _extract_glossary_section(contract_p.read_text(encoding="utf-8", errors="replace"))

    refs_report = qc_references(proj=proj, md_text=md_text, apply=apply_refs)
    term_report = qc_terminology(glossary, refs_report.get("text") or md_text, apply=False)
    trans_report = qc_transitions(md_text)

    report = {
        "project": str(proj),
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "references": {k: v for k, v in refs_report.items() if k != "text"},
        "terminology": term_report,
        "transitions": trans_report,
        "summary": {
            "refs_changed": refs_report.get("changed", 0),
            "refs_mapping_size": len(refs_report.get("mapping", {})),
            "refs_uncited": refs_report.get("uncited") or [],
            "term_rows": len(term_report.get("rows", [])),
            "term_suspects": sum(len(r.get("suspect", [])) for r in term_report.get("rows", [])),
            "logic_P0": len(trans_report.get("P0", [])),
            "logic_P1": len(trans_report.get("P1", [])),
            "logic_P2_gaps": trans_report.get("P2", {}).get("count", 0),
        },
    }

    # 人类可读摘要（供主控阅读后裁决；P0/P1 是否处理由主控决定）
    s = report["summary"]
    lines = [
        "══ 整合质检摘要 ══",
        f"引用重编号: {s['refs_mapping_size']} 个编号建立映射，{s['refs_changed']} 处引用组被改写"
        + (f"；未引用候选池条目: {s['refs_uncited']}" if s["refs_uncited"] else ""),
    ]
    for n in refs_report.get("notes", []):
        lines.append(f"  · {n}")
    lines.append(f"术语检查: {s['term_rows']} 条术语，{s['term_suspects']} 处可疑位置")
    for r in term_report.get("rows", []):
        flag = "（有可疑位置）" if r.get("suspect") else ""
        lines.append(f"  · {r['term']} ×{r['count']}{flag}")
    lines.append(f"逻辑清单: P0 矛盾 {s['logic_P0']} | P1 过度宣称 {s['logic_P1']} | P2 衔接缺口 {s['logic_P2_gaps']}")
    for g in trans_report.get("P2", {}).get("gaps", [])[:5]:
        lines.append(f"  · L{g['prev_para_line']}→L{g['next_para_line']}: 「{g['next_head']}…」缺衔接")
    summary = "\n".join(lines)

    json_path = ""
    try:
        out = main_md.parent / "qc_report.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        json_path = str(out)
    except Exception as exc:
        report.setdefault("notes", []).append(f"qc_report.json 写入失败: {exc}")

    return {"ok": True, "report": report, "summary": summary, "json_path": json_path}


def main(argv=None):
    ap = argparse.ArgumentParser(description="整合质检（机械性、确定性，不调用 LLM）")
    ap.add_argument("proj", help="项目目录（含 manuscript/main.md）")
    ap.add_argument("--apply-refs", action="store_true",
                    help="把引用重编号结果写回 main.md（默认只报告不写盘）")
    ap.add_argument("--json", action="store_true", help="额外打印完整 JSON report")
    args = ap.parse_args(argv)

    res = run_qc(args.proj, apply_refs=args.apply_refs)
    if not res.get("ok"):
        print(f"[integration_qc] 失败: {res.get('error')}", file=sys.stderr)
        return 1
    print(res["summary"])
    if res.get("json_path"):
        print(f"\nreport JSON: {res['json_path']}")
    if args.json:
        print(json.dumps(res["report"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
