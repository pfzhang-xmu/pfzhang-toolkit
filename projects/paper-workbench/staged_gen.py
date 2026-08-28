# -*- coding: utf-8 -*-
"""staged_gen.py — 分批生成管线（第一期骨架，2026-08-22）

设计见《分批生成方案》：契约先行 + 分段生成 + 段间门禁。

目录约定（项目内）：
    draft/contract.md          契约（范围/大纲/图表契约/引文分配/术语表）
    draft/gen_state.json       段级状态（done/pending/failed + 历史）
    draft/gen-log.md           迭代日志
    draft/sections/NN-slug.md  各段产物
    manuscript/main.md         拼装结果（gen-assemble）

CLI（经 wb.py generate 子命令暴露）：
    init      初始化 draft/ 结构与状态
    contract  生成契约（--ai 走 AI；默认模板填空）；已有契约时不覆盖
    section   生成一段（--sid；--dry-run 只出 prompt；--accept 跳过门禁直收）
    status    段级状态摘要
    assemble  拼装已接受段落 → manuscript/main.md（先备份）
"""
from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
CONTRACT_TEMPLATE = ENGINE / "templates" / "draft-contract.md"
PLACEHOLDER = "【填写】"


# ─────────────────────────── 路径与状态 ───────────────────────────

def draft_dir(proj):
    return Path(proj) / "draft"


def _paths(proj):
    d = draft_dir(proj)
    return {
        "draft": d,
        "contract": d / "contract.md",
        "state": d / "gen_state.json",
        "log": d / "gen-log.md",
        "sections": d / "sections",
    }


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def load_gen_state(proj):
    p = _paths(proj)["state"]
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_gen_state(proj, st):
    st["updated"] = _now()
    p = _paths(proj)["state"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def append_log(proj, what):
    p = _paths(proj)["log"]
    p.parent.mkdir(parents=True, exist_ok=True)
    line = f"- {_now()} | {what}\n"
    if p.exists():
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
    else:
        p.write_text("# 分批生成迭代日志（gen-log）\n\n" + line, encoding="utf-8")


# ─────────────────────────── init / contract ───────────────────────────

def gen_init(proj, force=False):
    ps = _paths(proj)
    existed = ps["state"].exists()
    if existed and not force:
        return {"ok": False, "msg": f"已初始化过（{ps['state']}），--force 重置"}
    ps["sections"].mkdir(parents=True, exist_ok=True)
    st = {"created": _now(), "updated": _now(), "contract_locked": False, "sections": {}}
    save_gen_state(proj, st)
    append_log(proj, "gen-init")
    return {"ok": True, "msg": f"已初始化 {ps['draft']}"}


def gen_contract(proj, use_ai=False, project_state=None):
    """生成 draft/contract.md。默认模板预填项目信息；--ai 委托 AI 起草再落盘。"""
    ps = _paths(proj)
    if ps["contract"].exists():
        return {"ok": False, "msg": f"契约已存在: {ps['contract']}（直接编辑它；删除后可重建）"}
    ps["draft"].mkdir(parents=True, exist_ok=True)
    tpl = CONTRACT_TEMPLATE.read_text(encoding="utf-8") if CONTRACT_TEMPLATE.exists() else "# 生成契约（模板缺失）\n"
    topic = (project_state or {}).get("topic", "")
    journal = (project_state or {}).get("journal", "")
    lang = (project_state or {}).get("lang", "en")
    tpl = tpl.replace("【项目名称】", topic or PLACEHOLDER)
    tpl = tpl.replace("【目标期刊】", journal or PLACEHOLDER)
    tpl = tpl.replace("【写作语言】", "英文" if lang == "en" else ("中文" if lang == "zh" else lang))
    # 有文献库时，预填引文分配表骨架
    refs = _load_ref_keys(proj)
    if refs:
        rows = "\n".join(f"| {r} | {PLACEHOLDER} | {PLACEHOLDER} |" for r in refs)
        tpl = tpl.replace("【引文分配行：| ref编号 | 分配章节 | 支撑要点 |】", rows)
    if use_ai:
        text = _ai_draft_contract(proj, tpl, project_state)
        if text:
            tpl = text
    ps["contract"].write_text(tpl, encoding="utf-8")
    append_log(proj, f"gen-contract ({'ai' if use_ai else 'template'}; refs={len(refs)})")
    return {"ok": True, "msg": f"契约已生成: {ps['contract']}（请人工补全并锁定——S0 检查点）"}


def _load_ref_keys(proj):
    """从 framework/references.md 提取文献键（ref1..refN）。"""
    try:
        import toolbox
        ref_file = Path(proj) / "framework" / "references.md"
        if not ref_file.exists():
            return []
        text = ref_file.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"```bibtex\s*(.*?)```", text, re.S)
        entries = toolbox.parse_bibtex(m.group(1) if m else text)
        return [e.get("id") for e in entries if e.get("id")]
    except Exception:
        return []


def _ai_draft_contract(proj, tpl, project_state):
    """委托 AI 基于模板与项目已有产物起草契约；任何失败返回 None（回退模板）。"""
    try:
        sys.path.insert(0, str(ENGINE / "web"))
        import ai_client
        context_parts = [f"## 契约模板（按此结构填充，保留全部章节标题）\n{tpl[:6000]}"]
        for rel in ("framework/outline.md", "framework/figures.md", "journal/chosen.md"):
            p = Path(proj) / rel
            if p.exists():
                context_parts.append(f"## 项目已有产物: {rel}\n{p.read_text(encoding='utf-8', errors='replace')[:3000]}")
        prompt = (
            "你是论文写作规划助手。请基于契约模板与项目已有产物，把契约中的【填写】占位全部做实：\n"
            "- 章节大纲：从 outline 提取，给出每节字数预算（总和符合期刊篇幅）\n"
            "- 图表契约：从 figures/outline 提取，给出编号/标题/插入章节/数据来源；表格标注生成方式=程序化渲染，图片标注类型(示意图/数据图)\n"
            "- 引文分配：把每条文献分配到支撑的章节（一条可分配多节，用分号分隔）\n"
            "- 术语表：从正文/大纲中提取物种与关键术语的命名规约\n"
            "只输出完整契约 Markdown，不要解释。\n\n" + "\n\n".join(context_parts)
        )
        resp = ai_client.chat([{"role": "user", "content": prompt}])
        text = (resp or "").strip() if isinstance(resp, str) else ""
        return text if len(text) > 400 else None
    except Exception:
        return None


# ─────────────────────────── 契约解析 ───────────────────────────

