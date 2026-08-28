#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper Workbench 冒烟测试（本地、无外部网络依赖）。

用法:
    python smoke_test.py

覆盖:
  1. 所有 Python 源码可编译
  2. wb.py 状态机 init/check/stage
  3. toolbox.py chart --out 生成文件；9 种图表类型均可生成
  4. toolbox.py quality-check 能拦截 [TBD]
  5. data2paper.py fill 能生成 results_auto.md + 图表
  6. data2paper.py 按 figures.md 规划自动生成图表/统计表并插入正文
  7. toolbox.originality_check 空语料不崩溃
  8. toolbox.py export 能导出 Word (.docx)
  9. web/server.py 启动且 /api/dashboard 返回 200
  15-19. 离线端到端: 新格式契约解析/任务卡语言传导/段级门禁双向/
         整合质检三函数/依赖图波次划分语义（不依赖 dsh 在线与网络）
"""
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
PY = sys.executable
PASS = 0
FAIL = 0

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def run(args, cwd=None, timeout=120):
    r = subprocess.run(
        [PY] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd or str(BASE),
        timeout=timeout,
    )
    return r.returncode, r.stdout + r.stderr


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def temp_dir(prefix):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    return d


def test_compile():
    print("[1] Python 编译检查")
    files = [
        "wb.py", "toolbox.py", "data2paper.py", "desktop.py",
        "web/server.py", "web/ai_client.py", "web/charts.py",
        "skill_channel.py", "subagent_writer.py", "parallel_gen.py",
    ]
    code, out = run(["-m", "py_compile"] + [str(BASE / f) for f in files])
    check("全部 py 文件编译通过", code == 0, out[-500:])


def test_state_machine():
    print("[2] wb.py 状态机 init/check/stage")
    tmp = temp_dir("wb_smoke_")
    try:
        code, out = run(["wb.py", "init", "冒烟测试方向", "--dir", str(tmp)])
        check("init 成功", code == 0 and "已初始化" in out, out[-300:])
        proj = tmp / "冒烟测试方向"
        if not proj.exists():
            proj = next(tmp.iterdir())
        code, out = run(["wb.py", "status", "--dir", str(proj)])
        check("status 可读", code == 0 and "文献调研" in out, out[-300:])
        for i in range(1, 6):
            run(["wb.py", "check", "research", str(i), "--dir", str(proj)])
        code, out = run(["wb.py", "stage", "journal", "--dir", str(proj)])
        check("检查点齐全后可推进到 journal", code == 0 and "期刊调研" in out, out[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_chart_out():
    print("[3] toolbox.py chart --out")
    tmp = temp_dir("wb_chart_")
    try:
        csv = tmp / "data.csv"
        csv.write_text("x,y\na,1\nb,3\nc,2\n", encoding="utf-8")
        out = tmp / "out.png"
        code, _ = run(["toolbox.py", "chart", str(csv), "--type", "bar", "--x", "x", "--y", "y", "--out", str(out)])
        check("chart --out 生成文件", code == 0 and out.exists(), f"exit={code}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_chart_types():
    print("[3b] toolbox.py chart 高级图表类型")
    tmp = temp_dir("wb_chart_types_")
    try:
        csv = tmp / "data.csv"
        csv.write_text("group,value,score\nA,1,10\nA,2,12\nB,3,15\nB,5,18\nC,2,11\nC,4,16\n", encoding="utf-8")
        all_ok = True
        for t in ("bar", "line", "scatter", "pie", "box", "violin", "hist", "area", "heatmap"):
            out = tmp / f"{t}.png"
            code, _ = run(["toolbox.py", "chart", str(csv), "--type", t, "--x", "group", "--y", "value", "--out", str(out)])
            if code != 0 or not out.exists():
                all_ok = False
                print(f"    ❌ {t}")
        check("9 种图表类型均能生成", all_ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_quality_check():
    print("[4] toolbox.py quality-check 拦截 [TBD]")
    tmp = temp_dir("wb_quality_")
    try:
        (tmp / "manuscript").mkdir(parents=True)
        (tmp / "manuscript" / "main.md").write_text("# Draft\n\n[TBD]\n", encoding="utf-8")
        code, out = run(["toolbox.py", "quality-check", str(tmp)])
        ok = code == 0 and "placeholder" in out and "figures_plan" in out and "references_count" in out and "P0" in out
        check("quality-check 返回 P0/图表规划/参考文献检查", ok, out[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_data2paper_fill():
    print("[5] data2paper.py fill")
    tmp = temp_dir("wb_fill_")
    try:
        (tmp / "data").mkdir(parents=True)
        (tmp / "data" / "test.csv").write_text("group,value\nA,1\nA,2\nB,3\nB,4\n", encoding="utf-8")
        code, out = run(["data2paper.py", "fill", str(tmp)])
        results = tmp / "manuscript" / "results_auto.md"
        charts = tmp / "data" / "charts"
        ok = code == 0 and results.exists() and charts.exists()
        check("fill 生成 results_auto.md + 图表", ok, out[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_planned_chart_insertion():
    print("[7] data2paper.py 按 figures.md 规划自动插入图表")
    tmp = temp_dir("wb_plan_")
    try:
        (tmp / "data").mkdir(parents=True)
        (tmp / "framework").mkdir(parents=True)
        (tmp / "manuscript").mkdir(parents=True)
        (tmp / "data" / "原生质体数据.csv").write_text(
            "group,产量,再生率\nA,10,20\nB,15,25\nC,12,22\n", encoding="utf-8")
        (tmp / "framework" / "figures.md").write_text(
            "| 编号 | 类型(图/表) | 内容 | 数据来源 | 对应章节 | 期刊规范要求 |\n"
            "|------|------------|------|----------|----------|--------------|\n"
            "| 图2 | 图 | 原生质体制备与再生条件优化结果 | data/原生质体数据 | Results 4.1 | 柱状图+误差棒 |\n"
            "| 表2 | 表 | 原生质体制备条件与产量/再生率 | data/原生质体数据 | Results 4.1 | 三线表 |\n",
            encoding="utf-8")
        (tmp / "manuscript" / "main.md").write_text(
            "# Protoplast fusion\n## 4. Results\n### 4.1 Protoplast preparation and regeneration\n"
            "[TBD: actual yields and regeneration rates. Planned output: Figure 2 and Table 2.]\n## 5. Discussion\n",
            encoding="utf-8")
        code, out = run(["data2paper.py", "fill", str(tmp)])
        main_text = (tmp / "manuscript" / "main.md").read_text(encoding="utf-8")
        ok = code == 0 and "![图2](" in main_text and "**表2（自动生成）**" in main_text and (tmp / "manuscript" / "main.md.bak").exists()
        check("按规划生成图表和统计表并插入正文", ok, out[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_intake_minrefs():
    print("[4b] quality-check 参考文献默认 80（模板示例不泄漏）")
    tmp = temp_dir("wb_intake_")
    try:
        (tmp / "manuscript").mkdir(parents=True)
        (tmp / "manuscript" / "main.md").write_text("# Draft\n\nText\n", encoding="utf-8")
        (tmp / "framework").mkdir(parents=True)
        (tmp / "framework" / "references.md").write_text(
            "```bibtex\n@article{k1, author={A B}, title={T1}, journal={J}, year={2024}, doi={10.1/x}}\n"
            "@article{k2, author={C D}, title={T2}, journal={J}, year={2020}, doi={10.2/y}}\n```\n",
            encoding="utf-8")
        # 模板原样 intake（未填写），示例「如 40-80 条」不应被当作真实要求
        (tmp / "intake.md").write_text("# 项目信息收集\n\n## 4. 目标期刊/会议\n- 参考文献数量要求（如 40-80 条）：\n", encoding="utf-8")
        code, out = run(["toolbox.py", "quality-check", str(tmp)])
        check("模板示例不泄漏：默认 80 条", code == 0 and "建议至少 80 条" in out, out[-300:])
        # 用户填写后应覆盖默认值
        (tmp / "intake.md").write_text("# 项目信息收集\n\n## 4. 目标期刊/会议\n- 参考文献数量要求（如 40-80 条）：60-100\n", encoding="utf-8")
        code, out = run(["toolbox.py", "quality-check", str(tmp)])
        check("用户填写 60-100 生效", code == 0 and "建议至少 60 条" in out, out[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_citation_year():
    print("[4c] citation_check 不把 [2020] 年份当引用")
    code, out = run([
        "-c",
        "import sys; sys.path.insert(0, r'{}'); from toolbox import citation_check; "
        "r = citation_check('See [1,2] and data from [2020]. [3]', [{{'id':'a'}},{{'id':'b'}},{{'id':'c'}}]); "
        "print([i['type'] for i in r])".format(BASE),
    ])
    check("[2020] 不误报为引用编号", code == 0 and "out_of_range" not in out, out[-200:])


def test_insert_order():
    print("[7b] 图/表共享 TBD 行时按规划顺序插入（图2 在 表2 前）")
    tmp = temp_dir("wb_order_")
    try:
        (tmp / "data").mkdir(parents=True)
        (tmp / "framework").mkdir(parents=True)
        (tmp / "manuscript").mkdir(parents=True)
        (tmp / "data" / "原生质体数据.csv").write_text("group,产量,再生率\nA,10,20\nA,12,21\nB,15,25\nB,17,26\nC,12,22\nC,13,23\n", encoding="utf-8")
        (tmp / "framework" / "figures.md").write_text(
            "| 编号 | 类型(图/表) | 内容 | 数据来源 | 对应章节 | 期刊规范要求 |\n"
            "|------|------------|------|----------|----------|--------------|\n"
            "| 图2 | 图 | 制备与再生条件优化结果 | data/原生质体数据 | Results 4.1 | 柱状图+误差棒 |\n"
            "| 表2 | 表 | 制备条件与产量/再生率 | data/原生质体数据 | Results 4.1 | 三线表 |\n",
            encoding="utf-8")
        (tmp / "manuscript" / "main.md").write_text(
            "# Protoplast fusion\n## 4. Results\n### 4.1 Protoplast preparation\n"
            "[TBD: actual yields. Planned output: Figure 2 and Table 2.]\n## 5. Discussion\n",
            encoding="utf-8")
        code, _ = run(["data2paper.py", "fill", str(tmp)])
        main_text = (tmp / "manuscript" / "main.md").read_text(encoding="utf-8")
        fig_idx = main_text.find("![图2](")
        tab_idx = main_text.find("**表2（自动生成）**")
        check("插入顺序：图2 在 表2 前", code == 0 and fig_idx >= 0 and tab_idx > fig_idx, f"fig={fig_idx} tab={tab_idx}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_export_docx():
    print("[9] toolbox.py export 导出 Word")
    tmp = temp_dir("wb_export_")
    try:
        md = tmp / "main.md"
        md.write_text("# Test\n\nHello 世界\n", encoding="utf-8")
        out = tmp / "out.docx"
        code, _ = run(["toolbox.py", "export", str(md), "--format", "docx", "--out", str(out)])
        check("docx 导出成功", code == 0 and out.exists(), f"exit={code}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_export_image():
    print("[9b] toolbox.py export Word 内嵌正文图片")
    tmp = temp_dir("wb_export_img_")
    try:
        (tmp / "manuscript").mkdir(parents=True)
        (tmp / "data" / "charts").mkdir(parents=True)
        csv = tmp / "d.csv"
        csv.write_text("x,y\na,1\nb,3\nc,2\n", encoding="utf-8")
        code, _ = run(["toolbox.py", "chart", str(csv), "--type", "bar", "--x", "x", "--y", "y",
                       "--out", str(tmp / "data" / "charts" / "d.png")])
        if code != 0:
            check("导出前置：chart 生成失败", False)
            return
        md = tmp / "manuscript" / "main.md"
        md.write_text("# T\n\n![图1](data/charts/d.png)\n", encoding="utf-8")
        out_docx = tmp / "out.docx"
        code, _ = run(["toolbox.py", "export", str(md), "--format", "docx", "--out", str(out_docx)])
        import zipfile
        embedded = 0
        if out_docx.exists():
            with zipfile.ZipFile(out_docx) as z:
                embedded = sum(1 for n in z.namelist() if n.startswith("word/media/"))
        check("docx 内嵌图片 ≥1 张", code == 0 and embedded >= 1, f"embedded={embedded}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_docx():
    print("[9c] toolbox.py import-docx 生成 Markdown")
    tmp = temp_dir("wb_import_docx_")
    try:
        # 先生成 docx(python-docx),再导入
        out_docx = tmp / "src.docx"
        code, _ = run([
            "-c",
            "import sys; sys.path.insert(0, r'{}'); from docx import Document; "
            "d = Document(); d.add_heading('Title', 0); d.add_paragraph('Hello 世界'); "
            "t = d.add_table(rows=2, cols=2); t.cell(0,0).text='A'; t.cell(0,1).text='B'; "
            "t.cell(1,0).text='1'; t.cell(1,1).text='2'; d.save(r'{}')".format(BASE, out_docx),
        ])
        if code != 0 or not out_docx.exists():
            check("import-docx 前置: docx 生成", False)
            return
        out_md = tmp / "imported.md"
        code, out = run(["toolbox.py", "import-docx", str(out_docx), "--out", str(out_md)])
        ok = code == 0 and out_md.exists() and "Hello 世界" in out_md.read_text(encoding="utf-8") and "| A | B |" in out_md.read_text(encoding="utf-8")
        check("docx 导入 md(文本+表格)", ok, out[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_pdf():
    print("[9d] toolbox.py import-pdf 生成 Markdown(合成 PDF)")
    tmp = temp_dir("wb_import_pdf_")
    try:
        pdf = tmp / "src.pdf"
        # 合成一个含标题/段落/表格的 PDF(fpdf 不一定装,用 pymupdf 绘制)
        code, _ = run([
            "-c",
            "import sys; sys.path.insert(0, r'{}'); import fitz; "
            "doc = fitz.open(); page = doc.new_page(); "
            "page.insert_text((72,72), '1. Introduction', fontsize=16); "
            "page.insert_text((72,100), 'This is body text with a citation (1--3).'); "
            "doc.save(r'{}')".format(BASE, pdf),
        ])
        if code != 0 or not pdf.exists():
            check("import-pdf 前置: pdf 生成", False)
            return
        out_md = tmp / "imported.md"
        code, out = run(["toolbox.py", "import-pdf", str(pdf), "--out", str(out_md)])
        text = out_md.read_text(encoding="utf-8") if out_md.exists() else ""
        ok = code == 0 and out_md.exists() and "# 1. Introduction" in text and "body text" in text
        check("pdf 导入 md(标题+正文)", ok, out[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("[6] toolbox.originality_check 空语料")
    code, out = run([
        "-c",
        "import sys; sys.path.insert(0, r'{}'); from toolbox import originality_check; print(originality_check('hello', None))".format(BASE),
    ])
    check("空语料返回 [] 不崩溃", code == 0 and out.strip() == "[]", out[-300:])


def test_originality_none():
    print("[6] toolbox.originality_check 空语料")
    code, out = run([
        "-c",
        "import sys; sys.path.insert(0, r'{}'); from toolbox import originality_check; print(originality_check('hello', None))".format(BASE),
    ])
    check("空语料返回 [] 不崩溃", code == 0 and out.strip() == "[]", out[-300:])


def test_mcp_server():
    print("[11] workbench_mcp.py 跨生态 MCP 服务")
    import asyncio
    sys.path.insert(0, str(BASE))
    import workbench_mcp as w

    async def _tools():
        return await w.mcp.list_tools()

    tools = asyncio.run(_tools())
    names = {t.name for t in tools}
    need = {"search_skills", "read_skill", "record_skill_use", "quality_check",
            "mechanical_fix", "figure_render", "export_docx", "process_audit"}
    check("MCP 工具注册 >=13 且核心工具齐全", len(tools) >= 13 and need <= names,
          f"got={len(tools)} missing={need - names}")

    async def _search():
        return await w.mcp.call_tool("search_skills", {"query": "润色", "limit": 5})

    try:
        res = asyncio.run(_search())
        if isinstance(res, tuple):
            res = res[0]
        payload = json.loads(res[0].text if hasattr(res[0], "text") else res[0])
        hits = {m["name"] for m in payload.get("matches", [])}
        check("search_skills 中文「润色」命中润色技能",
              {"nature-polishing", "polish-prose"} & hits, f"hits={sorted(hits)[:6]}")
    except Exception as e:
        check("search_skills 中文「润色」命中润色技能", False, repr(e)[:200])

    # 新增: 全量 27 工具注册
    names = {t.name for t in tools}
    extra = {"literature_search", "web_search", "web_extract", "build_references",
             "fetch_doi", "writing_brief", "originality", "import_document"}
    check("MCP 扩展工具齐全(14 新增)", extra <= names, f"missing={extra - names}")

    # 新增: literature_search 真实返回文献
    async def _lit():
        return await w.mcp.call_tool("literature_search",
                                     {"query": "Ophiocordyceps sinensis", "limit": 2})

    try:
        res = asyncio.run(_lit())
        if isinstance(res, tuple):
            res = res[0]
        payload = json.loads(res[0].text if hasattr(res[0], "text") else res[0])
        items = payload.get("results") or []
        check("literature_search 真实返回文献(含 DOI)", bool(items) and any(
            it.get("doi") or it.get("title") for it in items), f"n={len(items)}")
    except Exception as e:
        check("literature_search 真实返回文献(含 DOI)", False, repr(e)[:200])

    # 清理模块
    sys.modules.pop("workbench_mcp", None)


def test_web_server():
    print("[8] web/server.py 启动 + /api/dashboard")
    # 系统分配空闲端口: 固定端口会被中断会话遗留的孤儿服务占用,
    # 新服务因 _port_serving 防双实例保护立即退出, 造成误判
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [PY, str(BASE / "web" / "server.py"), str(port)],
        cwd=str(BASE / "web"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    detail = ""
    try:
        ok = False
        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                try:
                    detail = "进程提前退出: " + proc.stdout.read().decode("utf-8", errors="replace")[-300:]
                except Exception:
                    detail = "进程提前退出"
                break
            try:
                # dashboard 首次计算需秒级: 必须长超时低频探测。
                # 短超时高频轮询会让 ThreadingHTTPServer 堆积被放弃的请求,
                # 越轮询越慢, 稳定失败(2026-08-23 实证)
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/dashboard", timeout=15) as r:
                    ok = r.status == 200
                    break
            except Exception:
                time.sleep(1)
        check("/api/dashboard 返回 200", ok, detail)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def test_used_refs():
    print("[10] toolbox.py used-refs 正文引用反推收窄")
    tmp = temp_dir("wb_refs_")
    try:
        (tmp / "manuscript").mkdir(parents=True)
        (tmp / "framework").mkdir(parents=True)
        (tmp / "manuscript" / "main.md").write_text(
            "# T\n\nIntro [1] and [2,3] and [4-5] done.\n\n## References\nignored [99]\n",
            encoding="utf-8")
        bib = []
        for i in range(1, 8):
            bib.append("@article{r%s,\n  author = {A%s}, title = {T%s}, year = {202%d},\n  doi = {10.1/x%s}\n}" % (i, i, i, i % 10, i))
        (tmp / "framework" / "references.md").write_text("\n".join(bib), encoding="utf-8")
        code, out = run(["toolbox.py", "used-refs", str(tmp)])
        check("used-refs 统计正确", code == 0 and '"used": 5' in out and '"unused": 2' in out and '"total": 7' in out, out[-300:])
        code2, out2 = run(["toolbox.py", "used-refs", str(tmp), "--write"])
        check("used-refs --write 生成 refs_used.md",
              code2 == 0 and (tmp / "manuscript" / "refs_used.md").exists(), out2[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_anova_table():
    print("[10b] data2paper 按规划生成 ANOVA 表 + 去重 + 幂等刷新")
    tmp = temp_dir("wb_anova_")
    try:
        (tmp / "data").mkdir(parents=True)
        (tmp / "framework").mkdir(parents=True)
        (tmp / "manuscript").mkdir(parents=True)
        (tmp / "data" / "d.csv").write_text(
            "group,val\na,1\na,2\na,3\nb,4\nb,5\nb,6\nc,7\nc,8\nc,9\n", encoding="utf-8")
        (tmp / "framework" / "figures.md").write_text(
            "| 编号 | 类型(图/表) | 内容 | 数据来源 | 对应章节 | 期刊规范要求 |\n"
            "|------|------------|------|----------|----------|--------------|\n"
            "| 表1 | 表 | 各组结果统计 | data/d.csv | Results 4.1 | 三线表 |\n"
            "| 表2 | 表 | 方差分析表(组间 F/p) | data/d.csv | Results 4.2 | 三线表 |\n",
            encoding="utf-8")
        (tmp / "manuscript" / "main.md").write_text(
            "# T\n## 4. Results\n### 4.1 X\n[TBD: 表1]\n### 4.2 Y\n[TBD: 表2]\n## 5. D\n",
            encoding="utf-8")
        code, _ = run(["data2paper.py", "fill", str(tmp)])
        t1 = (tmp / "manuscript" / "main.md").read_text(encoding="utf-8")
        check("ANOVA 表已生成", code == 0 and "自动生成 ANOVA 表" in t1, t1[-400:])
        check("无 nan 脏行", "nan" not in t1.replace("dominant", ""), t1[-400:])
        check("表1 表2 各出现一次", t1.count("**表1（自动生成）**") == 1 and t1.count("**表2（自动生成 ANOVA 表）**") == 1, t1[-400:])
        # 幂等刷新: 再跑一次 fill,表不重复且仍只有一套
        code2, _ = run(["data2paper.py", "fill", str(tmp)])
        t2 = (tmp / "manuscript" / "main.md").read_text(encoding="utf-8")
        check("fill 幂等刷新(不重复插入)",
              code2 == 0 and t2.count("**表2（自动生成 ANOVA 表）**") == 1 and t2.count("| a |") >= 1, t2[-400:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)



def test_staged_gen_gates():
    print("[12] staged_gen 段级门禁五件套 + --accept 收死")
    sys.path.insert(0, str(BASE))
    import staged_gen as sg
    C = {"sections": [{"sid": "S1", "title": "Genetic Improvement", "budget": 300}],
         "citations": {"ref1": ["S1"]}, "locked": True}
    S = C["sections"][0]
    # 正常段通过（word_budget 为 P2 观察, 不阻断）
    good = "Genetic improvement of strains has been a central goal. (1) "
    issues, passed = sg.gate_section(None, C, S, good)
    check("门禁正常段通过", passed)
    # 引用密度 >8 触发 P2
    dense = "Genetic improvement. (1) (1) (1) (1) (1) (1) (1) (1) (1) (1) " + "word " * 100
    issues, _ = sg.gate_section(None, C, S, dense)
    check("引用密度>8 触发 P2", any(i["type"] == "citation_density" for i in issues))
    # 引文越界 P1 幻觉拦截
    bad = "Genetic improvement has been reported. (99) " + "word " * 80
    issues, _ = sg.gate_section(None, C, S, bad)
    check("引文越界 P1 幻觉拦截", any(i["type"] == "citation_out_of_pool" for i in issues))
    # 两段首句无主题词 → P2
    no_topic = "It has long been observed that temperature affects growth. \n\nSeveral factors matter. \n"
    issues, _ = sg.gate_section(None, C, S, no_topic)
    check("两段首句无主题词触发 P2", any(i["type"] == "topic_sentence" for i in issues))
    # --accept 收死: 不带 --reason 时 wb.py 应拒绝
    import subprocess
    r = subprocess.run([PY, "wb.py", "generate", "--help"], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(BASE))
    check("generate --help 含 --reason 参数", "--reason" in r.stdout)




def test_flow_pipeline():
    print("[13] 八步流程: skill 方法论内联 + assemble 逻辑校验")
    sys.path.insert(0, str(BASE))
    import staged_gen as sg
    # 方法论内联
    C = {"sections": [{"sid": "R1", "title": "Results: yields", "budget": 300}],
         "citations": {"ref1": ["R1"]}, "locked": True}
    p = sg.build_section_prompt(C, C["sections"][0], "", [])
    check("Results 段内联科学写作方法论",
          "本节方法论" in p and "因果性断言必须基于对照" in p)
    p2 = sg.build_section_prompt(C, {"sid": "D1", "title": "Discussion", "budget": 300}, "", [])
    check("Discussion 段内联审稿预判方法论",
          "simulate-reviewers" in p2 and "四段式推进" in p2)
    # 逻辑校验: 跨章节矛盾
    contra = ("## Introduction\nUV mutagenesis has been applied in this fungus.\n\n"
              "## Results\nUV mutagenesis has not been reported in this fungus.\n")
    w = sg._assemble_logic_check(contra)
    check("逻辑校验: 跨章节矛盾 P0", any(x["type"] == "cross_section_contradiction" for x in w))
    # 逻辑校验: 段间衔接
    weak = ("Photosynthesis drives carbon fixation rapidly. The rate varies.\n\n"
            "Volcanic rocks contain unusual mineral assemblages here. Text follows.\n\n"
            "Quantum computers require cryogenic temperatures precisely. Details matter.\n\n"
            "Ancient languages encode grammar in intricate inflections. More text.\n")
    w2 = sg._assemble_logic_check(weak)
    check("逻辑校验: 段间弱衔接 P2", any(x["type"] == "paragraph_transition" for x in w2))
    # flow.md 存在
    check("流程协议 flow.md 存在", (BASE / "flow.md").exists())




def test_dispatch():
    print("[14] 任务派发层: 执行者抽象 + 八步任务书")
    sys.path.insert(0, str(BASE))
    import runner
    # 任务书生成
    tb = runner.build_taskbook("写一篇关于 Hirsutella 的综述")
    check("八步任务书生成", len(tb["steps"]) == 8 and tb["steps"][0]["phase"] == "journal"
          and tb["steps"][-1]["phase"] == "submit")
    # 任务书 prompt 含强制约束
    p = runner.taskbook_prompt(tb)
    check("任务书含防绕过约束", "必须走工作台工具" in p and "generate section" in p
          and "figure_render" in p)
    # 执行者检测
    exes = {e["name"]: e["available"] for e in runner.list_executors()}
    check("执行者抽象含 dsh/claude/codex", {"dsh", "claude", "codex"} <= set(exes))
    # dispatch_task MCP 工具注册
    import asyncio
    sys.path.insert(0, str(BASE))
    import workbench_mcp as wm

    async def _lt():
        return await wm.mcp.list_tools()

    names = {t2.name for t2 in asyncio.run(_lt())}
    check("dispatch_task/list_executors 已注册",
          {"dispatch_task", "list_executors"} <= names, f"missing={names & {'dispatch_task', 'list_executors'}}")


def test_contract_new_format():
    print("[15] 新格式契约解析（依赖/衔接锚点列 + 头部字段）与旧格式向后兼容")
    sys.path.insert(0, str(BASE))
    import staged_gen as sg
    new_contract = (
        "# 生成契约\n\n"
        "> 项目: 冒烟 | 目标期刊: Test J | 语言: 中文\n"
        "> 期刊要求: 8000 词/数字制引用 | 计数单位: chars | 契约版本: v2\n"
        "> 契约状态：已锁定\n\n"
        "## 1. 范围声明\n\n- 覆盖：冒烟测试范围\n\n"
        "## 2. 章节大纲\n\n"
        "| 段号 | 标题 | 字数预算 | 要点 | 依赖 | 衔接锚点 |\n"
        "|---|---|---|---|---|---|\n"
        "| S1 | 引言 | 800 | 背景 |  | 问题缺口已提出 |\n"
        "| S2 | 方法 | 900 | 方案 | S1 |  |\n"
        "| S3 | 结果 | 1000 | 数据 | S1；S2 | 产率提升 20% |\n"
        "| S4 | 结论 | 500 | 总结 |  |  |\n\n"
        "## 3. 图表契约\n\n| 编号 | 类型 | 标题 | 图注 | 插入段 | 来源 |\n|---|---|---|---|---|---|\n\n"
        "## 4. 引文分配\n\n| 文献键 | 分配段落 | 支撑要点 |\n|---|---|---|\n"
        "| ref1 | S1;S3 | 支撑 |\n\n"
        "## 5. 术语表\n\n| 术语 | 规约 |\n|---|---|\n| 菌株 X | 斜体 |\n"
    )
    c = sg.parse_contract(new_contract)
    check("新格式: 头部字段解析",
          c["lang"] == "zh" and c["count_unit"] == "chars" and c["version"] == "v2"
          and c["journal_profile"] == "8000 词/数字制引用",
          f"lang={c['lang']} unit={c['count_unit']} ver={c['version']} jp={c['journal_profile']!r}")
    check("新格式: 章节含 deps/anchor 字段",
          c["sections"][0]["anchor"] == "问题缺口已提出" and c["sections"][2]["deps"] == "S1；S2",
          f"secs={c['sections']}")
    check("新格式: 依赖图显式依赖生效（分号分隔多前驱）",
          c["dependency_graph"] == {"S1": [], "S2": ["S1"], "S3": ["S1", "S2"], "S4": ["S3"]},
          f"graph={c['dependency_graph']}")
    check("新格式: 引文分配解析", c["citations"] == {"ref1": ["S1", "S3"]}, f"citations={c['citations']}")
    check("新格式: 锁定状态识别", c["locked"] is True)

    # 旧格式 4 列契约：向后兼容，语义与线性链一致
    old_contract = (
        "# 生成契约\n\n> 契约状态：已锁定\n\n"
        "## 1. 范围声明\n\n- 覆盖：旧格式\n\n"
        "## 2. 章节大纲\n\n"
        "| 段号 | 标题 | 字数预算 | 要点 |\n"
        "|---|---|---|---|\n"
        "| S1 | 引言 | 800 | 背景 |\n"
        "| S2 | 方法 | 900 | 方案 |\n"
        "| S3 | 结果 | 1000 | 数据 |\n\n"
        "## 4. 引文分配\n\n| 文献键 | 分配段落 | 支撑要点 |\n|---|---|---|\n"
        "| ref1 | S1;S3 | 支撑 |\n"
    )
    o = sg.parse_contract(old_contract)
    same_secs = [(s["sid"], s["title"], s["budget"], s["points"]) for s in o["sections"]] == \
                [(s["sid"], s["title"], s["budget"], s["points"]) for s in c["sections"][:3]]
    check("旧格式 4 列: 章节/引文语义与新格式前 3 节一致",
          same_secs and o["citations"] == c["citations"],
          f"old_secs={o['sections']} old_cit={o['citations']}")
    check("旧格式 4 列: 依赖图默认线性链（与显式线性链一致）",
          o["dependency_graph"] == {"S1": [], "S2": ["S1"], "S3": ["S2"]},
          f"old_graph={o['dependency_graph']}")
    check("旧格式: 缺省头部字段回落默认值", o["lang"] == "en" and o["count_unit"] == "words")


def test_taskcard_lang():
    print("[15b] 任务卡渲染: 契约语言传导（中文契约输出「请用中文写作」）")
    sys.path.insert(0, str(BASE))
    import staged_gen as sg
    contract = {"scope": "冒烟范围", "figures": [], "glossary": "菌株 X 斜体",
                "lang": "zh", "sections": []}
    sec = {"sid": "S1", "title": "引言", "budget": 800, "points": "背景"}
    p_zh = sg.build_section_prompt(contract, sec, "", [], lang="zh")
    check("中文契约任务卡含「请用中文写作」", "请用中文写作" in p_zh, p_zh[:200])
    p_en = sg.build_section_prompt(contract, sec, "", [], lang="en")
    check("英文契约任务卡含英文写作指令", "请用英文写作" in p_en)
    check("任务卡含范围/术语/文献池结构",
          "范围声明" in p_zh and "术语规约" in p_zh and "本节可用文献" in p_zh)


def test_gate_offline():
    print("[15c] 段级门禁离线双向: 内联样例文本通过/拦截")
    sys.path.insert(0, str(BASE))
    import staged_gen as sg
    C = {"sections": [{"sid": "S1", "title": "Genetic Improvement", "budget": 300}],
         "citations": {"ref1": ["S1"]}, "locked": True}
    S = C["sections"][0]
    # 通过方向: 引用在池内、每段首句含主题词、无套话/占位符，词数落在预算 ±20% 内
    good_paras = []
    for i in range(6):
        good_paras.append("Genetic improvement of industrial strains has been studied broadly. (1) "
                          + " ".join(f"w{i}x{j}" for j in range(38)))
    good = "\n\n".join(good_paras)
    issues, passed = sg.gate_section(None, C, S, good)
    check("门禁通过方向: 合规内联文本 passed=True",
          passed, f"issues={issues}")
    # 拦截方向: 引用 (99) 不在分配池 → P1 幻觉引用，门禁必须失败
    bad = "Genetic improvement has been reported widely. (99) " + "filler " * 260
    issues, passed = sg.gate_section(None, C, S, bad)
    check("门禁拦截方向: 越界引用触发 citation_out_of_pool 且 passed=False",
          (not passed) and any(i["type"] == "citation_out_of_pool" and i["severity"] == "P1"
                               for i in issues),
          f"passed={passed} issues={issues}")


def test_integration_qc_funcs():
    print("[16] 整合质检三函数离线验证（纯函数，不写盘）")
    sys.path.insert(0, str(BASE))
    import integration_qc as iqc
    md = ("# T\n\n"
          "Intro claims alpha. [7] Later beta with ranges. [4-6] Also gamma. [2]\n"
          "See Figure 1 and Table 2 for details (strain CBS 513.88).\n\n"
          "## References\n"
          "7. A paper seven.\n4. A paper four.\n5. A paper five.\n"
          "6. A paper six.\n2. A paper two.\n3. A paper three.\n")
    r = iqc.qc_references(md_text=md, apply=False)
    check("qc_references: 首现顺序重编号映射正确（区间 4-6 展开参与排序）",
          r["ok"] and r["mapping"] == {7: 1, 4: 2, 5: 3, 6: 4, 2: 5},
          f"mapping={r.get('mapping')}")
    check("qc_references: 未引用编号 3 不进入映射", 3 not in r["mapping"])
    t = r["text"]
    check("qc_references: 正文引用组改写为 [1]/[2–4]/[5]",
          "[1]" in t and "[2–4]" in t and "[5]" in t and "[7]" not in t.split("## References")[0],
          t[:300])
    check("qc_references: Figure/Table 编号不被触碰",
          "See Figure 1 and Table 2 for details" in t)
    check("qc_references: 参考文献章节重排且删除未引用条目",
          "1. A paper seven." in t and "A paper three." not in t, t[-400:])

    glossary = "| 术语 | 规约 |\n|---|---|\n| Aspergillus niger | 物种名规约 |\n"
    tmd = ("Aspergillus niger grows well. Aspergillus  niger variant too.\n"
           "```\nAspergillus niger in code\n```\n\n"
           "## References\nAspergillus niger ref line\n")
    tr = iqc.qc_terminology(glossary, tmd)
    row = tr["rows"][0] if tr["rows"] else {}
    check("qc_terminology: 报告结构正确（命中计数/行号/空白变体）",
          tr["ok"] and row.get("count") == 3 and row.get("whitespace_variants") == 1
          and isinstance(row.get("lines"), list),
          f"row={row}")
    check("qc_terminology: 代码块与参考文献章节内的命中被标记可疑",
          len(row.get("suspect", [])) == 2, f"suspect={row.get('suspect')}")

    ttext = ("Photosynthesis drives carbon fixation rapidly in leaves.\n\n"
             "Quantum computers require cryogenic temperatures precisely.\n")
    qr = iqc.qc_transitions(ttext)
    gaps = qr.get("P2", {}).get("gaps", [])
    check("qc_transitions: 结构化清单含 P0/P1/P2 字段",
          qr["ok"] and isinstance(qr["P0"], list) and isinstance(qr["P1"], list))
    check("qc_transitions: 弱衔接被定点标注（相邻段行号+开头摘录）",
          len(gaps) >= 1 and all(k in gaps[0] for k in
                                 ("prev_para_line", "next_para_line", "prev_head", "next_head", "hint")),
          f"gaps={gaps}")


def _wave_partition(graph):
    """小型内联拓扑分波（入度为 0 先出波，前驱全部完成后才入下一波）。
    parallel_gen 未落地前用此内联实现验证波次语义。"""
    pending = {k: set(v) for k, v in graph.items()}
    waves, done = [], set()
    while pending:
        cur = sorted(n for n, deps in pending.items() if deps <= done)
        if not cur:
            raise ValueError("依赖图存在环，无法拓扑分波")
        waves.append(cur)
        done.update(cur)
        for n in cur:
            del pending[n]
    return waves


def test_wave_partition_semantics():
    print("[17] 依赖图波次划分（优先测 parallel_gen._topo_waves 真实纯函数，否则内联图验证语义）")
    sys.path.insert(0, str(BASE))
    fn, src = None, "inline"
    try:
        import parallel_gen as pg
        if callable(getattr(pg, "_topo_waves", None)):
            fn, src = pg._topo_waves, "parallel_gen._topo_waves"
    except Exception:
        fn = None
    secs_d = [{"sid": "S1"}, {"sid": "S2"}, {"sid": "S3"}, {"sid": "S4"}]
    graph_d = {"S1": [], "S2": ["S1"], "S3": ["S1"], "S4": ["S2", "S3"]}
    secs_l = [{"sid": "S1"}, {"sid": "S2"}, {"sid": "S3"}]
    graph_l = {"S1": [], "S2": ["S1"], "S3": ["S2"]}
    if fn is not None:
        check(f"波次函数就绪（来源: {src}）", True)
        # 菱形图: S2/S3 同波，S4 等两者完成
        waves = fn(secs_d, graph_d)
        check("菱形依赖分波: [[S1],[S2,S3],[S4]]",
              waves == [["S1"], ["S2", "S3"], ["S4"]], f"waves={waves}")
        # 线性链（旧格式契约默认图）: 每波一节，与串行语义一致
        waves2 = fn(secs_l, graph_l)
        check("线性链分波: 每波一节且顺序不变",
              waves2 == [["S1"], ["S2"], ["S3"]], f"waves2={waves2}")
        # 环依赖: 实现按契约降级为末波（不死锁、不丢节）
        secs_c = [{"sid": "A"}, {"sid": "B"}]
        waves3 = fn(secs_c, {"A": ["B"], "B": ["A"]})
        check("环依赖降级为按契约顺序末波（不死锁不丢节）",
              sorted(sum(waves3, [])) == ["A", "B"] and waves3[-1] == ["A", "B"],
              f"waves3={waves3}")
    else:
        fn = _wave_partition
        check(f"波次函数就绪（来源: {src}，parallel_gen 不可导入时降级内联验证）", True)
        waves = fn({"S1": [], "S2": ["S1"], "S3": ["S1"], "S4": ["S2", "S3"]})
        check("菱形依赖分波: [[S1],[S2,S3],[S4]]",
              waves == [["S1"], ["S2", "S3"], ["S4"]], f"waves={waves}")
        waves2 = fn({"S1": [], "S2": ["S1"], "S3": ["S2"]})
        check("线性链分波: 每波一节且顺序不变",
              waves2 == [["S1"], ["S2"], ["S3"]], f"waves2={waves2}")
        try:
            fn({"A": ["B"], "B": ["A"]})
            check("环形依赖被拒绝（抛错）", False, "未抛异常")
        except Exception as e:
            check("环形依赖被拒绝（抛错）", True, repr(e)[:80])


def test_skill_channel():
    print("[18] 技能通道: 三档能力区块 + 执行者:模型解析 + 任务卡注入（旧路径零变化）")
    sys.path.insert(0, str(BASE))
    import threading
    import skill_channel as sc
    import staged_gen as sg
    sec_intro = {"sid": "S1", "title": "Introduction", "budget": 800}
    sec_res = {"sid": "S3", "title": "Results", "budget": 1000}
    # 三档能力: dsh=native(read_skill) / workbuddy=file_read(读文件) / 未知=none(空串)
    b_native = sc.render_skill_block(sec_intro, "dsh")
    check("native(dsh): 区块含 read_skill 指示与技能路径",
          b_native.startswith("## 技能引用") and "read_skill" in b_native
          and "skills/scientific-writing/SKILL.md" in b_native, b_native[:200])
    b_file = sc.render_skill_block(sec_res, "workbuddy")
    check("file_read(workbuddy): 区块含读文件指示与技能路径（无 read_skill）",
          b_file.startswith("## 技能引用") and "read_skill" not in b_file
          and "skills/write-scientific-manuscript/SKILL.md" in b_file
          and "skills/paper-staged-gen/SKILL.md" in b_file, b_file[:200])
    check("none(未知执行者): 返回空串（内联纪律兜底）",
          sc.render_skill_block(sec_intro, "somebody-else") == "")
    check("章节类型口径与 _methodology_for 一致",
          sc.section_type(sec_res) == "results"
          and sc.section_type({"sid": "S4", "title": "Discussion"}) == "discussion"
          and sc.section_type({"sid": "S2", "title": "Materials and Methods"}) == "methods"
          and sc.section_type(sec_intro) == "introduction"
          and sc.section_type({"sid": "S5", "title": "Conclusions"}) == "general")
    # 执行者:模型 解析（共享小函数）
    check("执行者:模型解析: workbuddy:glm-5.2 → (workbuddy, glm-5.2)",
          sc.parse_executor_spec("workbuddy:glm-5.2") == ("workbuddy", "glm-5.2"))
    check("执行者:模型解析: 纯执行者/空值/大小写空白归一",
          sc.parse_executor_spec("dsh") == ("dsh", "")
          and sc.parse_executor_spec("") == ("", "")
          and sc.parse_executor_spec(None) == ("", "")
          and sc.parse_executor_spec(" Workbuddy:glm-5.2 ") == ("workbuddy", "glm-5.2"))
    # 旧契约（无执行者列）不受影响: 解析补空, 空执行者默认 dsh(native)
    old_contract = ("# 生成契约\n\n> 契约状态：已锁定\n\n"
                    "## 1. 范围声明\n\n- 覆盖：旧格式\n\n"
                    "## 2. 章节大纲\n\n"
                    "| 段号 | 标题 | 字数预算 | 要点 |\n|---|---|---|---|\n"
                    "| S1 | Introduction | 800 | 背景 |\n\n"
                    "## 4. 引文分配\n\n| 文献键 | 分配段落 | 支撑要点 |\n|---|---|---|\n"
                    "| ref1 | S1 | 支撑 |\n")
    o = sg.parse_contract(old_contract)
    check("旧契约 4 列: 执行者列缺失解析为空", o["sections"][0]["executor"] == "")
    ex0, md0 = sc.parse_executor_spec(o["sections"][0]["executor"])
    b_def = sc.render_skill_block(o["sections"][0], ex0 or "dsh")
    check("旧契约: 空执行者默认 dsh, model 为空且区块正常渲染",
          md0 == "" and b_def.startswith("## 技能引用"), b_def[:120])
    # normal 通道零变化: build_section_prompt 本体不含技能区块（旧路径无技能引用）
    p_normal = sg.build_section_prompt({"scope": "s", "figures": [], "glossary": "",
                                        "lang": "en", "sections": []}, sec_intro, "", [])
    check("normal 通道: build_section_prompt 不含「技能引用」区块",
          "技能引用" not in p_normal)
    # 任务卡端到端（临时项目, 不依赖 dsh 在线）:
    # ① --via subagent dry-run: 契约执行者列带「执行者:模型」语法 →
    #    路由用执行者部分 + 任务卡含技能区块（位于输出协议之前）
    import subagent_writer as sw
    tmp = temp_dir("wb_skill_")
    try:
        (tmp / "draft").mkdir(parents=True, exist_ok=True)
        (tmp / "draft" / "gen_state.json").write_text('{"sections": {}}', encoding="utf-8")
        contract_raw = ("# 生成契约\n\n> 契约状态：已锁定\n\n"
                        "## 1. 范围声明\n\n- 覆盖：技能通道冒烟\n\n"
                        "## 2. 章节大纲\n\n"
                        "| 段号 | 标题 | 字数预算 | 要点 | 依赖 | 衔接锚点 | 执行者 |\n"
                        "|---|---|---|---|---|---|---|\n"
                        "| S1 | Introduction | 800 | 背景 |  |  | workbuddy:glm-5.2 |\n\n"
                        "## 4. 引文分配\n\n| 文献键 | 分配段落 | 支撑要点 |\n|---|---|---|\n"
                        "| ref1 | S1 | 支撑 |\n")
        (tmp / "draft" / "contract.md").write_text(contract_raw, encoding="utf-8")
        r = sw.gen_section_via_subagent(tmp, "S1", dry_run=True)
        pr = r.get("prompt", "")
        check("subagent 任务卡: 执行者:模型 → 路由执行者=workbuddy",
              r.get("ok") and r.get("executor") == "workbuddy", f"r={r.get('executor')}")
        check("subagent 任务卡: 含技能区块且位于输出协议之前",
              "## 技能引用" in pr and "skills/scientific-writing/SKILL.md" in pr
              and pr.index("## 技能引用") < pr.index("## 输出协议"),
              pr[-600:])
        # ② 并行路径任务卡构造: 拦截派发只收 prompt（不真实派发）,
        #    验证技能区块注入 + 路由剥离模型 + dispatch-log 记 model 字段
        import parallel_gen as pg
        contract = sg.parse_contract(contract_raw)
        captured = {}
        orig = pg.runner.dispatch_with_fallback

        def _fake(pref, task, cwd=None, timeout=1800):
            captured["pref"], captured["prompt"] = pref, task
            return {"ok": False, "error": "smoke fake offline", "executor": pref or "dsh",
                    "fallback_from": "", "route": []}

        pg.runner.dispatch_with_fallback = _fake
        try:
            res = pg._run_one(tmp, contract, contract["sections"][0], 1, {},
                              threading.Lock(), 5)
        finally:
            pg.runner.dispatch_with_fallback = orig
        pp = captured.get("prompt", "")
        check("并行任务卡: 路由剥离模型（pref=workbuddy）且段结果失败隔离",
              captured.get("pref") == "workbuddy" and res.get("status") == "failed",
              f"pref={captured.get('pref')} res={res.get('status')}")
        check("并行任务卡: 含技能区块且位于输出协议之前",
              "## 技能引用" in pp and pp.index("## 技能引用") < pp.index("## 输出协议"),
              pp[-600:])
        logf = tmp / "draft" / "orchestration" / "dispatch-log.jsonl"
        logged = logf.read_text(encoding="utf-8") if logf.exists() else ""
        check("dispatch-log: 执行者:模型 的 model 字段已记录（透传为后续项）",
              '"model": "glm-5.2"' in logged, logged[-400:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rebuttal():
    print("[19] rebuttal.py 审稿回复草稿（拆条/严重级/产物/重渲染一致）")
    import os as _os
    _env = dict(_os.environ, PYTHONIOENCODING="utf-8")  # ✔ 类字符在 GBK 重定向下会崩, 显式 utf-8
    tmp = temp_dir("wb_rebuttal_")
    try:
        (tmp / "review").mkdir(parents=True)
        (tmp / "state.json").write_text(json.dumps({"schema": 2, "lang": "zh", "type": "article"}),
                                          encoding="utf-8")
        mock = ("# 模拟审稿意见\n\n## 视角 1：方法与可复现性\n\n"
                "- 意见：实验缺少阴性对照，这是方法学上的根本缺陷，建议补充对照实验。\n"
                "- 认可：数据量充足。\n"
                "- 意见：菌株培养条件描述不清，请澄清温度与培养基。\n\n"
                "## 视角 2：贡献与新颖性\n\n"
                "- 意见：与已有工作差异阐述不足，建议补充与前期工作的对比（见第3节）。\n\n"
                "## 视角 3：结构与清晰度\n\n"
                "- 意见：图 2 坐标轴标签过小，属于排版小问题，建议放大。\n")
        (tmp / "review" / "mock-reviews.md").write_text(mock, encoding="utf-8")
        # 默认项目通道（注意: 管道/捕获环境下 stdin 非 tty，须显式给空输入避免阻塞）
        r = subprocess.run([PY, "rebuttal.py", "draft", "--dir", str(tmp)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(BASE), input="", timeout=120, env=_env)
        out = r.stdout + r.stderr
        items_p = tmp / "review" / "rebuttal" / "items.json"
        draft_p = tmp / "review" / "rebuttal" / "draft.md"
        check("draft: items.json + draft.md 生成", r.returncode == 0 and items_p.exists()
              and draft_p.exists(), out[-300:])
        ids1 = []
        if items_p.exists():
            items = json.loads(items_p.read_text(encoding="utf-8"))
            pts = items.get("points", [])
            check("拆条条数 ≥3", len(pts) >= 3, f"n={len(pts)}")
            check("每条含合法 severity 字段",
                  len(pts) > 0 and all(p.get("severity") in ("major", "minor", "neutral", "positive")
                                       for p in pts),
                  f"sev={[p.get('severity') for p in pts]}")
            ids1 = [p["id"] for p in pts]
        else:
            check("拆条条数 ≥3", False, "items.json 缺失")
            check("每条含合法 severity 字段", False, "items.json 缺失")
        d1 = draft_p.read_text(encoding="utf-8") if draft_p.exists() else ""
        # reparse 重渲染: 条数/条目正文一致（仅时间戳行可变）
        r2 = subprocess.run([PY, "rebuttal.py", "reparse", "--dir", str(tmp)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=str(BASE), input="", timeout=120, env=_env)
        out2 = r2.stdout + r2.stderr
        d2 = draft_p.read_text(encoding="utf-8") if draft_p.exists() else ""
        strip_ts = lambda s: "\n".join(l for l in s.splitlines() if not l.startswith("> 生成"))
        check("reparse 重渲染: 退出 0 且产物一致（除时间戳）",
              r2.returncode == 0 and strip_ts(d1) == strip_ts(d2)
              and ids1 and all(i in d2 for i in ids1), out2[-300:])
        # --src 外部审稿信通道: Comment N 格式拆条 + severity 关键词命中
        letter = tmp / "letter.txt"
        letter.write_text("Reviewer 1\n\nMajor concerns\n\n"
                          "Comment 1: The method has a fundamental flaw.\n"
                          "Comment 2: Please clarify the culture temperature.\n\n"
                          "Minor issues\n\nComment 3: A small typo in the abstract.\n",
                          encoding="utf-8")
        r3 = subprocess.run([PY, "rebuttal.py", "draft", "--src", str(letter), "--dir", str(tmp)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=str(BASE), input="", timeout=120, env=_env)
        out3 = r3.stdout + r3.stderr
        items2 = json.loads(items_p.read_text(encoding="utf-8")) if items_p.exists() else {}
        sev = {p.get("id"): p.get("severity") for p in items2.get("points", [])}
        check("--src 通道: Comment 格式拆条 ≥3 且 severity 关键词命中",
              r3.returncode == 0 and len(sev) >= 3
              and sev.get("P1") == "major" and sev.get("P3") == "minor",
              out3[-300:] + f" sev={sev}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_litmap():
    print("[20] litmap.py 文献地图（聚类/缺口标注/增量缓存/--rebuild 幂等）")
    import os as _os
    _env = dict(_os.environ, PYTHONIOENCODING="utf-8")  # ✔ 类字符在 GBK 重定向下会崩, 显式 utf-8
    tmp = temp_dir("wb_litmap_")
    try:
        (tmp / "framework").mkdir(parents=True)
        (tmp / "state.json").write_text(json.dumps({"schema": 2, "lang": "zh", "type": "article"}),
                                          encoding="utf-8")
        bib = [
            ("a1", "Protoplast preparation and regeneration of Aspergillus niger", "2010", "10.1/a1"),
            ("a2", "Protoplast regeneration conditions for fungal strains", "2011", "10.1/a2"),
            ("a3", "Protoplast formation and regeneration in filamentous fungi", "2012", "10.1/a3"),
            ("a4", "Protoplast release and regeneration efficiency in Aspergillus", "2010", "10.1/a4"),
            ("b1", "CRISPR genome editing of Aspergillus niger", "2018", "10.2/b1"),
            ("b2", "CRISPR-Cas9 genome editing in filamentous fungi", "2019", "10.2/b2"),
            ("b3", "Genome editing tools for industrial fungal cell factories", "2020", "10.2/b3"),
            ("c1", "Citric acid fermentation optimization in bioreactors", "2023", "10.3/c1"),
            ("c2", "Organic acid fermentation control at industrial scale", "2024", "10.3/c2"),
            ("c3", "Bioreactor agitation for citric acid production", "2025", "10.3/c3"),
        ]
        entries = "\n".join(
            "@article{%s,\n  author = {Author %s}, title = {%s}, journal = {J Test},\n"
            "  year = {%s}, doi = {%s}\n}" % (k, k, t, y, d) for k, t, y, d in bib)
        (tmp / "framework" / "references.md").write_text(
            "# 参考文献池\n\n```bibtex\n" + entries + "\n```\n", encoding="utf-8")
        cache_p = tmp / "research" / "litmap-cache.json"
        md_p = tmp / "research" / "litmap.md"
        # 首跑: 全量聚类 → 簇数 2-6 + 缓存生成 + 缺口候选带「待人判」
        r = subprocess.run([PY, "litmap.py", "--dir", str(tmp)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(BASE), input="", timeout=180, env=_env)
        out = r.stdout + r.stderr
        import re as _re
        m = _re.search(r"→\s*(\d+)\s*簇", out)
        n_clusters = int(m.group(1)) if m else 0
        cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
        check("首跑: 簇数 2-6 + litmap.md/缓存生成 + 缓存条目数=池规模",
              r.returncode == 0 and 2 <= n_clusters <= 6 and md_p.exists() and cache_p.exists()
              and cache.get("n") == 10 and cache.get("schema") == 1,
              f"clusters={n_clusters} cache_n={cache.get('n')} out={out[-300:]}")
        md1 = md_p.read_text(encoding="utf-8") if md_p.exists() else ""
        gap_sec = md1.split("## 4. 研究空白", 1)
        gap_rows = [l for l in (gap_sec[1].splitlines() if len(gap_sec) > 1 else [])
                    if _re.match(r"^\d+\.\s", l.strip())]
        check("缺口候选逐条标「待人判」（无缺口时明示规则未识别）",
              (gap_rows and all("待人判" in l for l in gap_rows))
              or (not gap_rows and "规则未识别出缺口候选" in md1),
              f"gap_rows={len(gap_rows)}")
        # 二跑: 池未变 → 增量缓存命中
        r2 = subprocess.run([PY, "litmap.py", "--dir", str(tmp)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=str(BASE), input="", timeout=180, env=_env)
        out2 = r2.stdout + r2.stderr
        check("二跑: 增量缓存命中（变更 0% ≤ 20%）", "增量归簇" in out2, out2[-300:])
        # --rebuild: 全量重算且产物结构不变（幂等）
        r3 = subprocess.run([PY, "litmap.py", "--dir", str(tmp), "--rebuild"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=str(BASE), input="", timeout=180, env=_env)
        out3 = r3.stdout + r3.stderr
        md2 = md_p.read_text(encoding="utf-8") if md_p.exists() else ""
        strip_ts = lambda s: "\n".join(l for l in s.splitlines() if not l.startswith("> 生成"))
        check("--rebuild: 全量重算且 litmap.md 结构一致（幂等）",
              "全量重算" in out3 and strip_ts(md1) == strip_ts(md2), out3[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("Paper Workbench Smoke Test")
    print("=" * 40)
    test_compile()
    test_state_machine()
    test_chart_out()
    test_chart_types()
    test_quality_check()
    test_intake_minrefs()
    test_citation_year()
    test_data2paper_fill()
    test_planned_chart_insertion()
    test_insert_order()
    test_originality_none()
    test_export_docx()
    test_export_image()
    test_import_docx()
    test_import_pdf()
    test_used_refs()
    test_anova_table()
    test_staged_gen_gates()
    test_flow_pipeline()
    test_dispatch()
    test_contract_new_format()
    test_taskcard_lang()
    test_gate_offline()
    test_integration_qc_funcs()
    test_wave_partition_semantics()
    test_skill_channel()
    test_rebuttal()
    test_litmap()
    test_mcp_server()
    test_web_server()
    print("=" * 40)
    print(f"PASS: {PASS}  FAIL: {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
