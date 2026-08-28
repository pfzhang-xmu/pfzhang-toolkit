# -*- coding: utf-8 -*-
"""runner.py — 工作台执行者抽象层（方案 B：执行者可插拔）。

收到写论文任务时，工作台生成八步任务书（基于 flow.md），派发给指定执行者
（dsh / claude / codex / workbuddy / 任意 MCP 客户端）。核心原则：

  - 谁执行不重要，重要的是子代理必须经工作台 MCP 拿结果
    （检索/门禁/绘图/导出），全程台账留痕。
  - dsh 后端立即可用（复用 dsh_bridge）；claude 后端代码已就绪，
    CLI 安装后即可启用（未装时明确报错，不静默）。
  - codex：PATH 缺失时回退绝对路径 ~/.codex/.sandbox-bin/codex.exe；
    exec 参数面实测确认：codex exec PROMPT --skip-git-repo-check -o 输出文件
    （prompt 经 stdin 传入，避开 Windows 命令行长度上限）。
  - workbuddy：WorkBuddy 捆绑 node 运行 app.asar.unpacked 内 codebuddy CLI，
    -p（print 模式）+ 任务卡经 stdin 传入（--output-format json、--max-turns 20、
    --tools "" 禁工具、--no-session-persistence 保证纯文本回收零副作用）。
    2026-08-24 实测：codebuddy 无 --prompt-file 类参数，prompt 位置参数受
    Windows 8191 字符上限约束；但 -p 模式下无位置 prompt 时从 stdin 读取
    （对齐 codex 的 stdin 方案），已实测短任务探活通过。
  - traework（TRAE SOLO CN）：2026-08-24 深度探测确认无 headless 入口——
    内置 CLI 仅 VS Code 式（开文件/扩展管理）；chat 子命令走 GUI 窗口；
    tunnel/serve-web 所需的 trae-solo-cn-tunnel.exe 未随 CN 版发布；
    无本地 agent server/WebSocket 服务；模型选择（mapping.json：
    kimi-k2.6/qwen3.8-max/Doubao-Seed 等）仅在登录态 GUI 内可用。不参测。

用法:
  from runner import dispatch, build_taskbook, list_executors
  dispatch("dsh", "写一篇关于 X 的综述", project=..., cwd=...)
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

WB = Path(__file__).resolve().parent
LEDGER = WB / "data" / "tool_ledger.jsonl"

# 默认执行者（容错路由的兜底；依据 2026-08-23 基准测试：基线稳定、复用现有会话池，见 data/benchmark-scores.json）
DEFAULT_EXECUTOR = "dsh"

# ── 执行者固定路径（不在 PATH 时的回退） ──
CODEX_FALLBACK = Path.home() / ".codex" / ".sandbox-bin" / "codex.exe"
WB_NODE = Path.home() / ".workbuddy" / "binaries" / "node" / "versions" / "22.22.2" / "node.exe"
WB_CLI = (Path.home() / "AppData" / "Local" / "Programs" / "WorkBuddy"
          / "resources" / "app.asar.unpacked" / "cli" / "bin" / "codebuddy")

# ── 台账（与 workbench_mcp 同一份） ──

def _ledger_append(entry: dict):
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S") + time.strftime("%z")
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        sys.stderr.write(f"[runner-ledger] 写入失败: {e}\n")


# ── 执行者检测 ──

def _codex_exe():
    """codex 可执行文件探测：PATH 优先，回退绝对路径 ~/.codex/.sandbox-bin/codex.exe。"""
    exe = shutil.which("codex")
    if exe:
        return exe
    return str(CODEX_FALLBACK) if CODEX_FALLBACK.exists() else ""


def _workbuddy_available():
    """workbuddy 可用性：捆绑 node 与 CLI 入口脚本同时存在。"""
    return bool(WB_NODE.exists() and WB_CLI.exists())


def list_executors():
    """返回可用执行者列表（含 availability）。"""
    out = []
    try:
        sys.path.insert(0, str(WB))
        import dsh_bridge
        out.append({"name": "dsh", "available": bool(dsh_bridge.is_available()),
                    "note": "DSH Agent（JSON-RPC）"})
    except Exception:
        out.append({"name": "dsh", "available": False, "note": "DSH 不可用"})
    out.append({"name": "claude", "available": bool(shutil.which("claude")),
                "note": "Claude Code CLI（headless + 工作台 MCP）"})
    out.append({"name": "codex", "available": bool(_codex_exe()),
                "note": "OpenAI Codex CLI（工作台 MCP；PATH 缺失时回退 ~/.codex/.sandbox-bin）"})
    out.append({"name": "workbuddy", "available": _workbuddy_available(),
                "note": "WorkBuddy CodeBuddy CLI（捆绑 node + app.asar.unpacked 入口）"})
    out.append({"name": "traework", "available": False,
                "note": "TRAE SOLO CN：深探测确认无 headless 入口（chat 走 GUI；tunnel.exe 缺失；无本地 agent server；模型选择仅登录态 GUI 内），不参测"})
    return out


# ── 派发（统一接口） ──

def dispatch(executor, task, cwd=None, timeout=1800):
    """按执行者类型派发任务。

    task: 完整指令（含任务书要求）。返回 {ok, text, tool_calls, executor, error?}。
    """
    e = str(executor or "dsh").lower()
    if e == "dsh":
        r = _dispatch_dsh(task, cwd, timeout)
    elif e == "claude":
        r = _dispatch_claude(task, cwd, timeout)
    elif e == "codex":
        r = _dispatch_codex(task, cwd, timeout)
    elif e == "workbuddy":
        r = _dispatch_workbuddy(task, cwd, timeout)
    elif e == "traework":
        r = {"ok": False, "error": "traework（TRAE SOLO CN）深探测确认无 headless 派发入口（chat 需 GUI；tunnel.exe 缺失；无本地服务），不参测"}
    else:
        r = {"ok": False, "error": f"未知执行者: {executor}（可选 dsh/claude/codex/workbuddy）"}
    # 归一化: dsh_bridge.delegate_task 不返回 ok 键, 用 text 判断
    if r.get("ok") is None:
        r["ok"] = bool(r.get("text"))
    r["executor"] = e
    _ledger_append({"tool": "dispatch", "executor": e, "ok": r.get("ok"),
                    "timeout": timeout, "note": (task or "")[:200]})
    return r


def dispatch_with_fallback(executor_pref, task, cwd=None, timeout=1800,
                           default_executor=DEFAULT_EXECUTOR):
    """容错路由（subagent_writer / parallel_gen 共享的派发入口）。

    执行顺序：
      ① 章节指定执行者（仅当 runner.list_executors 判定 available 时）
      → ② 失败/不可用则换默认执行者（dsh）重试一次
      → ③ 仍失败返回失败标记，由调用方决定降级（如单体路径）

    返回 dispatch() 的结果字典，附加路由字段：
      executor       实际成功执行者（失败时为最后尝试者）
      fallback_from  发生切换时的原首选执行者（空串=未切换）
      route          每次派发的结构化记录 [{executor, ok, elapsed, error, fallback_from}]
                     （调用方据此写 dispatch-log，日志条目含 executor/fallback_from 字段）
    """
    pref = str(executor_pref or "").strip().lower()
    avail = {e["name"]: bool(e.get("available")) for e in list_executors()}
    order = []  # [(执行者, fallback_from)]
    if pref and pref != default_executor:
        if avail.get(pref):
            order.append((pref, ""))             # ① 指定执行者可用：先试
        order.append((default_executor, pref))   # ② 不可用直接换默认 / 失败后换默认
    else:
        order.append((pref or default_executor, ""))  # 留空或指定=默认：直接派默认
    route, last = [], None
    for ex_name, fb_from in order:
        t0 = time.time()
        r = dispatch(ex_name, task, cwd=cwd, timeout=timeout)
        route.append({"executor": ex_name, "ok": bool(r.get("ok")),
                      "elapsed": int(time.time() - t0),
                      "error": "" if r.get("ok") else str(r.get("error") or "派发失败"),
                      "fallback_from": fb_from})
        last = r
        if r.get("ok"):
            break
    out = dict(last or {"ok": False, "error": "未执行任何派发"})
    out["executor"] = route[-1]["executor"] if route else (pref or default_executor)
    out["fallback_from"] = route[-1]["fallback_from"] if route else ""
    out["route"] = route
    return out


def _dispatch_dsh(task, cwd, timeout):
    try:
        sys.path.insert(0, str(WB))
        import dsh_bridge
    except Exception as ex:
        return {"ok": False, "error": f"dsh_bridge 不可用: {ex}"}
    if not dsh_bridge.is_available():
        return {"ok": False, "error": "DSH Agent 离线（端口 3080 无响应）"}
    return dsh_bridge.delegate_task(task, cwd=cwd, timeout=timeout)


def _dispatch_claude(task, cwd, timeout):
    exe = shutil.which("claude")
    if not exe:
        return {"ok": False, "error": "claude CLI 未安装——本后端待装后启用；现可用 dsh 后端"}
    mcp_cfg = WB / "mcp.json"
    cmd = [exe, "-p", task, "--output-format", "json"]
    if mcp_cfg.exists():
        cmd += ["--mcp-config", str(mcp_cfg)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=cwd or str(WB), timeout=timeout)
        return {"ok": r.returncode == 0, "text": r.stdout + r.stderr, "tool_calls": []}
    except Exception as ex:
        return {"ok": False, "error": f"claude 执行失败: {ex}"}


def _dispatch_codex(task, cwd, timeout):
    exe = _codex_exe()
    if not exe:
        return {"ok": False, "error": "codex CLI 未安装——本后端待装后启用；现可用 dsh 后端"}
    # 实测 exec 参数面：codex exec PROMPT --skip-git-repo-check -o 输出文件
    # prompt 经 stdin（"-"）传入，避开 Windows 命令行 8191 字符上限；
    # 最终正文经 -o 落到临时文件回读，避免与 JSONL 事件流混杂。
    cfg = WB / "codex-config.json"
    out_file = Path(tempfile.mkdtemp(prefix="codex_out_", dir=tempfile.gettempdir())) / "last.md"
    cmd = [exe, "exec", "-", "--skip-git-repo-check", "-o", str(out_file)]
    if cfg.exists():
        cmd += ["--config", str(cfg)]
    try:
        r = subprocess.run(cmd, input=task, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=cwd or str(WB), timeout=timeout)
        text = out_file.read_text(encoding="utf-8", errors="replace") if out_file.exists() else ""
        if r.returncode != 0 and not text:
            return {"ok": False, "text": r.stdout + r.stderr, "tool_calls": [],
                    "error": f"codex 退出码 {r.returncode}"}
        return {"ok": r.returncode == 0 or bool(text), "text": text or (r.stdout + r.stderr),
                "tool_calls": []}
    except Exception as ex:
        return {"ok": False, "error": f"codex 执行失败: {ex}"}


def _clean_protocol_noise(text):
    """剥离执行者响应文本中的输出协议噪声（任务 #20 收官小修）。

    任务卡「输出协议」要求子代理回报一行 JSON（{"sid": …}），非交互执行者
    （如 workbuddy -p）会把该回报行与协议复述行（写入文件/统计词数的叙述）
    一并带回，混入段产物。此处逐行剥离两类噪声：
      1. 单行 JSON 回报行（可被 json 解析且含 "sid" 键）；
      2. 协议复述/工具叙述行（写文件、词数统计等固定句式）。
    保守匹配：只删整行吻合特征的行，不触碰正文；返回清洗后文本。
    """
    if not text:
        return text
    narr = re.compile(
        r"^(?:Write to file|Writing the file|已完成撰写|正在写入文件"
        r"|使用\s*Write\s*工具|\*{0,2}统计词数|\*{0,2}词数统计)",
        re.I)
    kept = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("{") and s.endswith("}") and '"sid"' in s:
            try:
                if isinstance(json.loads(s), dict):
                    continue  # 输出协议要求的单行 JSON 回报
            except Exception:
                pass
        if s and narr.match(s):
            continue  # 协议复述/工具叙述行
        kept.append(ln)
    # 尾部悬空分隔线/空行: 噪声剥离后常残留 "---" 分隔线, 一并清理
    while kept and kept[-1].strip() in ("", "---", "***", "___"):
        kept.pop()
    return "\n".join(kept).strip("\n")


def _dispatch_workbuddy(task, cwd, timeout):
    exe = None
    if _workbuddy_available():
        exe = [str(WB_NODE), str(WB_CLI)]
    elif shutil.which("codebuddy"):
        exe = [shutil.which("codebuddy")]
    if not exe:
        return {"ok": False, "error": "workbuddy CLI 未找到（捆绑 node 或 app.asar.unpacked/cli/bin/codebuddy 缺失）"}
    # 实测参数面（2026-08-24 复核）：-p = 非交互打印模式（布尔）；任务卡不再走
    # 位置参数（Windows 命令行 8191 字符上限会截断长任务卡），改为经 stdin 传入
    # （无位置 prompt 时 -p 从 stdin 读取，实测探活通过；对齐 codex 的 stdin 方案）。
    # --tools "" 禁工具 + --no-session-persistence 保证纯文本回收、零副作用。
    cmd = exe + ["-p", "--output-format", "json", "--max-turns", "20",
                 "--tools", "", "--no-session-persistence"]
    try:
        r = subprocess.run(cmd, input=task, capture_output=True, text=True,
                           encoding="utf-8",
                           errors="replace", cwd=cwd or str(WB), timeout=timeout)
        out = r.stdout.strip()
        # --output-format json 返回事件数组，取所有 assistant 文本块拼接为正文（兜底原始 stdout）
        if out.startswith("["):
            try:
                evs = json.loads(out)
                parts = []
                for ev in evs if isinstance(evs, list) else []:
                    if ev.get("type") == "message" and ev.get("role") == "assistant":
                        for blk in ev.get("content", []):
                            if blk.get("type") in ("text", "output_text") and blk.get("text"):
                                parts.append(blk["text"])
                if parts:
                    out = "\n".join(parts)
            except Exception:
                pass
        out = _clean_protocol_noise(out)
        return {"ok": r.returncode == 0, "text": out + ("\n" + r.stderr if r.returncode else ""),
                "tool_calls": []}
    except Exception as ex:
        return {"ok": False, "error": f"workbuddy 执行失败: {ex}"}


# ── 任务书生成（基于 flow.md 八步） ──

TASKBOOK_STEPS = [
    {"phase": "journal", "label": "确定期刊", "action": "选定期刊并写入 journal/chosen.md",
     "tools": ["web_search", "literature_search"], "gate": "chosen.md 生成（含 author guidelines）"},
    {"phase": "literature", "label": "检索文献", "action": "多源检索→去重→DOI 核验→生成文献池",
     "tools": ["literature_search", "build_references", "fetch_doi"], "gate": "references.md 达标（条数/近5年/DOI）"},
    {"phase": "framework", "label": "确定框架", "action": "按期刊范文拆解结构→生成逐节框架+图表规划",
     "tools": ["read_skill"], "gate": "outline.md + figures.md 规划完整"},
    {"phase": "contract", "label": "文献↔章节匹配", "action": "generate contract，含引文分配表",
     "tools": ["generate contract"], "gate": "契约锁定（S0 人工检查点）"},
    {"phase": "write", "label": "skill 分段写作", "action": "逐段 generate section（方法论已内联）",
     "tools": ["generate section"], "gate": "段级五件套（机械/引文池/字数/密度/主题句）"},
    {"phase": "logic", "label": "逻辑校验", "action": "generate assemble + 逻辑一致性校验",
     "tools": ["generate assemble"], "gate": "跨章节矛盾 P0 清零"},
    {"phase": "review", "label": "拼装审核", "action": "全量门禁 + 模拟审稿",
     "tools": ["quality_check", "mechanical_fix"], "gate": "P0/P1=0，分数达期刊可投线"},
    {"phase": "submit", "label": "投稿材料", "action": "cover letter + Word 导出",
     "tools": ["export_docx"], "gate": "材料齐备 + process_audit clean"},
]


def build_taskbook(goal, project="", journal="", lang="en", ptype="review"):
    """把写论文指令编排成八步任务书。"""
    return {
        "goal": goal,
        "project": project,
        "journal": journal or "(自动推荐)",
        "lang": lang,
        "type": ptype,
        "protocol": "flow.md 八步编排协议（~/.dsh/papers/workbench/flow.md）",
        "steps": TASKBOOK_STEPS,
    }


def taskbook_prompt(taskbook):
    """把任务书转成派发 prompt（含强制经工作台 MCP 的约束）。"""
    lines = [
        f"# 论文写作任务（工作台派发）",
        f"目标: {taskbook['goal']}",
        f"项目: {taskbook['project'] or '(新建, 先 wb.py init)'}",
        f"期刊: {taskbook['journal']} | 语言: {taskbook['lang']} | 类型: {taskbook['type']}",
        "",
        "## 八步编排（按 flow.md 协议，每步门禁通过才进下一步）",
    ]
    for i, s in enumerate(taskbook["steps"], 1):
        lines.append(
            f"{i}. [{s['phase']}] {s['label']} — {s['action']}"
            f"（工具: {'/'.join(s['tools'])}；门禁: {s['gate']}）")
    lines += [
        "",
        "## 强制约束（必须遵守，否则产出不通过工作台验收）",
        "1. 检索必须走工作台工具 literature_search / build_references / web_search——禁止凭记忆编造文献。",
        "2. 写作走 generate section 分段生成（工作台已内联本节方法论），禁止跳过门禁。",
        "3. 拼装后必须 generate assemble（含逻辑校验）再 quality_check，报告 P0/P1/分数。",
        "4. 图表必须 figure_render、导出必须 export_docx——禁止手写绕过统一管线。",
        "5. 全程工具调用会写入工作台台账（tool_ledger.jsonl），process_audit 可追溯。",
        "",
        "完成后汇报：任务书每步状态、quality_check 分数与 P0/P1 清单、产出文件路径。",
    ]
    return "\n".join(lines)


# ── CLI（可选手动派发） ──

def main():
    import argparse
    ap = argparse.ArgumentParser(prog="runner", description="工作台任务派发")
    ap.add_argument("goal", nargs="?", default="", help="写论文指令")
    ap.add_argument("--executor", default="dsh", help="dsh/claude/codex/workbuddy")
    ap.add_argument("--project", default="", help="项目路径")
    ap.add_argument("--journal", default="", help="目标期刊")
    ap.add_argument("--dry-run", action="store_true", help="只生成任务书不派发")
    ap.add_argument("--list", action="store_true", help="列出可用执行者")
    args = ap.parse_args()
    if args.list:
        for e in list_executors():
            print(f"  {e['name']:<10} {'✓' if e['available'] else '✗'} {e['note']}")
        return
    if not args.goal:
        ap.error("缺少 goal（或改用 --list）")
    tb = build_taskbook(args.goal, project=args.project, journal=args.journal)
    prompt = taskbook_prompt(tb)
    if args.dry_run:
        print(prompt)
        return
    print(f"派发到 {args.executor} ...")
    r = dispatch(args.executor, prompt, cwd=args.project or None)
    if not r.get("ok"):
        print(f"✗ {r.get('error')}")
        sys.exit(1)
    text = str(r.get("text", ""))
    print(text[:3000] if text else "（无文本返回）")
    print(f"tool_calls: {len(r.get('tool_calls') or [])}")


if __name__ == "__main__":
    main()