def parse_contract(text):
    """解析契约 → {scope, sections, figures, citations, glossary, locked,
    dependency_graph, lang, count_unit, version, journal_profile}

    dependency_graph: sid → 前驱 sid 列表（「依赖」列填了用所填值；未填默认
    线性链 [上一个 sid]，首节为空列表）——与现行 _prev_summary 线性语义一致。
    """
    def _section(name):
        m = re.search(r"##\s*" + name + r"([\s\S]*?)(?=\n##\s|\Z)", text)
        return m.group(1).strip() if m else ""

    out = {"scope": _section("1\\. 范围声明"),
           "figures": _parse_table_rows(_section("3\\. 图表契约")),
           "citations": {},
           "glossary": _section("5\\. 术语表"),
           "locked": ("契约状态：已锁定" in text)}
    # 头部字段（旧契约无这些字段时全部走默认值，保持向后兼容）
    m = re.search(r"语言[:：]\s*(中文|英文)", text)
    out["lang"] = ("zh" if m.group(1) == "中文" else "en") if m else "en"
    m = re.search(r"计数单位[:：]\s*(words|chars)", text, re.I)
    out["count_unit"] = m.group(1).lower() if m else "words"
    m = re.search(r"契约版本[:：]\s*([^\s|｜]+)", text)
    out["version"] = m.group(1).strip() if m else ""
    m = re.search(r"期刊要求[:：]\s*([^|\n]*)", text)
    jp = m.group(1).strip() if m else ""
    out["journal_profile"] = "" if PLACEHOLDER in jp else jp
    # 章节大纲：表格行 | id | 标题 | 字数 | 要点 |（可选列）依赖 | 衔接锚点 | 执行者 |
    # 旧格式（4/6 列，无执行者列）按列索引取，超出列缺失时补空——解析结果与改前一致；
    # 执行者列（最末列）留空=默认执行者（容错路由兜底 dsh）
    secs = []
    for row in _parse_table_rows(_section("2\\. 章节大纲")):
        if len(row) >= 3 and row[0].strip():
            budget = re.search(r"(\d+)", row[2])
            secs.append({"sid": row[0].strip(), "title": row[1].strip(),
                         "budget": int(budget.group(1)) if budget else 0,
                         "points": row[3].strip() if len(row) > 3 else "",
                         "deps": row[4].strip() if len(row) > 4 else "",
                         "anchor": row[5].strip() if len(row) > 5 else "",
                         "executor": row[6].strip() if len(row) > 6 else ""})
    out["sections"] = secs
    # 依赖图：填了「依赖」列用所填值（分号分隔多个 sid）；留空默认线性前驱
    graph = {}
    prev_sid = None
    for s in secs:
        if s["deps"]:
            graph[s["sid"]] = [d.strip() for d in re.split(r"[;；]", s["deps"]) if d.strip()]
        else:
            graph[s["sid"]] = [prev_sid] if prev_sid else []
        prev_sid = s["sid"]
    out["dependency_graph"] = graph
    # 引文分配：| refX | 章节(可多个,分号) | 要点 |
    for row in _parse_table_rows(_section("4\\. 引文分配")):
        if len(row) >= 2 and row[0].strip():
            key = row[0].strip().strip("`")
            assigned = [s.strip() for s in re.split(r"[;；]", row[1]) if s.strip()]
            out["citations"][key] = assigned
    return out


def _parse_table_rows(md):
    """提取 Markdown 表格数据行（跳过表头与分隔行）。"""
    rows = []
    for ln in md.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # 分隔行
        rows.append(cells)
    if rows and all(re.fullmatch(r":?-{2,}:?", c) for c in rows[0] if c):
        rows = rows[1:]
    # 第一行视为表头
    return rows[1:] if len(rows) > 1 else []


def scan_contract_placeholders(text):
    """轻量扫描契约中【填写】占位符 → {"count": 出现次数, "sections": 所在章节列表}。

    只告警不硬拦截（锁定是人工检查点，保留人工语义）。
    """
    count = text.count(PLACEHOLDER)
    sections = []
    cur = ""
    for ln in text.splitlines():
        m = re.match(r"^#{2,3}\s*(.+?)\s*$", ln.strip())
        if m:
            cur = m.group(1).strip()
        if PLACEHOLDER in ln and cur and cur not in sections:
            sections.append(cur)
    return {"count": count, "sections": sections}


def normalize_section_budget(sec):
    """预算回退共享函数：契约未设字数预算（0/缺失）时默认按 800 词参与任务卡与门禁，
    返回 (章节副本, 注记)；有预算时原样返回。
    gen_section / subagent_writer / parallel_gen 三处统一调用，保证任务卡与门禁口径一致（不改写契约文件）。"""
    if sec.get("budget"):
        return sec, ""
    out = dict(sec)
    out["budget"] = 800
    return out, "（契约未设置字数预算，默认按 800 词）"


def contract_placeholder_warnings(contract_raw, locked):
    """占位符扫描共享前置检查：契约已锁定且残留【填写】时输出告警（只告警不硬拦截）。
    gen_section / subagent_writer / parallel_gen 三处共用同一口径。"""
    if not locked or not contract_raw:
        return []
    ph = scan_contract_placeholders(contract_raw)
    if ph["count"] <= 0:
        return []
    return [{"severity": "P1", "type": "contract_placeholder",
             "msg": f"契约仍有 {ph['count']} 处【填写】占位符（所在章节: {'、'.join(ph['sections'])}）——建议补全契约后再生成"}]


# ─────────────────────────── 段级 prompt ───────────────────────────

# ─────────────────────────── 技能方法论内联（断点①） ───────────────────────────

_METHODOLOGY = {
    "results": (
        "本节为结果/数据段，遵循 scientific-writing 论证纪律:\n"
        "  1. 每个结果先陈述可观察的数据或效应（含重复数、效应量或变异度），再给一句最小解释;\n"
        "  2. 因果性断言必须基于对照或统计检验，否则用 suggests / is associated with;\n"
        "  3. 不把相关性当因果，不把单条件结果外推到其他条件;\n"
        "  4. 图表先行引用，正文只提炼关键读数，不重复图内所有数字。"
    ),
    "discussion": (
        "本节为讨论段，遵循 simulate-reviewers 反对意见预判纪律:\n"
        "  1. 四段式推进: 本研究发现 → 与文献异同(带引用对比) → 局限与替代解释 → 可检验展望;\n"
        "  2. 主动预判审稿人可能的反对意见并在文本中回应，而不是藏起来;\n"
        "  3. 结论强度与证据强度匹配，禁止过度推广或暗示超出数据范围的意义;\n"
        "  4. 每段一个论点，避免罗列式堆砌。"
    ),
    "methods": (
        "本节为方法段，遵循可复现性纪律(stats-reporting-audit):\n"
        "  1. 材料/菌株/试剂/培养条件/统计方法逐一写清，变量与缩写全篇一致;\n"
        "  2. 统计检验说明具体方法(如单因素 ANOVA)与显著性水平(如 p<0.05);\n"
        "  3. 无实验数据的综述性方法段，明确说明检索/纳排标准与局限。"
    ),
    "intro": (
        "本节为引言段，遵循漏斗式结构(scientific-writing):\n"
        "  1. 大背景(1-2 句) → 领域进展 → 未解问题/缺口 → 本研究问题与路线 → 预告章节;\n"
        "  2. 每个事实性陈述后紧跟对应引用，禁止空挂引用;\n"
        "  3. 结尾明确本研究贡献边界，不夸大。"
    ),
    "general": (
        "本节写作纪律(evidence-claim 分级):\n"
        "  1. 每个事实性陈述带引用编号;\n"
        "  2. 段落主题句先行，证据支撑在后，段落间有过渡;\n"
        "  3. 结论强度与证据强度匹配——直接证据、近缘物种先例、作者推断不得混作等价。"
    ),
}


