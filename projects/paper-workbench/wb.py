#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Paper Workbench CLI — 论文写作审查全流程工作台引擎
==================================================
用户只需给出研究方向,引擎自动建立论文项目脚手架,跟踪
research → journal → framework → draft → review → submit
六阶段状态机,并在审查阶段生成分级任务工作簿。

用法(在论文项目目录内运行,或在任意目录用 --dir 指定):
  python wb.py init 「研究方向」 [--journal 期刊] [--dir 父目录] [--lang zh|en] [--type article|letter|review]
  python wb.py status                 # 当前状态总览
  python wb.py next                   # 当前阶段下一步动作(读检查清单+产物)
  python wb.py stage <name>           # 推进阶段(校验前置产物)
  python wb.py new <stage>            # 生成本阶段缺失的模板文件
  python wb.py check <stage> <n>      # 勾选检查点 n(1 基)
  python wb.py uncheck <stage> <n>    # 取消勾选
  python wb.py review-book            # 生成 review/tasks.md 审查任务工作簿
  python wb.py summary                # 汇总所有阶段与产物
  python wb.py doctor                 # 项目健康诊断

纯 Python 标准库,零依赖。引擎目录可移动:模板与检查清单随引擎走。
"""
import argparse
import datetime
import json
import os
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def _engine_root():
    """引擎根目录；PyInstaller 打包后指向解压资源目录。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path.home() / ".dsh" / "papers" / "workbench"))
    return Path(__file__).resolve().parent


ENGINE = _engine_root()
TEMPLATES = ENGINE / "templates"
CHECKLISTS = ENGINE / "checklists"
PAPERS_ROOT = Path.home() / ".dsh" / "papers"
STAGES = ["research", "journal", "framework", "draft", "review", "submit"]
# 论文类型 → 阶段序列(毕业论文跳过期刊调研;综述/论文/短文走完整六阶段)
STAGES_BY_TYPE = {
    "article": STAGES,
    "letter": STAGES,
    "review": STAGES,
    "thesis": ["research", "framework", "draft", "review", "submit"],
}
ARTICLE_TYPES = list(STAGES_BY_TYPE.keys())


def stages_for(st):
    """按项目类型返回阶段序列(向后兼容: 无 type/未知类型 → 默认六阶段)。"""
    t = (st or {}).get("type", "article")
    return STAGES_BY_TYPE.get(t, STAGES)


def last_project_path():
    """最近项目记录文件；打包后写用户目录（_MEIPASS 是临时目录，写入会丢失）。"""
    if getattr(sys, "frozen", False):
        return PAPERS_ROOT / ".last-project"
    return ENGINE / ".last-project"
STAGE_LABEL = {
    "research": "文献调研",
    "journal": "期刊调研",
    "framework": "框架搭建",
    "draft": "数据接入与写作",
    "review": "后期审查",
    "submit": "投稿材料",
}
# 每个阶段必须存在的产物(相对项目根)
STAGE_ARTIFACTS = {
    "research": ["research/literature.md", "research/search-log.md"],
    "journal": ["journal/chosen.md", "journal/shortlist.md"],
    "framework": ["framework/outline.md", "framework/contribution.md", "framework/data-requirements.md", "framework/references.md", "framework/figures.md", "framework/results-validation.md"],
    "draft": ["manuscript/main.md"],
    "review": ["review/final-report.md"],
    "submit": ["submit/cover-letter.md", "submit/checklist.md"],
}
STATE_FILE = "state.json"
STATE_MD = "STATE.md"


# ─────────────────────────── 状态读写 ───────────────────────────

def default_state(topic, journal, lang, ptype, created):
    return {
        "schema": 2,
        "topic": topic,
        "journal": journal,
        "lang": lang,
        "type": ptype,
        "created": created,
        "updated": created,
        "stage": "research",
        "stages": {
            s: {"done": False, "checked": []} for s in STAGES
        },
        "logs": [{"t": created, "what": "init"}],
    }


def slugify(text, maxlen=40):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", text)
    s = re.sub(r"\s+", "-", s).strip("-")
    return s[:maxlen] or "paper"


def find_project(start=None):
    """从 start(默认 cwd)向上找含 state.json 的论文项目目录。"""
    cur = Path(start or os.getcwd()).resolve()
    for d in [cur] + list(cur.parents):
        if (d / STATE_FILE).exists():
            return d
    return None


