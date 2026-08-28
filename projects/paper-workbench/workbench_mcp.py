# -*- coding: utf-8 -*-
"""workbench_mcp.py — 论文工作台统一 MCP 服务（stdio）。

目标：让**任何**支持 MCP 的 agent（Claude Code / Cursor / Codex / Qoder / TRAE / DSH …）
都能自主发现并调用论文工作台的全部能力与 ~/.dsh/skills 下的 400+ 学术技能，
且所有工具调用写入台账（tool_ledger.jsonl）——过程可审计，杜绝"阳奉阴违"
（agent 自己手写一遍绕过工具）。

工具分组：
  技能自主选择（写作/润色/审稿/投稿全流程技能发现）
    - search_skills      按任务语义搜技能（中英文皆可）
    - read_skill         读技能 SKILL.md 全文并自动登记
    - record_skill_use   登记技能使用（进台账，供过程审计）
  质量与后处理（强制走工具的关键环节）
    - quality_check      质量门禁（P0/P1/P2 + 分数 + 问题清单）
    - mechanical_fix     机械错误自动修复（空引用/连标点/格式）
  图表与导出
    - figure_render      三路绘图路由（data→matplotlib/NPG; schematic→PPT; origin）
    - export_docx        Word 导出（TNR 模板 + 图片内嵌）
  检索与文献池（学术库/网页/全文/文献池全链路）
    - literature_search  多源学术检索（OpenAlex/arXiv/Crossref/PubMed）
    - web_search          AnySearch 网页检索（灰色文献/期刊指南）
    - web_extract         AnySearch 抓取 URL 全文
    - build_references    检索生成/合并参考文献 BibTeX（可写项目池）
    - fetch_doi           DOI → 元数据（核验/补全）
  写作规范
    - writing_brief       项目/阶段写作规范 brief（句子/引用/主线纪律）
  诚信
    - originality         本地原创性/重复度检查
  导入
    - import_document     pdf/docx → 规范化 Markdown
  过程审计（防绕过）
    - process_audit      产物修改时间 vs 台账工具调用记录比对
    - ledger_query       查询台账

注册（任一 agent 的 MCP 配置；command 填本机 python 绝对路径，args 填本文件绝对路径）：
  { "mcpServers": { "paper-workbench": {
      "command": "C:/path/to/your/python.exe",
      "args": ["C:/path/to/workbench/workbench_mcp.py"] } } }

一键注册：python register_mcp.py
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# stdio 模式下标准输出属于 MCP 协议，诊断信息一律走 stderr
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

WB = Path(__file__).resolve().parent
PAPERS = Path.home() / ".dsh" / "papers"
SKILLS_ROOT = Path.home() / ".dsh" / "skills"
LEDGER = WB / "data" / "tool_ledger.jsonl"
SKILL_INDEX = WB / "data" / "skill_index.json"
PY = sys.executable  # server 由哪个解释器拉起, 子进程就用同一个

sys.path.insert(0, str(WB))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("paper-workbench")


# ─────────────────────────── 台账 ───────────────────────────

def _ledger_append(entry: dict):
    """工具调用留痕。这是"防阳奉阴违"的数据基础：真实工具调用才有记录。"""
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S") + time.strftime("%z")
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write(f"[ledger] 写入失败: {e}\n")


def _ledger_read():
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _run_cli(args, cwd=None, timeout=300):
    """统一 CLI 子进程调用（UTF-8 输出收集）。"""
    r = subprocess.run(
        [PY, "-X", "utf8"] + args,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=cwd or str(WB), timeout=timeout,
    )
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _ok(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


# ─────────────────────────── 技能索引 ───────────────────────────

# 中文任务词 → 英文关键词扩展（让中文 query 命中英文 description）
_TASK_ZH_EN = {
    "润色": ["polish", "prose", "style", "editing", "language"],
    "写作": ["writing", "write", "manuscript", "draft"],
    "审稿": ["review", "reviewer", "rebuttal", "response"],
    "投稿": ["submission", "submit", "cover letter"],
    "检索": ["search", "literature", "find", "fetch", "download"],
    "绘图": ["figure", "chart", "plot", "visual"],
    "表格": ["table", "tabular"],
    "引用": ["citation", "reference", "ref", "doi"],
    "统计": ["statistic", "stats", "data analysis"],
    "原创": ["originality", "plagiarism"],
    "结构": ["structure", "outline", "architecture", "framework"],
    "摘要": ["abstract", "summary", "highlights"],
    "封面": ["cover letter"],
    "海报": ["poster"],
    "幻灯": ["slides", "presentation", "ppt"],
    "英文": ["english", "language", "prose"],
    "综述": ["review", "survey", "literature"],
    "论证": ["argument", "evidence", "claim"],
    "实验": ["experiment", "reproducibility", "artifact"],
}


def _skill_dirs():
    out = []
    # Index both user-installed DSH skills and the skills shipped with this
    # checkout.  The original implementation only scanned ~/.dsh/skills,
    # which made the repository's own writing skills invisible to MCP agents.
    for root in (SKILLS_ROOT, WB / "skills"):
        if root.exists():
            out.extend(d for d in root.iterdir() if d.is_dir())
    return out


def _build_skill_index(force=False):
    """扫描 SKILL.md frontmatter（name+description）建索引；目录 mtime 变化才重建。"""
    dirs = _skill_dirs()
    newest = max((d.stat().st_mtime for d in dirs), default=0)
    if not force and SKILL_INDEX.exists():
        try:
            cache = json.loads(SKILL_INDEX.read_text(encoding="utf-8"))
            if cache.get("built_at_mtime") == newest and cache.get("count") == len(dirs):
                return cache["skills"]
        except Exception:
            pass
    skills = []
    for d in dirs:
        md = d / "SKILL.md"
        if not md.exists():
            continue
        try:
            head = md.read_text(encoding="utf-8", errors="replace")[:4000]
        except Exception:
            continue
        name = d.name
        m = re.search(r"^name:\s*(.+)$", head, re.M)
        if m:
            name = m.group(1).strip().strip('"\'')
        desc = ""
        m = re.search(r"^description:\s*(.+)$", head, re.M)
        if m:
            raw_desc = m.group(1).strip()
            if raw_desc in (">", "|", ">-", "|-"):
                # YAML folded/block descriptions: collect indented lines until
                # the frontmatter terminator, keeping the index useful even
                # when a skill uses a multi-line description.
                lines = head.splitlines()
                try:
                    start = next(i for i, line in enumerate(lines)
                                 if re.match(r"^description:\s*", line)) + 1
                    parts = []
                    for line in lines[start:]:
                        if line.strip() == "---":
                            break
                        if line.startswith((" ", "\t")):
                            parts.append(line.strip())
                        elif parts:
                            break
                    desc = " ".join(parts)
                except StopIteration:
                    desc = ""
            else:
                desc = raw_desc.strip('"\'')
        skills.append({"name": name, "dir": d.name, "description": desc})
    try:
        SKILL_INDEX.parent.mkdir(parents=True, exist_ok=True)
        SKILL_INDEX.write_text(json.dumps(
            {"built_at_mtime": newest, "count": len(dirs), "skills": skills},
            ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    sys.stderr.write(f"[skill-index] {len(skills)} skills indexed\n")
    return skills


def _expand_query(query: str):
    """中文任务词扩展为英文关键词集合。"""
    words = set(re.findall(r"[a-zA-Z0-9-]+", query.lower()))
    for zh, ens in _TASK_ZH_EN.items():
        if zh in query:
            words.update(ens)
    return words or {query.lower()}


# ─────────────────────────── 技能自主选择 ───────────────────────────

@mcp.tool()
def search_skills(query: str, limit: int = 12) -> str:
    """按当前任务搜索可用学术技能（写作/润色/审稿/投稿/绘图/检索…）。

    返回 name + 一句话描述 + 是否已安装。agent 写作前应先调用本工具，
    按 description 自主选择并 read_skill 阅读遵循，而不是凭记忆模仿。
    query 支持中文（如"润色"、"绘图"、"投稿"）或英文任务描述。"""
    t0 = time.time()
    skills = _build_skill_index()
    qwords = _expand_query(query)
    scored = []
    for s in skills:
        name_l = s["name"].lower()
        desc_l = s["description"].lower()
        score = 0
        for w in qwords:
            if len(w) < 2:
                continue
            if w in name_l:
                score += 5
            elif w in name_l.split("-"):
                score += 3
            if w in desc_l:
                score += 2
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    result = [s for _, s in scored[:max(1, min(limit, 40))]]
    _ledger_append({"tool": "search_skills", "query": query, "hits": len(result),
                    "ok": True, "ms": int((time.time() - t0) * 1000)})
    return _ok({"ok": True, "query": query, "total_skills": len(skills),
                "matches": result})


@mcp.tool()
def read_skill(name: str) -> str:
    """读取指定技能的 SKILL.md 全文（自动登记台账）。

    name 为技能目录名（如 nature-polishing / polish-prose / scientific-writing）。
    agent 决定使用某技能后必须先读全文，按技能协议执行。"""
    t0 = time.time()
    name = re.sub(r"[^a-zA-Z0-9_-]", "", name)
    for root in (SKILLS_ROOT,):
        d = root / name
        md = d / "SKILL.md"
        if md.exists():
            try:
                content = md.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                return _ok({"ok": False, "error": str(e)})
            _ledger_append({"tool": "read_skill", "skill": name, "ok": True,
                            "ms": int((time.time() - t0) * 1000)})
            return _ok({"ok": True, "skill": name, "content": content})
    _ledger_append({"tool": "read_skill", "skill": name, "ok": False})
    return _ok({"ok": False, "error": f"技能不存在: {name}（先 search_skills 查询）"})


@mcp.tool()
def record_skill_use(skill: str, project: str, note: str = "") -> str:
    """登记技能使用记录（写入台账）。

    agent 按技能执行写作/润色等任务时登记：skill=技能名, project=项目路径,
    note=用该技能做了什么。过程审计（process_audit）会核对台账中的技能
    使用记录与产物修改活动——写作期间零技能登记会被标记为可疑。"""
    skill = re.sub(r"[^a-zA-Z0-9_-]", "", skill)
    _ledger_append({"tool": "record_skill_use", "skill": skill,
                    "project": project, "note": note[:300], "ok": True})
    return _ok({"ok": True, "msg": f"已登记技能使用: {skill}"})


# ─────────────────────────── 质量与后处理 ───────────────────────────

@mcp.tool()
def quality_check(project: str) -> str:
    """对论文项目运行全量质量门禁（P0/P1/P2 分级 + 分数 + 问题清单）。

    任何稿件修改之后必须调用本工具复检。返回 score/level 与问题列表。
    P0=学术诚信/致命, P1=强烈建议修复, P2=润色建议。"""
    t0 = time.time()
    proj = Path(project)
    if not (proj / "state.json").exists():
        return _ok({"ok": False, "error": f"非工作台项目: {project}"})
    code, out = _run_cli(["toolbox.py", "quality-check", str(proj)], timeout=600)
    i = out.find("{")
    if code != 0 or i < 0:
        _ledger_append({"tool": "quality_check", "project": str(proj), "ok": False})
        return _ok({"ok": False, "error": out[-800:]})
    try:
        data = json.loads(out[i:])
    except Exception:
        _ledger_append({"tool": "quality_check", "project": str(proj), "ok": False})
        return _ok({"ok": False, "error": "输出解析失败", "raw": out[-800:]})
    issues = data.get("issues", [])
    summary = {
        "ok": True,
        "score": data.get("score", {}),
        "p0": [x for x in issues if x.get("severity") == "P0"],
        "p1": [x for x in issues if x.get("severity") == "P1"],
        "p2_count": sum(1 for x in issues if x.get("severity") == "P2"),
        "p2_types": sorted({x.get("type", "") for x in issues if x.get("severity") == "P2"}),
    }
    _ledger_append({"tool": "quality_check", "project": str(proj), "ok": True,
                    "score": summary["score"].get("score"),
                    "p0": len(summary["p0"]), "p1": len(summary["p1"]),
                    "ms": int((time.time() - t0) * 1000)})
    return _ok(summary)


@mcp.tool()
def mechanical_fix(file: str, dry_run: bool = False) -> str:
    """机械错误自动修复（连标点/双句号/空引用标记/全角残留等）。

    修改稿件后建议先 mechanical_fix 再 quality_check。
    dry_run=True 只报告不写入。"""
    t0 = time.time()
    p = Path(file)
    if not p.exists():
        return _ok({"ok": False, "error": f"文件不存在: {file}"})
    args = ["toolbox.py", "mechanical" + ("" if dry_run else "-fix"), str(p)]
    if dry_run:
        args.insert(2, "--dry-run")
    code, out = _run_cli(args, timeout=120)
    _ledger_append({"tool": "mechanical_fix", "file": str(p), "dry_run": dry_run,
                    "ok": code == 0, "ms": int((time.time() - t0) * 1000)})
    return _ok({"ok": code == 0, "output": out[-3000:]})


@mcp.tool()
def figure_render(spec_json: str) -> str:
    """三路绘图路由（确定性分发, 不依赖 AI 判断）。

    spec_json: {"type": "data|schematic|origin", "out": "输出路径", ...}
      - data      → matplotlib + NPG 出版级配色（柱/线/散点/箱线…, 带 data/spec）
      - schematic → PPT 路由绘制示意图/流程图（python-pptx → PowerPoint COM, 源 PPTX 存档）
      - origin    → Origin COM 出图（Origin 风格出版级）
    禁止用默认蓝橙配色; 示意图必须存档源 PPTX。"""
    t0 = time.time()
    try:
        spec = json.loads(spec_json)
    except Exception as e:
        return _ok({"ok": False, "error": f"spec_json 非法: {e}"})
    tmp = WB / "data" / "_mcp_fig_spec.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    code, out = _run_cli(["figure_router.py", "--json", str(tmp)], timeout=300)
    _ledger_append({"tool": "figure_render", "type": spec.get("type"),
                    "out": spec.get("out"), "ok": code == 0,
                    "ms": int((time.time() - t0) * 1000)})
    i = out.find("{")
    if i >= 0:
        try:
            return json.dumps(json.loads(out[i:]), ensure_ascii=False)
        except Exception:
            pass
    return _ok({"ok": code == 0, "output": out[-2000:]})


@mcp.tool()
def export_docx(md_path: str, out_path: str = "") -> str:
    """导出 Word（TNR 12pt 模板, 图片内嵌, 自动机械消毒）。"""
    t0 = time.time()
    p = Path(md_path)
    if not p.exists():
        return _ok({"ok": False, "error": f"文件不存在: {md_path}"})
    out = Path(out_path) if out_path else p.with_suffix(".docx")
    code, res = _run_cli(
        ["toolbox.py", "export", str(p), "--format", "docx", "--out", str(out)],
        timeout=300)
    _ledger_append({"tool": "export_docx", "file": str(p), "out": str(out),
                    "ok": code == 0 and out.exists(),
                    "ms": int((time.time() - t0) * 1000)})
    return _ok({"ok": code == 0 and out.exists(),
                "out": str(out),
                "size_kb": out.stat().st_size // 1024 if out.exists() else 0,
                "output": res[-1500:]})


# ─────────────────────────── 过程审计（防绕过） ───────────────────────────

# 后处理链工具集：稿件被直接修改后, 这些工具的台账记录证明处理链被真实执行
# integration_qc: --apply-refs 会改写 main.md, 其台账记录同样证明处理链被执行（任务18 #9）
_POST_TOOLS = {"quality_check", "mechanical_fix", "record_skill_use", "figure_render",
               "export_docx", "used_refs", "lang_check", "read_skill", "integration_qc"}


@mcp.tool()
def process_audit(project: str, grace_seconds: int = 30) -> str:
    """过程审计：比对产物修改时间与台账工具调用记录，暴露"绕过工具的直接修改"。

    规则（可执行口径：不禁止 agent 写字，但关键处理链必须走工具并留痕）：
      1. 稿件 mtime 晚于最近一次 quality_check 台账记录 → 修改未经门禁复检（P1）
      2. 稿件 mtime 晚于台账中一切后处理工具记录 → 完全绕过工具链（P0 级可疑）
      3. 活跃修改期间（近 7 天）零 record_skill_use/read_skill → 技能未经自主选择（P2 提示）
    返回 JSON 报告。投稿前 process_audit 应为 clean。"""
    t0 = time.time()
    proj = Path(project)
    main = proj / "manuscript" / "main.md"
    if not main.exists():
        return _ok({"ok": False, "error": f"无 manuscript/main.md: {project}"})
    main_mtime = main.stat().st_mtime
    entries = [e for e in _ledger_read()
               if str(proj).replace("\\\\", "\\") in str(e.get("project", ""))
               or str(e.get("file", "")).startswith(str(proj))]
    if not entries:
        # 项目未走 MCP 工具链（可能全程手动/DSH 直连）——单独口径
        _ledger_append({"tool": "process_audit", "project": str(proj), "ok": True})
        return _ok({"ok": True, "verdict": "no_ledger",
                    "msg": "台账中无该项目任何工具调用记录——若声称已按工作台流程处理, 则与台账矛盾",
                    "main_mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(main_mtime))})
    def _ts(e):
        try:
            return time.mktime(time.strptime(e["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            return 0.0
    post = [e for e in entries if e.get("tool") in _POST_TOOLS]
    last_qc = max((_ts(e) for e in post if e.get("tool") == "quality_check"), default=0.0)
    last_any = max((_ts(e) for e in post), default=0.0)
    week_ago = time.time() - 7 * 86400
    skill_uses = [e for e in entries if e.get("tool") in ("record_skill_use", "read_skill")
                  and _ts(e) >= week_ago]
    findings = []
    if main_mtime > last_any + grace_seconds:
        findings.append({
            "severity": "P0", "type": "bypassed_toolchain",
            "msg": f"main.md 最近修改({time.strftime('%m-%d %H:%M', time.localtime(main_mtime))})"
                   f"晚于台账中一切后处理工具记录——修改完全绕过工具链, 无机械修复/门禁复检/技能登记"})
    elif main_mtime > last_qc + grace_seconds:
        findings.append({
            "severity": "P1", "type": "unverified_edit",
            "msg": f"main.md 修改({time.strftime('%m-%d %H:%M', time.localtime(main_mtime))})"
                   f"晚于最近一次 quality_check——修改后未复跑质量门禁"})
    if not skill_uses and main_mtime >= week_ago:
        findings.append({
            "severity": "P2", "type": "no_skill_engagement",
            "msg": "近 7 天活跃修改但台账无任何技能阅读/使用登记——写作未经技能自主选择(阳奉阴违信号)"})
    verdict = "clean" if not findings else ("suspect" if any(
        f["severity"] == "P0" for f in findings) else "warn")
    _ledger_append({"tool": "process_audit", "project": str(proj), "ok": True,
                    "verdict": verdict, "findings": len(findings),
                    "ms": int((time.time() - t0) * 1000)})
    return _ok({
        "ok": True, "verdict": verdict,
        "main_mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(main_mtime)),
        "last_quality_check": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_qc)) if last_qc else None,
        "last_post_tool": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_any)) if last_any else None,
        "ledger_entries_total": len(entries),
        "skill_engagements_7d": len(skill_uses),
        "findings": findings,
    })


@mcp.tool()
def ledger_query(project: str = "", tool: str = "", last_n: int = 50) -> str:
    """查询工具调用台账（最近 last_n 条, 可按 project/tool 过滤）。"""
    entries = _ledger_read()
    if project:
        entries = [e for e in entries if project.replace("\\\\", "\\") in json.dumps(e, ensure_ascii=False)]
    if tool:
        entries = [e for e in entries if e.get("tool") == tool]
    _ledger_append({"tool": "ledger_query", "ok": True})
    return _ok({"ok": True, "count": len(entries[-last_n:]), "entries": entries[-last_n:]})




# ─────────────────────────── 检索与文献池 ───────────────────────────

@mcp.tool()
def literature_search(query: str, sources: str = "openalex,arxiv,crossref,pubmed", limit: int = 10) -> str:
    """多源学术检索（OpenAlex/arXiv/Crossref/PubMed），返回标题/年份/DOI/作者/出处。

    用于文献调研、找相关研究。与 web_search（网页/灰色文献）互补。
    来源: 四源自动去重合并。"""
    t0 = time.time()
    srcs = [s.strip() for s in (sources or "openalex,arxiv,crossref,pubmed").split(",") if s.strip()]
    try:
        import toolbox
        res = toolbox.search_literature(query, srcs, int(limit or 10))
        _ledger_append({"tool": "literature_search", "query": query, "sources": srcs,
                        "ok": True, "ms": int((time.time() - t0) * 1000)})
        return _ok({"ok": True, "results": res})
    except Exception as e:
        _ledger_append({"tool": "literature_search", "query": query, "ok": False})
        return _ok({"ok": False, "error": str(e)})


@mcp.tool()
def web_search(query: str, max_results: int = 8) -> str:
    """AnySearch 网页检索（学术库之外的网页/灰色文献/期刊指南/新闻）。

    与 literature_search（学术库元数据）互补。需在 app_config.json 配置
    anysearch API key（无 key 时返回配置提示, 不静默）。"""
    t0 = time.time()
    try:
        import toolbox
        res = toolbox.anysearch_search(query, int(max_results or 8))
        _ledger_append({"tool": "web_search", "query": query, "ok": True,
                        "ms": int((time.time() - t0) * 1000)})
        return _ok({"ok": True, "result": res})
    except Exception as e:
        _ledger_append({"tool": "web_search", "query": query, "ok": False})
        return _ok({"ok": False, "error": str(e)})


@mcp.tool()
def web_extract(url: str) -> str:
    """AnySearch 抓取指定 URL 全文（Markdown）。用于读取期刊作者指南页/文献全文。"""
    t0 = time.time()
    try:
        import toolbox
        res = toolbox.anysearch_extract(url)
        _ledger_append({"tool": "web_extract", "url": url, "ok": True,
                        "ms": int((time.time() - t0) * 1000)})
        return _ok({"ok": True, "result": res})
    except Exception as e:
        _ledger_append({"tool": "web_extract", "url": url, "ok": False})
        return _ok({"ok": False, "error": str(e)})


@mcp.tool()
def build_references(query: str, project: str = "", sources: str = "openalex,arxiv,crossref,pubmed",
                     limit: int = 30, min_total: int = 80) -> str:
    """检索并生成/合并参考文献 BibTeX（多源去重+DOI 核验+近 5 年优先+高相关标注）。

    project 提供时写入该项目的 framework/references.md。用于自动生成参考文献清单。"""
    t0 = time.time()
    srcs = [s.strip() for s in (sources or "openalex,arxiv,crossref,pubmed").split(",") if s.strip()]
    try:
        import toolbox
        res = toolbox.build_refs(query, srcs, int(limit or 30), int(min_total or 80),
                                 out_file=project or None)
        _ledger_append({"tool": "build_references", "query": query, "project": project,
                        "ok": True, "ms": int((time.time() - t0) * 1000)})
        return _ok({"ok": True, "result": res})
    except Exception as e:
        _ledger_append({"tool": "build_references", "query": query, "ok": False})
        return _ok({"ok": False, "error": str(e)})


@mcp.tool()
def fetch_doi(doi: str) -> str:
    """按 DOI 从 Crossref 获取文献元数据（标题/作者/年份/卷期页/期刊）。用于核验或补全参考文献。"""
    t0 = time.time()
    try:
        import toolbox
        res = toolbox.fetch_doi(doi)
        _ledger_append({"tool": "fetch_doi", "doi": doi, "ok": True,
                        "ms": int((time.time() - t0) * 1000)})
        return _ok({"ok": True, "result": res})
    except Exception as e:
        _ledger_append({"tool": "fetch_doi", "doi": doi, "ok": False})
        return _ok({"ok": False, "error": str(e)})


@mcp.tool()
def writing_brief(project: str, stage: str = "draft") -> str:
    """获取当前项目/阶段的写作规范 brief（句子纪律/引用纪律/主线锚定/字数预算）。

    agent 写作前先调用本工具拿项目级约束, 避免凭通用模板动笔。"""
    t0 = time.time()
    proj = Path(project)
    if not (proj / "state.json").exists():
        return _ok({"ok": False, "error": f"非工作台项目: {project}"})
    try:
        sys.path.insert(0, str(WB / "web"))
        import ai_client
        # 组装项目级写作约束：质量规则 + 阶段技能 + 检查点
        brief = []
        try:
            q = ai_client.load_quality_context(proj)
            if q:
                brief.append(q[:4000])
        except Exception:
            pass
        try:
            skills = ai_client.get_stage_skills(stage)
            if skills:
                brief.append("## 本阶段建议技能\\n" + "、".join(f"{n}（{d[:50]}）" for n, d in skills))
        except Exception:
            pass
        # 检查点要求
        try:
            st = json.loads((proj / "state.json").read_text(encoding="utf-8"))
            brief.append(f"## 当前阶段: {st.get('stage', '?')} / 类型: {st.get('type', '?')}")
        except Exception:
            pass
        _ledger_append({"tool": "writing_brief", "project": str(proj), "stage": stage,
                        "ok": True, "ms": int((time.time() - t0) * 1000)})
        return _ok({"ok": True, "stage": stage, "brief": "\n\n".join(brief)})
    except Exception as e:
        _ledger_append({"tool": "writing_brief", "project": str(proj), "ok": False})
        return _ok({"ok": False, "error": str(e)})

@mcp.tool()
def originality(content: str, corpus_dir: str = "") -> str:
    """本地原创性/重复度检查。corpus_dir 可选：已有语料库目录做相似度比对；缺省仅做基础检查。"""
    t0 = time.time()
    try:
        import toolbox
        res = toolbox.originality_check(content, corpus_dir or None)
        _ledger_append({"tool": "originality", "ok": True, "ms": int((time.time() - t0) * 1000)})
        return _ok({"ok": True, "result": res})
    except Exception as e:
        _ledger_append({"tool": "originality", "ok": False})
        return _ok({"ok": False, "error": str(e)})


# ─────────────────────────── 导入 ───────────────────────────

@mcp.tool()
def import_document(file: str, kind: str = "") -> str:
    """导入外部稿件为规范化 Markdown。kind=pdf/docx, 缺省按扩展名推断。

    docx: python-docx 直读(标题/表格识别); pdf: pdfplumber 直读(文本+表格+双栏)。"""
    t0 = time.time()
    p = Path(file)
    if not p.exists():
        return _ok({"ok": False, "error": f"文件不存在: {file}"})
    k = (kind or p.suffix.lower().lstrip("."))
    if k not in ("pdf", "docx"):
        return _ok({"ok": False, "error": f"不支持的格式: {k}（仅 pdf/docx）"})
    out = p.with_suffix(".md")
    code, res = _run_cli(["toolbox.py", f"import-{k}", str(p), "--out", str(out)], timeout=180)
    _ledger_append({"tool": "import_document", "file": str(p), "kind": k,
                    "ok": code == 0 and out.exists(),
                    "ms": int((time.time() - t0) * 1000)})
    return _ok({"ok": code == 0 and out.exists(), "out": str(out),
                "output": res[-1500:]})


# ─────────────────────────── 任务派发（方案 B：执行者可插拔） ───────────────────────────

@mcp.tool()
def list_executors() -> str:
    """列出可用执行者（dsh / claude / codex）及其可用性。

    dsh 后端立即可用；claude/codex 需 CLI 安装后启用（未装会标注不可用）。"""
    try:
        import runner
        exes = runner.list_executors()
        _ledger_append({"tool": "list_executors", "ok": True})
        return _ok({"ok": True, "executors": exes})
    except Exception as e:
        return _ok({"ok": False, "error": str(e)})


@mcp.tool()
def dispatch_task(goal: str, executor: str = "dsh", project: str = "", journal: str = "",
                  lang: str = "en", ptype: str = "review", timeout: int = 1800,
                  dry_run: bool = False) -> str:
    """派发写论文任务给执行者（dsh/claude/codex），八步任务书驱动（flow.md 协议）。

    生成八步任务书（确定期刊→检索→框架→文献↔章节→skill 分段写作→逻辑校验→
    拼装审核→投稿材料）→ 派发给指定执行者 → 台账留痕。
    任务书强制约束：检索/门禁/绘图/导出必须走工作台 MCP（防阳奉阴违）。
    dry_run=True 只返回任务书 prompt 不派发。"""
    t0 = time.time()
    try:
        import runner
    except Exception as e:
        return _ok({"ok": False, "error": f"runner 不可用: {e}"})
    tb = runner.build_taskbook(goal, project=project, journal=journal, lang=lang, ptype=ptype)
    prompt = runner.taskbook_prompt(tb)
    if dry_run:
        _ledger_append({"tool": "dispatch_task", "executor": executor, "goal": goal[:120],
                        "dry_run": True, "ok": True, "ms": int((time.time() - t0) * 1000)})
        return _ok({"ok": True, "dry_run": True, "taskbook": tb, "prompt": prompt})
    r = runner.dispatch(executor, prompt, cwd=project or None, timeout=int(timeout or 1800))
    _ledger_append({"tool": "dispatch_task", "executor": executor, "goal": goal[:120],
                    "ok": r.get("ok"), "ms": int((time.time() - t0) * 1000)})
    return _ok({"ok": r.get("ok"), "executor": executor, "taskbook": tb,
                "text": str(r.get("text", ""))[:4000],
                "tool_calls": len(r.get("tool_calls") or []),
                "error": r.get("error")})

# ─────────────────────────── 项目导航 ───────────────────────────