def _methodology_for(section):
    """按章节标题推断方法论类型并返回内联条款。"""
    title = (section.get("title") or "").lower()
    sid = (section.get("sid") or "").lower()
    if any(w in title or w in sid for w in ("result", "results", "数据", "结果")):
        return _METHODOLOGY["results"]
    if any(w in title or w in sid for w in ("discussion", "讨论")):
        return _METHODOLOGY["discussion"]
    if any(w in title or w in sid for w in ("method", "methods", "材料", "方法")):
        return _METHODOLOGY["methods"]
    if any(w in title or w in sid for w in ("introduction", "intro", "引言")):
        return _METHODOLOGY["intro"]
    return _METHODOLOGY["general"]


def build_section_prompt(contract, section, prev_summary, assigned_refs_detail, lang="en", extra_constraints=""):
    """段级 prompt 构建器。契约 + 前文摘要 + 分配文献 + 段级约束。"""
    lang_note = "请用英文写作（英式拼写）。" if lang == "en" else ("请用中文写作。" if lang == "zh" else "")
    lines = [
        "你正在分段撰写一篇学术论文的一个章节。严格遵守以下契约与约束，只输出本章节正文（含必要的 ### 小节标题），不要输出其他章节、摘要或解释。",
        "",
        f"## 目标章节: {section['sid']} {section['title']}",
        f"字数预算: 约 {section['budget']} 词（±20%）",
        f"本节要点: {section.get('points') or '（见契约大纲）'}",
        "",
        "## 范围声明（契约，不得超出）",
        contract.get("scope") or "（契约未填）",
        "",
        "## 图表契约（只可按下表引用，编号不得改动）",
    ]
    for f in contract.get("figures", []):
        lines.append("- " + " | ".join(f[:5]))
    lines += ["", "## 术语规约"]
    lines.append(contract.get("glossary") or "（无）")
    lines += ["", "## 前文摘要（仅用于衔接，不得重复其内容）"]
    lines.append(prev_summary or "（本节为开篇章节）")
    lines += [
        "",
        "## 本节可用文献（只允许引用此池内编号；每个事实性陈述必须带引用编号）",
    ]
    if assigned_refs_detail:
        lines.extend(assigned_refs_detail)
    else:
        lines.append("（本节无分配文献——只写方法/过渡性内容，不得做文献支撑的事实声称）")
    lines += [
        "",
        "## 写作约束（严格）",
        f"1. {lang_note}学术语调：使用 hedging 语言（may, suggest, appear to），禁止绝对化表述（proves, guarantees, always）。",
        "2. 禁止 AI 套话：in recent years / plays a crucial role / it is worth noting that / delve / landscape / a testament to。",
        "3. 表格严禁手写：表格由系统按契约 JSON 渲染插入，正文只需写对表格的一句话引导并以 Table N 指代（手写表格排版是模型高频错误点）。",
        "4. 图片位置用独立一行 ![Figure N. 图注](figures/文件名) 按契约插入，不得把图堆到文末。",
        "5. 引用编号置于句末标点外，格式 (1, 2, 4–6)。",
        "6. 每段一个主题句 + 支撑证据 + 衔接；段长 ≤250 词。",
    ]
    if extra_constraints:
        lines.append("7. " + extra_constraints)
    # 断点①: 按章节类型内联 skill 方法论（不依赖 agent 自觉读 skill）
    m = _methodology_for(section)
    if m:
        lines += ["", "## 本节方法论（遵循对应学术技能协议，写作必须满足）", m]
    return "\n".join(lines)


def _prev_summary(proj, contract, current_sid):
    """取前一已完成段的产物尾部摘要（≤200 词）。"""
    sids = [s["sid"] for s in contract.get("sections", [])]
    if current_sid not in sids or sids.index(current_sid) == 0:
        return ""
    prev_sid = sids[sids.index(current_sid) - 1]
    st = load_gen_state(proj) or {}
    info = (st.get("sections") or {}).get(prev_sid, {})
    fname = info.get("file", "")
    if not fname:
        return ""
    f = _paths(proj)["sections"] / fname
    if not (f and f.exists() and f.is_file()):
        return ""
    text = f.read_text(encoding="utf-8", errors="replace")
    words = text.split()
    return " ".join(words[-200:])


def _assigned_refs_detail(proj, contract, sid):
    """分配给本段的文献明细行（编号 + 题名 + 年份）。"""
    try:
        import toolbox
        ref_file = Path(proj) / "framework" / "references.md"
        text = ref_file.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"```bibtex\s*(.*?)```", text, re.S)
        entries = {e.get("id"): e for e in toolbox.parse_bibtex(m.group(1) if m else text)}
    except Exception:
        entries = {}
    pool, seen = [], set()
    for key, sids in contract.get("citations", {}).items():
        if sid in sids and key not in seen:
            seen.add(key)
            e = entries.get(key, {})
            num = re.sub(r"\D", "", key) or key
            pool.append(f"- [{num}] {e.get('author', '?')[:50]} ({e.get('year', '?')}). {e.get('title', '?')[:100]}")
    return pool


# ─────────────────────────── 段级门禁 ───────────────────────────

def _extract_cited_numbers(text):
    nums = set()
    for m in re.finditer(r"[\[\(]([^\]\)\n]{1,120})[\]\)]", text):
        inner = m.group(1)
        if re.search(r"[A-Za-z\u4e00-\u9fff]", re.sub(r"(?:Figure|Table|Figs?\.?|Tables?|Supplementary|and|et\s+al)", " ", inner, flags=re.I)):
            continue  # 含文字 → 非纯引用组
        for tok in re.finditer(r"(\d{1,3})(?:\s*[–\-]{1,2}\s*(\d{1,3}))?", inner):
            a = int(tok.group(1))
            b = int(tok.group(2)) if tok.group(2) else a
            if 1 <= a <= 999 and b <= 999:
                nums.update(range(a, b + 1))
    return nums


def gate_section(proj, contract, section, text):
    """段级门禁 → (issues, passed)。issues 为 [{severity, type, msg}]。"""
    issues = []
    # 1) 占位符（沿用 toolbox mechanical_check，取其 P0/P1）
    try:
        import toolbox
        for it in toolbox.mechanical_check(text):
            if it.get("severity") in ("P0", "P1"):
                issues.append({"severity": it["severity"], "type": it.get("type"), "msg": it.get("msg")})
    except Exception:
        pass
    # 2) 引文范围：本段引用 ⊆ 分配池
    cited = _extract_cited_numbers(text)
    allowed = set()
    for key, sids in contract.get("citations", {}).items():
        if section["sid"] in sids:
            num = re.sub(r"\D", "", key)
            if num.isdigit():
                allowed.add(int(num))
    out_of_pool = sorted(cited - allowed) if allowed else sorted(cited)
    if out_of_pool:
        issues.append({"severity": "P1", "type": "citation_out_of_pool",
                       "msg": f"引用编号 {out_of_pool[:12]} 不在本节分配池（{len(allowed)} 条）内——幻觉引用嫌疑"})
    # 3) 字数预算 ±20%（给模型的预算已按下探 10%，故上限按 ×1.1 判，避免老 P2）
    wc = len(re.findall(r"[A-Za-z][A-Za-z'\-]*|\d+", text))
    budget = section.get("budget") or 0
    if budget and not (budget * 0.8 <= wc <= budget * 1.1):
        issues.append({"severity": "P2", "type": "word_budget",
                       "msg": f"字数 {wc} 超出预算 {budget} 的 +10%/-20%"})
    # 4) 引用密度（参考审稿规则 M1: reference dumping）——单段 >8 个引用组 = 密度过高
    cite_groups = len(re.findall(r"[\[\(](?:\d+(?:\s*[-,–—]\s*\d+)*\s*(?:;|,)?)(?:\s*\d+)*[\)\]]", text))
    if cite_groups > 8:
        issues.append({"severity": "P2", "type": "citation_density",
                       "msg": f"本段 {cite_groups} 个引用组（上限 8）——reference dumping 信号, 建议精简为支撑性引用"})
    # 5) 段落首句主题句：非列表非代码块段落, 首句应包含核心名词（标题关键词/物种名）
    topic_words = set()
    for w in re.split(r"[^A-Za-z]", (section.get("title") or "")):
        if len(w) >= 4:
            topic_words.add(w.lower())
    if topic_words:
        misses = 0
        for para in re.split(r"\n{2,}", text):
            para = para.strip()
            if not para or para.startswith(("#", "|", "!", "- ", "* ")):
                continue
            first_sent = re.split(r"(?<=[.!?])\s", para)[0].lower()
            if not any(tw in first_sent for tw in topic_words):
                misses += 1
        if misses >= 2:
            issues.append({"severity": "P2", "type": "topic_sentence",
                           "msg": f"有 {misses} 段首句未出现本节主题词({sorted(topic_words)[:3]}…)——段首应为主题句"})
    passed = not any(i["severity"] in ("P0", "P1") for i in issues)
    return issues, passed


