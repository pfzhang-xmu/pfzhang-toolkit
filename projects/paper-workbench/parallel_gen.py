# -*- coding: utf-8 -*-
"""parallel_gen.py — 依赖图驱动的并行分段写作层（任务7）。

用 dsh_bridge 会话池按波次并发派发段级任务卡:
  1. 契约必须已锁定（报错措辞与 gen_section 一致, S0 人工检查点不可跳过）;
  2. parse_contract 输出的 dependency_graph 拓扑排序分波次（环依赖降级按契约顺序）;
  3. 波内线程池并发: acquire_session → 记 since_seq → 发任务卡 →
     wait_for_response → 剥围栏 → 过 gate_section（子代理产出无信任豁免）;
  4. 门禁失败: 先 L0 mechanical_fix 本地修复重判, 仍未过则重试 1 次重新派发
     （任务卡尾附问题清单, 不累积）;
  5. 单段失败不阻塞同波; 波次结束仍失败的段降级调 staged_gen.gen_section 单体重跑;
  6. 每段完成后把尾部 ≤200 词摘要存 gen_state 该段 anchor 字段,
     供后续波次依赖段拼接「所有前驱 sid 的摘要锚点集合」（首波走开篇章节文案）;
  7. 派发日志 draft/orchestration/dispatch-log.jsonl 复用
     subagent_writer._dispatch_log_append 格式（追加 wave 字段）。

CLI:
    python parallel_gen.py <proj> [--concurrency N] [--timeout S]
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE))
import staged_gen as sg          # noqa: E402  复用契约/任务卡/门禁, 不改其本体
import subagent_writer as sw    # noqa: E402  复用派发日志 jsonl 格式与词数口径
import skill_channel as sc      # noqa: E402  技能引用区块 + 执行者:模型解析
import dsh_bridge               # noqa: E402  会话池 acquire/release
import runner                   # noqa: E402  外部执行者子进程派发 + 容错路由

_STATE_LOCK = threading.Lock()   # gen_state.json 并发读改写保护
_ANCHOR_WORDS = 200              # 段间衔接用的尾部摘要词数


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _tail_words(text, n=_ANCHOR_WORDS):
    """取文本尾部 n 词（与 staged_gen._prev_summary 同一空白分词口径）。"""
    return " ".join(text.split()[-n:])


def _fname_of(sec):
    """段产物文件名, 与 gen_section 命名完全一致: <sid>-<标题slug>.md"""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", sec["title"]).strip("-")[:40].lower()
    return "%s-%s.md" % (sec["sid"], slug)


def _topo_waves(sections, graph):
    """dependency_graph 拓扑排序分波次（Kahn 分层）。
    环依赖节点降级为按契约顺序的最后一波, 避免死锁。返回 [[sid...], ...]。"""
    sids = [s["sid"] for s in sections]
    sid_set = set(sids)
    deps = {sid: [d for d in graph.get(sid, []) if d in sid_set] for sid in sids}
    waves, done, remaining = [], set(), list(sids)
    while remaining:
        wave = [sid for sid in remaining if all(d in done for d in deps[sid])]
        if not wave:
            wave = list(remaining)  # 环依赖: 按契约顺序整体入最后一波
        waves.append(wave)
        done.update(wave)
        wave_set = set(wave)
        remaining = [sid for sid in remaining if sid not in wave_set]
    return waves


def _anchor_of(proj, st, sid):
    """取某段的尾部摘要: 优先 gen_state 的 anchor 字段, 其次读产物文件尾部。"""
    info = (st.get("sections") or {}).get(sid) or {}
    if info.get("anchor"):
        return info["anchor"]
    f = sg._paths(proj)["sections"] / (info.get("file") or "")
    if f and f.exists():
        try:
            return _tail_words(f.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return ""
    return ""


def _save_entry(proj, sid, entry):
    """线程安全更新 gen_state 单段条目（格式与 gen_section 一致, 附 anchor 字段）。"""
    with _STATE_LOCK:
        st = sg.load_gen_state(proj) or {}
        st.setdefault("sections", {})[sid] = entry
        sg.save_gen_state(proj, st)


def _log(proj, rec):
    """派发日志: 复用 subagent_writer 的 dispatch-log.jsonl 格式。"""
    try:
        sw._dispatch_log_append(proj, rec)
    except Exception:
        pass


def _output_protocol(target, sid):
    """任务卡末尾的输出协议: 子代理按绝对路径自行落盘并回报单行 JSON。

    路径改绝对路径: 子代理会话 cwd 未必是项目目录, 相对路径会写丢。
    """
    report = '{"sid": "%s", "ok": true, "words": <词数>, "file": "%s"}' % (sid, target.name)
    return (
        "\n\n## 输出协议（严格遵守）\n"
        "只输出本章节的纯 Markdown 正文（含必要的 ### 小节标题），"
        "不要输出代码围栏、解释、前言或总结。\n"
        "把正文写入绝对路径 %s（已存在则覆盖），完成后回报一行 JSON: %s"
        % (target, report)
    )


def _run_one(proj, contract, sec, wave, anchors, anchors_lock, timeout):
    """并行写一段（线程内执行）。返回该段结果字典 {sid, status, file, via, ...}。

    整体包 try/except: 单段内部异常只降级该段, 不炸全局（ex.map 求值异常会穿透）。
    """
    sid = sec["sid"]
    try:
        return _run_one_impl(proj, contract, sec, wave, anchors, anchors_lock, timeout)
    except Exception as e:
        _log(proj, {"sid": sid, "wave": wave, "attempt": 0, "ok": False,
                    "executor": "", "fallback_from": "",
                    "elapsed": 0, "words": 0, "issues_n": 1, "via": "parallel"})
        return {"sid": sid, "status": "failed", "file": "", "words": 0,
                "via": "parallel", "issues": [{"severity": "P0", "type": "internal_error",
                                               "msg": "段内异常（已降级, 不影响其他段）: %s" % e}]}


def _run_one_impl(proj, contract, sec, wave, anchors, anchors_lock, timeout):
    """并行写一段的实际执行。门禁失败处理: 先 L0 mechanical_fix 重判; 仍未过则重试 1 次重新派发,
    任务卡尾部附「上一轮未通过门禁」问题清单（不累积）。"""
    sid = sec["sid"]
    fname = _fname_of(sec)
    target = sg._paths(proj)["sections"] / fname
    # prev_summary 位置传「所有前驱 sid 的摘要锚点集合」（首波为空 → 开篇章节文案）
    parts = []
    for d in contract.get("dependency_graph", {}).get(sid, []):
        a = anchors.get(d, "")
        if a:
            parts.append("[%s 尾部摘录] %s" % (d, a))
    prev_summary = "\n\n".join(parts)
    # 预算回退共享函数（与 gen_section/subagent_writer 口径一致）
    sec, budget_note = sg.normalize_section_budget(sec)
    prompt = sg.build_section_prompt(
        contract, sec, prev_summary,
        sg._assigned_refs_detail(proj, contract, sid),
        lang=contract.get("lang", "en"))
    if budget_note:
        prompt = prompt.replace("字数预算: 约 %d 词" % sec["budget"],
                                "字数预算: 约 %d 词 %s" % (sec["budget"], budget_note), 1)
    # 执行者路由: 章节「执行者」列指定（空=默认执行者 dsh）。
    # 支持「执行者:模型」语法: 路由只用执行者部分; 模型暂记 dispatch-log model 字段
    pref, exec_model = sc.parse_executor_spec(sec.get("executor"))
    # 技能引用区块（增强层, 注入于输出协议之前; 能力 none → 空串, 旧路径零变化）
    skill_block = sc.render_skill_block(sec, pref or "dsh")
    if skill_block:
        prompt += "\n\n" + skill_block
    prompt += _output_protocol(target, sid)

    issues, text, attempts = [], "", 0
    total = 2  # 首次派发 + 重试 1 次
    # 会话池路径仅用于 dsh 执行者; 外部执行者走 runner.dispatch 子进程路径
    # （容错路由）, 产物回收用响应文本 + 既有门禁, 不依赖会话池。
    use_pool = pref in ("", "dsh")
    exec_name = pref or "dsh"  # 日志用执行者名（外部路径在派发后由路由结果细化）
    while attempts < total:
        if not use_pool:
            # 外部执行者: runner.dispatch_with_fallback 子进程派发
            # （① 章节指定执行者 → ② 默认 dsh 重试一次 → ③ 仍失败返回失败标记）
            t0 = time.time()
            r = runner.dispatch_with_fallback(pref, prompt, cwd=str(proj), timeout=timeout)
            exec_name = r.get("executor", pref)
            # 路由结构化日志: 每次派发与切换各记一条（executor/fallback_from 字段）
            for rec in (r.get("route") or [])[:-1]:
                _log(proj, {"sid": sid, "wave": wave, "attempt": attempts + 1, "ok": False,
                            "executor": rec["executor"], "fallback_from": rec["fallback_from"],
                            "elapsed": rec["elapsed"], "words": 0, "issues_n": 1,
                            "via": "parallel", "model": exec_model,
                            "note": "routing-fallback"})
            elapsed = int(time.time() - t0)
            if not r.get("ok"):
                issues = [{"severity": "P0", "type": "dispatch_failed",
                           "msg": str(r.get("error") or "派发失败")}]
                _log(proj, {"sid": sid, "wave": wave, "attempt": attempts + 1, "ok": False,
                            "executor": exec_name, "fallback_from": r.get("fallback_from", ""),
                            "elapsed": elapsed, "words": 0, "issues_n": 1,
                            "via": "parallel", "model": exec_model})
                attempts += 1
                continue
            # 产物回收: 优先响应文本剥围栏（门禁无豁免）; 仅当本轮新写文件（mtime>=t0）
            # 且响应过短时兜底采信文件——不依赖会话池。
            text = sg._strip_code_fence(str(r.get("text") or ""))
            if len(text.strip()) < 200:
                try:
                    if target.exists() and target.stat().st_mtime >= t0:
                        ft = target.read_text(encoding="utf-8", errors="replace")
                        if len(ft.strip()) >= 200:
                            text = ft
                except Exception:
                    pass
            words = sw._word_count(text)
        else:
            try:
                # cwd 绑到项目目录; 租约 = 2× 单段 timeout（acquire_session 内强制回收超时占用）
                session = dsh_bridge.acquire_session(cwd=str(proj), lease_timeout=2 * timeout)
            except Exception as e:
                issues = [{"severity": "P0", "type": "no_session",
                           "msg": "会话池取会话异常: %s" % e}]
                break
            if not session:
                issues = [{"severity": "P0", "type": "no_session",
                           "msg": "会话池无可用会话（dsh 离线或池耗尽）"}]
                break
            t0 = time.time()  # 派发前记时: 回收时仅采信 mtime>=t0 的文件（防把上轮残留旧文件当本轮产出）
            r = {"text": ""}
            try:
                # 发送前记录最大 seq, 复用会话时只提取本轮新产生的事件
                since_seq = 0
                try:
                    hist = dsh_bridge.get_history(session, max_messages=200)
                    since_seq = dsh_bridge._get_max_seq(hist.get("events", []))
                except Exception:
                    since_seq = 0
                dsh_bridge.send_prompt(session, prompt)
                r = dsh_bridge.wait_for_response(session, timeout=timeout, since_seq=since_seq)
            except Exception as e:
                issues = [{"severity": "P0", "type": "dispatch_failed",
                           "msg": "dsh 会话异常: %s" % e}]
                _log(proj, {"sid": sid, "wave": wave, "attempt": attempts + 1, "ok": False,
                            "executor": "dsh", "fallback_from": "",
                            "elapsed": int(time.time() - t0), "words": 0, "issues_n": 1,
                            "via": "parallel", "model": exec_model})
                attempts += 1
                continue
            finally:
                dsh_bridge.release_session(session)
            # 回收产物: 优先取子代理按输出协议写入的文件, 但仅当本轮确实新写（mtime>=t0）
            # 才采信——防把上一轮残留旧文件当本轮产出接受; 兜底取响应文本剥围栏。
            timed_out = bool(r.get("timeout"))
            file_text = ""
            try:
                if target.exists() and target.stat().st_mtime >= t0:
                    file_text = target.read_text(encoding="utf-8", errors="replace")
            except Exception:
                file_text = ""
            if timed_out and len(file_text.strip()) < 200:
                # 超时分流: 超时且本轮无新落盘文件 → 记 timeout 走重试/降级,
                # 不得把超时时的部分响应当正常产出过门禁
                issues = [{"severity": "P0", "type": "timeout",
                           "msg": "子代理超时, 且本轮未写入新产物（部分响应不计为产出）"}]
                text = ""
            elif len(file_text.strip()) >= 200:
                text = file_text
            else:
                text = sg._strip_code_fence(str(r.get("text") or ""))
            words = sw._word_count(text)
            elapsed = int(time.time() - t0)
        if len(text.strip()) < 200:
            if not issues:
                issues = [{"severity": "P0", "type": "empty_output",
                           "msg": "子代理输出为空/过短"}]
        else:
            issues, passed = sg.gate_section(proj, contract, sec, text)
            if not passed:
                # L0: 机械类违规 → 本地规则修复（0 token）后重判
                mech = [i for i in issues if i["severity"] in ("P0", "P1")
                        and i.get("type") in sg._MECH_TYPES]
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
                # 成功: 落盘（覆盖）+ 更新 gen_state（条目格式与 gen_section 一致, 附 anchor）
                words = sw._word_count(text)
                anchor = _tail_words(text)
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding="utf-8")
                except Exception as e:
                    issues = [{"severity": "P0", "type": "write_failed",
                               "msg": "产物落盘失败: %s" % e}]
                    _log(proj, {"sid": sid, "wave": wave, "attempt": attempts + 1,
                                "ok": False, "executor": exec_name,
                                "fallback_from": "" if use_pool else r.get("fallback_from", ""),
                                "elapsed": elapsed, "words": words,
                                "issues_n": 1, "via": "parallel", "model": exec_model})
                    attempts += 1
                    continue
                entry = {"status": "done", "file": fname, "attempts": attempts + 1,
                         "issues": issues, "ts": _now(), "anchor": anchor}
                _save_entry(proj, sid, entry)
                sg.append_log(proj, "gen-parallel %s (wave %d): accepted (尝试 %d 次)"
                              % (sid, wave, attempts + 1))
                with anchors_lock:
                    anchors[sid] = anchor
                _log(proj, {"sid": sid, "wave": wave, "attempt": attempts + 1,
                            "ok": True, "executor": exec_name,
                            "fallback_from": "" if use_pool else r.get("fallback_from", ""),
                            "elapsed": elapsed, "words": words,
                            "issues_n": 0, "via": "parallel", "model": exec_model})
                return {"sid": sid, "status": "done", "file": fname,
                        "words": words, "via": "parallel", "issues": []}
        # 本轮未通过: 记日志; 若还有重试额度则附问题清单重新派发（不累积）
        _log(proj, {"sid": sid, "wave": wave, "attempt": attempts + 1, "ok": False,
                    "executor": exec_name,
                    "fallback_from": "" if use_pool else r.get("fallback_from", ""),
                    "elapsed": elapsed, "words": words, "issues_n": len(issues),
                    "via": "parallel", "model": exec_model})
        attempts += 1
        if attempts < total and issues:
            p0p1 = [i for i in issues if i["severity"] in ("P0", "P1")] or issues
            prompt = re.sub(r"## 上一轮未通过门禁.*$", "", prompt, flags=re.S).rstrip()
            prompt += ("\n\n## 上一轮未通过门禁，请修正后重写本节（保留正确部分）：\n- "
                       + "\n- ".join(i["msg"] for i in p0p1))
    return {"sid": sid, "status": "failed", "file": "", "words": 0,
            "via": "parallel", "issues": issues}


def gen_parallel(proj, concurrency=4, timeout=900):
    """并行分段写作主入口。

    前置检查（只读, 失败即返回不写任何文件, 措辞与 gen_section 一致）:
    gen_state 已初始化 / 契约存在 / 契约已锁定。
    返回汇总报告 {ok, msg, waves, sections{sid: 状态}, downgraded, failed}。
    """
    proj = Path(proj)
    st = sg.load_gen_state(proj)
    if st is None:
        return {"ok": False, "msg": "未初始化：先运行 generate init",
                "waves": 0, "sections": {}, "downgraded": [], "failed": []}
    cp = sg._paths(proj)["contract"]
    if not cp.exists():
        return {"ok": False, "msg": "缺少契约：先运行 generate contract 并人工锁定",
                "waves": 0, "sections": {}, "downgraded": [], "failed": []}
    contract_raw = cp.read_text(encoding="utf-8")
    contract = sg.parse_contract(contract_raw)
    if not contract["sections"]:
        return {"ok": False, "msg": "契约章节大纲为空, 无法并行生成",
                "waves": 0, "sections": {}, "downgraded": [], "failed": []}
    if not contract["locked"]:
        return {"ok": False, "msg": "契约未锁定（把契约中『契约状态：待锁定』改为『已锁定』再继续）——S0 人工检查点不可跳过",
                "waves": 0, "sections": {}, "downgraded": [], "failed": []}
    # 前置检查: dsh 离线明确报错, 不进波次派发（措辞同 gen_delegate 的离线提示）
    try:
        online = dsh_bridge.is_available()
    except Exception:
        online = False
    if not online:
        return {"ok": False, "msg": "DSH Agent 离线（端口 3080 无响应）——回退：手动执行 generate 各动作, 或去掉并行改逐段 generate section",
                "waves": 0, "sections": {}, "downgraded": [], "failed": []}
    waves = _topo_waves(contract["sections"], contract.get("dependency_graph", {}))
    sec_of = {s["sid"]: s for s in contract["sections"]}
    anchors, anchors_lock = {}, threading.Lock()
    warnings = sg.contract_placeholder_warnings(contract_raw, contract.get("locked"))
    st0 = sg.load_gen_state(proj) or {}
    for sid in sec_of:   # 预载历史锚点（支持续跑: 前驱已完成段直接复用其摘要）
        a = _anchor_of(proj, st0, sid)
        if a:
            anchors[sid] = a

    results, downgraded = {}, []
    for wi, wave in enumerate(waves, 1):
        st_now = sg.load_gen_state(proj) or {}
        # 已 done 的段跳过（续跑语义）; 单段失败不阻塞同波其他段
        todo = [sid for sid in wave
                if ((st_now.get("sections") or {}).get(sid) or {}).get("status") != "done"]
        if todo:
            workers = max(1, min(int(concurrency), len(todo)))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                outs = list(ex.map(lambda sid: _run_one(
                    proj, contract, sec_of[sid], wi, anchors, anchors_lock, timeout), todo))
            for o in outs:
                results[o["sid"]] = o

        # 本波结束仍失败的段 → 降级调 staged_gen.gen_section 单体重跑（失败隔离）
        for o in outs if todo else []:
            if o["status"] == "done":
                continue
            downgraded.append(o["sid"])
            fb = sg.gen_section(proj, o["sid"], retry=1)
            if fb.get("ok"):
                results[o["sid"]] = {"sid": o["sid"], "status": "done",
                                     "file": fb.get("file", ""), "via": "fallback",
                                     "issues": [], "wave": wi}
                anchors[o["sid"]] = _anchor_of(proj, sg.load_gen_state(proj) or {}, o["sid"])
            else:
                results[o["sid"]] = {"sid": o["sid"], "status": "failed", "file": "",
                                     "via": "fallback", "wave": wi,
                                     "issues": fb.get("issues") or [
                                         {"severity": "P0", "type": "fallback_failed",
                                          "msg": fb.get("msg", "降级重跑失败")}]}

    failed = [sid for sid, o in results.items() if o["status"] != "done"]
    ok_all = not failed
    msg = ("并行生成完成: %d 波次 / %d 段" % (len(waves), len(sec_of))
           + ("，降级重跑 %d 段: %s" % (len(downgraded), downgraded) if downgraded else "")
           + ("；全部通过门禁" if ok_all else "；仍失败: %s" % failed))
    sg.append_log(proj, "gen-parallel: %s" % msg)
    return {"ok": ok_all, "msg": msg, "waves": len(waves),
            "sections": {sid: {k: v for k, v in o.items()} for sid, o in results.items()},
            "downgraded": downgraded, "failed": failed, "warnings": warnings}


def main(argv=None):
    ap = argparse.ArgumentParser(description="依赖图驱动的并行分段写作（dsh 会话池, 门禁无豁免）")
    ap.add_argument("proj", help="项目目录（已 generate init 且契约已锁定）")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="波内并发数（默认 4, 与会话池容量一致）")
    ap.add_argument("--timeout", type=int, default=900,
                    help="单段子代理等待超时秒数（默认 900）")
    args = ap.parse_args(argv)
    r = gen_parallel(args.proj, concurrency=args.concurrency, timeout=args.timeout)
    print(("✔ " if r.get("ok") else "✗ ") + str(r.get("msg", "")))
    for sid, o in (r.get("sections") or {}).items():
        print("  %s [%s] %s via=%s" % (sid, o.get("status", "?"),
                                        o.get("file", ""), o.get("via", "parallel")))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
