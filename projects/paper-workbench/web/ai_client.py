#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper Workbench AI 客户端:OpenAI 兼容 /chat/completions。
配置保存在工作台根目录 app_config.json,API Key 仅本地保存。
"""
import json
import os
import re
import urllib.request
import urllib.error
from pathlib import Path

from wb import STAGE_LABEL

CONFIG_PATH = Path(__file__).resolve().parent.parent / "app_config.json"
# 推荐用环境变量提供 API Key，避免明文落盘；文件配置仍兼容旧用法
ENV_API_KEY = "PAPER_WORKBENCH_API_KEY"

# 延迟导入 dsh_bridge（与 server.py 一致，依赖 sys.path 含引擎根目录）；
# 失败时降级为只走直连 LLM，保证 ai_client 可独立使用。
_dsh_bridge = None
try:
    import dsh_bridge as _dsh_bridge  # type: ignore
except Exception:
    _dsh_bridge = None

DEFAULT_CONFIG = {
    "ai": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "",
        "model": "deepseek-chat",
        "temperature": 0.7,
        # 模型档位: flash(强约束+产出前自检表) / frontier(一等模型,只保留硬约束)
        # 读取失败或取值非法时一律回退 flash（安全侧），见 _current_tier()
        "tier": "flash",
        # True: DSH 在线时优先委托给 Agent 执行（默认，可利用 Agent 技能系统深度生成）
        # False: 始终走直连 LLM
        "use_dsh_delegate": True,
        # 委托超时秒数；Agent 执行通常比直连慢，给宽松一点
        "dsh_delegate_timeout": 600,
    },
    "skills": {"disabled": []},
}


def load_config():
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            # 合并默认值
            for k, v in DEFAULT_CONFIG.items():
                cfg.setdefault(k, v)
            if isinstance(cfg.get("ai"), dict):
                for k, v in DEFAULT_CONFIG["ai"].items():
                    cfg["ai"].setdefault(k, v)
            return cfg
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULT_CONFIG))


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _current_tier():
    """读取 ai.tier：合法取值 flash / frontier；读取失败或取值非法时回退 flash（安全侧）。"""
    try:
        tier = str(load_config().get("ai", {}).get("tier", "flash")).strip().lower()
    except Exception:
        return "flash"
    return tier if tier in ("flash", "frontier") else "flash"


def get_ai_settings():
    cfg = load_config()
    ai = cfg.get("ai", {})
    return {
        "base_url": ai.get("base_url", DEFAULT_CONFIG["ai"]["base_url"]),
        "model": ai.get("model", DEFAULT_CONFIG["ai"]["model"]),
        "temperature": ai.get("temperature", DEFAULT_CONFIG["ai"]["temperature"]),
        "api_key_set": bool(ai.get("api_key")) or bool(os.environ.get(ENV_API_KEY, "").strip()),
        "api_key_env": bool(os.environ.get(ENV_API_KEY, "").strip()),
        "use_dsh_delegate": bool(ai.get("use_dsh_delegate", True)),
        "dsh_delegate_timeout": int(ai.get("dsh_delegate_timeout", 600) or 600),
        "dsh_available": _dsh_bridge.is_available() if _dsh_bridge else False,
    }


def save_ai_settings(base_url=None, api_key=None, model=None, temperature=None,
                 use_dsh_delegate=None, dsh_delegate_timeout=None):
    cfg = load_config()
    ai = cfg.setdefault("ai", {})
    if base_url is not None:
        ai["base_url"] = str(base_url).strip()
    if api_key:  # 留空表示不修改
        ai["api_key"] = str(api_key).strip()
    if model is not None:
        ai["model"] = str(model).strip()
    if temperature is not None:
        try:
            ai["temperature"] = float(temperature)
        except (TypeError, ValueError):
            pass
    if use_dsh_delegate is not None:
        ai["use_dsh_delegate"] = bool(use_dsh_delegate)
    if dsh_delegate_timeout is not None:
        try:
            ai["dsh_delegate_timeout"] = max(60, int(dsh_delegate_timeout))
        except (TypeError, ValueError):
            pass
    save_config(cfg)
    return get_ai_settings()


def chat(messages, temperature=None):
    """调用 AI 返回回复文本。无 API Key 时若开启委托且 DSH 在线,改走 harness 深度链接。"""
    cfg = load_config()
    ai = cfg.get("ai", {})
    base_url = (ai.get("base_url") or "").strip().rstrip("/")
    api_key = (ai.get("api_key") or "").strip()
    if not api_key:
        # 环境变量优先（避免 API Key 明文落盘）
        api_key = os.environ.get(ENV_API_KEY, "").strip()
    model = (ai.get("model") or "").strip()
    # 无 API Key 时,若开启委托且 DSH 在线,改走 harness(与 generate_artifact 一致),避免直接报错。
    # 有 API Key 时保持直连,保留多轮上下文。
    if (not api_key) and bool(ai.get("use_dsh_delegate", True)) and _dsh_bridge is not None:
        try:
            if _dsh_bridge.is_available():
                instruction = ""
                for m in reversed(messages):
                    if m.get("role") == "user":
                        instruction = str(m.get("content", ""))
                        break
                if not instruction:
                    instruction = "\n".join(str(m.get("content", "")) for m in messages)
                if instruction.strip():
                    timeout = int(ai.get("dsh_delegate_timeout", 600) or 600)
                    result = _dsh_bridge.delegate_task(
                        instruction, timeout=timeout,
                    )
                    text = (result or {}).get("text", "").strip()
                    if text and "(超时" not in text and not text.startswith("DSH 错误"):
                        return _strip_code_fence(text)
        except Exception:
            pass  # 委托失败则回退直连
    if not base_url or not api_key or not model:
        raise RuntimeError("AI 未配置完整:请先在“AI 助手”里填写 Base URL / API Key / Model（或设置环境变量 PAPER_WORKBENCH_API_KEY）")
    if temperature is None:
        temperature = ai.get("temperature", 0.7)
    url = base_url + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(temperature),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"AI API HTTP {e.code}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"AI API 请求失败: {e}") from e
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"AI API 返回格式异常: {data}") from e


# ── 工具调用（函数调用式 Agent）──────────────────────────

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "literature_search",
        "description": "多源检索学术文献(OpenAlex/arXiv/Crossref/PubMed)，返回标题/年份/DOI/作者/出处。用于文献调研、找相关研究。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索词/研究方向"},
            "sources": {"type": "array", "items": {"type": "string"}, "description": "来源，默认 openalex,arxiv,crossref"},
            "limit": {"type": "integer", "description": "每个来源数量，默认 10"}
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "build_references",
        "description": "检索并生成/合并参考文献 BibTeX(多源去重+DOI 核验+近 5 年优先+高相关标注)，可写入项目 framework/references.md。用于自动生成参考文献清单。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "研究方向/关键词"},
            "project": {"type": "string", "description": "论文项目目录（可选，提供则写入项目 references.md）"},
            "limit": {"type": "integer", "description": "每个来源数量，默认 30"},
            "min_total": {"type": "integer", "description": "目标最少条数，默认 80"}
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "quality_check",
        "description": "对论文项目跑质量门禁，返回 P0/P1/P2 问题清单与 0-100 打分卡(≥90 可投/70-89 小修/50-69 大修/<50 不可投)。用于审查稿件是否可投。",
        "parameters": {"type": "object", "properties": {
            "project": {"type": "string", "description": "论文项目目录（必须，如 C:\\Users\\...\\papers\\xxx）"}
        }, "required": ["project"]}}},
    {"type": "function", "function": {
        "name": "audit_statistics",
        "description": "审计稿件文本的统计报告：均值缺 SD、p 值缺检验方法。返回问题行号与描述。",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "稿件正文/Results 段落文本"}
        }, "required": ["content"]}}},
    {"type": "function", "function": {
        "name": "fetch_doi",
        "description": "按 DOI 从 Crossref 获取文献元数据(标题/作者/年份/卷期页/期刊)。用于核验或补全参考文献。",
        "parameters": {"type": "object", "properties": {
            "doi": {"type": "string", "description": "DOI，如 10.1038/nature14539"}
        }, "required": ["doi"]}}},
    {"type": "function", "function": {
        "name": "data_stats",
        "description": "统计 CSV 数据各数值列的 n/均值/最小/最大/标准差。用于快速了解实验数据。",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string", "description": "CSV 文本（首行表头）"}
        }, "required": ["content"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "用 AnySearch 做网页检索（学术库之外的网页/灰色文献/期刊指南/新闻）。返回标题/URL/摘要。用于查期刊作者指南、最新进展、灰色文献。与 literature_search（学术库元数据）互补。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "检索词"},
            "max_results": {"type": "integer", "description": "结果数，默认 8"}
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "web_extract",
        "description": "用 AnySearch 抓取指定 URL 的全文（Markdown）。用于读取期刊指南页/文献全文。",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "要抓取的网页 URL"}
        }, "required": ["url"]}}},
]


def _run_tool(name, args):
    """执行工具调用，返回可读字符串结果（限制长度防刷屏）。"""
    try:
        import toolbox
    except Exception:
        return "工具执行失败：无法加载 toolbox"
    try:
        if name == "literature_search":
            res = toolbox.search_literature(
                str(args.get("query", "")),
                [s for s in (args.get("sources") or ["openalex", "arxiv", "crossref"]) if isinstance(s, str)],
                int(args.get("limit") or 10),
            )
            return json.dumps(res, ensure_ascii=False)[:6000]
        if name == "build_references":
            res = toolbox.build_refs(
                str(args.get("query", "")),
                [s for s in (args.get("sources") or ["openalex", "arxiv", "crossref", "pubmed"]) if isinstance(s, str)],
                int(args.get("limit") or 30),
                int(args.get("min_total") or 80),
                out_file=str(args["project"]) if args.get("project") else None,
            )
            return json.dumps(res, ensure_ascii=False)[:4000]
        if name == "quality_check":
            issues = toolbox.quality_check(str(args.get("project", "")))
            score = toolbox.quality_score(issues)
            return json.dumps({"issues": issues[:20], "score": score}, ensure_ascii=False)[:8000]
        if name == "audit_statistics":
            return json.dumps(toolbox.audit_stats(str(args.get("content", ""))), ensure_ascii=False)[:4000]
        if name == "fetch_doi":
            return json.dumps(toolbox.fetch_doi(str(args.get("doi", ""))), ensure_ascii=False)[:4000]
        if name == "data_stats":
            return json.dumps(toolbox.stats_csv(str(args.get("content", ""))), ensure_ascii=False)[:4000]
        if name == "web_search":
            return toolbox.anysearch_search(str(args.get("query", "")), int(args.get("max_results") or 8))[:6000]
        if name == "web_extract":
            return toolbox.anysearch_extract(str(args.get("url", "")))[:8000]
        return "未知工具: " + name
    except Exception as e:
        return f"工具 {name} 执行失败: {e}"


def chat_with_tools(messages, temperature=None, max_rounds=6):
    """工具调用式对话：模型可请求调用工具箱(检索/门禁/统计/DOI)，执行后回传结果继续。

    返回 (最终回复, 工具调用记录 trace)。不支持 tools 的端点会回退为普通对话。
    """
    cfg = load_config()
    ai = cfg.get("ai", {})
    base_url = (ai.get("base_url") or "").strip().rstrip("/")
    api_key = (ai.get("api_key") or "").strip()
    if not api_key:
        api_key = os.environ.get(ENV_API_KEY, "").strip()
    model = (ai.get("model") or "").strip()
    if not base_url or not api_key or not model:
        raise RuntimeError("AI 未配置完整:请先在“AI 助手”里填写 Base URL / API Key / Model（或设置环境变量 PAPER_WORKBENCH_API_KEY）")
    if temperature is None:
        temperature = ai.get("temperature", 0.7)
    url = base_url + "/chat/completions"
    trace = []
    msgs = [dict(m) for m in messages]
    for _ in range(max_rounds):
        payload = {"model": model, "messages": msgs, "temperature": float(temperature), "tools": TOOL_SCHEMAS}
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:800]
            # 端点不支持 tools 时降级为普通对话
            if e.code in (400, 422):
                return chat(messages, temperature), []
            raise RuntimeError(f"AI API HTTP {e.code}: {body}") from e
        except Exception as e:
            raise RuntimeError(f"AI API 请求失败: {e}") from e
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"AI API 返回格式异常: {data}") from e
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return (msg.get("content") or "").strip(), trace
        msgs.append(msg)
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            result = _run_tool(name, args)
            trace.append({"tool": name, "args": str(args)[:160], "result": result[:120]})
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})
    return "（工具调用轮次过多，已停止。可缩小问题范围后重试。）", trace


def get_enabled_skill_names(limit=40):
    """返回 ~/.dsh/skills 下未被禁用的技能名,供 prompt 作上下文。"""
    cfg = load_config()
    disabled = set(cfg.get("skills", {}).get("disabled", []))
    skills_root = Path.home() / ".dsh" / "skills"
    names = []
    if skills_root.exists():
        for d in sorted(skills_root.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists() and d.name not in disabled:
                names.append(d.name)
    return names[:limit]


# ── 阶段→技能白名单（2026-08-23 改造一：替代字母序前 40 的盲注入） ──
STAGE_SKILLS = {
    "research": ["academic-researcher", "nature-academic-search", "nature-downloader"],
    "journal": ["add-venue-profile", "tailor-to-venue"],
    "framework": ["write-scientific-manuscript", "scientific-writing", "paper-staged-gen"],
    "draft": ["write-scientific-manuscript", "scientific-writing", "nature-writing",
              "paper-figure-routing", "figure-origin"],
    "review": ["nature-reviewer", "simulate-reviewers", "nature-polishing", "polish-prose",
               "verify-citations", "nature-ref-verifier", "check-originality",
               "stats-reporting-audit"],
    "submit": ["sci-submission", "tailor-to-venue", "submission-audit"],
}


def _skill_description(name, limit=120):
    """解析技能 SKILL.md frontmatter 的 description（一句话,截断）。失败返回空串。"""
    p = Path.home() / ".dsh" / "skills" / name / "SKILL.md"
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    m = re.search(r"description:\s*(?:>[-|]?\s*\n((?:\s+.*\n?)+)|description:\s*(.+))", raw)
    if not m:
        return ""
    desc = " ".join((m.group(1) or m.group(2) or "").split())
    return desc[:limit]


def get_stage_skills(stage):
    """返回阶段建议技能 [(名称, 描述)]；技能不存在/被禁用时静默跳过。"""
    cfg = load_config()
    disabled = set(cfg.get("skills", {}).get("disabled", []))
    skills_root = Path.home() / ".dsh" / "skills"
    out = []
    for name in STAGE_SKILLS.get(stage, []):
        if name in disabled or not (skills_root / name / "SKILL.md").exists():
            continue
        out.append((name, _skill_description(name)))
    return out


# ── 质量上下文注入：写作准则 + 目标期刊要求 ──────────────────
# 引擎根目录（workbench/），quality_rules.md 与之同住
_ENGINE_ROOT = Path(__file__).resolve().parent.parent


def _read_truncated(path, limit=6000):
    try:
        p = Path(path)
        if not p.exists():
            return ""
        t = p.read_text(encoding="utf-8", errors="replace")
        return t[:limit] + ("\n…(截断)" if len(t) > limit else "")
    except Exception:
        return ""


def _chosen_is_empty(text):
    """chosen.md 未真正填写期刊要求时视为空。
    判据：含 ≥3 个 ___ 占位符（模板未填），或去掉结构符号后实质内容过短。"""
    if not text or not text.strip():
        return True
    if text.count("___") >= 3:
        return True
    body = re.sub(r"[#>\-\s|：:]", "", text)
    return len(body) < 30


def load_quality_context(project_path):
    """拼装注入生成 prompt 的质量上下文：写作准则 + 期刊要求 + 缺失提醒。"""
    parts = []
    rules = _read_truncated(_ENGINE_ROOT / "quality_rules.md", 6000)
    if rules.strip():
        parts.append("## 写作质量准则（必须遵守，产出前逐条自查）\n" + rules)
    chosen = ""
    if project_path:
        chosen = _read_truncated(Path(project_path) / "journal" / "chosen.md", 4000)
    if chosen.strip() and not _chosen_is_empty(chosen):
        parts.append("## 目标期刊硬性要求（格式/篇幅/结构/参考文献/图表规范，产出必须逐条符合）\n" + chosen)
    else:
        parts.append(
            "## 期刊要求缺失警告\n"
            "journal/chosen.md 尚未填写目标期刊的真实要求（篇幅/结构/参考文献格式/图表规范）。\n"
            "在 journal 阶段必须先检索并填写 chosen.md，再进入写作；否则格式无法达标。\n"
            "若本次已是写作阶段而 chosen.md 仍为空，请在产出顶部显式标注「⚠ 期刊格式未校验」。"
        )
    return "\n\n".join(parts)


def build_stage_prompt(topic, journal, stage, stage_label, checklist, artifacts, project_context="", project_path=None):
    skill_names = get_enabled_skill_names()
    lines = [
        "你是一个学术论文全流程写作助手。请根据下面的项目信息和当前阶段任务,直接输出可写入产物的 Markdown 内容。",
        "要求:内容具体、可执行、不编造文献/数据;若需要引用请用占位符并标注待核验。",
        "",
        f"研究方向: {topic or '(未提供)'}",
        f"目标期刊/会议: {journal or '(未定,自动推荐)'}",
        f"当前阶段: {stage_label} ({stage})",
        "",
        "## 本阶段检查清单",
    ]
    for i, item in enumerate(checklist, 1):
        lines.append(f"{i}. {item}")

    # 阶段级硬性要求：保证框架阶段预留足够图表，写作阶段预留可替换占位符
    if stage == "research":
        lines.append("")
        lines.append("## 检索阶段工具指引（把文献检索做实）")
        lines.append("- 学术库元数据（标题/DOI/作者/出处）用 literature_search（OpenAlex/arXiv/Crossref/PubMed）。")
        lines.append("- 期刊作者指南、最新进展、灰色文献、行业网页用 web_search（AnySearch 网页检索）；")
        lines.append("  拿到关键网页后用 web_extract 读全文，尤其目标期刊的投稿指南（为 journal 阶段的 chosen.md 备料）。")
        lines.append("- 每条关键结论须落到可核验来源（DOI 或 URL），禁止凭记忆编造文献。")
    if stage == "journal":
        lines.append("")
        lines.append("## 期刊阶段硬性要求（格式达标的源头，必须做实）")
        lines.append("- 必须检索目标期刊的**官方作者指南**，把 journal/chosen.md 的每一项空白填满，禁止留 ___：")
        lines.append("  ①选定期刊全名；②文章类型；③篇幅约束（正文字数/页数、图表上限、参考文献上限）；")
        lines.append("  ④结构要求（必需章节及顺序）；⑤参考文献格式（编号/作者-年份、样式名）；")
        lines.append("  ⑥图表规范（尺寸/dpi/格式/色盲安全/图注要求）；⑦伦理与数据可用性要求。")
        lines.append("- 给出 3-5 篇该期刊近期范文，逐篇拆解章节组织/引言模式/结果呈现/讨论结构，写入 chosen.md。")
        lines.append("- 若检索不到某项，明确写「未公开，按保守默认: …」而不是留空，确保后续阶段有规可依。")
        lines.append("- 本阶段产出直接决定 draft 的格式与 figures.md 的图表规范，务必具体可执行。")
    if stage == "framework":
        lines.append("")
        lines.append("## 框架阶段硬性要求")
        lines.append("- framework/figures.md 必须规划至少 5 张图 + 3 张表，不能只写 1-2 张。")
        lines.append("- 每一行都必须填写：编号 / 类型 / 内容 / 本图回答的 Claims / 数据来源（建议写 data/xxx.csv）/ 对应章节 / 期刊规范要求。")
        lines.append("- 表格还必须含三列：**绘制**（取 自动判定|自动绘图|生图|文字图注，可先全填『自动判定』由工作台复核）、**关键视觉**（拟走生图的图必填：一句话写明构图/元素/风格）、**兜底**（固定填『文字图注』）。")
        lines.append("- 一图一主消息：每张图只回答一个 Claims，不混无关结果；表头标指标方向（PSNR ↑）+ 单位 + n + 显著性。")
        lines.append("- outline.md 的 Results 每个小节必须标注对应证据（图 X / 表 Y），与 figures.md 一致。")
        lines.append("")
        lines.append("## 图表理解（按数据形态选对图，写进 figures.md 的『期刊规范要求』列）")
        lines.append("- 连续变量单组分布 → 箱线图/直方图；两组/多组比较 → 箱线+散点叠加（标注 n、误差棒、检验与 p）。")
        lines.append("- 两个连续变量关系 → 散点图（配回归线仅在机制成立时）；时间/剂量序列 → 折线图。")
        lines.append("- 类别计数/占比 → 条形图（有序则排序），避免饼图；多因素 → 分组条形或热力图。")
        lines.append("- 每个 figures.md 行必须写明：用哪种图、为什么这种图最能回答该 Claim、x/y 轴与单位、统计标注方式。")
        lines.append("- 图表规范遵循 chosen.md：尺寸/dpi/格式/色盲安全色板；图注须自成一体（定义 panel/组/单位/n/误差棒/显著性/缩写）。")
        lines.append("- 禁止装饰性图表（3D、阴影、冗余网格）；数据墨水比优先，审稿人一眼可读。")
        lines.append("- 绘图路线三判定：①有真实数据源（data/ 下 CSV/XLSX）→ 自动绘图（工作台代码生成）；②无数据但属概念/框架/机制示意图且能用一句话写清关键视觉 → 生图（第三方生图 API，未配置时自动降级文字图注）；③其余 → 文字图注（结构化描述）。SPSS 等桌面软件不参与绘图（无可靠自动化接口）。")
        lines.append("- 数据图必须在表格中写明数据来源文件（data/xxx.csv）；每图仍遵守一图一主消息。")
        lines.append("")
        lines.append("## Contribution-First 五问(PaperSpine 铁律，写 outline 前先答)")
        lines.append('- ①主导贡献是什么：一句、具体、可被证伪（本领域缺Y，本文补上，而不是我们做了X）。',)
        lines.append('- ②领域具体缺口在哪：点名缺哪一格的洞、它为何仍开着（不是X研究不足）。',)
        lines.append('- ③审稿人要相信该贡献需要什么证据：先于检查你有什么。',)
        lines.append("- ④证据缺口是什么：把③减②的差距写进 framework/contribution.md 的缺失证据；非空必须软化声称。")
        lines.append("- ⑤声称边界到哪：哪些不能 claim，写进强声称允许/软化或避免两格。")
        lines.append("- 必须生成 framework/contribution.md（四节字段全填，禁止 TODO/待定）；以及 framework/results-validation.md（每个贡献对应一个 Results 小节）。")
        lines.append("")
        lines.append("## 框架阶段叙事要求（讲好一个故事）")
        lines.append("- 先写「一句话论证」: In [系统/问题], we show [推进] using [方法], supported by [证据], with [边界]。全文每个部分都必须服务这句话。")
        lines.append("- 建立「术语账本」: 首次接触材料时锁定术语规范形式与缩写，全文统一，禁止别名漂移。")
        lines.append("- outline 每节标注「故事任务」：引言=漏斗（大问题→具体缺口→贡献预告）、Results 每小节=讲哪个证据/回答什么问题、Discussion=回扣哪些贡献。")
        lines.append("- 段落任务映射: 每段只做一件事（背景/缺口/方法/结果/对比/机制/意义/局限），一段两职先拆段。")
        lines.append("- 在 outline 里先起草摘要 5 句故事线：问题 1 句 → 缺口 1 句 → 方法 2 句 → 结果 2 句 → 意义 1 句。")
        lines.append("- 填写 framework/writing-rationale.md 写作理由矩阵（≥8 行，首行全篇框架）：每个写作单元的动机/参考模式/证据锚点/计划动作/最终检查。")
    elif stage == "draft":
        lines.append("")
        lines.append("## 写作阶段硬性要求")
        lines.append("- Results 每个小节必须写 `[TBD: ... Planned output: Figure X and Table Y.]` 占位，供 data2paper.py 后续自动替换。")
        lines.append("- 正文中不要手写死图表路径；图表由 data2paper.py 按 figures.md 规划自动生成并插入。")
        lines.append("- 每个 [TBD] 都要尽量具体，注明缺的是哪份数据（与 framework/figures.md 的数据来源对应）。")
        lines.append("")
        lines.append("## 叙事结构（讲好一个故事——最高优先级）")
        lines.append("- 摘要=故事预告：问题→方法→结果→意义 四要素俱全，脱离正文可读，不堆术语。")
        lines.append("- 引言=漏斗：大问题→具体缺口（现有方法为什么不够）→本文贡献（显式列出）→路线图；读者应能在引言末段知道「这篇文章要干什么」。")
        lines.append("- Results=正片：每节先交代目的→报告结果（带数字/统计/图表引用）→一句解释；证据跟着叙述走，图表编号与正文一一对应。")
        lines.append("- Results 是承诺法庭: 每个 Results 小节先写指针句「本小节测试引言承诺 [X]，用证据 [Y]，支持到 [强度]，但不声称多于 [边界]」，对照 framework/results-validation.md 逐小节省。")
        lines.append("- 每个贡献须被测试: 若有贡献(C#)没有对应 Results 小节，要么补实验/补证据，要么把声称降为不承诺（贡献不得悬空）。")
        lines.append("- Discussion=影评：先讲最重要发现→与已有工作对比（consistent with / 与……一致）→机制解释→局限→具体意义；不要重复 Results 数字。")
        lines.append("- 一段一事：每段主题句先行；段落间用内容衔接，禁止 Moreover/Furthermore/此外 堆砌。")
        lines.append("- 每个 Claim 必须能指到证据（图/表/数据/文献）；观察与解释分开：show（直接证据）vs suggest（解释/推测）。")
        lines.append("- 动词校准: 强证据用 show/demonstrate，趋势级证据用 suggest/indicate，未证实机制用 may/could；禁止 first/unique/unprecedented/revolutionary 等无据通用声称。")
        lines.append("- 术语统一: 遵守框架阶段锁定的术语账本，规范形式全文一致，禁止引入账本外的别名。")
        lines.append("- 第四面墙: 正文绝不出现写作过程语言（「针对审稿意见」「重新组织」「初稿」「导师反馈」等）——直接以动机陈述呈现。")
        lines.append("- 逐段修订而非整篇重写: 用户指出问题时只改被点名的段落/声称，其余保持原样；涉及结构变动先确认。")
        lines.append("")
        lines.append("## 分节写作要点")
        lines.append("- 引言: 首段直接立大问题（1-2 句内给出任务/应用背景，不要空转）；技术挑战链收口——链尾的局限 = 本文方案要解决的挑战，禁止「naive 方案再改进」的俗套。")
        lines.append("- Method: 每个方法模块按 动机(Motivation)→机制(Mechanism)→证据(Evidence) 三要素组织。")
        lines.append("- Results: 每小节以 To test X, we Y / 为验证 X，我们… 开头交代目的。")
        lines.append("- Conclusion: 四件套（总结贡献→结果回扣→局限→展望），不引新材料。")
        lines.append("- 语言: 句长 10-30 词为主；禁止连续 3 句同首词；em dash 每段最多 1 个；避免 AI 味连接词堆叠。")
        lines.append("")
        lines.append("## 句子纪律（产出前自查）")
        lines.append("- 单句 ≤35 词，一句只做一个论断；产出前自查并消除连续标点 `..`、`,,`、`;;`。")
        lines.append("- 摘要 ≤250 词且问题/缺口/方法/结果/意义五要素俱全；禁止双句号与超长句。")
        lines.append("")
        lines.append("## 主线锚定")
        lines.append("- 每个小节首句必须声明本节与主题物种/研究主线的关系。")
        lines.append('- 外围/近缘物种文献必须用桥接句引入（如 "Studies on the related species X suggest..."），禁止无桥接直接引用外围文献。')
        lines.append("- 偏离主线的内容宁可不写。")
        lines.append("")
        lines.append("## 引用纪律")
        lines.append("- 全文引文风格必须唯一（以 journal/chosen.md 的期刊要求为准），禁止 `[1][2]` 与 `[1,2]` 两种风格混用。")
        lines.append("- 每个 `[n]` 所在句必须陈述该文献实际证明的具体内容，禁止空挂引文。")
        lines.append("- 只允许引用 research/framework 阶段已核验的文献编号，禁止虚构新编号；年份与卷期之间用单一分隔符，禁止 `;;`。")
        lines.append("")
        lines.append("## 章节归属规则（综述文体）")
        lines.append("- 代谢产物/共产品类内容（如 kojic acid、xylitol、有机酸等）必须归入「超越主产品/综合价值链」类主题小节，禁止塞入酶制剂/工具/方法类章节。")
        lines.append('- GRAS、安全性类声称必须带限定语与证据边界（如 "is generally regarded as safe under defined usage conditions [n]"），禁止无限定的绝对安全表述。')
    elif stage == "review":
        lines.append("")
        lines.append("## 审查阶段要求")
        lines.append("- 逐段修订: 只改被点名的段落/声称，其余保持原样；结构变动先确认再动手。")
        lines.append("- 术语账本保持稳定: 修订不得重新引入已锁定的术语别名。")
        lines.append("- 修订后只重跑与改动相关的检查（引用/统计/叙事），不整篇重来。")
        lines.append("- 若用户反馈揭示前提错误，回到框架阶段重新确认「一句话论证」，不要在错误前提上打补丁。")
        lines.append("")
        lines.append("## 三维独立审稿(PaperSpine 审稿人视角)")
        lines.append("- 用三个互相隔离的视角评估，各自给 evidence_status（supported/missing/weak/n/a）+ 具体 revision command，不只给结论：")
        lines.append("  ①Methods & Reproducibility Reviewer——技术可靠性/证据充分性/可复现/缺消融与基线；")
        lines.append("  ②Contribution & Novelty Reviewer——新颖性/显著性/与前作差异/引用可信度；")
        lines.append("  ③Structure & Clarity Reviewer——结构/声称清晰度/图表可读/期刊惯例。")
        lines.append("- 每个声称问「支持/反对它的证据在哪儿」；把 CRITICAL/MAJOR 发现转成可执行的反对登记(问题+预先修复+状态)。")
        lines.append("- 修订不得为讨好审稿人而夸大：只按 evidence_status 调整声称强度，不制造新证据。")
        lines.append("- P0/P1 结论必须来自 `python toolbox.py quality-check` / review-auto 等工具的真实输出，禁止自述通过（教训见 lessons.md 2026-08-20）。")

    if stage in ("research", "framework", "draft"):
        lines.append("")
        lines.append("## 参考文献硬性要求")
        lines.append("- 参考文献总数不少于 80 条；目标期刊要求更高时按期刊执行（常见 80-120 条）。")
        lines.append("- 近 5 年文献占比不低于 40%，优先纳入 2022 年及以后的文献。")
        lines.append("- 所有文献必须来自真实检索/DOI 核验，禁止编造；framework/references.md 用 BibTeX 保存。")

    stage_skills = get_stage_skills(stage)
    if stage_skills:
        lines.append("")
        lines.append("## 本阶段建议技能（位于 ~/.dsh/skills/<name>/SKILL.md；与当前产物相关时应先阅读并遵循，不适用可跳过）")
        for nm, desc in stage_skills:
            lines.append(f"- {nm}" + (f"：{desc}" if desc else ""))

    # 注入写作质量准则 + 目标期刊要求（写作/格式/图表达标的关键）
    qc = load_quality_context(project_path)
    if qc.strip():
        lines.append("")
        lines.append(qc)

    lines.append("")
    lines.append("## 需要生成/完善的产物文件")
    for a in artifacts:
        lines.append(f"- {a}")
    lines.append("")
    if project_context:
        lines.append("## 项目已有内容(供参考)")
        lines.append(project_context[:6000])
        lines.append("")
    # 档位分叉: flash 档追加产出前自检表指令；frontier 档保留一等模型发挥空间，硬约束照常
    if _current_tier() == "flash":
        lines.append("## 产出前自检表(flash 档)")
        lines.append("- 输出前在内部逐项对照本文全部硬性要求与写作质量准则自查；未通过项先自行修正再输出。")
        lines.append("- 最终正文中不得包含自检表本身，只输出成品。")
        lines.append("")
    lines.append("请直接输出 Markdown,不要解释过程。")
    return "\n".join(lines)


def _strip_code_fence(text):
    """若 Agent 回复整体被 ```markdown ... ``` 包裹，剥掉围栏只留正文。"""
    t = text.strip()
    if t.startswith("```"):
        # 去掉首行围栏
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1:]
        # 去掉末尾围栏
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def generate_artifact(project_path, stage, topic, journal, checklist, artifacts, existing_text=""):
    """调用 AI 为当前阶段生成首个缺失产物的内容。

    默认策略：若 DSH Agent 在线且配置开启（ai.use_dsh_delegate=True），
    优先委托给 DSH Agent 执行——Agent 可读取项目文件、调用 paper-workbench /
    paper-pipeline 等技能系统，生成深度优于直连 LLM。任何失败/超时回退到 chat()。
    """
    stage_label = STAGE_LABEL.get(stage, stage)
    context = ""
    intake_path = Path(project_path) / "intake.md"
    if intake_path.exists():
        try:
            intake_text = intake_path.read_text(encoding="utf-8", errors="replace")
            if intake_text.strip():
                context += "## 项目信息收集（用户填写，必须优先遵循）\n" + intake_text[:8000] + "\n\n"
        except Exception:
            pass
    if existing_text:
        context += "## 已有内容（供参考/续写）\n" + existing_text[:6000]
    # 质量规则注入：把 quality_rules.md + chosen.md（load_quality_context）并入上下文，
    # 让从 GitHub 下载、提炼进 quality_rules 的写作/审稿规则真正生效（2026-08-22）。
    try:
        _qctx = load_quality_context(project_path)
        if _qctx:
            context += "\n" + _qctx + "\n\n"
    except Exception:
        pass
    prompt = build_stage_prompt(topic, journal, stage, stage_label, checklist, artifacts, context, project_path=project_path)

    # 1) 尝试 DSH 委托
    cfg = load_config()
    ai_cfg = cfg.get("ai", {})
    use_delegate = bool(ai_cfg.get("use_dsh_delegate", True))
    timeout = int(ai_cfg.get("dsh_delegate_timeout", 600) or 600)
    if use_delegate and _dsh_bridge is not None:
        try:
            if _dsh_bridge.is_available():
                instruction = (
                    "【论文工作台委托执行】你是被论文工作台调用的 Agent。"
                    "请直接输出可写入产物的 Markdown 内容，不要任何解释或前后缀。\n\n"
                    + prompt
                )
                result = _dsh_bridge.delegate_task(
                    instruction, cwd=str(project_path), timeout=timeout,
                )
                text = (result or {}).get("text", "").strip()
                if text and "(超时" not in text and not text.startswith("DSH 错误"):
                    return _strip_code_fence(text)
                # 否则继续回退到直连 LLM
        except Exception:
            pass  # 任何异常都回退

    # 2) 回退：直连 LLM
    reply = chat([{"role": "user", "content": prompt}])
    return reply.strip()