# ─────────────────────────── section 主流程 ───────────────────────────

_MECH_TYPES = {"mechanical", "mechanical_long_sentence", "empty_citation", "latex_residue", "bom"}


def _l1_patch(proj, contract, section, text, issues):
    """L1 定点修补：只发违规句上下文, AI 返回修好的句子, 程序替换。

    返回 (patched_text, ok)。一次只修一个违规, 替换后由调用方全量重跑门禁。"""
    try:
        import ai_client
    except Exception:
        return text, False
    # 定位第一个可修补的局部违规（引文越界/套话/长句/缺 hedging——机械类走 L0）
    chosen = None
    for it in issues:
        t = it.get("type", "")
        if t in _MECH_TYPES:
            continue
        if t in ("citation_out_of_pool",):
            chosen = ("citation", "上一段引用了不在本节分配池的文献编号。请只保留/替换为以下可用编号中的合理项:\n" +
                      "可用编号: " + ", ".join(sorted(str(x) for x in _allowed_citations(contract, section))))
            break
        if t in ("topic_sentence", "citation_density", "word_budget"):
            chosen = ("sentence", f"问题: {it.get('msg', '')}. 请仅返回修正后的整段文本（保留正确部分）。")
            break
    if not chosen:
        return text, False
    # 违规句上下文：取违规所在段落
    para = _locate_issue_para(text, issues[0])
    patch_prompt = (
        "你正在修补一段学术文本的局部问题。只输出修正后的完整段落，不要解释。\n\n"
        "## 问题\n" + chosen[1] + "\n\n"
        "## 当前段落\n" + (para or text[:1200])
    )
    try:
        resp = ai_client.chat([{"role": "user", "content": patch_prompt}])
    except Exception:
        return text, False
    patched = _strip_code_fence(str(resp or "")).strip()
    if not patched or len(patched) < 50:
        return text, False
    if para and para in text:
        return text.replace(para, patched, 1), True
    return text, False


def _allowed_citations(contract, section):
    out = set()
    for key, sids in contract.get("citations", {}).items():
        if section["sid"] in sids:
            num = re.sub(r"\D", "", key)
            if num.isdigit():
                out.add(int(num))
    return out


def _locate_issue_para(text, issue):
    """返回首个含引用编号/超长句的段落（用于 L1 上下文切取）。"""
    for para in re.split(r"\n{2,}", text):
        if len(para) > 60:
            return para
    return ""


# ─────────────────────────── section 主流程 ───────────────────────────

