# -*- coding: utf-8 -*-
"""subagent_writer.py — 经 dsh 子代理会话的可选章节写作通道（方案 B 扩展）。

与 staged_gen.gen_section（ai_client 直连）并列的可选路径：主控把段级任务卡
（staged_gen.build_section_prompt 渲染，参数与 gen_section 完全一致）派发给
dsh 会话（runner.dispatch("dsh", ...)），回收正文后走同一段级门禁
（staged_gen.gate_section）——核心纪律：子代理产出无任何信任豁免。

回退方式 = 不传 --via subagent（旧路径逐字节不变）。
派发台账: draft/orchestration/dispatch-log.jsonl（每行一条派发记录）。
"""
from __future__ import annotations

import datetime
import json
import re
import sys
import time
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE))
import staged_gen as sg  # noqa: E402  复用契约加载/任务卡/门禁, 不改其本体
import skill_channel as sc  # noqa: E402  技能引用区块 + 执行者:模型解析


# ─────────────────────────── 输出协议与派发台账 ───────────────────────────

# 任务卡末尾追加的输出协议（子代理只回正文, 便于原样回收）
_OUTPUT_PROTOCOL = (
    "\n\n## 输出协议（严格遵守）\n"
    "只输出本章节的纯 Markdown 正文（含必要的 ### 小节标题），"
    "不要输出代码围栏、解释、前言或总结。"
)


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _word_count(text):
    """与 staged_gen.gate_section 同一套词数统计口径。"""
    return len(re.findall(r"[A-Za-z][A-Za-z'\-]*|\d+", text))