def load_state(proj):
    with open(proj / STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(proj, st):
    st["updated"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    with open(proj / STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    write_state_md(proj, st)


def write_state_md(proj, st):
    """生成人类可读 STATE.md。"""
    lines = ["# Paper Workbench State", ""]
    lines.append(f"- 研究方向: {st['topic']}")
    lines.append(f"- 目标期刊/会议: {st['journal'] or '(未定,自动推荐)'}")
    lines.append(f"- 语言: {st['lang']} | 类型: {st['type']}")
    lines.append(f"- 当前阶段: **{STAGE_LABEL.get(st['stage'], st['stage'])}** ({st['stage']})")
    lines.append(f"- 创建: {st['created']} | 更新: {st['updated']}")
    lines.append("")
    lines.append("## 阶段进度")
    for s in STAGES:
        done = st["stages"][s]["done"]
        mark = "✅" if done else ("🔄" if s == st["stage"] else "⬜")
        lines.append(f"- {mark} {STAGE_LABEL[s]} ({s}) — 已勾选检查点 {len(st['stages'][s]['checked'])} 项")
    lines.append("")
    lines.append("## 检查点详情")
    for s in STAGES:
        ck = read_checklist(s)
        checked = set(st["stages"][s]["checked"])
        lines.append(f"### {STAGE_LABEL[s]}")
        for i, item in enumerate(ck, 1):
            box = "[x]" if i in checked else "[ ]"
            lines.append(f"- {box} ({i}) {item}")
    lines.append("")
    (proj / STATE_MD).write_text("\n".join(lines), encoding="utf-8")


def read_checklist(stage):
    """读取 checklists/<stage>.md 中的检查项(以 '- [ ] ' 开头的行)。"""
    p = CHECKLISTS / f"{stage}.md"
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="replace")
    return [m.group(1).strip() for m in re.finditer(r"^\s*-\s*\[\ \]\s+(.*)$", text, re.M)]


def missing_artifacts(proj, st, stage=None):
    """返回仍缺失的产物(相对项目根)。stage 缺省取当前阶段。"""
    stage = stage or st["stage"]
    miss = []
    for rel in STAGE_ARTIFACTS.get(stage, []):
        if not (proj / rel).exists():
            miss.append(rel)
    return miss


def list_projects_in_papers():
    """扫描 ~/.dsh/papers 下所有含 state.json 的项目目录。"""
    root = PAPERS_ROOT
    if not root.exists():
        return []
    return sorted([p for p in root.iterdir() if (p / STATE_FILE).exists()])


def ensure_project(args):
    """定位论文项目目录:优先 args.dir,其次向上找,其次最近项目,其次 ~/.dsh/papers 唯一项目。"""
    if args is not None and getattr(args, "dir", None):
        cand = Path(args.dir).resolve()
        if (cand / STATE_FILE).exists():
            return cand
        print(f"✗ 指定目录不是论文项目: {cand}")
        sys.exit(1)
    proj = find_project()
    if proj is None:
        lp = last_project_path()
        if lp.exists():
            cand = Path(lp.read_text(encoding="utf-8").strip())
            if (cand / STATE_FILE).exists():
                return cand
        found = list_projects_in_papers()
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            print("✗ 找到多个论文项目,请用 --dir 指定其中一个:")
            for p in found:
                print(f"    {p}")
            sys.exit(1)
        print("✗ 未找到论文项目。请在项目目录内运行,或用 --dir 指定项目路径;先 python wb.py init 「研究方向」")
        sys.exit(1)
    return proj


def _template_for(rel, ptype):
    """按文章类型优先选择类型化模板(如 outline-thesis.md);不存在则用默认模板。"""
    if ptype and ptype in STAGES_BY_TYPE:
        alt = TEMPLATES / (Path(rel).stem + "-" + ptype + Path(rel).suffix)
        if alt.exists():
            return alt
    return TEMPLATES / Path(rel).name


def cmd_init(args):
    """初始化一个新论文项目(脚手架)。"""
    parent = Path(args.dir).resolve() if args.dir else Path.cwd().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    topic = args.topic.strip()
    slug = slugify(topic)
    proj = parent / slug
    if (proj / STATE_FILE).exists():
        print(f"✗ 项目已存在: {proj}")
        sys.exit(1)
    for sub in ["research", "journal", "framework", "manuscript", "data", "review", "submit"]:
        (proj / sub).mkdir(parents=True, exist_ok=True)
    created = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    st = default_state(topic, args.journal or "", args.lang, args.type, created)
    save_state(proj, st)
    for rel in STAGE_ARTIFACTS["research"]:
        tpl = _template_for(rel, args.type)
        dst = proj / rel
        if not dst.exists() and tpl.exists():
            dst.write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
    intake_tpl = TEMPLATES / "intake.md"
    intake_dst = proj / "intake.md"
    if not intake_dst.exists() and intake_tpl.exists():
        intake_dst.write_text(intake_tpl.read_text(encoding="utf-8"), encoding="utf-8")
    last_project_path().write_text(str(proj), encoding="utf-8")
    print(f"✔ 论文工作台已初始化: {proj}")
    print(f"  研究方向: {topic} | 类型: {args.type}")
    print(f"  目标期刊: {args.journal or '(未指定 → 期刊调研阶段自动推荐: wb.py recommend + select-venue skill)'}")
    print(f"  下一步: cd {proj} && python {sys.argv[0]} next")


def cmd_status(args):
    """查看当前项目状态总览。"""
    proj = ensure_project(args)
    st = load_state(proj)
    print(f"研究方向: {st['topic']}")
    print(f"目标期刊: {st['journal'] or '(未定)'}")
    print(f"文章类型: {st.get('type', 'article')}")
    print(f"当前阶段: {STAGE_LABEL.get(st['stage'], st['stage'])} ({st['stage']})")
    print()
    for s in stages_for(st):
        mark = "✅" if st["stages"][s]["done"] else ("🔄" if s == st["stage"] else "⬜")
        miss = missing_artifacts(proj, st, s)
        extra = f" | 缺产物: {', '.join(miss)}" if miss and not st["stages"][s]["done"] else ""
        print(f"  {mark} {STAGE_LABEL[s]} — 检查点 {len(st['stages'][s]['checked'])}/{len(read_checklist(s))}{extra}")


def cmd_next(args):
    """显示当前阶段下一步动作(读检查清单+产物)。"""
    proj = ensure_project(args)
    st = load_state(proj)
    stage = st["stage"]
    print(f"== 当前阶段: {STAGE_LABEL[stage]} ({stage}) ==")
    ck = read_checklist(stage)
    checked = set(st["stages"][stage]["checked"])
    pending = [(i, t) for i, t in enumerate(ck, 1) if i not in checked]
    if pending:
        print(f"未完成检查点 {len(pending)} 项:")
        for i, t in pending:
            print(f"  [{i}] {t}")
    miss = missing_artifacts(proj, st)
    if miss:
        print(f"缺失产物: {', '.join(miss)}  (运行: python {sys.argv[0]} new {stage})")
    print()
    print(f"完成全部检查点并生成产物后,运行: python {sys.argv[0]} stage {next_stage(stage, st)}")


def next_stage(stage, st=None):
    seq = stages_for(st)
    i = seq.index(stage)
    return seq[i + 1] if i + 1 < len(seq) else stage


def cmd_stage(args):
    proj = ensure_project(args)
    st = load_state(proj)
    name = args.name
    if name not in stages_for(st):
        print(f"✗ 未知阶段 {name},可选: {', '.join(stages_for(st))}")
        sys.exit(1)
    cur = st["stage"]
    if stages_for(st).index(name) < stages_for(st).index(cur):
        print(f"✗ 不能回退阶段({name} < {cur})。如确需重做请手动编辑 state.json")
        sys.exit(1)
    # 校验当前阶段产物
    miss = missing_artifacts(proj, st)
    if miss:
        print(f"✗ 当前阶段({cur})产物缺失: {', '.join(miss)}")
        print("  先运行: python {0} new {1}".format(sys.argv[0], cur))
        sys.exit(1)
    # 校验当前阶段检查点(可用 --force 跳过)
    if not getattr(args, "force", False):
        ck = read_checklist(cur)
        checked = set(st["stages"][cur]["checked"])
        valid = {i for i in checked if 1 <= i <= len(ck)}
        if ck and len(valid) < len(ck):
            print(f"✗ 当前阶段({cur})检查点未全部完成 ({len(checked)}/{len(ck)})")
            print("  完成检查点: python {0} check {1} <n>;如确需强制推进: python {0} stage {2} --force --reason \"具体原因\"".format(sys.argv[0], cur, name))
            sys.exit(1)
    # 贡献门禁（PaperSpine 铁律）: 进入 draft（写正文）前必须有已确认贡献，且字段非空
    if name == "draft" and not getattr(args, "force", False):
        try:
            import toolbox
            cq = toolbox.contribution_check(proj)
            cbad = [x for x in cq if x.get("severity") in ("P0", "P1")]
            if cbad:
                print(f"✗ 贡献门禁未通过（{len(cbad)} 项 P0/P1），不能开始写正文:")
                for x in cbad[:8]:
                    print(f"  [{x.get('severity')}] {x.get('msg')}")
                print("  请先完整填写 framework/contribution.md 并确认；如确需跳过，用 --force")
                sys.exit(1)
            rv = toolbox.results_validation_check(proj)
            rbad = [x for x in rv if x.get("severity") in ("P0", "P1")]
            if rbad:
                print(f"✗ Results 承诺-证据映射未通过（{len(rbad)} 项 P0/P1）:")
                for x in rbad[:8]:
                    print(f"  [{x.get('severity')}] {x.get('msg')}")
                print("  请先填写 framework/results-validation.md，使每个贡献有对应 Results 小节")
                sys.exit(1)
        except Exception as e:
            print(f"⚠ 贡献门禁检查失败（继续推进）: {e}")

    # 数据门禁（E2/阶段二 H3）: 进入 review 前必须建立 data/SOURCES.md 数据账本（--force 可显式跳过并留痕）
    if name == "review" and not getattr(args, "force", False):
        try:
            import toolbox
            dg = toolbox.data_gate(proj)
            dbad = [x for x in dg if x.get("severity") in ("P0", "P1")]
            if dbad:
                print(f"✗ 数据门禁未通过（{len(dbad)} 项）:")
                for x in dbad[:8]:
                    print(f"  [{x.get('severity')}] {x.get('msg')}")
                print("  请先建立 data/SOURCES.md 并与 data-requirements.md 核对；如确需跳过，用 --force（会留痕）")
                sys.exit(1)
        except Exception as e:
            print(f"⚠ 数据门禁检查失败（继续推进）: {e}")

    # 文本质量门禁（B1/B3）: 进入 review 前拦截机械缺陷与论述-引文不匹配（--force 可显式跳过并留痕）
    if name == "review" and not getattr(args, "force", False):
        try:
            import toolbox
            main_md = proj / "manuscript" / "main.md"
            if main_md.exists():
                text = main_md.read_text(encoding="utf-8", errors="replace")
                tq = list(toolbox.mechanical_check(text))
                # 构造 ref_entries（照搬 toolbox.quality_check 的方式: references.md -> ```bibtex 块 -> parse_bibtex）
                ref_entries = []
                ref_file = proj / "framework" / "references.md"
                if ref_file.exists():
                    ref_text = ref_file.read_text(encoding="utf-8", errors="replace")
                    m_ref = re.search(r"```bibtex\s*(.*?)```", ref_text, re.S)
                    bib = m_ref.group(1) if m_ref else ref_text
                    try:
                        parsed = toolbox.parse_bibtex(bib)
                        if parsed and "error" not in parsed[0]:
                            ref_entries = parsed
                    except Exception:
                        ref_entries = []
                if ref_entries:
                    tq += toolbox.citation_context_check(text, ref_entries)
                tbad = [x for x in tq if x.get("severity") in ("P0", "P1")]
                if tbad:
                    print(f"✗ 文本质量门禁未通过（{len(tbad)} 项 P0/P1），不能进入 review:")
                    for x in tbad[:8]:
                        print(f"  [{x.get('severity')}] {x.get('msg')}")
                    print("  修复后重试；如确认风险，可用 --force 并必须给出 --reason")
                    sys.exit(1)
        except Exception as e:
            print(f"⚠ 文本质量门禁检查失败（继续推进）: {e}")

    # 质量门禁: 进入 submit 前拦截 P0/P1（--force 可显式跳过）
    if name == "submit" and not getattr(args, "force", False):
        try:
            import toolbox
            q = toolbox.quality_check(proj)
            bad = [x for x in q if x.get("severity") in ("P0", "P1")]
            if bad:
                print(f"✗ 质量门禁未通过（{len(bad)} 项 P0/P1），不能进入 submit:")
                for x in bad[:10]:
                    print(f"  [{x.get('severity')}] {x.get('msg')}")
                print("  修复后重试；如确认风险，可显式使用 --force")
                sys.exit(1)
        except Exception as e:
            print(f"⚠ 质量门禁检查失败（继续推进）: {e}")

    if name != cur:
        st["stage"] = name
    st["stages"][cur]["done"] = True
    force_tag = ""
    if getattr(args, "force", False):
        reason = (getattr(args, "reason", None) or "").strip()
        if not reason or reason == "未填原因":
            print("✗ 使用 --force 必须同时给出具体 --reason（说明为何跳过门禁）")
            sys.exit(1)
        force_tag = f" [force:{reason}]"
    st["logs"].append({"t": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "what": f"stage->{name}{force_tag}"})
    save_state(proj, st)
    print(f"✔ 阶段已推进: {STAGE_LABEL[cur]} → {STAGE_LABEL[name]}")
    if name == "framework":
        intake = proj / "intake.md"
        filled = intake.exists() and len(intake.read_text(encoding="utf-8", errors="replace").strip()) > 20
        if not filled:
            print("  提示: 建议先填写项目信息收集 intake.md（Web 项目详情页可编辑），再生成框架")
    if name == "review":
        print("  提示: 审查阶段运行 python {0} review-book 生成任务工作簿".format(sys.argv[0]))
    for rel in STAGE_ARTIFACTS.get(name, []):
        tpl = _template_for(rel, st.get("type", "article"))
        if not (proj / rel).exists() and tpl.exists():
            (proj / rel).parent.mkdir(parents=True, exist_ok=True)
            (proj / rel).write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  已生成模板: {rel}")


def cmd_new(args):
    """生成本阶段缺失的模板文件。"""
    proj = ensure_project(args)
    st = load_state(proj)
    stage = args.stage
    if stage not in STAGES:
        print(f"✗ 未知阶段 {stage}")
        sys.exit(1)
    made = []
    for rel in STAGE_ARTIFACTS.get(stage, []):
        tpl = _template_for(rel, st.get("type", "article"))
        dst = proj / rel
        if not dst.exists() and tpl.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(tpl.read_text(encoding="utf-8"), encoding="utf-8")
            made.append(rel)
    if made:
        print("✔ 已生成:")
        for rel in made:
            print(f"  - {rel}")
    else:
        print("(无新模板生成;全部产物已存在或模板缺失)")


def cmd_check(args):
    """勾选/取消勾选当前阶段检查点。review 阶段勾选时自动运行 quality_check 校验。"""
    proj = ensure_project(args)
    st = load_state(proj)
    ck = read_checklist(args.stage)
    n = args.n
    if not (1 <= n <= len(ck)):
        print(f"✗ 检查点序号越界(1..{len(ck)})")
        sys.exit(1)
    checked = set(st["stages"][args.stage]["checked"])
    stale = sorted(i for i in checked if not (1 <= i <= len(ck)))
    if stale:
        print(f"⚠ 清理历史无效检查点编号(超出 1..{len(ck)}): {stale}")
        checked -= set(stale)
    if args.uncheck:
        checked.discard(n)
        verb = "uncheck"
    else:
        # review 阶段勾选时，自动运行 quality_check 检查 P0/P1
        if args.stage == "review" and not getattr(args, "force", False):
            try:
                import toolbox
                q = toolbox.quality_check(proj)
                p0 = [x for x in q if x.get("severity") == "P0"]
                p1 = [x for x in q if x.get("severity") == "P1"]
                if p0:
                    print(f"⚠ 质量检查发现 {len(p0)} 项 P0 问题（必须修复）:")
                    for x in p0[:5]:
                        print(f"  [P0] {x.get('msg', '')}")
                    print("  请先修复所有 P0 问题再勾选检查点，或使用 --force 跳过")
                    sys.exit(1)
                if p1:
                    print(f"⚠ 质量检查发现 {len(p1)} 项 P1 问题:")
                    for x in p1[:5]:
                        print(f"  [P1] {x.get('msg', '')}")
                    if n == 9:
                        print("  检查点 [9] 要求全部 P0/P1 已关闭，请先修复上述问题")
                        sys.exit(1)
                    print("  建议修复后再勾选，或使用 --force 强制跳过")
            except ImportError:
                pass
            except SystemExit:
                raise
            except Exception as e:
                print(f"⚠ 质量检查执行失败（允许勾选）: {e}")
        checked.add(n)
        verb = "check"
    before = set(st["stages"][args.stage]["checked"])
    if before == checked:
        print(f"(无变化) {STAGE_LABEL[args.stage]} 检查点 [{n}] 已处于目标状态，跳过日志与保存")
        return
    force_tag = ""
    if getattr(args, "force", False):
        reason = (getattr(args, "reason", None) or "").strip()
        if not reason or reason == "未填原因":
            print("✗ 使用 --force 必须同时给出具体 --reason（说明为何跳过质量检查）")
            sys.exit(1)
        force_tag = f" [force:{reason}]"
    st["stages"][args.stage]["checked"] = sorted(checked)
    st["logs"].append({"t": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "what": f"{verb} {args.stage} {n}{force_tag}"})
    save_state(proj, st)
    print(f"{'✗ 已取消' if args.uncheck else '✔ 已勾选'} {STAGE_LABEL[args.stage]} 检查点 [{n}] {ck[n-1][:60]}")


# 按研究方向关键词粗筛的期刊映射。命中则推荐对应学科期刊,未命中用 DEFAULT_JOURNALS
JOURNAL_MAP = [
    (["人工智能", "机器学习", "深度学习", "大模型", "语言模型", "agent", "智能体", "多智能体", "强化学习",
      "神经网络", "transformer", "llm", "nlp", "自然语言", "联邦学习", "隐私计算", "推荐系统", "数据挖掘",
      "安全", "知识图谱", "图神经网络", "扩散模型", "生成模型", "检索", "artificial", "intelligence",
      "deep learning", "neural", "machine learning"],
     ["neurips", "icml", "iclr", "aaai", "acl", "ijcai", "nature-machine-intelligence", "journal-of-machine-learning-research"],
     "人工智能/机器学习"),
    (["视觉", "图像", "cv", "computer vision", "目标检测", "分割", "识别"],
     ["cvpr", "ieee-transactions-on-pattern-analysis-and-machine-intelligence", "iccv", "nature-machine-intelligence"],
     "计算机视觉"),
    (["经济", "金融", "会计", "管理", "营销", "运营", "博弈", "计量", "财政", "贸易",
      "economics", "finance", "accounting", "marketing", "econometrics", "trading", "supply chain", "management", "operations"],
     ["american-economic-review", "econometrica", "quarterly-journal-of-economics", "journal-of-finance",
      "journal-of-financial-economics", "management-science", "the-accounting-review", "journal-of-marketing", "operations-research"],
     "经济/金融/管理"),
    (["医学", "临床", "肿瘤", "癌症", "心血管", "内科", "外科", "药", "健康", "疾病",
      "clinical", "cancer", "cardio", "surgery", "drug", "patient", "disease", "oncology", "medicine", "therapy"],
     ["the-lancet", "nejm", "jama", "the-bmj", "cancer-cell", "journal-of-clinical-oncology", "nature-medicine",
      "gut", "circulation", "blood"],
     "医学/临床"),
    (["生物", "细胞", "分子", "基因", "蛋白", "免疫", "神经科学", "发育", "微生物", "fungi", "fungal",
      "protoplast", "microbial", "yeast", "bacteria", "enzyme", "菌", "育种", "发酵", "biology", "cell",
      "gene", "protein", "immunology", "neuroscience", "genome", "evolution"],
     ["cell", "nature-cell-biology", "molecular-cell", "developmental-cell", "current-biology", "nature-neuroscience",
      "the-embo-journal", "nature-immunology", "nature-genetics", "the-isme-journal"],
     "生命科学"),
    (["物理", "量子", "光学", "光子", "凝聚态", "引力", "宇宙", "粒子", "physics", "quantum", "optics",
      "photonics", "condensed", "gravity", "cosmology", "particle"],
     ["physical-review-letters", "physical-review-x", "prx-quantum", "nature-physics", "nature-photonics",
      "reviews-of-modern-physics", "new-journal-of-physics", "astrophysical-journal-letters"],
     "物理/天文"),
    (["化学", "材料", "催化", "能源", "电池", "纳米", "聚合物", "电化学", "chemistry", "catalyst", "battery",
      "nanoparticle", "polymer", "electrochem", "synthesis", "materials", "energy"],
     ["journal-of-the-american-chemical-society", "angewandte-chemie-international-edition", "nature-chemistry",
      "nature-materials", "advanced-materials", "advanced-energy-materials", "chem", "joule", "acs-nano", "nature-catalysis"],
     "化学/材料/能源"),
    (["地球", "环境", "生态", "气候", "水文", "海洋", "大气", "地质", "遥感", "可持续发展", "climate",
      "ecology", "environmental", "hydrology", "ocean", "atmosphere", "geology", "remote sensing", "sustainability"],
     ["nature-geoscience", "nature-climate-change", "nature-sustainability", "geophysical-research-letters",
      "water-research", "environmental-science-and-technology", "ecology-letters", "global-ecology-and-biogeography", "the-cryosphere"],
     "地球/环境/生态"),
    (["社会", "政治", "心理", "行为", "公共", "政策", "管理", "组织", "教育", "psychology", "sociology",
      "behavior", "policy", "education", "organization", "politics"],
     ["american-sociological-review", "american-political-science-review", "administrative-science-quarterly",
      "organization-science", "strategic-management-journal", "journal-of-consumer-research", "psychological-science"],
     "社会科学"),
    (["数学", "统计", "概率", "优化", "代数", "几何", "拓扑", "数论", "分析", "mathematics", "statistics",
      "probability", "optimization", "algebra", "geometry", "topology", "number theory"],
     ["annals-of-mathematics", "acta-mathematica", "journal-of-the-american-mathematical-society",
      "inventiones-mathematicae", "econometric-theory", "advances-in-mathematics"],
     "数学"),
    (["综述", "review", "survey", "展望", "视角"],
     ["nature-reviews-physics", "nature-reviews-materials", "nature-reviews-chemistry", "reviews-of-modern-physics",
      "physics-reports", "annual-review-of-economics", "journal-of-economic-literature", "trends-in-ecology-and-evolution"],
     "综述类"),
]
DEFAULT_JOURNALS = ["nature", "science", "pnas", "nature-communications", "science-advances"]


def cmd_recommend(args):
    """按研究方向粗筛候选期刊,输出推荐工作单。"""
    topic = (args.topic or "").strip()
    proj = None
    if getattr(args, "dir", None):
        cand = Path(args.dir).resolve()
        if (cand / STATE_FILE).exists():
            proj = cand
    if proj is None:
        proj = find_project()
        if proj is None:
            found = list_projects_in_papers()
            if len(found) == 1:
                proj = found[0]

    skills_root = Path.home() / ".dsh" / "skills"
    have = set()
    if skills_root.exists():
        for d in skills_root.iterdir():
            if d.is_dir() and (d / "SKILL.md").exists():
                have.add(d.name.lower())

    def in_lib(name):
        if name in have:
            return True
        return any(s.startswith(name + "-") for s in have if name)

    low = topic.lower()
    hits = []
    for keys, journals, disc in JOURNAL_MAP:
        if any(k in low for k in keys):
            hits.append((disc, journals))
    if not hits:
        hits = [("综合/跨学科", DEFAULT_JOURNALS)]
    print(f"研究方向: {topic}")
    print(f"命中学科: {'、'.join(h[0] for h in hits)}")
    print()
    print("推荐候选期刊(按优先序,将在期刊调研阶段精筛):")
    rows = []
    for disc, journals in hits:
        print(f"  [{disc}]")
        for j in journals:
            ok = "✓库内" if in_lib(j) else "△库外"
            rows.append((j, disc, ok))
            print(f"    - {j} {ok}")
    print()
    print("下一步: select-venue 技能精筛(影响因子/分区/审稿周期/匹配度) + study-exemplars 范文拆解")
    print("       确认后写入 journal/shortlist.md 与 journal/chosen.md")
    if proj is not None:
        out = proj / "journal" / "shortlist.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        lines = ["# 候选期刊/会议短名单(自动推荐初稿)"]
        lines.append(f"> 方向: {topic} | 生成: {now}")
        lines.append("")
        lines.append("| 期刊/会议 | 学科 | 库内? | 影响因子/级别 | 审稿周期 | 匹配度(1-5) | 理由 |")
        lines.append("|-----------|------|-------|--------------|----------|-------------|------|")
        for j, disc, ok in rows:
            lines.append(f"| {j} | {disc} | {ok} | (待查) | (待查) | | |")
        lines.append("")
        lines.append("> 精筛任务: select-venue 技能按方向/贡献类型评分;检索近 3 年该方向论文的期刊分布交叉验证;")
        lines.append("> 读期刊包 SKILL.md 核对投稿要求;最终与用户确认 1 个主投 + 2 个备选。")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"✔ 推荐工作单已写入: {out}")


def cmd_review_book(args):
    proj = ensure_project(args)
    st = load_state(proj)
    out = proj / "review" / "tasks.md"
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "# 审查任务工作簿 (Review Tasks)",
        "",
        f"项目: {st['topic']}  |  生成时间: {now}",
        "",
        "## 审查流水线(逐项执行,产物写入 review/ 目录)",
        "",
        "| # | 审查项 | 调用技能 | 产物 | 状态 |",
        "|---|--------|---------|------|------|",
        "| 1 | Claim-证据映射审计 | verify-claims | review/claims.md | ⬜ |",
        "| 2 | 引用真实性核验 | verify-citations + nature-ref-verifier | review/citations.md | ⬜ |",
        "| 3 | 统计报告审计 | stats-reporting-audit | review/statistics.md | ⬜ |",
        "| 4 | 模拟审稿(三维独立: Methods/Contribution/Clarity) | simulate-reviewers / nature-reviewer / paper-reviewer | review/mock-reviews.md | ⬜ |",
        "| 5 | 结构与叙事审计 | refactor-structure / reflect-paper | review/structure.md | ⬜ |",
        "| 6 | 语言润色与 AI 味去除 | nature-polishing / polish-prose | review/prose.md | ⬜ |",
        f"| 7 | 期刊格式 preflight | {'<journal skill 的 submission/preflight>' if st['journal'] else 'preflight-check / 对应期刊 skill'} | review/format.md | ⬜ |",
        "| 8 | 原创性检查 | check-originality | review/originality.md | ⬜ |",
        "",
        "## 三维独立审稿(PaperSpine: 每发现须带 evidence_status + 具体修复动作)",
        "",
        "| # | 视角 | 关注的反对点 | 发现(evidence_status) | 预先修复 | 状态 |",
        "|---|------|--------------|------------------------|----------|------|",
        "| A1 | Methods & Reproducibility | 技术可靠性/证据充分性/可复现/缺消融与基线 | | | ⬜ |",
        "| B1 | Contribution & Novelty | 新颖性/显著性/与前作差异/引用可信度 | | | ⬜ |",
        "| C1 | Structure & Clarity | 结构/声称清晰度/图表可读/期刊惯例 | | | ⬜ |",
        "",
        "## 问题清单(审查后逐条填写)",
        "",
        "| # | 严重级 | 来源(审查项) | 位置(章节/行) | 问题描述 | 建议修复 | 状态 |",
        "|---|--------|--------------|---------------|----------|----------|------|",
        "| 1 | P0 | | | | | 待修复 |",
        "",
        "> P0=必须修复(学术诚信/致命错误) P1=强烈建议 P2=可选润色。全部 P0/P1 关闭后进入 submit 阶段。",
        "",
        "## 修改记录",
        "",
        "| 时间 | 修改内容 | 对应问题 # |",
        "|------|----------|-----------|",
        "",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✔ 审查工作簿已生成: {out}")
    print("  审查流水线 8 项请按 SKILL.md(paper-workbench)指引逐项执行并填写问题清单")


def cmd_review_auto(args):
    """半自动执行审查流水线中可代码化的 7 步：
    引用核验 / 统计审计 / 原创性 / 格式 preflight / 结构叙事 / PaperSpine 维度 / 学术规范(贡献/承诺映射/AI味/章节)。
    产物写入 review/citations.md、review/statistics.md、review/originality.md、review/format.md、review/structure.md、review/paperspine.md，
    并把问题按 P0/P1/P2 汇总进 review/tasks.md 问题清单。
    AI 类审查(claims/mock-reviews/prose)仍需按工作簿由 agent 执行。
    """
    proj = ensure_project(args)
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        import toolbox
    except Exception as e:
        print(f"✗ 无法加载 toolbox: {e}")
        sys.exit(1)
    # 文章类型感知(与 toolbox.quality_check 同一套): review 综述 / thesis 毕业论文
    ptype = "article"
    st_path = proj / "state.json"
    if st_path.exists():
        try:
            ptype = json.loads(st_path.read_text(encoding="utf-8")).get("type", "article")
        except Exception:
            pass
    is_review = ptype == "review"
    review_dir = proj / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    issues = []  # (severity, source, location, msg)

    # 1) 引用核验
    print("== [1/7] 引用核验 (toolbox.verify_bibtex, Crossref) ==")
    ref_file = proj / "framework" / "references.md"
    lines = ["# 引用核验报告", "", f"> 生成: {now} | 工具: toolbox.verify_bibtex (Crossref)", ""]
    if not ref_file.exists():
        lines.append("> ⚠ 缺少 framework/references.md，无法核验")
        issues.append(("P1", "引用核验", "", "缺少 framework/references.md"))
    else:
        ref_text = ref_file.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"```bibtex\s*(.*?)```", ref_text, re.S)
        bib = m.group(1) if m else ref_text
        results = toolbox.verify_bibtex(bib)
        errs = [r for r in results if r.get("status") in ("not_found", "mismatch")]
        lines.append(f"共 {len(results)} 条，异常 {len(errs)} 条（not_found/mismatch）：")
        lines.append("")
        lines.append("| 状态 | 条目 | 说明 |")
        lines.append("|------|------|------|")
        for r in results:
            lines.append("| {} | {} | {} |".format(r.get("status"), (r.get("id") or "")[:40], (r.get("note") or "")[:80]))
        for r in errs:
            issues.append(("P1", "引用核验", (r.get("id") or ""), (r.get("note") or "")))
        if not errs:
            lines.append("")
            lines.append("- ✅ 全部可核验文献通过 Crossref 校验（无 DOI 的条目为 no_doi，需人工补充）")
    (review_dir / "citations.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  → review/citations.md")

    # 2) 统计审计
    print("== [2/6] 统计报告审计 (toolbox.audit_stats) ==")
    main_md = proj / "manuscript" / "main.md"
    lines = ["# 统计报告审计", "", f"> 生成: {now} | 工具: toolbox.audit_stats", ""]
    if not main_md.exists():
        lines.append("> ⚠ 缺少 manuscript/main.md")
        issues.append(("P0", "统计审计", "", "缺少 manuscript/main.md"))
    else:
        text = main_md.read_text(encoding="utf-8", errors="replace")
        res = [i for i in toolbox.audit_stats(text) if i.get("type") != "ok"]
        lines.append(f"发现 {len(res)} 处统计报告疑点：")
        for i in res:
            lines.append(f"- L{i.get('line')}: {i.get('msg')}")
            issues.append(("P1", "统计审计", "L{}".format(i.get("line")), i.get("msg", "")))
        if not res:
            lines.append("- ✅ 未发现明显统计报告缺失")
    (review_dir / "statistics.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  → review/statistics.md")

    # 3) 原创性
    print("== [3/7] 原创性检查 (toolbox.originality_check) ==")
    lines = ["# 原创性/重复度检查", "", f"> 生成: {now} | 工具: toolbox.originality_check (本地 n-gram)", ""]
    if not main_md.exists():
        lines.append("> ⚠ 缺少 manuscript/main.md")
    else:
        corpus = str(proj / "data") if (proj / "data").exists() else None
        results = toolbox.originality_check(main_md.read_text(encoding="utf-8", errors="replace"), corpus)
        if results:
            lines.append(f"与本地语料发现 {len(results)} 个高重叠文件（重叠 >5%）：")
            for r in results[:10]:
                lines.append(f"- {r['file']}: 重叠 {r['overlap']:.1%}")
                issues.append(("P2", "原创性", "", "与 {} 重叠 {:.1%}".format(Path(r["file"]).name, r["overlap"])))
        else:
            lines.append("- ✅ 未发现与本地语料的高重叠片段（无语料或重叠 ≤5%）")
    (review_dir / "originality.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  → review/originality.md")

    # 4) 格式 preflight
    print("== [4/7] 格式 preflight (占位符/章节完整性) ==")
    lines = ["# 格式 Preflight", "", f"> 生成: {now} | 规则: 占位符/章节完整性/图表规划", ""]
    if not main_md.exists():
        lines.append("> ⚠ 缺少 manuscript/main.md")
    else:
        text = main_md.read_text(encoding="utf-8", errors="replace")
        import re as _re_ph
        _ph_patterns = [
            (_re_ph.compile(r'\[TBD[^\]]*\]'), "[TBD]"),
            (_re_ph.compile(r'\[DATA REQUIRED\]'), "[DATA REQUIRED]"),
            (_re_ph.compile(r'<!--\s*/?INSERT-(?:FIG|TAB)\s*-->'), "<!-- INSERT-FIG/TAB -->"),
        ]
        n_p0 = sum(len(p.findall(text)) for p, _ in _ph_patterns)
        n_p1 = text.count("___")
        if n_p0:
            ph_detail = ", ".join(f"{label} {len(p.findall(text))}" for p, label in _ph_patterns if p.findall(text))
            lines.append(f"- [ ] 占位符残留 {n_p0} 处（{ph_detail}）")
            issues.append(("P0", "格式", "", f"占位符残留 {n_p0} 处（{ph_detail}）"))
        else:
            lines.append("- [x] 无 [TBD]/[DATA REQUIRED]/INSERT-FIG 占位符")
        if n_p1:
            lines.append(f"- [ ] 下划线占位符 ___ 残留 {n_p1} 处")
            issues.append(("P1", "格式", "", f"下划线占位符 ___ 残留 {n_p1} 处"))
        else:
            lines.append("- [x] 无 ___ 占位符")
        for sec in ("Introduction", "Methods", "Results", "Discussion", "References"):
            if sec not in text:
                # 综述可用合并/主题章节(如 Results and Discussion), 不强制五个全有
                if is_review and sec in ("Results", "Discussion"):
                    lines.append(f"- [~] 综述未检测到独立章节 {sec}(允许 Results and Discussion 合并节或主题式章节)")
                    continue
                lines.append(f"- [ ] 缺少章节: {sec}")
                issues.append(("P1", "格式", "", f"缺少章节 {sec}"))
            else:
                lines.append(f"- [x] 章节存在: {sec}")
        if "Data Availability" not in text and "data availability" not in text.lower() and "data and materials availability" not in text.lower():
            lines.append("- [ ] 缺少章节: Data Availability")
            issues.append(("P1", "格式", "", "缺少章节 Data Availability"))
        else:
            lines.append("- [x] 章节存在: Data Availability")
        fig_plan = proj / "framework" / "figures.md"
        if fig_plan.exists():
            plan_text = fig_plan.read_text(encoding="utf-8", errors="replace")
            n_figs = len(re.findall(r"^\|\s*图\d+", plan_text, re.M))
            n_tabs = len(re.findall(r"^\|\s*表\d+", plan_text, re.M))
            min_figs, min_tabs = (3, 2) if is_review else (5, 3)
            lines.append(f"- 图表规划: {n_figs} 图 / {n_tabs} 表（要求 ≥{min_figs} 图 {min_tabs} 表）")
            if n_figs < min_figs:
                issues.append(("P1", "格式", "", f"图表规划不足: 图 {n_figs}/{min_figs}"))
            if n_tabs < min_tabs:
                issues.append(("P1", "格式", "", f"图表规划不足: 表 {n_tabs}/{min_tabs}"))
        else:
            lines.append("- [ ] 缺少 framework/figures.md")
            issues.append(("P1", "格式", "", "缺少 framework/figures.md"))
        # 参考文献数量与近五年比例
        ref_file = proj / "framework" / "references.md"
        if ref_file.exists():
            ref_text = ref_file.read_text(encoding="utf-8", errors="replace")
            m_ref = re.search(r"```bibtex\s*(.*?)```", ref_text, re.S)
            bib = m_ref.group(1) if m_ref else ref_text
            try:
                parsed = toolbox.parse_bibtex(bib)
                if parsed and "error" not in parsed[0]:
                    n_refs = len(parsed)
                    import datetime as _dt
                    cy = _dt.datetime.now().year
                    recent = sum(1 for e in parsed if str(e.get("year", "")).isdigit() and int(e["year"]) >= cy - 4)
                    ratio = recent / n_refs if n_refs else 0
                    min_refs = 80
                    intake_path = proj / "intake.md"
                    if intake_path.exists():
                        it_text = intake_path.read_text(encoding="utf-8", errors="replace")
                        m_ir = re.search(r"参考文献数量[^\n]{0,40}?[：:]\s*(\d+)\s*[-~—到至]\s*(\d+)", it_text)
                        if m_ir:
                            min_refs = int(m_ir.group(1))
                    lines.append(f"- 参考文献: {n_refs} 条（要求 ≥{min_refs}），近5年 {ratio:.0%}（要求 ≥40%）")
                    if n_refs < min_refs:
                        issues.append(("P1", "格式", "", f"参考文献数量偏少: {n_refs}/{min_refs}"))
                    if ratio < 0.4:
                        issues.append(("P1", "格式", "", f"近5年文献占比 {ratio:.0%} < 40%"))
                else:
                    lines.append("- [ ] framework/references.md 无可解析的 BibTeX 文献")
                    issues.append(("P1", "格式", "", "references.md 无可解析 BibTeX"))
            except Exception as e:
                lines.append(f"- ⚠ 参考文献解析失败: {e}")
        else:
            lines.append("- [ ] 缺少 framework/references.md")
            issues.append(("P1", "格式", "", "缺少 framework/references.md"))
    (review_dir / "format.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  → review/format.md")

    # 5) 结构叙事审计（讲好一个故事）
    print("== [5/7] 结构叙事审计 (toolbox.narrative_check) ==")
    lines = ["# 结构叙事审计", "", f"> 生成: {now} | 工具: toolbox.narrative_check（摘要四要素/引言漏斗/结果证据密度/讨论回扣）", ""]
    if not main_md.exists():
        lines.append("> ⚠ 缺少 manuscript/main.md")
    else:
        text = main_md.read_text(encoding="utf-8", errors="replace")
        # 综述无自己的 Results/Discussion 回扣, 跳过对应叙事项
        skip = {"narrative_results", "narrative_voice"} | ({"narrative_discussion"} if is_review else set())
        res = [i for i in toolbox.narrative_check(text) if i.get("type") != "ok" and i.get("type") not in skip]
        lines.append(f"发现 {len(res)} 处叙事问题：")
        for i in res:
            lines.append(f"- [{i.get('severity')}] {i.get('type')}: {i.get('msg')}")
            issues.append((i.get("severity"), "结构叙事", "", i.get("msg", "")))
        if not res:
            lines.append("- ✅ 叙事结构未发现明显问题")
    (review_dir / "structure.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  → review/structure.md")

    # 6) PaperSpine 维度报告（贡献门禁 / 承诺-证据映射 / AI味 / 章节经济性）
    print("== [6/7] PaperSpine 维度报告 (contribution / results / humanize / economy) ==")
    lines = ["# PaperSpine 维度报告", "", f"> 生成: {now} | 工具: toolbox.contribution_check / results_validation_check / humanize_check / section_economy_check", ""]
    lang = "zh"
    if main_md.exists():
        mtext = main_md.read_text(encoding="utf-8", errors="replace")
        lang = "zh" if re.search(r"[一-鿿]", mtext) else "en"
    for name, fn in [("贡献门禁", getattr(toolbox, "contribution_check", None)),
                     ("承诺-证据映射", getattr(toolbox, "results_validation_check", None)),
                     ("章节经济性", getattr(toolbox, "section_economy_check", None))]:
        if fn is None:
            lines.append(f"### {name}")
            lines.append("- ⚠ 当前 toolbox 无此检查（旧版本），跳过。")
            continue
        try:
            res = fn(proj)
            lines.append(f"### {name}")
            if res:
                for i in res:
                    lines.append(f"- [{i.get('severity')}] {i.get('type')}: {i.get('msg')}")
                    issues.append((i.get("severity"), "PaperSpine-" + name, "", i.get("msg", "")))
            else:
                lines.append("- ✅ 通过")
        except Exception as e:
            lines.append(f"- ⚠ {name} 执行失败: {e}")
    if main_md.exists() and hasattr(toolbox, "humanize_check"):
        human = toolbox.humanize_check(main_md.read_text(encoding="utf-8", errors="replace"), lang)
        lines.append("### AI味/人性化(D1-D5)")
        if human["findings"]:
            for f in human["findings"]:
                lines.append(f"- [{f.get('severity')}] {f.get('type')}: {f.get('msg')}")
                issues.append((f.get("severity"), "PaperSpine-humanize", "", f.get("msg", "")))
        else:
            lines.append("- ✅ 未发现明显 AI 味")
    (review_dir / "paperspine.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  → review/paperspine.md")

    # 汇总进 review/tasks.md 问题清单
    tasks = review_dir / "tasks.md"
    if tasks.exists():
        ttext = tasks.read_text(encoding="utf-8", errors="replace")
        existing = re.findall(r"^\| (\d+) \| P\d \|", ttext, re.M)  # 只统计问题清单行(第3列为 P0/P1/P2)
        start = (max([int(x) for x in existing]) if existing else 0) + 1
        new_rows = [
            "| {} | {} | {} | {} | {} | (见 review/*.md) | 待修复 |".format(i, sev, src, loc, msg)
            for i, (sev, src, loc, msg) in enumerate(issues, start)
        ]
        if new_rows:
            marker = "\n> P0=必须修复"
            block = "\n".join(new_rows) + "\n"
            if marker in ttext:
                ttext = ttext.replace(marker, block + marker, 1)
            else:
                ttext += "\n" + block
            tasks.write_text(ttext, encoding="utf-8")
    print("=" * 40)
    print(f"汇总: {len(issues)} 个问题已写入 review/tasks.md")
    for sev, src, loc, msg in issues:
        print(f"  [{sev}] {src} {loc} {msg}")
    print("AI 类审查(claims/mock-reviews/prose)请按 review-book 工作簿继续。")

    # 自动汇总 review/final-report.md
    p0_issues = [(i, s, l, m) for i, (s, l_src, l, m) in enumerate(issues, 1) if s == "P0"]
    p1_issues = [(i, s, l, m) for i, (s, l_src, l, m) in enumerate(issues, 1) if s == "P1"]
    p2_count = len(issues) - len(p0_issues) - len(p1_issues)

    # 7) 学术规范审查（E31-E45，移植自 review-gap-report.md）
    print("== [7/7] 学术规范审查 (toolbox.academic_norm_check) ==")
    lines = ["# 学术规范审查", "", f"> 生成: {now} | 工具: toolbox.academic_norm_check（E31-E45）", ""]
    if not main_md.exists():
        lines.append("> ⚠ 缺少 manuscript/main.md")
    else:
        text = main_md.read_text(encoding="utf-8", errors="replace")
        ref_entries = []
        if ref_file.exists():
            ref_text = ref_file.read_text(encoding="utf-8", errors="replace")
            m_ref = re.search(r"```bibtex\s*(.*?)```", ref_text, re.S)
            bib = m_ref.group(1) if m_ref else ref_text
            try:
                parsed = toolbox.parse_bibtex(bib)
                if parsed and "error" not in parsed[0]:
                    ref_entries = parsed
            except Exception:
                ref_entries = []
        results = toolbox.academic_norm_check(text, ref_entries)
        # 按 P0/P1/P2 分组展示
        p0 = [r for r in results if r.get("severity") == "P0"]
        p1 = [r for r in results if r.get("severity") == "P1"]
        p2 = [r for r in results if r.get("severity") == "P2"]
        lines.append(f"共 {len(results)} 项问题（P0: {len(p0)} / P1: {len(p1)} / P2: {len(p2)}）：")
        lines.append("")
        for sev_label, items in (("P0 必须修复", p0), ("P1 建议修复", p1), ("P2 提示", p2)):
            if items:
                lines.append(f"### {sev_label}")
                for r in items:
                    line_str = f"L{r.get('line')}" if r.get("line") else ""
                    lines.append(f"- [{r.get('severity')}] {r.get('type')}: {r.get('msg')} {line_str}".rstrip())
                    issues.append((r.get("severity"), "学术规范", line_str, r.get("msg", "")))
        if not results:
            lines.append("- ✅ 学术规范未发现明显问题")
    (review_dir / "academic-norm.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"  → review/academic-norm.md")

    # 8) 语言质量（LanguageTool；2026-08-22 开源集成；仅信息性，不计入门禁：
    #    拉丁学名会被拼写规则误报，避免污染 P0/P1 清单；人工按报告抽查）
    print("== [8/8] 语言质量 (lang_check / LanguageTool) ==")
    lines = ["# 语言质量检查", "", f"> 生成: {now} | 工具: lang_check.py (LanguageTool, picky 模式)",
             "> 说明: 本报告为信息性参考，不计入门禁问题清单（拉丁学名易被拼写规则误报）。", ""]
    if not main_md.exists():
        lines.append("> ⚠ 缺少 manuscript/main.md")
    else:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import lang_check as _lc
            text = main_md.read_text(encoding="utf-8", errors="replace")
            lang = "en-US" if re.search(r"[A-Za-z]{4,}", text[:2000]) else "zh-CN"
            matches, mode = _lc.lang_check(_lc.strip_markdown(text), lang, prefer_local=True)
            body = _lc.render_report(str(main_md), matches, mode, lang, 60)
            lines.append(body.split("\n", 1)[1] if "\n" in body else body)
            print(f"  → review/language.md（{len(matches)} 条，模式 {mode}）")
        except Exception as e:
            lines.append(f"> ⚠ 语言检查跳过（网络/依赖不可用）: {e}")
            print(f"  → 语言检查跳过: {e}")
    (review_dir / "language.md").write_text("\n".join(lines), encoding="utf-8")

    # 各审查项状态
    # 各审查项状态
    review_items = [
        ("引用核验", "review/citations.md"),
        ("统计审计", "review/statistics.md"),
        ("原创性", "review/originality.md"),
        ("格式 preflight", "review/format.md"),
        ("结构审计", "review/structure.md"),
        ("PaperSpine 维度", "review/paperspine.md"),
        ("学术规范", "review/academic-norm.md"),
        ("语言质量", "review/language.md"),
    ]
    summary_rows = []
    for name, path in review_items:
        fp = review_dir / path.split("/")[-1]
        if fp.exists():
            fc = fp.read_text(encoding="utf-8", errors="replace")
            issue_count = fc.count("[P0]") + fc.count("[P1]") + fc.count("- [ ]")
            if issue_count == 0 and "✅" in fc:
                summary_rows.append(f"| {name} | {path} | ✅ 通过 | 0 |")
            else:
                summary_rows.append(f"| {name} | {path} | ⚠ 需修改 | {issue_count} |")
        else:
            summary_rows.append(f"| {name} | {path} | ⬜ 未执行 | - |")

    # AI 类审查（claims/mock-reviews/prose）
    for name, path in [("Claim-证据", "review/claims.md"), ("模拟审稿", "review/mock-reviews.md"), ("语言润色", "review/prose.md")]:
        fp = review_dir / path.split("/")[-1]
        if fp.exists():
            summary_rows.append(f"| {name} | {path} | ✅ 已执行 | - |")
        else:
            summary_rows.append(f"| {name} | {path} | ⬜ 未执行 | - |")

    # 遗留问题
    leftover_rows = []
    for i, (sev, src, loc, msg) in enumerate(issues, 1):
        if sev in ("P0", "P1"):
            leftover_rows.append(f"| {i} | {sev} | {src}: {msg} | 待修复 |")

    # 投稿结论
    can_submit = len(p0_issues) == 0 and len(p1_issues) == 0

    fr_lines = [
        "# 审查终报告",
        "",
        f"> 生成: {now} | 由 review-auto 自动汇总",
        "",
        "## 审查汇总",
        "| 审查项 | 产物 | 结论(通过/需修改) | 关键问题数 |",
        "|--------|------|-------------------|-----------|",
    ]
    fr_lines.extend(summary_rows)
    fr_lines.extend([
        "",
        f"## 遗留问题(P0/P1 必须关闭) — 共 {len(p0_issues)} P0 / {len(p1_issues)} P1 / {p2_count} P2",
        "| # | 严重级 | 问题 | 状态 |",
        "|---|--------|------|------|",
    ])
    if leftover_rows:
        fr_lines.extend(leftover_rows)
    else:
        fr_lines.append("| - | - | 无 P0/P1 遗留问题 | ✅ |")
    fr_lines.extend([
        "",
        "## 投稿结论",
        f"- [{'x' if can_submit else ' '}] 可投稿(全部 P0/P1 已关闭)",
        f"- [{' ' if can_submit else 'x'}] 需继续修改(见遗留问题)",
    ])
    (review_dir / "final-report.md").write_text("\n".join(fr_lines), encoding="utf-8")
    print(f"  → review/final-report.md ({'可投稿' if can_submit else '需修改: ' + str(len(p0_issues)) + ' P0, ' + str(len(p1_issues)) + ' P1'})")


def cmd_summary(args):
    proj = ensure_project(args)
    st = load_state(proj)
    print(f"== 论文工作台汇总: {st['topic']} ==")
    print(f"目标期刊: {st['journal'] or '(未定)'} | 类型: {st.get('type', 'article')} | 当前阶段: {STAGE_LABEL.get(st['stage'])}")
    for s in stages_for(st):
        ck = read_checklist(s)
        checked = st["stages"][s]["checked"]
        valid = [i for i in checked if 1 <= i <= len(ck)]
        invalid = sorted(set(checked) - set(valid))
        miss = missing_artifacts(proj, st, s)
        note = f"，⚠无效编号 {invalid}" if invalid else ""
        print(f"  [{s}] {'✅' if st['stages'][s]['done'] else ('🔄' if s==st['stage'] else '⬜')} "
              f"检查点 {len(valid)}/{len(ck)}{note} | 产物缺失: {', '.join(miss) if miss else '无'}")
    logs = st.get("logs", [])
    if logs:
        print("\n最近动作:")
        for lg in logs[-5:]:
            print(f"  {lg['t'][:19]} {lg['what']}")


def cmd_doctor(args):
    proj = ensure_project(args)
    st = load_state(proj)
    print(f"== 健康诊断: {proj} ==")
    issues = 0
    for s in stages_for(st):
        ck = read_checklist(s)
        checked = set(st["stages"][s]["checked"])
        valid = {i for i in checked if 1 <= i <= len(ck)}
        inval = sorted(checked - valid)
        if inval:
            print(f"  ⚠ [{s}] 含无效检查点编号(越界): {inval}——已从完成度统计中剔除，请用 check 或手工修正 state.json")
            issues += 1
        miss = missing_artifacts(proj, st, s)
        if st["stages"][s]["done"]:
            if miss:
                print(f"  ⚠ [{s}] 已标记完成但缺产物: {', '.join(miss)}")
                issues += 1
            if len(ck) and len(valid) < len(ck):
                print(f"  ⚠ [{s}] 已标记完成但检查点未全勾 ({len(valid)}/{len(ck)})")
                issues += 1
        elif s == st["stage"]:
            if miss:
                print(f"  ⚠ [{s}] 当前阶段缺产物: {', '.join(miss)}")
                issues += 1
    if issues == 0:
        print("  ✔ 未发现问题")
    else:
        print(f"\n  🚫 发现 {issues} 项待修复——修复前推进阶段会被门禁拦截（--force 会写入留痕日志）")


def cmd_import(args):
    """导入外部稿件(docx/pdf) → manuscript/imported_<fmt>.md,便于审阅对比。"""
    proj = ensure_project(args)
    src = args.file
    if not Path(src).exists():
        print(f"✗ 文件不存在: {src}")
        sys.exit(1)
    try:
        import toolbox
    except Exception as e:
        print(f"✗ 无法加载 toolbox: {e}")
        sys.exit(1)
    ext = Path(src).suffix.lower().lstrip(".")
    if ext == "pdf":
        out, stats = toolbox.import_pdf(src, str(proj / "manuscript" / "imported_pdf.md"), args.max_pages)
    elif ext == "docx":
        out, stats = toolbox.import_docx(src, str(proj / "manuscript" / "imported_docx.md"),
                                         str(proj / "manuscript" / "imported_docx_images"))
    else:
        print(f"✗ 不支持的格式: {ext}(支持 pdf / docx)")
        sys.exit(1)
    print(f"✔ 已导入 → {out}")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("\n提示: 对比后如需替换正文, 把 imported_*.md 复制为 manuscript/main.md。")



def cmd_refs_fix(args):
    """参考文献核验/修复管线（委托 refs_pipeline）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import refs_pipeline
    if not hasattr(args, "max"):
        args.max = 60
    refs_pipeline.cmd_fix(args)


def cmd_rebuttal(args):
    """审稿意见回复草稿（委托 rebuttal）。"""
    proj = ensure_project(args)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import rebuttal
    except ImportError as e:
        print(f"✗ rebuttal 不可用: {e}")
        sys.exit(1)
    args.dir = str(proj)
    rebuttal.cmd_rebuttal(args)


def cmd_litmap(args):
    """选题文献地图：主题聚类+年份分布+缺口候选（委托 litmap）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import litmap
    except ImportError as e:
        print(f"✗ litmap 不可用: {e}")
        sys.exit(1)
    if not getattr(args, "standalone", False):
        proj = ensure_project(args)
        args.dir = str(proj)
    litmap.cmd_litmap(args)


def cmd_generate(args):
    """分批生成管线：契约先行 + 分段生成 + 段间门禁（见 分批生成方案）。"""
    proj = ensure_project(args)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import staged_gen as sg
    st = load_state(proj)
    act = args.action
    if act == "init":
        r = sg.gen_init(proj, force=args.force)
    elif act == "contract":
        r = sg.gen_contract(proj, use_ai=args.ai, project_state=st)
    elif act == "section":
        if not args.sid:
            print("✗ section 需要 --sid（如 --sid S1）")
            sys.exit(1)
        if args.accept and not getattr(args, "reason", None):
            print("✗ --accept 必须带 --reason（说明为何跳过门禁, 理由会写入 gen_state 与台账）")
            sys.exit(1)
        if getattr(args, "via", "normal") == "subagent" and args.accept:
            # 子代理通道未实现 --accept 语义, 显式报错（此前静默忽略, 用户以为跳过了门禁）
            print("✗ --via subagent 通道暂不支持 --accept（请走默认通道, 或去掉 --accept 正常过门禁）")
            sys.exit(1)
        if getattr(args, "via", "normal") == "subagent":
            # 可选通道: 经 dsh 子代理会话写作（产出无信任豁免, 仍过同套门禁）；
            # 不传 --via 时此分支不进, 原路径逐字节不变
            import subagent_writer
            r = subagent_writer.gen_section_via_subagent(proj, args.sid, timeout=args.timeout,
                                                         retry=args.retry, dry_run=args.dry_run)
            if r.get("prompt"):
                print(r["prompt"])
                return
            for i in r.get("issues", []):
                print(f"  [{i['severity']}] {i['type']}: {i['msg']}")
        else:
            r = sg.gen_section(proj, args.sid, dry_run=args.dry_run, accept=args.accept, retry=args.retry,
                               accept_reason=getattr(args, "reason", None))
            if r.get("prompt"):
                print(r["prompt"])
                return
            for i in r.get("issues", []):
                print(f"  [{i['severity']}] {i['type']}: {i['msg']}")
    elif act == "status":
        r = sg.gen_status(proj)
        if r.get("ok"):
            print(f"契约锁定: {'是' if r['locked'] else '否（段落生成会被拒绝）'}")
            for row in r["rows"]:
                print(f"  {row['sid']} [{row['status']}] {row['title'][:40]} (尝试 {row['attempts']}) {row['file']}")
            return
    elif act == "assemble":
        r = sg.gen_assemble(proj)
    elif act == "tables":
        if args.extract:
            r = sg.tables_extract(proj, md_path=args.src)
        else:
            r = sg.gen_tables(proj, tid=getattr(args, "tid", None), gen=args.gen)
    elif act == "abstract":
        r = sg.gen_abstract(proj, dry_run=args.dry_run, retry=args.retry, max_words=args.max_words)
        if r.get("prompt"):
            print(r["prompt"])
            return
        for i in r.get("issues", []):
            print(f"  [{i['severity']}] {i['type']}: {i['msg']}")
    elif act == "delegate":
        r = sg.gen_delegate(proj, timeout=args.timeout)
        if r.get("ok") and r.get("text"):
            print(r["text"])
    elif act == "parallel":
        import parallel_gen
        r = parallel_gen.gen_parallel(proj, concurrency=args.concurrency,
                                      timeout=args.timeout)
        for sid, o in (r.get("sections") or {}).items():
            print("  %s [%s] %s via=%s" % (sid, o.get("status", "?"),
                                            o.get("file", ""), o.get("via", "parallel")))
    else:
        print(f"✗ 未知动作: {act}")
        sys.exit(1)
    print(("✔ " if r.get("ok") else "✗ ") + str(r.get("msg", "")))
    if not r.get("ok"):
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(prog="wb", description="Paper Workbench CLI — 论文写作审查全流程工作台")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="初始化论文项目")
    p.add_argument("topic", help="研究方向(一句话)")
    p.add_argument("--journal", default="", help="目标期刊/会议(可留空,自动推荐)")
    p.add_argument("--dir", default=None, help="项目父目录(默认当前目录)")
    p.add_argument("--lang", default="zh", choices=["zh", "en"], help="写作语言")
    p.add_argument("--type", default="article", choices=ARTICLE_TYPES, help="文章类型: article 论文 / letter 短文 / review 综述 / thesis 毕业论文")
    p.set_defaults(fn=cmd_init)

    for name, help_ in [("status", "查看状态"), ("next", "下一步动作"), ("summary", "汇总"), ("doctor", "健康诊断")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--dir", default=None, help="论文项目目录(默认自动查找)")
        p.set_defaults(fn={"status": cmd_status, "next": cmd_next, "summary": cmd_summary, "doctor": cmd_doctor}[name])

    p = sub.add_parser("stage", help="推进阶段")
    p.add_argument("name", choices=STAGES)
    p.add_argument("--dir", default=None, help="论文项目目录(默认自动查找)")
    p.add_argument("--force", action="store_true", help="跳过检查点未全勾的校验")
    p.add_argument("--reason", default=None, help="强制推进原因(留痕,建议填写)")
    p.set_defaults(fn=cmd_stage)

    p = sub.add_parser("new", help="生成本阶段模板")
    p.add_argument("stage", choices=STAGES)
    p.add_argument("--dir", default=None, help="论文项目目录(默认自动查找)")
    p.set_defaults(fn=cmd_new)

    p = sub.add_parser("check", help="勾选检查点")
    p.add_argument("stage", choices=STAGES)
    p.add_argument("n", type=int)
    p.add_argument("--uncheck", action="store_true")
    p.add_argument("--force", action="store_true", help="跳过质量检查，强制勾选")
    p.add_argument("--reason", default=None, help="强制勾选原因(留痕,建议填写)")
    p.add_argument("--dir", default=None, help="论文项目目录(默认自动查找)")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("recommend", help="按方向推荐候选期刊(粗筛)")
    p.add_argument("topic", help="研究方向")
    p.add_argument("--dir", default=None, help="论文项目目录(可选,写入短名单)")
    p.set_defaults(fn=cmd_recommend)

    p = sub.add_parser("review-book", help="生成审查任务工作簿")
    p.add_argument("--dir", default=None, help="论文项目目录(默认自动查找)")
    p.set_defaults(fn=cmd_review_book)

    p = sub.add_parser("review-auto", help="半自动执行可代码化的审查步骤(引用核验/统计审计/原创性/格式/结构/PaperSpine维度)")
    p.add_argument("--dir", default=None, help="论文项目目录(默认自动查找)")
    p.set_defaults(fn=cmd_review_auto)

    p = sub.add_parser("import", help="导入外部稿件 docx/pdf → manuscript/imported_*.md")
    p.add_argument("file", help="docx 或 pdf 文件路径")
    p.add_argument("--dir", default=None, help="论文项目目录(默认自动查找)")
    p.add_argument("--max-pages", type=int, default=None, help="pdf 最多读多少页")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("refs-fix", help="参考文献核验/修复管线(CrossRef 标题反查,见 refs_pipeline.py)")
    p.add_argument("file", help="references.md 路径")
    p.add_argument("--apply", action="store_true", help="写回修复(默认预览)")
    p.add_argument("--report", default=None, help="报告输出路径")
    p.add_argument("--max", type=int, default=60, help="标题反查上限")
    p.set_defaults(fn=cmd_refs_fix)

    p = sub.add_parser("rebuttal", help="审稿意见回复草稿(point-by-point,见 rebuttal.py)")
    p.add_argument("action", nargs="?", default="draft", choices=["draft", "reparse"],
                   help="draft=生成草稿; reparse=按人工编辑后的 items.json 重渲染")
    p.add_argument("--src", default=None, help="外部审稿信文件(缺省读 review/mock-reviews.md + tasks.md)")
    p.add_argument("--gen", action="store_true", help="委托 AI 填 Response 草稿(标草稿-待人定稿,不自动应用)")
    p.add_argument("--dir", default=None, help="论文项目目录(默认自动查找)")
    p.set_defaults(fn=cmd_rebuttal)

    p = sub.add_parser("litmap", help="选题文献地图:主题聚类+年份分布+缺口候选(见 litmap.py)")
    p.add_argument("--refs", default=None, help="references.md 路径(缺省读项目 framework/references.md)")
    p.add_argument("--query", default=None, help="在线检索关键词补充文献池(默认关,失败明确报错)")
    p.add_argument("--limit", type=int, default=20, help="--query 最大条数")
    p.add_argument("--standalone", action="store_true", help="独立模式(不依赖论文项目)")
    p.add_argument("--rebuild", action="store_true", help="忽略增量缓存,全量重算")
    p.add_argument("--out", default=None, help="litmap.md 输出路径")
    p.add_argument("--dir", default=None, help="论文项目目录(默认自动查找)")
    p.set_defaults(fn=cmd_litmap)

    p = sub.add_parser("generate", help="分批生成管线(契约先行/分段生成/门禁/拼装)")
    p.add_argument("action", choices=["init", "contract", "section", "status", "assemble", "tables", "abstract", "delegate", "parallel"])
    p.add_argument("--sid", default=None, help="section 动作的段号(如 S1)")
    p.add_argument("--ai", action="store_true", help="contract: 委托 AI 起草")
    p.add_argument("--dry-run", action="store_true", help="section: 只输出 prompt 不调模型")
    p.add_argument("--accept", action="store_true", help="section: 跳过门禁接受（须带 --reason）")
    p.add_argument("--reason", default=None, help="section --accept 的理由（写入 gen_state/台账）")
    p.add_argument("--retry", type=int, default=1, help="门禁失败重试次数")
    p.add_argument("--force", action="store_true", help="init: 重置")
    p.add_argument("--tid", default=None, help="tables: 表格编号(如 \"Table 1\")")
    p.add_argument("--gen", action="store_true", help="tables: 委托 AI 生成行数据 JSON")
    p.add_argument("--extract", action="store_true", help="tables: 从现有稿件提取管道表 → draft/tables/*.json")
    p.add_argument("--src", default=None, help="tables --extract: 源 Markdown(默认 manuscript/main.md)")
    p.add_argument("--max-words", type=int, default=250, help="abstract: 摘要词数上限")
    p.add_argument("--timeout", type=int, default=1800, help="delegate: Agent 超时秒数")
    p.add_argument("--via", default="normal", choices=["normal", "subagent"],
                   help="section: 写作通道（normal=ai_client 直连, 默认; subagent=经 dsh 子代理会话）")
    p.add_argument("--concurrency", type=int, default=4,
                   help="parallel: 波内并发数(默认 4, 与会话池容量一致)")
    p.add_argument("--dir", default=None, help="指定项目目录(默认自动发现)")
    p.set_defaults(fn=cmd_generate)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