def _strip_code_fence(t):
    t = t.strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```$", t)
    return m.group(1) if m else t


def gen_section(proj, sid, dry_run=False, accept=False, retry=1, accept_reason=None):
    st = load_gen_state(proj)
    if st is None:
        return {"ok": False, "msg": "未初始化：先运行 generate init"}
    cp = _paths(proj)["contract"]
    if not cp.exists():
        return {"ok": False, "msg": "缺少契约：先运行 generate contract 并人工锁定"}
    contract_raw = cp.read_text(encoding="utf-8")
    contract = parse_contract(contract_raw)
    sec = next((s for s in contract["sections"] if s["sid"] == sid), None)
    if sec is None:
        return {"ok": False, "msg": f"契约章节大纲中无 {sid}；现有: {[s['sid'] for s in contract['sections']]}"}
    if not contract["locked"] and not (dry_run or accept):
        return {"ok": False, "msg": "契约未锁定（把契约中『契约状态：待锁定』改为『已锁定』再继续）——S0 人工检查点不可跳过"}

    # P1 预算回退：共享函数（契约未设预算时默认 800 词, 不改写契约文件）
    sec, budget_note = normalize_section_budget(sec)
    # P0 语言传导：把契约头部解析出的 lang 传给 prompt 构建（此前漏传恒为 en）
    prompt = build_section_prompt(
        contract, sec, _prev_summary(proj, contract, sid),
        _assigned_refs_detail(proj, contract, sid),
        lang=contract.get("lang", "en"))
    if budget_note:
        prompt = prompt.replace(f"字数预算: 约 {sec['budget']} 词",
                                f"字数预算: 约 {sec['budget']} 词 {budget_note}", 1)
    if dry_run:
        return {"ok": True, "msg": "dry-run prompt", "prompt": prompt}

    # P1 占位符扫描：共享前置检查（正式生成且契约已锁定时, 残留【填写】只告警不硬拦截）
    warnings = contract_placeholder_warnings(contract_raw, contract.get("locked"))

    sys.path.insert(0, str(ENGINE / "web"))
    try:
        import ai_client
    except Exception as e:
        return {"ok": False, "msg": f"ai_client 不可用: {e}"}

    attempts = 0
    issues, text = [], ""
    l1_budget = 2  # L1 定点修补上限, 超则升级 L2
    while attempts <= retry + l1_budget:
        if not text:
            # 初次生成 或 L2 整段重写
            resp = ai_client.chat([{"role": "user", "content": prompt}])
            text = _strip_code_fence(str(resp or ""))
        if len(text.strip()) < 200:
            issues = [{"severity": "P0", "type": "empty_output", "msg": "模型输出为空/过短"}]
            attempts += 1
            if attempts > retry:
                break
            continue
        issues, passed = gate_section(proj, contract, sec, text)
        if passed or accept:
            break
        # ── 分级处理 ──
        p0p1 = [i for i in issues if i["severity"] in ("P0", "P1")]
        mech_p0p1 = [i for i in p0p1 if i.get("type") in _MECH_TYPES]
        if mech_p0p1 and attempts == 0:
            # L0: 机械类违规 → 本地规则修复, 0 token
            try:
                import toolbox
                fixed = toolbox.mechanical_fix(text)
                if fixed and fixed != text:
                    text = fixed
                    issues, passed = gate_section(proj, contract, sec, text)
                    if passed:
                        break
            except Exception:
                pass
            attempts += 1
            if attempts > retry:
                break
            continue
        # L1: 局部违规 → 定点修补 (~1/10 token)
        if attempts <= l1_budget:
            patched, ok_patch = _l1_patch(proj, contract, sec, text, issues)
            if ok_patch and patched != text:
                text = patched
                attempts += 1
                continue
        # L2: 结构性/升级 → 整段重写, prompt 恒定（失败清单作独立修正指令, 不累积）
        attempts += 1
        if attempts <= retry + l1_budget:
            prompt = re.sub(r"## 上一轮未通过门禁.*$", "", prompt, flags=re.S).rstrip()
            prompt += ("\n\n## 上一轮未通过门禁，请修正后重写本节（保留正确部分）：\n- "
                       + "\n- ".join(i["msg"] for i in p0p1))

    accepted = accept or not any(i["severity"] in ("P0", "P1") for i in issues)
    fname = f"{sid}-{re.sub(r'[^A-Za-z0-9]+', '-', sec['title']).strip('-')[:40].lower()}.md"
    if accepted:
        f = _paths(proj)["sections"] / fname
        f.write_text(text, encoding="utf-8")
    entry = {
        "status": "done" if accepted else "failed",
        "file": fname if accepted else "",
        "attempts": attempts + 1,
        "issues": issues,
        "ts": _now(),
    }
    if accepted and accept_reason:
        entry["bypassed"] = True
        entry["reason"] = accept_reason
    st.setdefault("sections", {})[sid] = entry
    save_gen_state(proj, st)
    append_log(proj, f"gen-section {sid}: {'accepted' if accepted else 'FAILED'} (尝试 {attempts + 1} 次; 问题 {len(issues)})")
    result = {"ok": accepted, "msg": f"{sid} {'已接受 → ' + fname if accepted else '未通过门禁'}",
              "issues": issues, "file": fname if accepted else ""}
    if warnings:
        result["warnings"] = warnings
    return result


# ─────────────────────────── status / assemble ───────────────────────────

def gen_status(proj):
    st = load_gen_state(proj)
    if st is None:
        return {"ok": False, "msg": "未初始化"}
    cp = _paths(proj)["contract"]
    contract_raw = cp.read_text(encoding="utf-8") if cp.exists() else ""
    contract = parse_contract(contract_raw) if contract_raw else {"sections": [], "locked": False}
    rows = []
    for s in contract.get("sections", []):
        info = (st.get("sections") or {}).get(s["sid"], {})
        rows.append({"sid": s["sid"], "title": s["title"],
                     "status": info.get("status", "pending"),
                     "attempts": info.get("attempts", 0),
                     "file": info.get("file", "")})
    out = {"ok": True, "locked": contract.get("locked", False), "rows": rows}
    if contract_raw:
        # 附契约残留占位计数（与生成前置检查同一口径）
        out["placeholders"] = scan_contract_placeholders(contract_raw)["count"]
    return out


def _assemble_logic_check(md_text):
    """拼装后逻辑一致性校验（断点②）。

    返回 warnings 列表（不硬拦截——启发式判断，仅供审查提示）。
    1. 跨章节矛盾（复用 toolbox E39 P0）
    2. 段间衔接：连续段首句有关联（连接词或上段关键词重复）
    3. 过度宣称（复用 overstatement_check）
    """
    warnings = []
    try:
        sys.path.insert(0, str(ENGINE))
        import toolbox
    except Exception:
        return warnings
    # 1) 跨章节矛盾（自实现更全词库 + toolbox 兜底）
    _intro_m = re.search(r"(?:^#+\s*(?:\d+[.．、]\s*)?(?:introduction|引言)\s*\n)(.*?)(?=\n\s*#+|\Z)",
                         md_text, re.M | re.S | re.I)
    _body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    if _intro_m:
        _intro_text = _intro_m.group(1)
        _rest = _body.replace(_intro_m.group(0), "", 1)
        _affirm = re.compile(r"(?:has|have)\s+been\s+(?:applied|established|reported|demonstrated)|was\s+established|is\s+available|are\s+available|已应用|已建立|已报道|已有报道", re.I)
        _deny = re.compile(r"(?:has|have)\s+not\s+been\s+(?:reported|established|applied)|not\s+available|absent|尚未报道|未见报道|无报道", re.I)
        _techs = ["UV mutagenesis", "ARTP", "protoplast fusion", "CRISPR", "ATMT",
                  "Agrobacterium", "PEG transformation", "PEG-mediated",
                  "紫外", "原生质体融合", "基因编辑"]
        for _t in _techs:
            _tp = re.compile(re.escape(_t), re.I)
            _ia = any(_affirm.search(_intro_text[max(0, m.start() - 80):m.end() + 80]) for m in _tp.finditer(_intro_text))
            _bd = any(_deny.search(_rest[max(0, m.start() - 80):m.end() + 80]) for m in _tp.finditer(_rest))
            if _ia and _bd:
                warnings.append({"severity": "P0", "type": "cross_section_contradiction",
                                 "msg": f"引言/摘要称 {_t} 已应用，但正文明确说尚未报道——跨章节矛盾"})
                break
    # toolbox E39 兜底
    try:
        for it in toolbox.cross_section_contradiction_check(md_text):
            if not any(w.get("type") == "cross_section_contradiction" for w in warnings):
                warnings.append({"severity": it.get("severity", "P0"), "type": "cross_section_contradiction",
                                 "msg": it.get("msg", "")})
    except Exception:
        pass
    # 2) 段间衔接（启发式）
    try:
        paras = [p.strip() for p in md_text.split("\n\n") if p.strip() and not p.startswith(("#", "|", "!", "!["))]
        connectives = ("however", "therefore", "thus", "in addition", "furthermore", "moreover",
                       "consequently", "nevertheless", "specifically", "finally", "first", "second",
                       "however", "为此", "因此", "此外", "进一步", "总之")
        weak = 0
        for i in range(1, len(paras)):
            first = re.split(r"(?<=[.!?])\s", paras[i])[0].lower() if paras[i] else ""
            if not first:
                continue
            prev_words = set(re.findall(r"[a-z]{5,}", paras[i - 1].lower()))
            if not any(c in first for c in connectives) and not (prev_words & set(re.findall(r"[a-z]{5,}", first))):
                weak += 1
        if weak >= 3:
            warnings.append({"severity": "P2", "type": "paragraph_transition",
                             "msg": f"{weak} 个段落首句与上一段无衔接词或关键词重复——段间过渡弱"})
    except Exception:
        pass
    # 3) 过度宣称
    try:
        for it in toolbox.overstatement_check(md_text):
            if it.get("severity") in ("P0", "P1"):
                warnings.append({"severity": it.get("severity", "P1"), "type": "overstatement",
                                 "msg": it.get("msg", "")[:120]})
    except Exception:
        pass
    return warnings


def gen_assemble(proj):
    st = load_gen_state(proj)
    if st is None:
        return {"ok": False, "msg": "未初始化"}
    cp = _paths(proj)["contract"]
    if not cp.exists():
        return {"ok": False, "msg": "缺少契约"}
    contract = parse_contract(cp.read_text(encoding="utf-8"))
    parts, missing = [], []
    sid_of_part = []
    for s in contract.get("sections", []):
        info = (st.get("sections") or {}).get(s["sid"], {})
        if info.get("status") != "done":
            missing.append(s["sid"])
            continue
        f = _paths(proj)["sections"] / info.get("file", "")
        if f.exists():
            parts.append(f.read_text(encoding="utf-8").strip())
            sid_of_part.append(s["sid"])
    if missing:
        return {"ok": False, "msg": f"尚有段落未完成: {missing}（全部接受后才拼装）"}
    # S7: 按契约把已渲染表格插入对应段末尾（二期）
    frags = tables_render(proj, quiet=True)
    insert_map = _contract_insert_map(contract)
    stray = []
    for frag in frags:
        sid = insert_map.get(frag["id"])
        if sid in sid_of_part:
            idx = sid_of_part.index(sid)
            parts[idx] = parts[idx] + "\n\n" + frag["md"]
        else:
            stray.append(frag["id"])
    # S6: 摘要后置产物前置（标题后、首段前）
    abstract_f = _paths(proj)["draft"] / "abstract.md"
    if abstract_f.exists():
        parts.insert(0, abstract_f.read_text(encoding="utf-8").strip())
    main = Path(proj) / "manuscript" / "main.md"
    if main.exists():
        bak = main.with_name(f"main.md.bak-{datetime.date.today():%Y%m%d}-assemble")
        if not bak.exists():
            bak.write_text(main.read_text(encoding="utf-8"), encoding="utf-8")
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    # 断点②: 拼装后逻辑一致性校验
    logic_warnings = _assemble_logic_check(main.read_text(encoding="utf-8"))
    msg = f"已拼装 {len(parts)} 部分 → {main}（旧版已备份）"
    if stray:
        msg += f"；注意: {stray} 未找到插入段，已追加在对应表格片段内待人工定位"
    if logic_warnings:
        msg += f"；逻辑校验: {len(logic_warnings)} 项提示（{'; '.join(w['type'] for w in logic_warnings[:4])}...）"
        append_log(proj, f"gen-assemble: 逻辑校验 {len(logic_warnings)} 项提示")
    append_log(proj, f"gen-assemble: {len(parts)} 部分; 表格 {len(frags)} 张")
    return {"ok": True, "msg": msg, "logic_warnings": logic_warnings}


# ─────────────────────────── S7 表格程序化渲染（二期） ───────────────────────────

def _tables_dir(proj):
    d = _paths(proj)["draft"] / "tables"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _table_slug(tid):
    return re.sub(r"[^A-Za-z0-9]+", "-", str(tid)).strip("-").lower()


def _contract_tables(contract):
    """从契约图表契约中取表格行: [{id, title, caption, insert_sid, source}]。"""
    out = []
    for row in contract.get("figures", []):
        if len(row) < 5:
            continue
        tid, ttype, title, cap, insert = row[0], row[1], row[2], row[3], row[4]
        if "表" not in ttype and "table" not in ttype.lower():
            continue
        out.append({"id": tid, "title": title, "caption": cap,
                    "insert_sid": insert, "source": row[5] if len(row) > 5 else ""})
    return out


def _contract_insert_map(contract):
    return {t["id"]: t["insert_sid"] for t in _contract_tables(contract)}


def validate_table_json(spec):
    """表格 JSON 校验 → issues 列表（空 = 通过）。"""
    issues = []
    if not spec.get("id"):
        issues.append("缺少 id")
    headers = spec.get("headers") or []
    rows = spec.get("rows") or []
    if not headers:
        issues.append("缺少 headers")
    if not rows:
        issues.append("缺少 rows")
    ncol = len(headers)
    if headers and ncol < 2:
        issues.append("列数过少")
    for i, r in enumerate(rows):
        if len(r) != ncol:
            issues.append(f"第 {i + 1} 行 {len(r)} 列 ≠ 表头 {ncol} 列")
        if not any(str(c).strip() for c in r):
            issues.append(f"第 {i + 1} 行全空")
    return issues


def render_table_md(spec):
    """表格 JSON → 标准管道表（表头加粗，单元格内 | 转义、换行转分号）。"""
    def cell(c):
        s = str(c).replace("\n", "; ").replace("|", "\\|").strip()
        return s or " "
    head = "| " + " | ".join(f"**{cell(h)}**" for h in spec["headers"]) + " |"
    sep = "|" + "|".join(["---"] * len(spec["headers"])) + "|"
    body = ["| " + " | ".join(cell(c) for c in r) + " |" for r in spec["rows"]]
    title = spec.get("title") or ""
    prefix = f"## {spec['id']}. {title}\n\n" if title else ""
    return prefix + "\n".join([head, sep] + body)


def tables_render(proj, quiet=False):
    """渲染 draft/tables/*.json → [{id, md, issues}]；仅返回校验通过的片段。"""
    frags = []
    for f in sorted(_tables_dir(proj).glob("*.json")):
        try:
            spec = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            if not quiet:
                print(f"✗ {f.name}: JSON 解析失败 {e}")
            continue
        issues = validate_table_json(spec)
        if issues:
            if not quiet:
                print(f"✗ {f.name}: " + "; ".join(issues))
            continue
        frags.append({"id": spec["id"], "md": render_table_md(spec), "issues": []})
        if not quiet:
            print(f"✔ {f.name} → {spec['id']}（{len(spec['rows'])} 行 x {len(spec['headers'])} 列）")
    return frags


def tables_extract(proj, md_path=None):
    """从现有 Markdown 提取管道表 → draft/tables/*.json（存量稿件迁移路径）。"""
    md = Path(md_path) if md_path else Path(proj) / "manuscript" / "main.md"
    if not md.exists():
        return {"ok": False, "msg": f"缺少 {md}"}
    text = md.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    saved, i, tnum = [], 0, 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$", lines[i + 1]):
            header = [c.strip() for c in ln.strip("|").split("|")]
            header = [re.sub(r"^\*\*|\*\*$", "", h).strip() for h in header]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            # 表标题：向上找最近的 ## Table X. ... 标题行
            title, tid = "", ""
            for k in range(i - 1, max(-1, i - 6), -1):
                m = re.match(r"^#{1,6}\s*(Table\s*\d+[.．])\s*(.*)", lines[k].strip())
                if m:
                    tid = re.sub(r"[.．]\s*$", "", m.group(1)).strip()
                    title = m.group(2).strip()
                    break
            if not tid:
                tnum += 1
                tid = f"Table-X{tnum}"
            spec = {"id": tid, "title": title, "headers": header, "rows": rows}
            issues = validate_table_json(spec)
            if not issues:
                out = _tables_dir(proj) / (_table_slug(tid) + ".json")
                out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
                saved.append(f"{tid} ({len(rows)} 行)")
            i = j
            continue
        i += 1
    append_log(proj, f"tables-extract: {len(saved)} 张 → draft/tables/")
    return {"ok": bool(saved), "msg": "；".join(saved) if saved else "未发现标准管道表"}


def tables_gen_ai(proj, tid):
    """委托 AI 按契约生成表格行数据 JSON（模型只产数据，排版由渲染器负责）。"""
    cp = _paths(proj)["contract"]
    if not cp.exists():
        return {"ok": False, "msg": "缺少契约"}
    contract = parse_contract(cp.read_text(encoding="utf-8"))
    trow = next((t for t in _contract_tables(contract) if t["id"] == tid), None)
    if trow is None:
        return {"ok": False, "msg": f"契约图表契约中无表格 {tid}；现有: {[t['id'] for t in _contract_tables(contract)]}"}
    sys.path.insert(0, str(ENGINE / "web"))
    try:
        import ai_client
    except Exception as e:
        return {"ok": False, "msg": f"ai_client 不可用: {e}"}
    prompt = (
        "你是学术论文表格数据整理助手。请按契约把下表的内容整理成结构化行数据。\n"
        f"表格: {trow['id']} | 标题: {trow['title']} | 图注要点: {trow['caption']} | 数据来源: {trow['source']}\n"
        "要求: 只输出一个 JSON，结构为 {\"id\", \"title\", \"headers\": [...], \"rows\": [[...], ...]}；"
        "单元格内不得换行；内容必须来自项目已有材料，不得编造。\n"
        "可参考的项目材料见下（若材料不足，输出空 rows 并在 title 末尾标注 (待补数据)。\n\n"
    )
    # 注入相关段产物作为数据来源（最多 6000 字）
    st = load_gen_state(proj) or {}
    ctx = []
    for sid, info in (st.get("sections") or {}).items():
        f = _paths(proj)["sections"] / info.get("file", "")
        if f.exists():
            ctx.append(f"## 段落 {sid}\n" + f.read_text(encoding="utf-8", errors="replace")[:2000])
    prompt += "\n\n".join(ctx)[:6000] or "（无已完成段落，请输出空 rows）"
    resp = str(ai_client.chat([{"role": "user", "content": prompt}]) or "")
    m = re.search(r"\{[\s\S]*\}", resp)
    if not m:
        return {"ok": False, "msg": "AI 未返回 JSON"}
    try:
        spec = json.loads(m.group(0))
    except Exception as e:
        return {"ok": False, "msg": f"JSON 解析失败: {e}"}
    spec["id"] = tid
    issues = validate_table_json(spec)
    if issues:
        return {"ok": False, "msg": "AI 产出不合格: " + "; ".join(issues)}
    out = _tables_dir(proj) / (_table_slug(tid) + ".json")
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    append_log(proj, f"tables-gen {tid}: {len(spec['rows'])} 行")
    return {"ok": True, "msg": f"{tid} 行数据已生成 → {out}（请人工核对后渲染）"}


def gen_tables(proj, tid=None, gen=False):
    """CLI 入口: 默认渲染全部；--gen --tid 委托 AI 生成行数据。"""
    if gen:
        if not tid:
            return {"ok": False, "msg": "--gen 需要 --tid"}
        return tables_gen_ai(proj, tid)
    frags = tables_render(proj)
    if not frags:
        return {"ok": False, "msg": "无可渲染表格（draft/tables/ 无有效 JSON；可先 tables --extract 迁移存量表格）"}
    out_dir = _paths(proj)["draft"] / "tables_rendered"
    out_dir.mkdir(parents=True, exist_ok=True)
    for frag in frags:
        (out_dir / (_table_slug(frag["id"]) + ".md")).write_text(frag["md"] + "\n", encoding="utf-8")
    return {"ok": True, "msg": f"已渲染 {len(frags)} 张表格 → {out_dir}（assemble 时自动按契约插入）"}


# ─────────────────────────── S6 摘要后置生成（二期） ───────────────────────────

def _section_summaries(proj, contract, per_section_words=120):
    """取各已完成段的压缩摘要（标题 + 每段首句），作为摘要生成的输入（不用全文）。"""
    st = load_gen_state(proj) or {}
    out = []
    for s in contract.get("sections", []):
        info = (st.get("sections") or {}).get(s["sid"], {})
        if info.get("status") != "done":
            continue
        f = _paths(proj)["sections"] / info.get("file", "")
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        heads = re.findall(r"^#{1,6}\s*(.+)$", text, re.M)
        firsts = []
        for para in re.split(r"\n\s*\n", text):
            p = para.strip()
            if not p or p.startswith("#") or p.startswith("|") or p.startswith("!["):
                continue
            m = re.match(r"^.+[.!?]", p)
            firsts.append((m.group(0) if m else p)[:200])
        out.append(f"### {s['sid']} {s['title']}\n小节: {'; '.join(heads[:6])}\n首句摘要: {' '.join(firsts)[:per_section_words * 6]}")
    return "\n\n".join(out)


_ABSTRACT_STOP = set("""a an the and or of in on for to with by from as is are was were be been being
this that these those it its we our they their which who whom what when where how not no
can could may might shall should will would must also however moreover furthermore thus
review reviews study studies paper evidence domain domains claim claims table figure section""".split())


def check_abstract_alignment(abstract_text, body_text, max_words=250):
    """摘要-正文对齐校验（启发式）：字数上限 + 每句显著词在正文的出现率 ≥50%。"""
    issues = []
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*|\d+", abstract_text)
    if max_words and len(words) > max_words:
        issues.append({"severity": "P1", "type": "abstract_length",
                       "msg": f"摘要 {len(words)} 词超出上限 {max_words}"})
    body_tokens = set(re.sub(r"[^a-z0-9 ]", " ", body_text.lower()).split())
    for s in re.split(r"(?<=[.!?])\s+", abstract_text.strip()):
        toks = [t for t in re.findall(r"[a-z][a-z'\-]{5,}|\d+", s.lower()) if t not in _ABSTRACT_STOP]
        if len(toks) < 3:
            continue
        hit = sum(1 for t in toks if t in body_tokens)
        if hit / len(toks) < 0.5:
            issues.append({"severity": "P1", "type": "abstract_unsupported",
                           "msg": f"摘要句缺乏正文支撑（{hit}/{len(toks)} 显著词命中）: {s[:70]}..."})
    return issues


def gen_abstract(proj, dry_run=False, retry=1, max_words=250):
    """全文定稿后基于各段摘要生成 Abstract/Keywords，并做对齐校验。"""
    st = load_gen_state(proj)
    if st is None:
        return {"ok": False, "msg": "未初始化"}
    cp = _paths(proj)["contract"]
    if not cp.exists():
        return {"ok": False, "msg": "缺少契约"}
    contract = parse_contract(cp.read_text(encoding="utf-8"))
    done = [s["sid"] for s in contract.get("sections", [])
            if (st.get("sections") or {}).get(s["sid"], {}).get("status") == "done"]
    if not done:
        return {"ok": False, "msg": "无已完成段落——摘要必须后置生成（S6）"}
    summaries = _section_summaries(proj, contract)
    journal_note = ""
    chosen = Path(proj) / "journal" / "chosen.md"
    if chosen.exists():
        jt = chosen.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"摘要[:：]([^\n]{0,120})", jt)
        if m:
            journal_note = f"期刊摘要要求: {m.group(1).strip()}\n"
    # 摘要语言跟随契约语言（中文项目若未指示, 模型默认产出英文摘要,
    # 英文显著词对中文正文的对齐校验必然失败——任务 #20 收官小修）
    lang_note = "请用中文写作（拉丁学名保留原文，Keywords 可用中文分号分隔）。\n" \
        if contract.get("lang") == "zh" else ""
    prompt = (
        "你是学术论文摘要写作助手。下面的各章节压缩摘要（不是全文）写一篇摘要。\n"
        + lang_note
        + journal_note
        + f"硬性要求: ≤{max_words} 词；不得引入各节摘要中不存在的论断/数字/结论；"
        "结构: 背景 1-2 句 → 方法/范围 1 句 → 核心发现 2-4 句 → 结论/展望 1-2 句；"
        "不得出现引用编号与缩写（首次出现需全称）。\n"
        "输出两部分: '## Abstract' 正文与 '## Keywords'（5-8 个，分号分隔）。\n\n"
        + summaries[:9000]
    )
    if dry_run:
        return {"ok": True, "msg": "dry-run prompt", "prompt": prompt}
    sys.path.insert(0, str(ENGINE / "web"))
    try:
        import ai_client
    except Exception as e:
        return {"ok": False, "msg": f"ai_client 不可用: {e}"}
    body_text = "\n\n".join(
        (_paths(proj)["sections"] / (st.get("sections", {}).get(s, {}) or {}).get("file", "")).read_text(encoding="utf-8", errors="replace")
        for s in done if (_paths(proj)["sections"] / (st.get("sections", {}).get(s, {}) or {}).get("file", "")).exists())
    attempts, issues, text = 0, [], ""
    while attempts <= retry:
        resp = str(ai_client.chat([{"role": "user", "content": prompt}]) or "")
        text = _strip_code_fence(resp)
        m = re.search(r"##\s*Abstract\s*\n([\s\S]*?)(?=##\s*Keywords|\Z)", text)
        abody = m.group(1).strip() if m else text.strip()
        issues = check_abstract_alignment(abody, body_text, max_words=max_words)
        if not any(i["severity"] == "P1" for i in issues):
            break
        attempts += 1
        if attempts <= retry:
            prompt += ("\n\n## 上一轮未通过对齐校验，请修正（不得引入正文没有的内容）：\n- "
                       + "\n- ".join(i["msg"] for i in issues))
    accepted = not any(i["severity"] == "P1" for i in issues)
    if accepted:
        (_paths(proj)["draft"] / "abstract.md").write_text(text.strip() + "\n", encoding="utf-8")
        append_log(proj, f"gen-abstract: accepted (尝试 {attempts + 1})")
    return {"ok": accepted,
            "msg": ("摘要已生成 → draft/abstract.md（assemble 时自动前置）" if accepted else "未通过对齐校验"),
            "issues": issues}


# ─────────────────────────── 三期：DSH Agent 委托编排 ───────────────────────────

def gen_status_detail(proj):
    """段级状态 + 表格/摘要/契约扩展信息（供 CLI/Web/委托共用）。"""
    base = gen_status(proj)
    if not base.get("ok"):
        return base
    ps = _paths(proj)
    base["contract_exists"] = ps["contract"].exists()
    base["tables"] = sorted(f.stem for f in (ps["draft"] / "tables").glob("*.json")) if (ps["draft"] / "tables").exists() else []
    base["abstract_done"] = (ps["draft"] / "abstract.md").exists()
    base["sections_files"] = sorted(f.name for f in ps["sections"].glob("*.md")) if ps["sections"].exists() else []
    return base


def gen_delegate(proj, timeout=1800):
    """把 S0→S8 编排委托给 DSH Agent（核心纪律直接内联在委托提示词中，不再依赖技能文件）。

    工作台只负责组装"当前进度 + 下一步指令"，写作执行交给 Agent；
    Agent 离线时返回提示（回退：用户手动跑 generate 各动作）。
    """
    st = gen_status_detail(proj)
    if not st.get("ok"):
        return st
    proj = Path(proj)
    engine = ENGINE / "wb.py"
    done = [r["sid"] for r in st["rows"] if r["status"] == "done"]
    pending = [r["sid"] for r in st["rows"] if r["status"] != "done"]
    lines = [
        "为下面的论文项目执行/续跑分批生成管线。严格遵守以下核心纪律（已内联，无需阅读外部技能文件）：",
        "- 契约未锁定不得写正文；",
        "- 引文只用契约分配池内编号；",
        "- 表格只产行数据走程序化渲染，禁止手写表格；",
        "- 摘要后置；",
        "- 图表需求一律经 figure_router.py 路由：数据图→matplotlib/NPG；示意图→PPT 路由(存档源 PPTX)；Origin 风格→figure_origin。",
        "",
        f"项目目录: {proj}",
        f'引擎命令: python "{engine}" generate <action> --dir "{proj}" ...',
        "",
        "## 当前进度",
        f"- 契约: {'存在' if st['contract_exists'] else '不存在'}；锁定: {'是' if st['locked'] else '否'}",
        f"- 段落: 已完成 {done or '无'}；待完成 {pending or '无'}",
        f"- 表格 JSON: {st['tables'] or '无'}；摘要产物: {'有' if st['abstract_done'] else '无'}",
        "",
        "## 下一步编排（按此顺序）",
    ]
    if not st["contract_exists"]:
        lines.append("1. 运行 generate contract（可加 --ai 起草），然后**停下**请用户补全并锁定契约（不得替用户锁定）。")
    elif not st["locked"]:
        lines.append("1. 契约未锁定——**停下**，提示用户补全 draft/contract.md 并把状态改为「已锁定」。")
    else:
        n = 1
        for sid in pending:
            lines.append(f"{n}. generate section --sid {sid}（失败重试 1 次后仍失败则停下报告）")
            n += 1
        if not st["tables"]:
            lines.append(f"{n}. generate tables --extract（迁移存量表格）；如无存量，按契约用 tables --gen 逐表产数据")
            n += 1
        if not st["abstract_done"]:
            lines.append(f"{n}. generate abstract（对齐校验失败则修正后重试）")
            n += 1
        lines.append(f'{n}. generate assemble，然后跑 python "{ENGINE / "toolbox.py"}" quality-check "{proj}" 全量门禁')
    lines += ["", "每一步执行后复查 generate status；全部完成后向用户汇报段级结果与门禁分数。"]
    instruction = "\n".join(lines)

    sys.path.insert(0, str(ENGINE))
    try:
        import dsh_bridge
    except Exception as e:
        return {"ok": False, "msg": f"dsh_bridge 不可用: {e}（可手动逐步运行 generate 动作）"}
    if not dsh_bridge.is_available():
        return {"ok": False, "msg": "DSH Agent 离线（端口 3080 无响应）——回退：手动执行 generate 各动作"}
    try:
        result = dsh_bridge.delegate_task(instruction, cwd=str(proj), timeout=timeout)  # preset 用 dsh_bridge.DEFAULT_PRESET 动态解析
    except Exception as e:
        return {"ok": False, "msg": f"委托失败: {e}"}
    text = str((result or {}).get("text", "")).strip()
    # 校验 DSH 会话工具调用——是否跑过引擎命令（纪律已内联提示词，技能阅读不再强制校验）
    tool_calls = (result or {}).get("tool_calls") or []
    engine_calls = [tc for tc in tool_calls
                    if str(tc.get("tool", "")).lower() in ("bash", "shell")
                    and ("wb.py" in str(tc.get("result_preview", "")) or "generate" in str(tc.get("result_preview", "")))]
    status_flags = []
    if not engine_calls:
        status_flags.append("引擎命令未确认（未见 wb.py/generate 执行）")
    append_log(proj, f"gen-delegate: 已委托 DSH Agent（timeout={timeout}s; 工具调用 {len(tool_calls)} 次）")
    return {"ok": True, "msg": "已委托 DSH Agent 执行分批生成编排",
            "text": text[:3000] if text else "（Agent 未返回文本，请用 generate status 查看进度）",
            "tool_calls": len(tool_calls),
            "engine_calls": len(engine_calls),
            "skill_engagements": 0,
            "warnings": status_flags}
