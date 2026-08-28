# -*- coding: utf-8 -*-
"""skill_channel.py — 任务卡技能引用通道（技能引用层，增强）。

理念：任务卡的写作纪律目前全部内联（保底，所有执行者通用）；本模块在其上
加一层「技能引用」：按执行者能力注入技能引用区块——
  - 有 skill 体系的执行者（dsh, native）：指示用 read_skill 加载完整技能协议;
  - 无 skill 体系但能读文件的执行者（workbuddy/codex, file_read）：指示直接
    读技能文件并遵循;
  - 其他/未知执行者（none）：不注入（内联纪律兜底，任务卡行为与旧路径一致）。
内联是地板，技能引用是天花板，两者共存；技能加载失败不阻塞写作（回落内联纪律）。

技能文件随工作台 vendor 于 skills/<name>/SKILL.md（原样保留，不改写）。

另提供契约执行者列「执行者:模型」语法（如 workbuddy:glm-5.2）的解析共享小
函数：派发路由只用执行者部分；模型部分暂记入 dispatch-log 的 model 字段
（模型透传需改 runner.py，后续项）。
"""
from __future__ import annotations

from pathlib import Path

ENGINE = Path(__file__).resolve().parent
# vendor 技能根目录（相对工作台的相对路径用于任务卡展示）
SKILLS_DIR = ENGINE / "skills"

# ─────────────────────────── 执行者能力分级 ───────────────────────────

# 执行者 → 技能加载能力分级:
#   native    有 skill 体系（指示用 read_skill 加载完整技能协议）
#   file_read 无 skill 体系但可读文件（指示读技能文件并遵循）
#   none      其他/未知（不注入技能区块，内联纪律兜底）
# 不在表中的执行者一律按 none 处理。
SKILL_REGISTRY = {
    "dsh": "native",
    "workbuddy": "file_read",
    "codex": "file_read",
}

# ─────────────────────────── 章节类型 → 技能映射 ───────────────────────────

# 章节类型 → 技能名列表。类型判定口径与 staged_gen._methodology_for 一致
# （introduction/methods/results/discussion/general）。
# paper-staged-gen 为分批生成编排纪律（引文池硬约束/表格只产数据/摘要后置），
# 与所有段级任务卡场景匹配，故各类型共同引用。
SECTION_SKILLS = {
    "introduction": ["scientific-writing", "paper-staged-gen"],
    "methods": ["scientific-writing", "paper-staged-gen"],
    "results": ["write-scientific-manuscript", "paper-staged-gen"],
    "discussion": ["write-scientific-manuscript", "paper-staged-gen"],
    "general": ["scientific-writing", "paper-staged-gen"],
}

# 各能力的加载指示措辞（{name}/{path} 占位）
_LOAD_HINT = {
    "native": "用 read_skill 加载技能 {name}（或按其协议读取 {path}）并遵循",
    "file_read": "读取技能文件 {path} 并遵循其中纪律",
}


# ─────────────────────────── 章节类型判定 ───────────────────────────

def section_type(section):
    """按章节标题/段号推断章节类型，口径与 staged_gen._methodology_for 一致。

    返回 introduction/methods/results/discussion/general 之一。
    """
    title = (section.get("title") or "").lower()
    sid = (section.get("sid") or "").lower()
    if any(w in title or w in sid for w in ("result", "results", "数据", "结果")):
        return "results"
    if any(w in title or w in sid for w in ("discussion", "讨论")):
        return "discussion"
    if any(w in title or w in sid for w in ("method", "methods", "材料", "方法")):
        return "methods"
    if any(w in title or w in sid for w in ("introduction", "intro", "引言")):
        return "introduction"
    return "general"


def executor_capability(executor):
    """执行者 → 技能加载能力分级（未登记的执行者一律 none）。"""
    return SKILL_REGISTRY.get(str(executor or "").strip().lower(), "none")


def skill_path(name):
    """技能文件相对工作台的路径（任务卡展示用），如 skills/<name>/SKILL.md。"""
    return "skills/%s/SKILL.md" % name


# ─────────────────────────── 技能区块渲染 ───────────────────────────

def render_skill_block(section, executor):
    """按执行者能力渲染「## 技能引用」区块（任务卡注入于输出协议之前）。

    - executor 为空 → 默认 dsh（native）;
    - 能力为 none → 返回空串（旧路径零变化，内联纪律兜底）;
    - 技能文件缺失时该技能行跳过（不注入失效路径）。
    """
    exec_name = str(executor or "").strip() or "dsh"
    cap = executor_capability(exec_name)
    if cap == "none":
        return ""
    names = SECTION_SKILLS.get(section_type(section), SECTION_SKILLS["general"])
    lines = [
        "## 技能引用（增强纪律，叠加于上文内联方法论之上）",
        "加载失败不得阻塞写作——回落上文内联方法论继续完成本节。",
    ]
    hint_tpl = _LOAD_HINT[cap]
    for n in names:
        f = SKILLS_DIR / n / "SKILL.md"
        if not f.exists():  # vendor 缺失的技能不注入，避免给出失效路径
            continue
        lines.append("- %s" % hint_tpl.format(name=n, path=skill_path(n)))
    if len(lines) <= 2:  # 全部技能文件缺失 → 等同无技能引用
        return ""
    return "\n".join(lines)


# ─────────────────────────── 执行者:模型 语法 ───────────────────────────

def parse_executor_spec(spec):
    """解析契约执行者列「执行者:模型」语法 → (executor, model)。

    - "workbuddy:glm-5.2" → ("workbuddy", "glm-5.2")
    - "dsh"               → ("dsh", "")
    - ""（留空/旧契约无此列）→ ("", "")
    执行者部分小写归一（与 runner.dispatch 口径一致）；模型部分原样保留。
    """
    s = str(spec or "").strip()
    if not s:
        return "", ""
    if ":" in s:
        ex, model = s.split(":", 1)
        return ex.strip().lower(), model.strip()
    return s.lower(), ""