def _dispatch_log_append(proj, rec):
    """追加一条派发记录到 draft/orchestration/dispatch-log.jsonl（目录按需创建）。"""
    d = sg.draft_dir(proj) / "orchestration"
    d.mkdir(parents=True, exist_ok=True)
    rec = dict(rec)
    rec["ts"] = _now()
    with open(d / "dispatch-log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ─────────────────────────── 主流程 ───────────────────────────

def gen_section_via_subagent(proj, sid, timeout=900, retry=1, dry_run=False):
    """经 dsh 子代理会话写一段。前置检查/任务卡/产物落盘与 gen_section 对齐。

    返回结构化 envelope:
        {ok, sid, status(done/failed/timeout), words, file, gate_passed,
         issues, executor, msg}
    门禁失败处理: 先 L0 机械修复（toolbox.mechanical_fix）再重判；仍未过则
    重试（重新派发, 任务卡尾部附「上一轮未通过门禁」问题清单, 不累积）。
    """
    # a) 前置检查（与 gen_section 一致：gen_state 存在 / 契约存在 / 契约已锁定）
    st = sg.load_gen_state(proj)
    if st is None:
        return {"ok": False, "sid": sid, "status": "failed", "words": 0, "file": "",
                "gate_passed": False, "issues": [], "executor": "dsh",
                "msg": "未初始化：先运行 generate init"}
    cp = sg._paths(proj)["contract"]
    if not cp.exists():
        return {"ok": False, "sid": sid, "status": "failed", "words": 0, "file": "",
                "gate_passed": False, "issues": [], "executor": "dsh",
                "msg": "缺少契约：先运行 generate contract 并人工锁定"}
    contract_raw = cp.read_text(encoding="utf-8")
    contract = sg.parse_contract(contract_raw)
    sec = next((s for s in contract["sections"] if s["sid"] == sid), None)
    if sec is None:
        return {"ok": False, "sid": sid, "status": "failed", "words": 0, "file": "",
                "gate_passed": False, "issues": [], "executor": "dsh",
                "msg": f"契约章节大纲中无 {sid}；现有: {[s['sid'] for s in contract['sections']]}"}
    if not contract["locked"] and not dry_run:
        return {"ok": False, "sid": sid, "status": "failed", "words": 0, "file": "",
                "gate_passed": False, "issues": [], "executor": "dsh",
                "msg": "契约未锁定（把契约中『契约状态：待锁定』改为『已锁定』再继续）——S0 人工检查点不可跳过"}

    # b) 任务卡（与 gen_section 相同参数, 含 _prev_summary 与 _assigned_refs_detail）
    # P0 语言传导: 补传契约头部解析出的 lang（此前漏传, 中文契约任务卡会要求英文写作）
    # P1 预算回退/占位符前置检查: 与 gen_section 同一共享函数口径
    sec, budget_note = sg.normalize_section_budget(sec)
    # 执行者路由首选：契约章节大纲可选「执行者」列（留空=默认执行者）
    pref = (sec.get("executor") or "").strip()
    # 「执行者:模型」语法（如 workbuddy:glm-5.2）: 路由只用执行者部分,
    # 模型部分暂记入 dispatch-log 的 model 字段（透传需改 runner.py, 后续项）
    exec_name, exec_model = sc.parse_executor_spec(pref)
    warnings = sg.contract_placeholder_warnings(contract_raw, contract.get("locked"))
    prompt = sg.build_section_prompt(
        contract, sec, sg._prev_summary(proj, contract, sid),
        sg._assigned_refs_detail(proj, contract, sid),
        lang=contract.get("lang", "en"))
    if budget_note:
        prompt = prompt.replace(f"字数预算: 约 {sec['budget']} 词",
                                f"字数预算: 约 {sec['budget']} 词 {budget_note}", 1)
    # 技能引用区块（增强层, 注入于输出协议之前; 能力 none → 空串, 旧路径零变化）
    skill_block = sc.render_skill_block(sec, exec_name or "dsh")
    if skill_block:
        prompt += "\n\n" + skill_block
    # c) 末尾追加输出协议
    prompt += _OUTPUT_PROTOCOL
    if dry_run:
        return {"ok": True, "sid": sid, "status": "done", "words": 0, "file": "",
                "gate_passed": False, "issues": [], "executor": exec_name or "dsh",
                "warnings": warnings,
                "msg": "dry-run prompt", "prompt": prompt}

    import runner  # runner 与工作台同目录, ENGINE 已入 sys.path

    issues, text, status = [], "", "failed"
    attempts = 0
    total = retry + 1  # 首次派发 + retry 次重试
    last_executor, last_fb = exec_name or "dsh", ""  # 供最终 envelope 引用的路由结果
    while attempts < total:
        # d) 派发子代理会话（容错路由：章节指定执行者 → 默认 dsh 重试一次 → 仍失败返回失败标记）
        t0 = time.time()
        r = runner.dispatch_with_fallback(exec_name, prompt, cwd=str(proj), timeout=timeout)
        elapsed = int(time.time() - t0)
        last_executor = r.get("executor", exec_name or "dsh")
        last_fb = r.get("fallback_from", "")
        # 路由结构化日志：每次派发与切换各记一条（executor/fallback_from 字段）
        for rec in (r.get("route") or [])[:-1]:
            _dispatch_log_append(proj, {"sid": sid, "attempt": attempts + 1, "ok": rec["ok"],
                                        "executor": rec["executor"], "fallback_from": rec["fallback_from"],
                                        "elapsed": rec["elapsed"], "words": 0, "issues_n": 1,
                                        "model": exec_model, "note": "routing-fallback"})
        # 离线/派发失败（含默认执行者兜底后仍失败）：明确报错, 不静默（回退 = 去掉 --via subagent）
        if not r.get("ok"):
            issues = [{"severity": "P0", "type": "dispatch_failed",
                       "msg": str(r.get("error") or "派发失败")}]
            _dispatch_log_append(proj, {"sid": sid, "attempt": attempts + 1, "ok": False,
                                        "executor": last_executor, "fallback_from": last_fb,
                                        "elapsed": elapsed, "words": 0, "issues_n": 1,
                                        "model": exec_model})
            return {"ok": False, "sid": sid, "status": "failed", "words": 0, "file": "",
                    "gate_passed": False, "issues": issues, "executor": last_executor,
                    "msg": f"派发失败（{last_executor}）: {issues[0]['msg']}（回退：去掉 --via subagent, 走普通路径）"}
        # e) 回收正文（复用 staged_gen._strip_code_fence 处理代码围栏）
        text = sg._strip_code_fence(str(r.get("text") or ""))
        words = _word_count(text)
        ok_hit = True
        if len(text.strip()) < 200:
            ok_hit = False
            issues = [{"severity": "P0", "type": "empty_output", "msg": "子代理输出为空/过短"}]
        else:
            # f) 门禁：子代理产出无信任豁免
            issues, passed = sg.gate_section(proj, contract, sec, text)
            if not passed:
                # L0: 机械类违规 → 本地规则修复, 0 token（模仿 gen_section 的 L0 分支）
                p0p1 = [i for i in issues if i["severity"] in ("P0", "P1")]
                mech = [i for i in p0p1 if i.get("type") in sg._MECH_TYPES]
                if mech:
                    try:
                        import toolbox
                        fixed = toolbox.mechanical_fix(text)
                        if fixed and fixed != text:
                            text = fixed
                            issues, passed = sg.gate_section(proj, contract, sec, text)
                    except Exception:
                        pass
            if passed:
                status = "done"
                _dispatch_log_append(proj, {"sid": sid, "attempt": attempts + 1, "ok": True,
                                            "executor": last_executor, "fallback_from": last_fb,
                                            "elapsed": elapsed, "words": words, "issues_n": 0,
                                            "model": exec_model})
                break
            ok_hit = False
        _dispatch_log_append(proj, {"sid": sid, "attempt": attempts + 1, "ok": ok_hit,
                                    "executor": last_executor, "fallback_from": last_fb,
                                    "elapsed": elapsed, "words": words, "issues_n": len(issues),
                                    "model": exec_model})
        attempts += 1
        if attempts < total:
            # L2 式重试：附「上一轮未通过门禁」问题清单（不累积, 模仿 gen_section）
            p0p1 = [i for i in issues if i["severity"] in ("P0", "P1")] or issues
            prompt = re.sub(r"## 上一轮未通过门禁.*$", "", prompt, flags=re.S).rstrip()
            prompt += ("\n\n## 上一轮未通过门禁，请修正后重写本节（保留正确部分）：\n- "
                       + "\n- ".join(i["msg"] for i in p0p1))

    # g) 成功：落盘 + 更新 gen_state（条目格式与 gen_section 完全一致）
    words = _word_count(text)
    accepted = status == "done"
    fname = f"{sid}-{re.sub(r'[^A-Za-z0-9]+', '-', sec['title']).strip('-')[:40].lower()}.md"
    if accepted:
        f = sg._paths(proj)["sections"] / fname
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    entry = {
        "status": "done" if accepted else "failed",
        "file": fname if accepted else "",
        "attempts": (attempts + 1) if accepted else (attempts or 1),
        "issues": issues,
        "ts": _now(),
    }
    st.setdefault("sections", {})[sid] = entry
    sg.save_gen_state(proj, st)
    sg.append_log(proj, f"gen-section {sid} (via=subagent): "
                        f"{'accepted' if accepted else 'FAILED'} (尝试 {entry['attempts']} 次; 问题 {len(issues)})")
    return {"ok": accepted, "sid": sid, "status": status, "words": words,
            "file": fname if accepted else "", "gate_passed": accepted,
            "issues": issues, "executor": last_executor, "warnings": warnings,
            "msg": f"{sid} {'已接受 → ' + fname if accepted else '未通过门禁'}"}
