#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Paper Workbench Toolbox — 集成开源科研工具的统一入口。

集成工具:
- OpenAlex (pyalex)  — 文献检索 / 元数据
- arXiv (arxiv)     — 预印本检索
- Crossref (HTTP)   — DOI 核验 / 文献检索
- bibtexparser      — BibTeX 解析
- pandas / matplotlib / seaborn — 数据分析与图表

CLI 用法:
  python toolbox.py search "大语言模型 多智能体" --sources openalex,arxiv,crossref --limit 10
  python toolbox.py fetch 10.1038/nature14539
  python toolbox.py verify-bib references.bib
  python toolbox.py stats data.csv
  python toolbox.py chart data.csv --type bar --x year --y sales --out out.png
"""
import argparse
import csv
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
import difflib

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ─────────────────────────── 文献检索 ───────────────────────────

def _safe_http_json(url, timeout=30, retries=3):
    """带 429/5xx 指数退避重试的 HTTP GET → JSON。"""
    import time
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "paper-workbench/1.0 (mailto:research@example.com)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise RuntimeError("HTTP retries exhausted")


def search_openalex(query, limit=10):
    """OpenAlex 检索（直接 HTTP API，无需 pyalex 库）。"""
    try:
        url = "https://api.openalex.org/works?search={}&per_page={}&mailto=research@example.com".format(
            urllib.parse.quote(query), min(limit, 50))
        data = _safe_http_json(url)
        out = []
        for w in data.get("results", []):
            authors = [(a.get("author") or {}).get("display_name", "") for a in (w.get("authorships") or []) if a]
            out.append({
                "source": "openalex",
                "title": w.get("title") or w.get("display_name") or "",
                "year": w.get("publication_year"),
                "doi": (w.get("doi") or "").replace("https://doi.org/", ""),
                "authors": authors[:15],
                "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name") if w.get("primary_location") else None,
                "url": w.get("id"),
                "citations": w.get("cited_by_count") or 0,
            })
        return out
    except Exception as e:
        return [{"source": "openalex", "error": str(e)}]


def search_arxiv(query, limit=10):
    """arXiv 检索（直接 HTTP API + XML 解析，无需 arxiv 库）。"""
    try:
        import xml.etree.ElementTree as ET
        url = "http://export.arxiv.org/api/query?search_query={}&max_results={}&sortBy=relevance".format(
            urllib.parse.quote(query), min(limit, 50))
        req = urllib.request.Request(url, headers={"User-Agent": "paper-workbench/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            xml_data = r.read().decode("utf-8")
        root = ET.fromstring(xml_data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        out = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""
            published_el = entry.find("atom:published", ns)
            year = int(published_el.text[:4]) if published_el is not None and published_el.text else None
            doi_el = entry.find("atom:doi", ns)
            doi = doi_el.text if doi_el is not None and doi_el.text else ""
            id_el = entry.find("atom:id", ns)
            url_val = id_el.text if id_el is not None and id_el.text else ""
            authors = []
            for author in entry.findall("atom:author", ns):
                name_el = author.find("atom:name", ns)
                if name_el is not None and name_el.text:
                    authors.append(name_el.text)
            out.append({
                "source": "arxiv",
                "title": title,
                "year": year,
                "doi": doi,
                "authors": authors[:15],
                "venue": "arXiv",
                "url": url_val,
            })
        return out
    except Exception as e:
        return [{"source": "arxiv", "error": str(e)}]


def search_crossref(query, limit=10):
    try:
        url = "https://api.crossref.org/works?rows={}&query={}".format(limit, urllib.parse.quote(query))
        data = _safe_http_json(url)
        out = []
        for item in data.get("message", {}).get("items", []):
            out.append({
                "source": "crossref",
                "title": (item.get("title") or [""])[0],
                "year": (item.get("issued", {}).get("date-parts") or [[None]])[0][0],
                "doi": item.get("DOI", ""),
                "authors": [a.get("given", "") + " " + a.get("family", "") for a in item.get("author", [])],
                "venue": (item.get("container-title") or [""])[0] if item.get("container-title") else None,
                "url": item.get("URL"),
            })
        return out
    except Exception as e:
        return [{"source": "crossref", "error": str(e)}]


def search_europepmc(query, limit=10):
    """Europe PMC 检索（覆盖 PubMed + 预印本，免费无需 key）。"""
    try:
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={}&format=json&pageSize={}".format(
            urllib.parse.quote(query), limit)
        data = _safe_http_json(url)
        out = []
        for r in data.get("resultList", {}).get("result", []):
            authors = [a.strip() for a in (r.get("authorString") or "").split(",") if a.strip()]
            out.append({
                "source": "pubmed",
                "title": r.get("title", ""),
                "year": r.get("pubYear"),
                "doi": r.get("doi", ""),
                "authors": authors[:10],
                "venue": (r.get("journalInfo") or {}).get("journal", {}).get("title"),
                "url": r.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url") if r.get("fullTextUrlList") else None,
            })
        return out
    except Exception as e:
        return [{"source": "pubmed", "error": str(e)}]


def search_literature(query, sources=("openalex", "arxiv", "crossref", "pubmed"), limit=10):
    merged = []
    for src in sources:
        src = src.strip().lower()
        if src in ("openalex",):
            merged.extend(search_openalex(query, limit))
        elif src in ("arxiv",):
            merged.extend(search_arxiv(query, limit))
        elif src in ("crossref",):
            merged.extend(search_crossref(query, limit))
        elif src in ("pubmed", "europepmc"):
            merged.extend(search_europepmc(query, limit))
    # 按 DOI 去重
    seen = set()
    dedup = []
    for item in merged:
        key = item.get("doi") or item.get("title") or ""
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        dedup.append(item)
    return dedup


# ── AnySearch 网页/垂直检索（MCP over HTTP，远程 api.anysearch.com）──
ANYSEARCH_URL = "https://api.anysearch.com/mcp"


def _anysearch_key():
    """读取 AnySearch API key：环境变量 ANYSEARCH_API_KEY 优先，其次 ZCode MCP 配置。
    找不到则返回空串（匿名访问，限流更低）。不在本文件重复存 key。"""
    import os
    k = os.environ.get("ANYSEARCH_API_KEY", "").strip()
    if k:
        return k
    try:
        cfg = Path.home() / ".zcode" / "cli" / "config.json"
        d = json.loads(cfg.read_text(encoding="utf-8"))
        auth = (d.get("mcp", {}).get("servers", {}).get("anysearch", {})
                .get("headers", {}).get("Authorization", ""))
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
    except Exception:
        pass
    return ""


def anysearch_search(query, max_results=8):
    """调用 AnySearch MCP 的 search 工具做网页检索，返回 Markdown 结果文本。
    补充学术库（OpenAlex/arXiv/Crossref）之外的网页/灰色文献/指南来源。"""
    if not query or not str(query).strip():
        return "query 不能为空"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Anysearch-Client": "mcp/1.0.0",
    }
    key = _anysearch_key()
    if key:
        headers["Authorization"] = "Bearer " + key
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "search",
                       "arguments": {"query": str(query), "max_results": int(max_results or 8)}}}
    req = urllib.request.Request(ANYSEARCH_URL, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"AnySearch 调用失败: {e}"
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else json.loads(raw)
    except Exception:
        return raw[:3000]
    content = (d.get("result") or {}).get("content") or []
    texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(texts).strip() if texts else "(AnySearch 无结果)"


def anysearch_extract(url):
    """调用 AnySearch MCP 的 extract 工具，抓取指定 URL 全文（Markdown）。"""
    if not url or not str(url).strip():
        return "url 不能为空"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Anysearch-Client": "mcp/1.0.0",
    }
    key = _anysearch_key()
    if key:
        headers["Authorization"] = "Bearer " + key
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "extract", "arguments": {"url": str(url)}}}
    req = urllib.request.Request(ANYSEARCH_URL, data=json.dumps(body).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"AnySearch 抓取失败: {e}"
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else json.loads(raw)
    except Exception:
        return raw[:3000]
    content = (d.get("result") or {}).get("content") or []
    texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(texts).strip() if texts else "(抓取无内容)"


def _clean_crossref_text(s):
    """剥掉 Crossref 返回标题中的 HTML 标签(<i> 等)与多余空白。"""
    if not s:
        return s
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_doi(doi):
    doi = doi.strip().replace("https://doi.org/", "").replace("http://dx.doi.org/", "")
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    try:
        data = _safe_http_json(url)
        m = data.get("message", {})
        return {
            "doi": doi,
            "title": _clean_crossref_text((m.get("title") or [""])[0]),
            "year": (m.get("issued", {}).get("date-parts") or [[None]])[0][0],
            "authors": [a.get("given", "") + " " + a.get("family", "") for a in m.get("author", [])],
            "venue": _clean_crossref_text((m.get("container-title") or [""])[0]) if m.get("container-title") else None,
            "volume": m.get("volume", ""),
            "issue": m.get("issue", ""),
            "page": m.get("page", ""),
            "url": m.get("URL"),
        }
    except Exception as e:
        return {"doi": doi, "error": str(e)}


# ─────────────────────────── BibTeX 解析与核验 ───────────────────────────

def parse_bibtex(text):
    try:
        import bibtexparser
        from bibtexparser.bparser import BibTexParser
        parser = BibTexParser(common_strings=True)
        db = bibtexparser.loads(text, parser=parser)
        entries = []
        for e in db.entries:
            entries.append({
                "id": e.get("ID", ""),
                "type": e.get("ENTRYTYPE", ""),
                "title": _strip_braces(e.get("title", "")),
                "author": _strip_braces(e.get("author", "")),
                "year": _strip_braces(e.get("year", "")),
                "journal": _strip_braces(e.get("journal", e.get("booktitle", ""))),
                "doi": _strip_braces(e.get("doi", "")),
                "volume": _strip_braces(e.get("volume", "")),
                "number": _strip_braces(e.get("number", "")),
                "pages": _strip_braces(e.get("pages", "")),
            })
        return entries
    except Exception:
        return _parse_bibtex_fallback(text)


def _parse_bibtex_fallback(text):
    """标准库 BibTeX 解析器（bibtexparser 不可用时降级）。"""
    entries = []
    for m in re.finditer(r'@(\w+)\s*\{\s*([^,\s]+)\s*,', text):
        etype = m.group(1).lower()
        eid = m.group(2).strip()
        start = m.end()
        depth = 1
        pos = start
        while pos < len(text) and depth > 0:
            if text[pos] == '{':
                depth += 1
            elif text[pos] == '}':
                depth -= 1
            pos += 1
        body = text[start:pos-1]
        entry = {"id": eid, "type": etype, "title": "", "author": "",
                 "year": "", "journal": "", "doi": "",
                 "volume": "", "number": "", "pages": ""}
        for fm in re.finditer(r'(\w+)\s*=\s*[\{"]?(.*?)[\}"]?\s*,?\s*(?=\w+\s*=|\Z)', body, re.S):
            key = fm.group(1).lower()
            val = _strip_braces(fm.group(2).strip().rstrip(','))
            if key in entry:
                entry[key] = val
        entries.append(entry)
    return entries


def _strip_braces(s):
    """剥掉 bibtexparser 保留的外层花括号，如 '{2007}' → '2007'、'{Title}' → 'Title'。"""
    if not isinstance(s, str):
        return s
    s = s.strip()
    while len(s) >= 2 and s.startswith("{") and s.endswith("}"):
        s = s[1:-1].strip()
    return s


def verify_bibtex(text):
    entries = parse_bibtex(text)
    if entries and "error" in entries[0]:
        return entries
    out = []
    for e in entries:
        doi = e.get("doi", "")
        if not doi:
            out.append({**e, "status": "no_doi", "note": "缺少 DOI,无法自动核验"})
            continue
        meta = fetch_doi(doi)
        if "error" in meta:
            # arXiv 预印本 DOI(10.48550/arxiv.xxxx)不在 Crossref 注册,
            # 404 是预期行为 → 走 arXiv API 二次核验,避免误标 not_found
            if doi.lower().startswith("10.48550/arxiv."):
                arxiv_id = doi.lower().split("arxiv.", 1)[-1]
                got = _arxiv_api_verify(arxiv_id)
                if got:
                    title_ok = _fuzzy_equal(e.get("title", ""), got[0])
                    year_ok = (str(e.get("year", "")).strip() == str(got[1])) if (e.get("year") and got[1]) else True
                    out.append({
                        **e, "status": "verified" if title_ok and year_ok else "mismatch",
                        "note": f"arXiv DOI(Crossref 不注册)→ arXiv API 核验: 标题{'匹配' if title_ok else '不匹配'};年份{'匹配' if year_ok else '不匹配'}",
                        "crossref_title": got[0], "crossref_year": got[1],
                    })
                    continue
            out.append({**e, "status": "not_found", "note": f"DOI 未解析: {meta['error']}"})
            continue
        title_ok = _fuzzy_equal(e.get("title", ""), meta.get("title", ""))
        # CrossRef 未返回年份（含瞬时故障）时不按不匹配处理
        year_ok = (not e.get("year")) or (not meta.get("year")) or (str(e["year"]).strip() == str(meta["year"]).strip())
        out.append({
            **e,
            "status": "verified" if title_ok and year_ok else "mismatch",
            "note": f"标题{'匹配' if title_ok else '不匹配'};年份{'匹配' if year_ok else '不匹配'}",
            "crossref_title": meta.get("title"),
            "crossref_year": meta.get("year"),
        })
    return out


def _fuzzy_equal(a, b):
    """标题/题名近似比较（2026-08-22 强化）。

    容忍：大小写、HTML 标签（<i>/<scp>）、非 ASCII 字符（中文括注、en-dash、
    声调符号）、标点与空白差异；仍不足时用词集合 Jaccard 相似度兜底。
    """
    def _norm(s):
        s = re.sub(r"<[^>]+>", " ", s or "")
        s = "".join(ch for ch in s.lower() if ord(ch) < 128)
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return " ".join(s.split())
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na == nb or (len(na) > 10 and (na in nb or nb in na)):
        return True
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return False
    jaccard = len(ta & tb) / len(ta | tb)
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    return max(jaccard, seq) >= 0.80


def _arxiv_api_verify(arxiv_id, timeout=25):
    """轻量 arXiv API 核验单条预印本: 返回 (title, year) 或 None。

    用 HTTP 直查 export.arxiv.org(id_list),避免 arxiv 库 Client 无超时参数、
    网络不佳时拖垮整条核验流水线(实测 id_list 请求可超时 >120s)。
    """
    url = "https://export.arxiv.org/api/query?id_list={}&max_results=1".format(urllib.parse.quote(arxiv_id))
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "paper-workbench/1.0 (mailto:research@example.com)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            xml = r.read().decode("utf-8", errors="replace")
        tm = re.search(r"<entry>[\s\S]*?<title>(.*?)</title>", xml, re.S)
        ym = re.search(r"<published>(\d{4})", xml)
        if not tm:
            return None
        title = re.sub(r"\s+", " ", tm.group(1)).strip()
        if title.lower().startswith("arxiv:"):  # 无结果时 arXiv 返回占位标题
            return None
        return (title, int(ym.group(1)) if ym else None)
    except Exception:
        return None


def qfix(project_dir, issue_type):
    """质量诊断一键修复: 可自动修的类型就地修, 其余返回定位信息供人工修改。

    返回 {"ok": bool, "msg": str, "file": str, "line": int|None}
    """
    project = Path(project_dir)
    main_md = project / "manuscript" / "main.md"

    def _loc(file_rel, line=None):
        return {"ok": False, "msg": "该问题需人工修改，已定位到文件，可直接在预览中编辑。", "file": file_rel, "line": line}

    if issue_type == "data_availability":
        if not main_md.exists():
            return {"ok": False, "msg": "缺少 manuscript/main.md", "file": "manuscript/main.md", "line": None}
        text = main_md.read_text(encoding="utf-8", errors="replace")
        if "Data Availability" in text or "数据可用性" in text:
            return {"ok": False, "msg": "已存在 Data Availability 章节，请人工核对内容", "file": "manuscript/main.md", "line": None}
        st = project / "state.json"
        ptype = "article"
        if st.exists():
            try:
                ptype = json.loads(st.read_text(encoding="utf-8")).get("type", "article")
            except Exception:
                pass
        if ptype in ("review", "thesis"):
            decl = "\n## 数据可用性\n本文无原始实验数据；引用的数据集与文献来源已在正文与参考文献中标注。\n"
        else:
            decl = "\n## Data Availability\nAll datasets and code supporting the findings of this study are available in a public repository (anonymous link for review); accession numbers will be provided upon acceptance.\n"
        text = text.rstrip() + decl
        main_md.write_text(text, encoding="utf-8")
        return {"ok": True, "msg": "已自动追加 Data Availability 声明，请核对后按实际仓库链接修改", "file": "manuscript/main.md", "line": None}

    if issue_type == "figure_legend":
        if not main_md.exists():
            return {"ok": False, "msg": "缺少 manuscript/main.md", "file": "manuscript/main.md", "line": None}
        text = main_md.read_text(encoding="utf-8", errors="replace")
        if re.search(r"Figure Legends|Figure Captions|图注", text, re.I):
            return {"ok": False, "msg": "已存在图注章节，请人工核对", "file": "manuscript/main.md", "line": None}
        decl = "\n## Figure Legends\n**Figure 1.** (待填：图题与图注，说明每组实验/数据含义、误差棒与显著性标注)\n"
        text = text.rstrip() + decl
        main_md.write_text(text, encoding="utf-8")
        return {"ok": True, "msg": "已追加 Figure Legends 模板，请按图表规划 framework/figures.md 逐图填写", "file": "manuscript/main.md", "line": None}

    if issue_type == "figures_insert":
        import subprocess as _sp
        try:
            r = _sp.run([sys.executable, str(ENGINE / "data2paper.py"), "fill", str(project)],
                        capture_output=True, text=True, encoding="utf-8", timeout=300)
            out = r.stdout.strip().splitlines()[-3:]
            return {"ok": True, "msg": "已运行 data2paper fill：\n" + "\n".join(out), "file": "manuscript/main.md", "line": None}
        except Exception as e:
            return {"ok": False, "msg": f"自动填充失败: {e}", "file": "manuscript/main.md", "line": None}

    if issue_type in ("figures_plan", "tables_plan"):
        # 自动在 framework/figures.md 补齐缺失的图表规划占位行(按文章类型的最低数量要求)
        return _pad_figures_plan(project, issue_type)

    if issue_type == "citations_uncited":
        try:
            res = used_refs(project_dir, write=True)
            if "error" in res:
                return {"ok": False, "msg": res["error"], "file": "framework/references.md", "line": None}
            return {"ok": True, "msg": f"已生成引用收窄清单 manuscript/refs_used.md（正文引用 {res['used']}/{res['total']} 条，未引用 {res['unused']} 条），请按清单删减或补引", "file": "manuscript/refs_used.md", "line": None}
        except Exception as e:
            return {"ok": False, "msg": f"生成失败: {e}", "file": "framework/references.md", "line": None}

    if issue_type == "placeholder":
        if not main_md.exists():
            return {"ok": False, "msg": "缺少 manuscript/main.md", "file": "manuscript/main.md", "line": None}
        lines = main_md.read_text(encoding="utf-8", errors="replace").splitlines()
        import re as _re_qf
        hits = [i + 1 for i, ln in enumerate(lines) if _re_qf.search(r'\[TBD[^\]]*\]|\[DATA REQUIRED\]|___|<!--\s*/?INSERT-(?:FIG|TAB)\s*-->', ln)]
        if hits:
            return {"ok": False, "msg": f"占位符需人工替换：第 {', '.join(str(h) for h in hits[:10])} 行", "file": "manuscript/main.md", "line": hits[0]}
        return {"ok": False, "msg": "未发现占位符", "file": "manuscript/main.md", "line": None}

    # 其余类型: 返回定位信息, 人工修改
    return _loc(_file_of_type(issue_type), None)


def _pad_figures_plan(project, issue_type):
    """一键修复 figures_plan / tables_plan: 在 framework/figures.md 补齐缺失的规划占位行。

    与 quality_check 的计数规则保持一致(行首 `| 图N |` / `| 表N |` 计数),
    补齐到文章类型的最低数量要求(review 3图2表, 其余 5图3表)。
    """
    import re as _re
    st = project / "state.json"
    ptype = "article"
    if st.exists():
        try:
            ptype = json.loads(st.read_text(encoding="utf-8")).get("type", "article")
        except Exception:
            pass
    min_figs, min_tabs = (3, 2) if ptype == "review" else (5, 3)
    target = "图" if issue_type == "figures_plan" else "表"
    target_min = min_figs if issue_type == "figures_plan" else min_tabs

    fig_path = project / "framework" / "figures.md"
    if not fig_path.exists():
        fig_path.parent.mkdir(parents=True, exist_ok=True)
        fig_path.write_text(
            "# 图表规划表\n\n"
            "| 编号 | 类型(图/表) | 内容 | 数据来源 | 对应章节 | 期刊规范要求 |\n"
            "|------|------------|------|----------|----------|--------------|\n",
            encoding="utf-8",
        )
    lines = fig_path.read_text(encoding="utf-8", errors="replace").splitlines()
    # 统计当前数量
    have_figs = len(_re.findall(r"^\|\s*图\d+", "\n".join(lines), _re.M))
    have_tabs = len(_re.findall(r"^\|\s*表\d+", "\n".join(lines), _re.M))
    missing = target_min - (have_figs if target == "图" else have_tabs)
    if missing <= 0:
        return {"ok": False, "msg": f"{target}规划数量已达标({have_figs}图/{have_tabs}表)，无需补齐", "file": "framework/figures.md", "line": None}
    # 动态推断表格列数: 取第一个含 | 的表格行; 无则默认 6 列
    cols = 6
    for ln in lines:
        if "|" in ln:
            cols = max(ln.count("|") - 1, 3)
            break
    pad = ["待补充"] * (cols - 3)  # 编号/类型/内容 之后的列填「待补充」
    added = []
    for i in range(1, missing + 1):
        n = have_figs + (i if target == "图" else 0)
        if target == "表":
            n = have_tabs + i
        row = f"| {target}{n} | {target} | 待补充：{target}题与内容说明（一键修复补齐，请编辑填充） | " + " | ".join(pad) + " |"
        # 避免编号重复导致计数不准
        if _re.match(rf"^\|\s*{target}\d+", row) and not any(_re.match(rf"^\|\s*{target}{n}\s*\|", l) for l in lines):
            lines.append(row)
            added.append(f"{target}{n}")
    fig_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not added:
        return {"ok": False, "msg": f"未新增{target}规划行（编号可能已存在），请人工核对 framework/figures.md", "file": "framework/figures.md", "line": None}
    return {"ok": True, "msg": f"已在 framework/figures.md 自动补齐 {len(added)} 行{target}规划占位：{'、'.join(added)}。请编辑「待补充」处填写真实内容", "file": "framework/figures.md", "line": None}


def _file_of_type(issue_type):
    """issue type → 定位文件(与 quality_check 的附加 file 逻辑保持一致)。"""
    m = {
        "references_count": "framework/references.md", "references_recency": "framework/references.md",
        "figures_plan": "framework/figures.md", "tables_plan": "framework/figures.md",
        "contribution_missing": "framework/contribution.md", "contribution_field": "framework/contribution.md",
        "contribution_soften": "framework/contribution.md",
        "results_validation_missing": "framework/results-validation.md", "results_validation_col": "framework/results-validation.md",
        "results_validation_hard": "framework/results-validation.md", "results_validation_gap": "framework/results-validation.md",
    }
    return m.get(issue_type, "manuscript/main.md")


def used_refs(project_dir, write=False):
    """从正文 [n] 引用反推 framework/references.md 候选池中实际被引用的条目。

    解决「references.md 是检索候选池、不是最终引用表」的工作流缺口:
    正文定稿后运行,输出已引用清单与未引用冗余条目,供作者收窄列表或补引。
    """
    project = Path(project_dir)
    main_md = project / "manuscript" / "main.md"
    refs_md = project / "framework" / "references.md"
    if not main_md.exists() or not refs_md.exists():
        return {"error": "缺少 manuscript/main.md 或 framework/references.md"}
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n",
                    main_md.read_text(encoding="utf-8", errors="replace"),
                    maxsplit=1, flags=re.I)[0]
    cited = set()
    for m in re.finditer(r"[\[\(]([^\]\)\n]{1,160})[\]\)]", body):
        inner = re.sub(r"\d{1,3}(?:,\d{3})+", "NUM", m.group(1))
        inner = re.sub(r"\d+\s*±\s*\d+", "NUM", inner)
        # 屏蔽菌株/保藏号 (CBS 513.88, ATCC 1015, NRRL 6241...) 与括号内度量值 ("~34 Mb", "33.9 Mb", "(14,165 ORFs)")
        # —— 其中数字会被误当引用编号, 大编号 (513) 会触发 "引用越界" 误报
        inner = re.sub(r"(?:CBS|ATCC|NRRL|DSM|NBRC|IFO|JCM|DSMZ)\s*[A-Z0-9][0-9.]*", "STRAIN", inner, flags=re.I)
        inner = re.sub(r"~?\d+(?:\.\d+)?\s*(?:Mb|kb|bp|kbp|g/L|mg|mg/L|\u03bcg|mL|ml|nm|\u03bcm)[\s\S]{0,12}", "MEASURE", inner, flags=re.I)
        for tok in re.finditer(r"(?<![\dA-Za-z])\d{1,3}(?:\s*[-–—]{1,2}\s*\d{1,3})?(?!\d)", inner):
            part = tok.group(0).strip()
            if not part:
                continue
            if re.search(r"[-–—]", part):
                try:
                    a, b = re.split(r"\s*[-–—]{1,2}\s*", part, 1)
                    a, b = int(a), int(b)
                    if a > 999 or b > 999 or b < a:
                        continue
                    cited.update(range(a, b + 1))
                except ValueError:
                    continue
            else:
                try:
                    n = int(part)
                    if n > 999:
                        continue
                    cited.add(n)
                except ValueError:
                    continue
    entries = parse_bibtex(refs_md.read_text(encoding="utf-8", errors="replace"))
    if entries and "error" in entries[0]:
        return {"error": entries[0]["error"]}
    used, unused = [], []
    for i, e in enumerate(entries, 1):
        (used if i in cited else unused).append({"num": i, **e})
    if write:
        out = project / "manuscript" / "refs_used.md"
        lines = ["# 正文引用清单(由 used-refs 从正文 [n] 引用反推)", ""]
        lines.append(f"- 候选池: framework/references.md 共 {len(entries)} 条")
        lines.append(f"- 正文实际引用: {len(used)} 条 | 未引用: {len(unused)} 条")
        lines.append("")
        lines.append("## 已引用(按编号)")
        for e in used:
            lines.append(f"{e['num']}. {e.get('author', '')} ({e.get('year', '')}). {e.get('title', '')}.")
        lines.append("")
        lines.append("## 未引用(候选池冗余,建议删除或补引)")
        for e in unused[:100]:
            lines.append(f"- [{e['num']}] {str(e.get('title', ''))[:80]}")
        if len(unused) > 100:
            lines.append(f"- ... 其余 {len(unused) - 100} 条略")
        out.write_text("\n".join(lines), encoding="utf-8")
        return {"total": len(entries), "used": len(used), "unused": len(unused), "file": str(out)}
    return {"total": len(entries), "used": len(used), "unused": len(unused)}


# ─────────────────────────── 数据分析与图表 ───────────────────────────

def stats_csv(text):
    import statistics
    text = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {"error": "空数据"}
    cols = list(rows[0].keys())
    out = {}
    for c in cols:
        nums = []
        for r in rows:
            try:
                nums.append(float(r[c]))
            except (TypeError, ValueError):
                pass
        if nums:
            out[c] = {
                "count": len(nums),
                "mean": round(statistics.mean(nums), 4),
                "min": round(min(nums), 4),
                "max": round(max(nums), 4),
                "stdev": round(statistics.stdev(nums), 4) if len(nums) > 1 else 0,
            }
    return {"columns": cols, "rows": len(rows), "stats": out}


def make_chart(filename, content, chart_type="bar", x_col=None, y_col=None, title="", out=None):
    from web.charts import generate_chart  # noqa: 复用同一图表模块
    out_dir = Path(out).parent if out else None
    res = generate_chart(filename, content, chart_type, x_col, y_col, title, out_dir)
    if out and res.get("format") in ("png", "svg"):
        # generate_chart 已经写文件;如果 out 指定了具体路径,重命名
        src = Path(res["file"])
        dst = Path(out)
        if src != dst and src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            res["file"] = str(dst)
            res["rel"] = str(dst)
    return res


def cn2bib(text):
    """把 CNKI/万方导出的中文题录（GB/T 7714 格式）解析为 BibTeX。

    支持 [J] 期刊 / [D] 学位论文 / [M] 专著 / [C] 会议等常见类型。
    示例输入（CNKI 导出→GB/T 7714）：
      [1] 张三, 李四. 原生质体融合技术研究进展[J]. 菌物学报, 2021, 40(5): 1234-1245.
    """
    out = []
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        m = re.match(r"^\[?\d*\]?\s*(?P<authors>[^。]+?)[.．]\s*(?P<title>.+?)\[(?P<type>[JDMCR])\][.．]?\s*(?P<rest>.*)$", line)
        if not m:
            continue
        authors = m.group("authors").strip()
        title = m.group("title").strip()
        etype = m.group("type")
        rest = m.group("rest").strip()
        year = ""
        venue = vol = issue = pages = ""
        ym = re.search(r"(19\d{2}|20\d{2})", rest)
        if ym:
            year = ym.group(1)
        if etype == "J":
            vm = re.search(r"([^,，]+?)[,，]\s*(19\d{2}|20\d{2})(?:,\s*(\d+)(?:\((\d+)\))?(?::\s*([\d\-—~]+))?)?", rest)
            if vm:
                venue = vm.group(1).strip()
                year = vm.group(2) or year
                vol = vm.group(3) or ""
                issue = vm.group(4) or ""
                pages = vm.group(5) or ""
            entry_type = "article"
            venue_key = "journal"
        elif etype in ("D",):
            venue = rest.split("年")[0].strip("[,，:： ]") if "年" in rest else rest[:40]
            venue = re.sub(r"[,，:：]?\s*(19\d{2}|20\d{2})\s*[.．]?$", "", venue).strip()
            entry_type = "phdthesis"
            venue_key = "school"
        elif etype in ("M",):
            venue = rest.split("年")[0].strip("[,，:： ]") if "年" in rest else rest[:40]
            venue = re.sub(r"[,，:：]?\s*(19\d{2}|20\d{2})\s*[.．]?$", "", venue).strip()
            entry_type = "book"
            venue_key = "publisher"
        else:
            venue = rest[:60]
            entry_type = "misc"
            venue_key = "note"
        author_str = authors.replace(",", " and ").replace("，", " and ").replace("、", " and ")
        fields = [f"  author = {{{author_str}}}", f"  title = {{{title}}}"]
        if venue:
            fields.append(f"  {venue_key} = {{{venue}}}")
        if year:
            fields.append(f"  year = {{{year}}}")
        if vol:
            fields.append(f"  volume = {{{vol}}}")
        if issue:
            fields.append(f"  number = {{{issue}}}")
        if pages:
            fields.append(f"  pages = {{{pages}}}")
        out.append(f"@{entry_type}{{cn{i},\n" + ",\n".join(fields) + "\n}")
    return out


# ─────────────────────────── 引用格式化 / 统计审计 / 原创性 / 导出 ───────────────────────────

def _clean_author(name):
    name = name.strip()
    parts = name.split()
    if len(parts) >= 2:
        return parts[-1] + " " + " ".join(parts[:-1])[:1] + "."
    return name


def format_references(bib_file, style="springer-numeric"):
    """读取 BibTeX，从 Crossref 补全卷/期/页，并按风格输出参考文献列表。"""
    text = Path(bib_file).read_text(encoding="utf-8", errors="replace")
    return format_references_text(text, style)


def format_references_text(text, style="springer-numeric"):
    entries = parse_bibtex(text)
    if entries and "error" in entries[0]:
        return entries
    out = []
    for i, e in enumerate(entries, 1):
        meta = fetch_doi(e.get("doi", "")) if e.get("doi") else {}
        authors_raw = e.get("author", "")
        authors = [a.strip() for a in authors_raw.replace(" and ", ",").split(",") if a.strip()]
        if not authors and meta.get("authors"):
            authors = meta["authors"]
        title = e.get("title", "")
        journal = e.get("journal", "")
        year = e.get("year", meta.get("year", ""))
        volume = meta.get("volume", "")
        issue = meta.get("issue", "")
        page = meta.get("page", "")
        doi = e.get("doi", meta.get("doi", ""))
        if style in ("springer-numeric", "ama"):
            auth_str = ", ".join(authors[:6]) + (" et al." if len(authors) > 6 else "")
            ref = f"{i}. {auth_str}. {title}. {journal}. {year}"
            if volume:
                ref += f";{volume}"
                if issue:
                    ref += f"({issue})"
            if page:
                ref += f":{page}"
            if doi:
                ref += f". doi:{doi}"
            out.append(ref)
        elif style == "apa":
            auth_str = ", ".join(_clean_author(a) for a in authors[:20])
            if len(authors) > 20:
                auth_str += "..."
            ref = f"{auth_str} ({year}). {title}. {journal}, {volume}({issue}), {page}. https://doi.org/{doi}"
            out.append(ref)
        else:
            out.append(f"{i}. {authors_raw} ({year}). {title}. {journal}. doi:{doi}")
    return out


def _bibtex_key(title, year, author=""):
    """从标题/年份/作者生成一个简单 BibTeX key。

    取作者的姓氏(最后一个词),跳过单字母缩写(J. / W. 等)与纯首字母,
    避免生成 j2026xxx / w2024xxx 这类畸形 key(作者串如 "Wang, J." 时旧逻辑取到 "J")。
    """
    first = ""
    if author:
        parts = re.findall(r"[A-Za-z\u4e00-\u9fff]+", author)
        for p in reversed(parts):
            if len(p) >= 2:
                first = p.lower()
                break
        if not first and parts:
            first = parts[-1].lower()
    words = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]+", title or "")
    slug = "".join(words[:4]).lower() if words else "ref"
    return f"{first}{year}{slug[:24]}" or "ref"


def _result_to_bibtex(r):
    """把检索结果 dict 转成 BibTeX 字符串。"""
    title = (r.get("title") or "").strip().replace("{", "").replace("}", "")
    authors = r.get("authors") or []
    author_str = " and ".join(authors)
    year = r.get("year") or ""
    journal = (r.get("venue") or "").strip()
    doi = (r.get("doi") or "").strip()
    key = _bibtex_key(title, year, authors[0] if authors else "")
    return (
        f"@article{{{key},\n"
        f"  author = {{{author_str}}},\n"
        f"  title = {{{title}}},\n"
        f"  journal = {{{journal}}},\n"
        f"  year = {{{year}}},\n"
        f"  doi = {{{doi}}}\n"
        f"}}"
    )


def build_refs(query, sources=("openalex", "arxiv", "crossref", "pubmed"), limit=30,
               min_total=80, recent_years=5, out_file=None, existing_bib=""):
    """检索多源文献、去重、核验 DOI，并生成/合并 BibTeX 参考文献清单。

    这是“找相关文献并正确引用”的核心自动化入口。
    增强：不足 min_total 时自动加大 limit 多轮补取（最多 3 轮）；
    同一时代内按被引次数排序（OpenAlex）；高相关条目（标题与检索词
    共享 ≥2 个词）在 BibTeX 中标注 % 高相关。
    """
    import datetime
    current_year = datetime.datetime.now().year
    seen = set()
    cleaned = []
    verified = []
    proc_idx = 0
    cur_limit = limit
    for round_no in range(1, 4):
        results = search_literature(query, sources, cur_limit)
        for r in results:
            if "error" in r:
                continue
            key = ((r.get("doi") or "") or (r.get("title") or "")).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            cleaned.append(r)
        # 核验本轮新增条目（按 cleaned 下标推进，避免重复核验）
        while proc_idx < len(cleaned):
            r = cleaned[proc_idx]
            proc_idx += 1
            doi = r.get("doi") or ""
            if doi:
                meta = fetch_doi(doi)
                if "error" not in meta:
                    r["title"] = r.get("title") or meta.get("title", "")
                    r["year"] = r.get("year") or meta.get("year", "")
                    r["authors"] = r.get("authors") or meta.get("authors", [])
                    r["venue"] = r.get("venue") or meta.get("venue", "")
            if r.get("title") and r.get("year"):
                verified.append(r)
        if len(verified) >= min_total or round_no >= 3:
            break
        cur_limit = int(cur_limit * 1.5) + 10

    existing_dois = set()
    existing_titles = set()
    existing_entries = []
    if existing_bib:
        parsed = parse_bibtex(existing_bib)
        if parsed and "error" not in parsed[0]:
            existing_entries = parsed
            for e in existing_entries:
                if e.get("doi"):
                    existing_dois.add(e["doi"].lower())
                if e.get("title"):
                    existing_titles.add(e["title"].lower())

    new_entries = []
    for r in verified:
        if r.get("doi") and r["doi"].lower() in existing_dois:
            continue
        if r.get("title") and r["title"].lower() in existing_titles:
            continue
        new_entries.append(r)

    # 近 5 年优先，同档内按被引次数倒序
    def sort_key(r):
        y = r.get("year") or 0
        try:
            y = int(y)
        except (TypeError, ValueError):
            y = 0
        return (y >= current_year - (recent_years - 1), y, r.get("citations") or 0)
    new_entries.sort(key=sort_key, reverse=True)

    # 高相关标注：标题与检索词共享 ≥2 个词（词长 ≥3）
    stop = {"the", "and", "for", "with", "from", "that", "this", "are", "was", "were", "its", "has", "had", "not", "but"}
    qwords = {w.lower() for w in re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]{3,}", query) if w.lower() not in stop}

    def _highly_relevant(r):
        if not qwords:
            return False
        twords = set(re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", (r.get("title") or "").lower()))
        return len(twords & qwords) >= 2

    bibtex_strs = []
    for r in new_entries:
        s = _result_to_bibtex(r)
        if _highly_relevant(r):
            s = "% 高相关(标题与检索词匹配 ≥2)\n" + s
        bibtex_strs.append(s)
    total = len(existing_entries) + len(new_entries)
    recent_count = sum(
        1 for r in verified
        if str(r.get("year", "")).isdigit() and int(r["year"]) >= current_year - (recent_years - 1)
    ) + sum(
        1 for e in existing_entries
        if str(e.get("year", "")).isdigit() and int(e["year"]) >= current_year - (recent_years - 1)
    )

    if out_file:
        out = Path(out_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        new_block = "\n\n".join(bibtex_strs)
        if out.exists():
            old_text = out.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"```bibtex\s*(.*?)```", old_text, re.S)
            if m:
                # 已存在带 bibtex 块的文件: 保留原头部与块外内容, 只把新条目并入块内
                old_block = m.group(1).strip()
                combined = old_block
                if combined and new_block:
                    combined += "\n\n" + new_block
                elif not combined:
                    combined = new_block
                new_text = (old_text[: m.start()] + "```bibtex\n" + combined + "\n```" + old_text[m.end():])
                out.write_text(new_text, encoding="utf-8")
            else:
                # 原文件无 bibtex 块: 追加一个新块
                with out.open("a", encoding="utf-8") as f:
                    f.write("\n```bibtex\n" + new_block + "\n```\n")
        else:
            # 新文件: 用模板创建
            lines = [
                "# 参考文献初稿(BibTeX)",
                "",
                "> 自动检索生成，请人工核验并补全卷/期/页；禁止编造。",
                "",
                "```bibtex",
                new_block,
                "```",
                "",
                "## 待补条目",
                f"- [ ] 当前共 {total} 条，建议至少 {min_total} 条（常见 80-120 条）",
                "- [ ] 近 5 年文献占比不低于 40%",
                "- [ ] 所有 DOI 已核验",
                "",
            ]
            out.write_text("\n".join(lines), encoding="utf-8")

    return {
        "query": query,
        "found": len(verified),
        "new": len(new_entries),
        "total": total,
        "recent_5y": recent_count,
        "rounds": round_no,
        "file": str(out_file) if out_file else None,
        "sources": list(sources),
    }


def citation_check(md_text, bib_entries):
    """检查正文编号引用与参考文献列表是否一致。支持 [1] 方括号与 (1-3) 圆括号两种编号制。"""
    cited = set()
    # 只扫正文, 排除 References 节及其后的图注/表注区块(含 600dpi 等文件名数字)
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    # 剥离 Markdown 图片/链接（2026-08-22）：文件名如 Figure_1_600dpi.jpg 的括号路径
    # 会被引用组正则捕获并误判为引用编号（600 → 引用越界误报）
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)
    # 支持 (1--3, 9, 10)、(42, 48--54, 84--87)、(21--24, 27--34, 70--72, 81, 87; Supplementary Table S4)
    # 等混合写法: 先抓括号内整段, 再提取编号 token(1-3 位数字或范围), 4 位数字视为年份跳过,
    # 排除 Table S4 / Figure 2 等字母后数字。
    for m in re.finditer(r"[\[\(]([^\]\)\n]{1,160})[\]\)]", body):
        inner = m.group(1)
        # 屏蔽统计表达式中的千分位数字与 ± 范围,防止 (mean 53,640±6,980 tokens) 被当引用编号
        inner = re.sub(r"\d{1,3}(?:,\d{3})+", "NUM", inner)
        inner = re.sub(r"\d+\s*±\s*\d+", "NUM", inner)
        # 屏蔽菌株/保藏号 (CBS 513.88, ATCC 1015, NRRL 6241...) 与括号内度量值 ("~34 Mb", "33.9 Mb", "(14,165 ORFs)")
        # —— 其中数字会被误当引用编号, 大编号 (513) 会触发 "引用越界" 误报
        inner = re.sub(r"(?:CBS|ATCC|NRRL|DSM|NBRC|IFO|JCM|DSMZ)\s*[A-Z0-9][0-9.]*", "STRAIN", inner, flags=re.I)
        inner = re.sub(r"~?\d+(?:\.\d+)?\s*(?:Mb|kb|bp|kbp|g/L|mg|mg/L|\u03bcg|mL|ml|nm|\u03bcm)[\s\S]{0,12}", "MEASURE", inner, flags=re.I)
        for tok in re.finditer(r"(?<![\dA-Za-z])\d{1,3}(?:\s*[-–—]{1,2}\s*\d{1,3})?(?!\d)", inner):
            part = tok.group(0).strip()
            if not part:
                continue
            if re.search(r"[-–—]", part):
                try:
                    a, b = re.split(r"\s*[-–—]{1,2}\s*", part, 1)
                    a, b = int(a), int(b)
                    if a > 999 or b > 999 or b < a:
                        continue  # 年份或非法范围
                    cited.update(range(a, b + 1))
                except ValueError:
                    continue
            else:
                try:
                    n = int(part)
                    if n > 999:
                        continue  # 年份
                    cited.add(n)
                except ValueError:
                    continue
    issues = []
    n = len(bib_entries)
    if not cited:
        issues.append({"severity": "P1", "type": "citations_missing", "msg": "正文未发现编号引用"})
        return issues
    max_cited = max(cited)
    if max_cited > n:
        issues.append({"severity": "P1", "type": "citations_out_of_range", "msg": f"正文引用编号最大 {max_cited}，但参考文献仅 {n} 条"})
    uncited = [i for i in range(1, n + 1) if i not in cited]
    if uncited:
        issues.append({"severity": "P2", "type": "citations_uncited", "msg": f"有 {len(uncited)} 条参考文献未被正文引用: {uncited[:10]}"})
    return issues


def audit_stats(md_text):
    """简单统计报告审计：检查常见缺失。"""
    issues = []
    lines = md_text.splitlines()
    for i, line in enumerate(lines, 1):
        low = line.lower()
        # 统计意义的 mean/average 才提示: \b 词边界排除 means/meaning/meant 等动词用法;
        # 再排除 "mean that / does not mean / this means" 等动词句式
        stat_mean = re.search(r"\bmean\b|\baverage\b", low) and not re.search(
            r"\b(?:does not mean|do not mean|mean that|means that|this means|what does this mean|would mean|may mean|it means)\b", low)
        if stat_mean:
            if "sd" not in low and "±" not in low and "+/-" not in low and "ci" not in low and "confidence" not in low:
                issues.append({"line": i, "type": "missing_sd", "text": line.strip()[:100], "msg": "出现均值但未见 SD/±"})
        if ("p <" in low or "p =" in low or "p<" in low or "p=" in low) and not any(k in low for k in ["t-test", "anova", "chi-square", "test", "fisher"]):
            issues.append({"line": i, "type": "missing_test", "text": line.strip()[:100], "msg": "出现 p 值但未说明检验方法"})
        if ("results" in low or "table" in low or "figure" in low) and "n=" not in low and "n =" not in low:
            # 不强制，只提示
            pass
    if not issues:
        issues.append({"line": 0, "type": "ok", "text": "", "msg": "未发现明显统计报告缺失"})
    return issues


def narrative_check(md_text):
    """叙事/故事线审计：检查稿件是否「讲好一个故事」。

    从 nature-writing / paper-spine / quality_rules.md 提炼的代码化规则子集：
    - 摘要四要素（问题/方法/结果/意义）
    - 引言漏斗（缺口 + 贡献预告）
    - Results 证据密度（数字/统计/图表引用）
    - Discussion 解释性回扣 + 局限
    - 段落卫生（超长段、过渡词堆砌）
    """
    issues = []
    text = md_text or ""
    low = text.lower()

    def _section(pattern):
        """匹配章节标题,捕获到下一个「级别 ≤ 本标题」的标题为止(子节 ### 4.1 不截断)。
        注意: 部分调用方 pattern 自带 (.*?)(?=\\n\\s*#+|\\Z) 尾巴, m.group(0) 会吞正文,
        因此只取第一行(标题行)计算级别与起点, 忽略尾巴。"""
        m = re.search(pattern, text, re.M | re.S | re.I)
        if not m:
            return None
        head_line = m.group(0).splitlines()[0]
        level = len(head_line) - len(head_line.lstrip("#"))
        head_end = m.start() + len(head_line)  # 标题行结束(不含换行), 忽略 pattern 尾巴
        rest = text[head_end:]
        for mm in re.finditer(r"^(\#{1,6})\s+\S", rest, re.M):
            if len(mm.group(1)) <= level:
                return rest[: mm.start()]
        return rest

    # 1. 摘要四要素
    abs_text = _section(r"(?:^#+\s*(?:abstract|摘要)\s*\n)(.*?)(?=\n\s*#+|\Z)")
    if abs_text:
        a = abs_text.lower()
        elements = {
            "问题/背景": ["we investigate", "we study", "here we", "remains", "remain", "limited", "challenge", "bottleneck", "problem", "issue", "问题", "研究", "挑战", "背景"],
            "方法": ["we propose", "we develop", "we present", "method", "approach", "search", "review", "analys", "evaluat", "survey", "方法", "提出", "开发", "采用", "实验", "检索", "综述"],
            "结果": ["we show", "we find", "result", "improve", "achiev", "结果表明", "我们发现", "显著", "提升", "达到", "增长"],
            "意义": ["suggest", "imply", "important", "provide", "意义", "为", "推动", "促进", "应用"],
        }
        missing = [k for k, sigs in elements.items() if not any(s in a for s in sigs)]
        if missing:
            issues.append({"severity": "P1", "type": "narrative_abstract",
                           "msg": f"摘要缺少故事要素: {', '.join(missing)}——摘要应四要素俱全（问题→方法→结果→意义），脱离正文可读"})
    else:
        # 期刊格式变体: Nature 系期刊常无 Abstract 标题(隐式摘要)
        head = text[:800].lower()
        implicit = any(w in head for w in ["we show", "we find", "we propose", "here we", "结果表明", "我们发现", "本文提出"])
        if implicit:
            issues.append({"severity": "P2", "type": "narrative_abstract",
                           "msg": "未找到 Abstract 标题——若为期刊隐式摘要格式可忽略；否则请补摘要（问题/方法/结果/意义四要素）"})
        else:
            issues.append({"severity": "P1", "type": "narrative_abstract", "msg": "未找到 Abstract/摘要 章节"})

    # 2. 引言漏斗: 缺口 + 贡献
    intro_text = _section(r"(?:^#+\s*(?:\d+[.．、]\s*)?introduction\s*\n|^#+\s*引言\s*\n)(.*?)(?=\n\s*#+|\Z)")
    if intro_text:
        i = intro_text.lower()
        gap_words = ["gap", "limited", "remains", "unclear", "lacking", "few studies", "然而", "但", "缺口", "不足", "空白", "尚未", "缺乏", "挑战"]
        contrib_words = ["we propose", "we present", "we develop", "we introduce", "contribution", "本文提出", "本文贡献", "我们提出", "贡献", "in this paper", "in this work", "this review", "the present review", "本综述", "本文综述", "我们"]
        if not any(w in i for w in gap_words):
            issues.append({"severity": "P1", "type": "narrative_intro",
                           "msg": "引言缺少「研究缺口」信号——故事要从缺口切入（漏斗叙事: 大问题→具体缺口→本文做什么）"})
        if not any(w in i for w in contrib_words):
            issues.append({"severity": "P1", "type": "narrative_intro",
                           "msg": "引言缺少「贡献预告」——读者应在引言末段看到本文要做什么"})
        # 引言首段: 先立大问题, 不抢跑贡献、不空转
        first_paras = [p for p in re.split(r"\n\s*\n", intro_text) if p.strip()]
        if first_paras:
            first = first_paras[0].lower()
            pm = re.search(r"\bwe (propose|present|introduce|develop)\b|本文提出|我们提出|our (method|approach|framework)", first)
            if pm:
                # 贡献抢跑: propose 之前没有问题/任务/技术/缺口语境
                prefix = first[:pm.start()]
                if not re.search(r"\b(task|problem|application|challenge|field|domain|industry|technique|method|key|important|slow|limited|bottleneck|gap|few studies|关键|问题|任务|应用|领域|挑战|重要|技术|缺口|不足|空白|尚未|缺乏)\b", prefix, re.I):
                    issues.append({"severity": "P2", "type": "narrative_intro_open",
                                   "msg": "引言首段直接进入贡献（贡献抢跑）——先立大问题/任务背景，再引入本文方案"})
            elif not re.search(r"\b(task|problem|application|challenge|field|domain|industry|important|关键|问题|任务|应用|领域|挑战|重要)\b", first):
                issues.append({"severity": "P2", "type": "narrative_intro_open",
                               "msg": "引言首段未见问题/任务/应用语境（背景空转）——首段应直接立大问题"})
    else:
        issues.append({"severity": "P2", "type": "narrative_intro",
                       "msg": "未找到 Introduction 标题——部分期刊引言无标题可忽略；但缺口与贡献检查仍基于全文"})

    # 3. Results 证据密度（支持 Results and Discussion 合并节）
    res_text = _section(r"(?:^#+\s*(?:\d+[.．、]\s*)?results?(?:\s+(?:and|&)\s+discussion)?\s*\n|^#+\s*结果(?:与|和)?讨论?\s*\n)(.*?)(?=\n\s*#+|\Z)")
    if res_text:
        evidence = re.findall(r"(?:\d+(?:\.\d+)?\s*[±%]|p\s*[<=]|p\s*[<>]=?\s*\d|figure\s*\d|fig\.?\s*\d|图\s*\d|表\s*\d|significant|显著)", res_text, re.I)
        if len(evidence) < 3:
            issues.append({"severity": "P1", "type": "narrative_results",
                           "msg": f"Results 证据密度低（仅 {len(evidence)} 处数字/统计/图表引用）——每节应回答「结果是什么」，证据跟着叙述走"})
    else:
        issues.append({"severity": "P1", "type": "narrative_results", "msg": "未找到 Results/结果 章节"})

    # 4. Discussion 回扣 + 局限（支持 Results and Discussion 合并节）
    disc_text = _section(r"(?:^#+\s*(?:\d+[.．、]\s*)?discussion\s*\n|^#+\s*results?(?:\s+(?:and|&)\s+)?discussion\s*\n|^#+\s*结果(?:与|和)?讨论\s*\n|^#+\s*讨论\s*\n)(.*?)(?=\n\s*#+|\Z)")
    if disc_text:
        d = disc_text.lower()
        interpret = ["consistent with", "suggest", "our results", "we interpret", "说明", "表明", "与", "一致", "综上", "我们认为", "可能", "likely"]
        if not any(w in d for w in interpret):
            issues.append({"severity": "P2", "type": "narrative_discussion",
                           "msg": "Discussion 缺少解释性回扣（consistent with / 表明 / 综上）——讨论应解释结果，而非重复 Results"})
        if "limit" not in d and "局限" not in d:
            issues.append({"severity": "P2", "type": "narrative_discussion",
                           "msg": "Discussion 未提及局限——诚实的故事需要边界"})
    else:
        issues.append({"severity": "P1", "type": "narrative_discussion", "msg": "未找到 Discussion/讨论 章节"})

    # 5. 段落卫生: 过渡词堆砌 + 主题句后置 + 超长段
    for i, ln in enumerate(text.splitlines(), 1):
        if re.match(r"^(Moreover|Furthermore|Additionally|In addition|Besides|此外|另外|再者)[,，:：]", ln.strip()):
            issues.append({"severity": "P2", "type": "narrative_flow",
                           "msg": f"L{i}: 段落以过渡词开头——优先用内容连接段落，避免 Moreover/此外 堆砌"})
        if re.match(r"^(?:As mentioned|As discussed|As stated|It is worth noting|It should be noted|如前所述|如上所述|值得一提的是|需要指出)[^,，:：]{0,15}[,，:：]", ln.strip(), re.I):
            issues.append({"severity": "P2", "type": "narrative_flow",
                           "msg": f"L{i}: 段落以「如前所述」类回顾开头——主题句后置，先给断言再衔接"})
    for i, para in enumerate(re.split(r"\n\s*\n", text)):
        # 跳过管道表格块与代码块（2026-08-22）：表格整体算一段会误报超长
        if para.lstrip().startswith(("|", "```")):
            continue
        if len(para.split()) > 250:
            issues.append({"severity": "P2", "type": "narrative_flow",
                           "msg": f"第 {i + 1} 段超过 250 词——一段一事，考虑拆分"})
    # 5.1 Methods 禁忌空话（可复现性检查）
    for m in re.finditer(r"under standard conditions|using routine methods|data were analyzed statistically|the method was validated|按照标准方法|采用常规方法|数据经统计分析", text, re.I):
        issues.append({"severity": "P1", "type": "method_vague",
                       "msg": f"Methods 禁忌空话: 「{m.group(0)[:40]}」——必须给出具体条件/参数/统计细节（可复现性）"})

    # 6. 语态漂移: Results 只报告证据，解释留给 Discussion
    if res_text:
        if re.search(r"\bsuggests?\b|\bimplies?\b|is likely|可能|表明|意味着", res_text, re.I):
            issues.append({"severity": "P2", "type": "narrative_voice",
                           "msg": "Results 出现解释性语态(suggest/表明/可能)——正片只报告证据，解释留给 Discussion"})
    if disc_text:
        if not re.search(r"consistent with|suggest|indicate|likely|due to|explain|我们认为|说明|可能|综上", disc_text, re.I):
            issues.append({"severity": "P1", "type": "narrative_voice",
                           "msg": "Discussion 缺少解释语态(suggest/consistent with/可能)——讨论应解释结果而非只报告"})

    # 7. 图表引用顺序（首次出现应编号递增;忽略 [Insert Figure N] 占位与图注区;重复引用不算逆序）
    if res_text:
        res_body = re.sub(r"\[?Insert (Figure|Table|Fig\.?) \d+\]?", "", res_text, flags=re.I)
        nums = [int(x) for x in re.findall(r"(?:figure|fig\.?|图)\s*(\d+)", res_body, re.I)]
        first_pos = {}
        for i, n in enumerate(nums):
            first_pos.setdefault(n, i)
        order = [n for n, _ in sorted(first_pos.items(), key=lambda kv: kv[1])]
        if len(order) >= 2 and any(order[i] > order[i + 1] for i in range(len(order) - 1)):
            issues.append({"severity": "P1", "type": "narrative_figorder",
                           "msg": "Results 图表引用逆序（首次出现应编号递增）——正文顺序与图表编号不对齐"})

    # 8. 空泛比较（对比必须有数字支撑）
    # 校准(真实已发表论文实测): 只认「X-er than / compared to|with / outperform」比较结构,
    # 排除固定短语(rather/other/another than)、方位词、improve 系列; 窗口含 than 后 20 字符以捕获数字。
    # outperform 分支窗口放宽到 40 字符: 「X outperforms Y by 3.1 points」的 by N 句式
    # 数字常在 outperform 后 21-35 字符处, 20 字符窗口会误伤。
    for m in re.finditer(
        r"(?:[A-Za-z]+er|more|less)\s+(?:than|compared\s+to|compared\s+with)[^。.]{0,20}|"
        r"outperform(?:s|ed|ing)?\b[^。.]{0,40}|显著高于|显著低于|优于[^。.\n]{0,20}",
        text, re.I):
        seg = m.group(0)
        low = seg.lower()
        if low.startswith(("rather than", "other than", "another than", "further than", "whether than", "either than")):
            continue  # 固定短语，非比较结构
        if not re.search(r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|hundred)\b", seg, re.I):
            # 比较对象为对照类词(control/baseline/对照)时,数字常在别处或图内 → 降为提示
            if re.search(r"\b(control|baseline|parental|mock|previous|conventional|traditional|standard|untreated|expected|对照|对照组|基线|亲本)\b", seg, re.I):
                issues.append({"severity": "P2", "type": "vague_compare",
                               "msg": f"对比「{seg.strip()[:50]}」未见具体数字——若数字在图/表或后文中请忽略，否则补数值"})
            else:
                issues.append({"severity": "P1", "type": "vague_compare",
                               "msg": f"空泛比较无数字支撑: 「{seg.strip()[:50]}」——对比必须有具体数值/统计"})

    # 9. 结论不引新材料（谢幕规则）
    concl_text = _section(r"(?:^#+\s*(?:6[.．、]?\s*)?conclusion\s*\n|^#+\s*结论\s*\n)(.*?)(?=\n\s*#+|\Z)")
    if concl_text:
        if re.findall(r"\[\d+\]", concl_text) or re.findall(r"(?:figure|fig\.?|图)\s*\d+", concl_text, re.I):
            issues.append({"severity": "P2", "type": "narrative_conclusion",
                           "msg": "结论中出现新引用/新图表——谢幕不引新材料，只收束已有论证"})

    # 10. 一段两意（转折词密集 = 可能承载多任务；校准: 真实论文 3 个转折词也常见, ≥4 才提示）
    for i, para in enumerate(re.split(r"\n\s*\n", text)):
        turns = re.findall(r"\b(however|nevertheless|in contrast|yet|but)\b|然而|但是|不过|相反|却", para.lower())
        if len(turns) >= 4 and len(para.split()) > 40:
            issues.append({"severity": "P2", "type": "narrative_overload",
                           "msg": f"第 {i + 1} 段出现 {len(turns)} 个转折词——可能承载多重任务，考虑拆分"})

    # 11. Claim 句证据锚（claims-gap 简化版：声称句须就近挂数字/图表/引用）
    # 校准: 只查显式声称动词句(we show/find/demonstrate/achieve/我们证明等),排除 our approach 意义句(provides/enables 不构成声称)
    claim_sents = re.findall(
        r"([^.!?]*(?:we show|we find|we demonstrate|we achieve|我们证明|我们表明|我们展示)[^.!?]*[.!?])",
        text, re.I)
    weak = [s.strip() for s in claim_sents
            if not re.search(r"\d|figure|fig|图\s*\d|table|表\s*\d|p\s*[<=]", s, re.I)]
    if weak:
        issues.append({"severity": "P1", "type": "claim_no_evidence",
                       "msg": f"发现 {len(weak)} 个无证据锚的声称句（如: 「{weak[0][:60]}」）——声称必须就近挂数字/图表/引用"})

    # 12. 句式多样性: 连续 3 句同一实词开头（过滤冠词/代词等停用词）
    _stop_heads = {"the", "a", "an", "this", "these", "those", "we", "our", "it", "its", "in", "on", "for", "with", "of", "and", "to",
                   "after", "before", "during", "however", "therefore", "thus", "as"}
    # 先剥离代码块与 markdown 标题行、表格行(含 | 的 markdown 表格或 2+ 连续空格的纯文本对齐表格)、
    # 表格分隔线(--- 或 === 或 +---+),避免污染句子切分
    body_text = re.sub(r"```.*?```", " ", text, flags=re.S)
    body_text = re.sub(r"^\s*#{1,6}.*$", "", body_text, flags=re.M)
    body_text = re.sub(r"^\s*\|.*\|\s*$", "", body_text, flags=re.M)          # markdown 管道表格行
    body_text = re.sub(r"^\s*[+|\-=]{3,}\s*$", "", body_text, flags=re.M)     # 表格分隔线
    body_text = re.sub(r"^\s*\S.*\S {2,}\S.*\S\s*$", "", body_text, flags=re.M)  # 纯文本对齐表格行(行内含 2+ 连续空格)
    sentences = [s for s in re.split(r"(?<=[.!?。！？])\s+", body_text) if s.strip()]
    for i in range(len(sentences) - 2):
        heads = []
        for s in sentences[i:i + 3]:
            m = re.match(r"\s*[\"'(\[]*([A-Za-z\u4e00-\u9fff]+)", s)
            h = m.group(1).lower() if m else ""
            heads.append(h if h not in _stop_heads else "")
        if heads[0] and heads[0] == heads[1] == heads[2]:
            issues.append({"severity": "P2", "type": "narrative_repetition",
                           "msg": f"连续 3 句以同一词「{heads[0]}」开头——句式单调，调整首词"})

    if not issues:
        issues.append({"severity": "P2", "type": "ok", "msg": "叙事结构未发现明显问题"})
    return issues


# ─────────────────────────── PaperSpine 对齐检查 ───────────────────────────
# 以下函数移植自 PaperSpine 的 contribution_check / results_validation_check /
# humanize_check / section_economy_check / integrity_audit 的理念，全部标准库实现。

_PLACEHOLDER_RE = [
    (r"^\s*todo\b", True), (r"^\s*tbd\b", True), (r"^\s*fixme\b", True),
    (r"^\s*xxx\b", True), (r"^\s*n/?a\b", True), (r"^\s*-+\s*$", True),
    (r"^\s*\.\.\.+\s*$", True), (r"^\s*<[^>]*>\s*$", True), (r"^\s*\[[^\]]*\]\s*$", True),
    (r"^\s*待填\s*$", True), (r"^\s*待定\s*$", True), (r"^\s*无\s*$", True),
    (r"^\s*__+\s*$", True),
]

def _is_placeholder(cell, min_chars=12):
    """PaperSpine contribution_check 的占位符判定：TODO/TBD/<...>/待定/... 或实字数不足。

    增强：以全角/半角括号开头的单元格是模板填写提示（如「（明确、可被证伪的单一声称）」），
    不是真实内容；「a / b / c」多选项列表表示尚未选定（如贡献类型）。
    """
    value = (cell or "").strip()
    if not value:
        return True
    if value.startswith(("（", "(")):
        return True  # 模板说明文字特征
    if value.count(" / ") >= 2:
        return True  # 多选项列表未选定
    if "__" in value:
        return True  # 下划线占位（模板 ___ / C1: __ 等）
    low = value.lower()
    for pat, _ in _PLACEHOLDER_RE:
        if re.search(pat, low):
            return True
    stripped = re.sub(r"[`*_~\]\[<>]", "", value)
    stripped = re.sub(r"[　 ]", "", stripped)
    if len(stripped) < min_chars:
        # 中文信息密度高：含 ≥4 个中文字符即视为已填写（如「暂无」「证据不足」）
        cjk = sum(1 for ch in stripped if "\u4e00" <= ch <= "\u9fff")
        if not (cjk >= 4 and len(stripped) >= 4):
            return True
    return False


def _md_table_rows(text):
    """提取 Markdown 表格前 N 行：返回 (表头, 数据行)。标准库实现。"""
    rows = []
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and all(set(c) <= {"-", ":", " "} for c in cells if c):
            continue  # 分隔行
        rows.append(cells)
    return (rows[0], rows[1:]) if rows else ([], [])


def contribution_check(project_dir):
    """贡献门禁（PaperSpine confirmed_contribution 对齐）。

    读取 framework/contribution.md，要求四节字段齐全且非占位符：
      Core Contribution    主声称 / 类型 / 审稿人记忆点
      Why Needed           领域问题 / 具体缺口 / 具体挑战 / 先前为何解不了
      How Responds         设计回应 / 所需证据 / 可用证据 / 缺失证据
      Claim Boundary       强声称允许 / 软化或避免 / novelty风险 / significance风险
    『缺失证据』与『边界』为空 → P0（作者最易跳过、审稿最受罚的两格）。
    """
    project = Path(project_dir)
    path = project / "framework" / "contribution.md"
    if not path.exists():
        return [{"severity": "P0", "type": "contribution_missing",
                 "msg": "缺 framework/contribution.md —— PaperSpine 铁律：无已确认贡献不写正文。请先用 contribution 模板填写并确认。"}]
    text = path.read_text(encoding="utf-8", errors="replace")
    issues = []
    sections = {}
    current = None
    for raw in text.splitlines():
        m = re.match(r"^#{1,3}\s+(.*\S)\s*$", raw)
        if m:
            current = m.group(1).strip().lower()
            sections.setdefault(current, [])
        else:
            if current:
                sections[current].append(raw)
    def _matches(key, k):
        return key in k or k in key

    def body(section_keys):
        parts = []
        for k, lines in sections.items():
            if any(_matches(key, k) for key in section_keys):
                parts.append("\n".join(lines))
        return "\n".join(parts)

    def _field(section_keys, terms, label):
        txt = body(section_keys)
        hdr, rows = _md_table_rows(txt)
        for row in rows:
            if len(row) < 2:
                continue
            cell = row[0].lower()
            if any(t in cell for t in terms):
                return " ".join(row[1:])
        return ""

    fields = {
        "主声称": (("core contribution", "main contribution", "contribution", "核心贡献"), ("main statement", "main contribution", "contribution statement", "主声称", "贡献")),
        "类型": (("core contribution",), ("contribution type", "type", "类型")),
        "审稿人记忆点": (("core contribution",), ("reviewer payoff", "记忆点", "payoff", "reviewer")),
        "缺失证据": (("how", "respond", "回应"), ("evidence missing", "缺失证据", "missing", "缺失")),
        "可用证据": (("how", "respond", "回应"), ("evidence available", "可用证据", "available", "可用")),
        "强声称允许": (("claim", "boundary", "边界"), ("strong claims allowed", "强声称允许", "claims allowed", "允许")),
        "软化或避免": (("claim", "boundary", "边界"), ("soften", "avoid", "软化", "避免")),
        "novelty风险": (("claim", "boundary", "边界"), ("novelty", "novelty风险")),
        "significance风险": (("claim", "boundary", "边界"), ("significance", "significance风险")),
    }
    sev_of = {"缺失证据": "P0", "软化或避免": "P0", "强声称允许": "P0", "主声称": "P0", "novelty风险": "P1", "significance风险": "P1", "可用证据": "P1", "类型": "P1", "审稿人记忆点": "P2"}
    # 字段最小实字数：长陈述型字段要求实内容；边界/风险字段短一点也算（如 "too narrow" 是合法边界）
    min_chars_of = {"主声称": 12, "缺失证据": 12, "可用证据": 12, "审稿人记忆点": 8,
                    "强声称允许": 6, "软化或避免": 6, "novelty风险": 6, "significance风险": 6, "类型": 4}
    for label, (section_keys, terms) in fields.items():
        val = _field(section_keys, terms, label)
        is_placeholder = _is_placeholder(val, min_chars_of.get(label, 12))
        if is_placeholder:
            issues.append({"severity": sev_of[label], "type": "contribution_field",
                           "msg": f"framework/contribution.md 字段『{label}』为空/占位符（TODO/TBD/省略号/实字数不足）。该决定未真正做出，不能作为写作依据。"})
    # 反向：缺失证据非空但主声称仍满强度 → 提示软化
    missing_ev = _field(("how", "respond", "回应"), ("evidence missing", "缺失证据", "missing", "缺失"), "evidence missing")
    if not _is_placeholder(missing_ev, 12):
        issues.append({"severity": "P2", "type": "contribution_soften",
                       "msg": "检测到『缺失证据』已列出——请确认正文对应声称已软化或补足，勿以此为满强度声称。"})
    return issues


def results_validation_check(project_dir):
    """Results 承诺-证据映射（PaperSpine results_validation 对齐）。

    读 framework/results-validation.md，逐行校验两列『Contribution Claim Tested』与
    『Result/Evidence』不可空；声明贡献数(n)=Results 小节数不足 → 提示。"""
    project = Path(project_dir)
    path = project / "framework" / "results-validation.md"
    if not path.exists():
        return [{"severity": "P1", "type": "results_validation_missing",
                 "msg": "缺 framework/results-validation.md —— 每个 Results 小节都应对应一个贡献承诺(C1/C2…)。请在 draft 前用模板填写。"}]
    text = path.read_text(encoding="utf-8", errors="replace")
    hdr, rows = _md_table_rows(text)
    if not hdr:
        return [{"severity": "P1", "type": "results_validation_missing", "msg": "results-validation.md 无可解析表格。"}]

    def _col_idx(name_terms):
        for i, h in enumerate(hdr):
            hl = h.lower()
            if any(t in hl for t in name_terms):
                return i
        return -1
    c_idx = _col_idx(("contribution claim", "claim tested", "contribution", "承诺"))
    e_idx = _col_idx(("result/evidence", "result / evidence", "evidence", "结果"))
    u_idx = _col_idx(("results unit", "unit", "小节"))

    issues = []
    data = [r for r in rows if any(c for c in r)]
    if c_idx < 0:
        issues.append({"severity": "P1", "type": "results_validation_col", "msg": "results-validation.md 缺『Contribution Claim Tested』列。"})
    if e_idx < 0:
        issues.append({"severity": "P1", "type": "results_validation_col", "msg": "results-validation.md 缺『Result/Evidence』列。"})
    if c_idx >= 0 and e_idx >= 0:
        mapped, claimed = 0, set()
        for row in data:
            unit = row[u_idx] if u_idx >= 0 and u_idx < len(row) else f"第{data.index(row)+1}小节"
            claim = row[c_idx].strip() if c_idx < len(row) else ""
            ev = row[e_idx].strip() if e_idx < len(row) else ""
            if _is_placeholder(claim, 3) or not claim:
                issues.append({"severity": "P1", "type": "results_validation_hard",
                               "msg": f"{unit}: 『Contribution Claim Tested』为空/占位——纯指标行，验证不了任何承诺。映射到 C1/C2… 或删掉该小节。"})
            if _is_placeholder(ev, 3) or not ev:
                issues.append({"severity": "P1", "type": "results_validation_hard",
                               "msg": f"{unit}: 『Result/Evidence』为空/占位——只有承诺没有结果支撑。"})
            if claim and ev:
                mapped += 1
                for mm in re.findall(r"C\d+", claim):
                    claimed.add(mm)
        if data and mapped < len(data):
            issues.append({"severity": "P2", "type": "results_validation_gap",
                           "msg": f"仅有 {mapped}/{len(data)} 个 Results 小节同时有承诺与证据。"})
    return issues


# --- humanize（AI 味）多维检测，移植自 PaperSpine humanize_check 的有害子集 ---
_AI_CONN_ZH = ["首先", "其次", "再次", "最后", "综上所述", "总而言之", "总的来说", "此外",
               "另外", "不仅如此", "值得注意的是", "需要指出的是", "不容忽视的是",
               "具有重要意义", "具有重要作用", "发挥重要作用", "产生深远影响",
               "因此", "由此可见", "与此同时", "进一步而言", "换言之", "基于此", "显然"]
_AI_CONN_EN = ["firstly", "secondly", "thirdly", "finally", "in conclusion", "to sum up",
               "furthermore", "moreover", "additionally", "it is worth noting",
               "it should be pointed out", "plays a crucial role", "has significant implications",
               "therefore", "thus", "consequently", "in this regard", "in other words"]
_WEAK_ZH = ["具有重要意义", "具有重要价值", "提供理论基础", "提供参考", "奠定基础", "值得关注",
            "不容忽视", "越来越受到关注", "具有广阔前景", "有待进一步研究"]
_WEAK_EN = ["plays an important role", "is of great significance", "provides a theoretical basis",
            "provides reference", "lays a foundation", "deserves attention", "cannot be ignored",
            "has broad prospects", "further research is needed"]


def _sentence_lengths(text):
    parts = re.split(r"[。！？!?；;.\n]+", text)
    return [len(s.strip()) for s in parts if 5 < len(s.strip()) < 300]


def _paragraphs_of(text, min_len=20):
    return [re.sub(r"\s+", " ", p.strip()) for p in re.split(r"\n\s*\n+", text) if len(re.sub(r"\s+", " ", p.strip())) > min_len]


def _stat_stddev(vals):
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    return (sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5


def _similarity_ratio(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a[:300], b[:300]).ratio()


def humanize_check(md_text, lang="zh"):
    """AI 味/人性化多维检测（D1-D5 阈值版，移植自 PaperSpine humanize_check 有害子集）。

    D1 句长节奏 / D2 相邻段相似 + 重复开头 / D3 信息密度 / D4 连接词密度 / D5 长破折号。
    返回 {ok, findings:[{severity,type,msg}]}。
    """
    text = md_text or ""
    # 只审计正文, 排除 References 节及其后的图注/表注区块
    text = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", text, maxsplit=1, flags=re.I)[0]
    findings = []
    conn = _AI_CONN_ZH if lang == "zh" else _AI_CONN_EN
    weak = _WEAK_ZH if lang == "zh" else _WEAK_EN

    # D1 句长标准差 / CV
    lens = _sentence_lengths(text)
    if len(lens) > 2:
        std = _stat_stddev(lens)
        mean = sum(lens) / len(lens)
        cv = std / mean if mean else 0.0
        if std < 6:
            findings.append({"severity": "P2", "type": "ai_sentence_rhythm",
                             "msg": f"D1 句长标准差 {std:.1f} < 6——句子长度过于整齐（AI 典型特征），请穿插短句与长句。"})
        if len(lens) >= 3 and 0 < cv < 0.25:
            findings.append({"severity": "P2", "type": "ai_sentence_rhythm",
                             "msg": f"D1 句长变异系数 CV={cv:.2f} < 0.25——节奏极均匀，需变化。"})
        elif len(lens) >= 3 and cv < 0.35:
            findings.append({"severity": "P2", "type": "ai_sentence_rhythm",
                             "msg": f"D1 句长变异系数 CV={cv:.2f} < 0.35，节奏略均匀，建议混入长短句。"})

    # D2 相邻段落相似度 + 重复开头
    paras = [p for p in _paragraphs_of(text)
             if not re.search(r"\[(?:Insert|在此插入|TBD|DATA REQUIRED)|插入图|插入表", p, re.I)]
    if len(paras) >= 2:
        sims = [_similarity_ratio(paras[i], paras[i + 1]) for i in range(len(paras) - 1)]
        if sims and max(sims) > 0.65:
            findings.append({"severity": "P2", "type": "ai_paragraph_sim",
                             "msg": f"D2 存在相邻段落高度相似(最大相似 {max(sims):.2f} > 0.65)——疑似复制改写不足。"})
        elif sims and sum(sims) / len(sims) > 0.45:
            findings.append({"severity": "P2", "type": "ai_paragraph_sim",
                             "msg": "D2 相邻段落平均相似度偏高，建议重写重复表述。"})
    starts = []
    generic_start = ("本文", "本研究", "此外", "因此", "首先", "其次") if lang == "zh" else ("this study", "this review", "furthermore", "therefore", "firstly", "secondly")
    for p in paras:
        s = re.sub(r"^[\s\"'（(【\[]+", "", p)[:8 if lang == "zh" else 12].lower()
        starts.append(s)
    if starts:
        rep = sum(1 for s in starts if s and starts.count(s) > 1)
        if rep / len(starts) > 0.35:
            findings.append({"severity": "P2", "type": "ai_repeat_start", "msg": f"D2 句子/段落开头重复率 {rep/len(starts):.0%} 偏高。"})

    # D3 连接词密度 + 泛词
    low = text.lower()
    conn_count = sum(low.count(c.lower()) for c in conn)
    conn_density = conn_count / max(len(text) / 1000, 0.001)
    if conn_density > 8:
        findings.append({"severity": "P2", "type": "ai_connector",
                         "msg": f"D4 连接词密度 {conn_density:.1f}/千字 > 8——『首先/此外/综上所述』等堆砌是强 AI 信号，优先用内容衔接。"})
    elif conn_density > 4.8:
        findings.append({"severity": "P2", "type": "ai_connector",
                         "msg": f"D4 连接词密度 {conn_density:.1f}/千字 偏高(阈值 4.8)，减少模板化过渡。"})
    weak_count = sum(low.count(w.lower()) for w in weak)
    if weak_count and weak_count / max(len(text) / 1000, 0.001) > 4:
        findings.append({"severity": "P2", "type": "ai_generic",
                         "msg": f"D3 空洞泛词(具有重要意义/提供理论基础等)密度 {weak_count/max(len(text)/1000,0.001):.1f}/千字 偏高，改具体表述。"})

    # D5 长破折号分隔符(忽略长度 ≥20 的纯破折号行——pandoc 表格分隔线)
    if re.search(r"(?m)^\s*(?:[-—–―]\s*){3,}$", text) and not re.search(r"(?m)^\s*(?:[-—–―]\s*){20,}$", text):
        findings.append({"severity": "P2", "type": "ai_dash", "msg": "检测到长破折号分隔行(---/———)——强 AI 信号，改用小标题或空行。"})

    # D3 信息锚点不足：连续两段无数字/图表/机制词
    anchor_re = re.compile(r"\d+(?:\.\d+)?\s*[%±]?|figure\s*\d|fig\.?\s*\d|图\s*\d|表\s*\d|p\s*[<>=]")
    anchorless = [i for i, p in enumerate(paras, 1) if not anchor_re.search(p) and not re.search(r"机制|通路|受体|蛋白|基因|表达|调控|mechanism|pathway|receptor", p, re.I)]
    if len(paras) > 1 and len(anchorless) / len(paras) > 0.5:
        findings.append({"severity": "P2", "type": "ai_low_anchor",
                         "msg": f"D3 信息锚点稀少：{len(anchorless)}/{len(paras)} 段无数字/图表/机制词。段落要有可核查的信息锚点。"})

    return {"ok": not findings, "findings": findings}


def section_economy_check(project_dir):
    """章节经济性（PaperSpine section_economy 对齐）：顶层级章节 ≤6，薄章节(<120 单元)并入邻节。"""
    project = Path(project_dir)
    path = project / "manuscript" / "main.md"
    if not path.exists():
        return [{"severity": "P1", "type": "economy_missing", "msg": "缺 manuscript/main.md，无法做章节经济性检查。"}]
    text = path.read_text(encoding="utf-8", errors="replace")

    def _unit(s):
        s = re.sub(r"[#|`*_\[\]()]", " ", s)
        cjk = len(re.findall(r"[一-鿿]", s))
        words = len(re.findall(r"[A-Za-z]+", s))
        return cjk + words

    sections = []
    cur = None
    # 非正文章节的标题前缀:front/back matter、图注表注区块、致谢基金声明等,不参与章节经济性统计
    _SKIP_SECTIONS = re.compile(
        r"^(abstract|摘要|title|references|参考文献|data availability|数据可用性|keywords|关键词|"
        r"statement of significance|significance statement|tables?|figures?|"
        r"table \d|figure \d|图\d|表\d|acknowledg?ements|funding|conflict|declarations?|"
        r"supplementary|abbreviations|caption|图注|表注)",
        re.I,
    )
    sections = []
    cur = None
    first_heading = True
    # 正文章节名的已知集合(编号前缀或标准章节名)
    _KNOWN_SECTIONS = re.compile(
        r"^\d+[.．、]|^(introduction|background|materials|methods|results|discussion|conclusions?|summary|abstract|keywords|references)",
        re.I,
    )
    for raw in text.splitlines():
        # 只统计 H2 顶层级章节，排除 H1 文档标题与 H3+ 小节
        m = re.match(r"^#{2}\s+(.*\S)\s*$", raw)
        if m:
            title = m.group(1).strip()
            # 首个非标准命名的 H2 视为文档大标题(如投稿稿件的论文题目),跳过
            if first_heading and not _KNOWN_SECTIONS.match(title):
                first_heading = False
                continue
            first_heading = False
            # 跳过 front/back matter 与 pandoc 表格头误转的标题(含 ** 加粗标记)
            if not _SKIP_SECTIONS.match(title) and "**" not in title:
                sections.append([title, []])
            continue
        if sections:
            sections[-1][1].append(raw)

    issues = []
    titles = [s[0] for s in sections]
    # 综述/毕业论文章节天然多(主题章节/学位论文章节),上限放宽
    st_path = project / "state.json"
    ptype = "article"
    if st_path.exists():
        try:
            import json as _json
            ptype = _json.loads(st_path.read_text(encoding="utf-8")).get("type", "article")
        except Exception:
            pass
    max_sections = 8 if ptype in ("review", "thesis") else 6
    if len(titles) > max_sections:
        issues.append({"severity": "P1", "type": "economy_sections",
                       "msg": f"顶层级章节 {len(titles)} 个 > {max_sections} 上限：{'、'.join(titles[:10])}。请把薄/重叠节并入邻节(如 Experimental Setup 并入 Results 开头)。"})
    for i, (title, body) in enumerate(sections):
        if i < len(sections) - 1 and _unit(" ".join(body)) < 120:
            issues.append({"severity": "P2", "type": "economy_stub",
                           "msg": f"章节『{title}』过薄(约 {_unit(' '.join(body))} 内容单元,约1-2段)——建议并入相邻章节。"})
    return issues


def data_gate(project_dir):
    """数据门禁(E2):进入 review 前必须满足——data/SOURCES.md 存在且非空;
    data-requirements.md 的核对状态非全空。返回问题列表(空=通过)。
    对应: 阶段二 H3 + 3.5 提示词 R1-R4 + 审计 P2(合成数据红线)。"""
    issues = []
    project = Path(project_dir)
    src = project / "data" / "SOURCES.md"
    req = project / "framework" / "data-requirements.md"
    data_dir = project / "data"
    has_data = data_dir.exists() and any(p.is_file() for p in data_dir.iterdir())
    if not src.exists():
        issues.append({"severity": "P0", "type": "sources_missing",
                       "msg": "缺 data/SOURCES.md —— 每个数据文件须标注来源(真实实验/用户提供/合成演示/推断)与采集时间。Results 出现数值前必须先建立数据账本。"})
    else:
        txt = src.read_text(encoding="utf-8", errors="replace").strip()
        if len(txt) < 20:
            issues.append({"severity": "P0", "type": "sources_empty",
                           "msg": "data/SOURCES.md 为空/过短，未记录任何数据文件来源"})
    if has_data and not src.exists():
        issues.append({"severity": "P0", "type": "sources_missing_with_data",
                       "msg": f"data/ 下存在 {len([p for p in data_dir.iterdir() if p.is_file()])} 个文件但没有 SOURCES.md —— 有数据无来源账本，禁止入 Results"})
    if req.exists():
        req_txt = req.read_text(encoding="utf-8", errors="replace")
        if "⬜" in req_txt and "已提供" not in req_txt and "不适用" not in req_txt and "✅" not in req_txt and "✓" not in req_txt:
            issues.append({"severity": "P1", "type": "req_unreconciled",
                           "msg": "data-requirements.md 全部未核对（无 已提供/不适用/✅ 标记），请与 data/SOURCES.md 逐项核对"})
    else:
        issues.append({"severity": "P1", "type": "req_missing",
                       "msg": "缺 framework/data-requirements.md（数据需求核对基准）"})
    return issues


def refs_ledger(project_dir, write=True):
    """E3 引用账本:把 references.md 全量核验结果写成 evidence/refs.jsonl,
    每条含 逐字段结果 + verified_at + input_hash。返回 (entries, summary, hash)。
    对应: 3.7 C1-C3 + 审计 P3(核验不可审计/变更即过期)。"""
    import hashlib
    import datetime
    import json as _json
    from collections import Counter
    project = Path(project_dir)
    ref_file = project / "framework" / "references.md"
    if not ref_file.exists():
        return [], {"error": "references.md missing"}, None
    raw = ref_file.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"```bibtex\s*(.*?)```", raw, re.S)
    bib = m.group(1) if m else raw
    h = hashlib.sha256(bib.encode("utf-8")).hexdigest()
    res = verify_bibtex(bib)
    ev = project / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    entries = []
    for r in res:
        entries.append({
            "id": r.get("id"), "doi": r.get("doi"), "status": r.get("status"),
            "fields": {"bib_title": r.get("title"), "bib_year": r.get("year"),
                       "cr_title": r.get("crossref_title"), "cr_year": r.get("crossref_year")},
            "note": r.get("note", ""), "verified_at": now, "input_hash": h,
        })
    if write:
        with open(ev / "refs.jsonl", "w", encoding="utf-8") as f:
            for e in entries:
                f.write(_json.dumps(e, ensure_ascii=False) + "\n")
    summary = dict(Counter(e["status"] for e in entries))
    return entries, summary, h


def _record_quality_history(project_dir, issues):
    """质量收敛历史：显式质检时追加一条打分记录到 review/quality-history.json（保留最近 50 条）。

    供 Web 工作台绘制"收敛到可投"趋势曲线；被动读取（dashboard）不记录，避免刷页污染。
    """
    try:
        from datetime import datetime
        hist_file = Path(project_dir) / "review" / "quality-history.json"
        hist_file.parent.mkdir(parents=True, exist_ok=True)
        hist = []
        if hist_file.exists():
            try:
                loaded = json.loads(hist_file.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    hist = loaded
            except Exception:
                hist = []
        sc = quality_score(issues)
        hist.append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "score": sc.get("score"), "level": sc.get("level"),
            "p0": sc.get("p0"), "p1": sc.get("p1"), "p2": sc.get("p2"),
        })
        hist_file.write_text(json.dumps(hist[-50:], ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass  # 历史记录失败不影响门禁主流程


def quality_check(project_dir, record=False):
    """质量门禁：检查稿件是否达到“可投/小修”级别。按文章类型适配(综述/毕业论文)。

    record=True 时把本次打分追加进 review/quality-history.json（CLI/显式质检用）。
    """
    import re
    import json as _json
    project = Path(project_dir)
    issues = []
    # 文章类型感知: review 综述(无 Results/图表要求低), thesis 毕业论文(中文结构/章节多)
    ptype = "article"
    st_path = project / "state.json"
    if st_path.exists():
        try:
            ptype = _json.loads(st_path.read_text(encoding="utf-8")).get("type", "article")
        except Exception:
            pass
    manuscript = project / "manuscript" / "main.md"
    if not manuscript.exists():
        return [{"severity": "P0", "type": "missing_manuscript", "msg": "缺少 manuscript/main.md"}]
    text = manuscript.read_text(encoding="utf-8", errors="replace")
    # E6: 协议/研究方案稿(TBD 占位且定位为 study design/protocol)跳过叙述与章节噪音
    is_protocol = (len(re.findall(r"\[TBD[^\]]*\]", text)) >= 3
                   and ("study design" in text.lower() or "protocol" in text.lower()))
    is_letter = ptype == "letter"

    # 0. 期刊要求就绪性：chosen.md 未填写则格式无法校验（P1 阻断）
    chosen = project / "journal" / "chosen.md"
    chosen_txt = chosen.read_text(encoding="utf-8", errors="replace") if chosen.exists() else ""
    if (not chosen_txt.strip()) or chosen_txt.count("___") >= 3:
        issues.append({"severity": "P1", "type": "journal_requirements_missing",
                       "msg": "journal/chosen.md 未填写目标期刊要求（篇幅/结构/参考文献格式/图表规范），格式无法校验——请先在 journal 阶段做实"})

    # 1. 占位符 (正则匹配: [TBD], [TBD:...], [DATA REQUIRED], ___ , <!-- INSERT-FIG/TAB -->)
    _ph_patterns_qc = [
        (re.compile(r'\[TBD[^\]]*\]'), "P0", "[TBD]"),
        (re.compile(r'\[DATA REQUIRED\]'), "P0", "[DATA REQUIRED]"),
        (re.compile(r'<!--\s*/?INSERT-(?:FIG|TAB)\s*-->'), "P0", "<!-- INSERT-FIG/TAB -->"),
        (re.compile(r'___'), "P1", "___"),
        # 2026-08-22 补充：HS 项目实战漏检的两类占位符
        (re.compile(r'\[\s*Insert\s+(?:Figure|Table)\s+\d+[^]]*\]', re.I), "P1", "[Insert Figure/Table n ...]"),
        (re.compile(r'\[[^][]*(?:待补充|待填写|to be added|to be provided|作者信息)[^][]*\]', re.I), "P1", "[作者信息/内容待补充]"),
    ]
    for pat, sev, label in _ph_patterns_qc:
        matches = pat.findall(text)
        if matches:
            issues.append({"severity": sev, "type": "placeholder", "msg": f"存在 {len(matches)} 处 {label} 占位符", "count": len(matches)})

    # 2. 数据可用性(综述/毕业论文不强制英文 Data Availability)
    if ptype in ("review", "thesis"):
        if "数据可用性" not in text and "data availability" not in text.lower() and "data and materials availability" not in text.lower():
            issues.append({"severity": "P2", "type": "data_availability", "msg": "缺少数据可用性声明（数据可用性/Data Availability）——综述/毕业论文如无原始数据可写「不适用」并删除本提示"})
    elif "Data Availability" not in text:
        issues.append({"severity": "P1", "type": "data_availability", "msg": "缺少 Data Availability 章节"})
    elif "available from the corresponding author" in text.lower() and "repository" not in text.lower():
        issues.append({"severity": "P1", "type": "data_availability", "msg": "Data Availability 仍是“按需索取”，建议改为公共仓库"})

    # 3. 强声称人工复核（动词校准：强证据才用强词）——仅检查正文，排除 References/图注等列表区块
    body_for_overclaim = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", text, maxsplit=1, flags=re.I)[0]
    # 只保留真正的强声称词; 剔除高频误报词(robust/always/significantly/comprehensive/optimal 在科学写作中是常规用法)
    overclaims = ["first", "novel", "state-of-the-art", "groundbreaking", "revolutionary",
                  "unprecedented", "unique", "首个", "首次", "首创", "前所未有的"]
    # 否定语境 = 谨慎表达("not always"/"no robust"), 不构成强声称
    NEG_CTX = re.compile(r"\b(?:not|no|never|without|hardly|rarely|seldom|non-|non|neither|nor|lack(?:s|ing)? of|absence of|而不是|并非|并非|不是|并非|无|未|避免|不能|缺乏)\b", re.I)
    # 作者主体: 英文强词若前文无作者主体(we/our/this study), 多半是描述他人研究或方法论讨论, 降级为弱提示
    AUTH_CTX = re.compile(r"\b(?:we|our|this study|the present study|本文|我们|本研究|该研究)\b", re.I)
    for w in overclaims:
        for m in re.finditer(r"\b" + w + r"\b", body_for_overclaim, re.I):
            if NEG_CTX.search(body_for_overclaim[max(0, m.start() - 120):m.end() + 40]):
                continue  # 否定语境跳过
            prefix = body_for_overclaim[max(0, m.start() - 160):m.start()]
            if w not in ("首个", "首次", "首创", "前所未有的") and not AUTH_CTX.search(prefix):
                issues.append({"severity": "P2", "type": "overclaim",
                               "msg": f"出现强声称词 “{w}”（若为描述他人研究或方法论讨论可忽略），请核对是否为本研究声称"})
            else:
                issues.append({"severity": "P2", "type": "overclaim",
                               "msg": f"出现强声称词 “{w}”，请人工核对是否有数据支撑"})
            break  # 每词报一条即可

    # 4. 统计报告
    for it in audit_stats(text):
        if it.get("type") != "ok":
            issues.append({"severity": "P1", "type": "stats", "msg": it["msg"], "line": it.get("line")})

    # 5. 图表自明(接受 Figure Legends / Figure Captions / 图注 章节,
    #    或内嵌图注写法 ![Figure 1. ...](...) —— 2026-08-22 扩展)
    has_legend_section = re.search(r"Figure Legends|Figure Captions|图注", text, re.I)
    has_inline_caption = re.search(r"!\[\s*Figure\s*\d+[.．]", text)
    if re.search(r"Figure \d", text) and not (has_legend_section or has_inline_caption):
        issues.append({"severity": "P2", "type": "figure_legend", "msg": "正文引用了 Figure 但缺少 Figure Legends/Captions 章节或内嵌图注"})

    # 5.1 图表规划数量(综述 3图2表即可;论文/毕业论文至少 5 图 3 表)
    min_figs, min_tabs = (3, 2) if ptype == "review" else (5, 3)
    fig_plan = project / "framework" / "figures.md"
    if fig_plan.exists():
        plan_text = fig_plan.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
        n_figs = len(re.findall(r"^\|\s*图\d+", plan_text, re.M))
        n_tabs = len(re.findall(r"^\|\s*表\d+", plan_text, re.M))
        if n_figs < min_figs:
            issues.append({"severity": "P1", "type": "figures_plan", "msg": f"图表规划不足：至少 {min_figs} 张图，当前 {n_figs} 张"})
        if n_tabs < min_tabs:
            issues.append({"severity": "P1", "type": "tables_plan", "msg": f"图表规划不足：至少 {min_tabs} 张表，当前 {n_tabs} 张"})
    else:
        issues.append({"severity": "P1", "type": "figures_plan", "msg": f"缺少 framework/figures.md 图表规划，建议至少规划 {min_figs} 图 {min_tabs} 表"})

    # 5.2 有数据但未插入自动图表
    data_dir = project / "data"
    has_data = data_dir.exists() and any(p.suffix.lower() in (".csv", ".xlsx") for p in data_dir.rglob("*"))
    if has_data and not re.search(r"!\[(?:图|Figure)", text):
        issues.append({"severity": "P1", "type": "figures_insert", "msg": "data/ 已有数据但正文未插入自动图表，请运行 python data2paper.py fill"})

    # 5.3 参考文献数量与近五年比例（期刊通常有明确要求，常见 80-120 篇）
    import datetime
    current_year = datetime.datetime.now().year
    min_refs = 80
    intake_path = project / "intake.md"
    if intake_path.exists():
        try:
            intake_text = intake_path.read_text(encoding="utf-8", errors="replace")
            # 仅当用户在冒号后实际填写了数字区间才覆盖默认值。
            # 模板示例行「参考文献数量要求（如 40-80 条）：」冒号后无数字，
            # 不会被命中，避免示例值把默认 80 条防线悄悄降级为 40。
            m = re.search(r"参考文献数量[^\n]{0,40}?[：:]\s*(\d+)\s*[-~—到至]\s*(\d+)", intake_text)
            if m:
                min_refs = int(m.group(1))
            else:
                m = re.search(r"参考文献数量[^\n]{0,40}?[：:]\s*(\d+)", intake_text)
                if m:
                    min_refs = int(m.group(1))
        except Exception:
            pass
    ref_file = project / "framework" / "references.md"
    ref_entries = []
    if ref_file.exists():
        ref_text = ref_file.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"```bibtex\s*(.*?)```", ref_text, re.S)
        bib = m.group(1) if m else ref_text
        try:
            parsed = parse_bibtex(bib)
            if parsed and "error" not in parsed[0]:
                ref_entries = parsed
        except Exception:
            ref_entries = []
        n_refs = len(ref_entries)
        if n_refs == 0:
            issues.append({"severity": "P1", "type": "references_count", "msg": "framework/references.md 中没有可解析的 BibTeX 文献"})
        else:
            # 时效性只按同行评议条目计算(2026-08-23): 专利/书籍等历史或法规证据
            # (如负性声明核验所引的中国专利)不承担文献时效性预期, 纳入分母会把
            # 评审要求的证据性文档误判为"文献陈旧"(HS 修订实证: 40/100→38%)
            articles = [e for e in ref_entries
                        if str(e.get("type", "article")).lower() in ("article", "journal", "inproceedings")]
            if not articles:
                articles = ref_entries
            recent = sum(1 for e in articles if str(e.get("year", "")).isdigit() and int(e["year"]) >= current_year - 4)
            ratio = recent / len(articles)
            if n_refs < min_refs:
                issues.append({"severity": "P1", "type": "references_count", "msg": f"参考文献数量偏少：当前 {n_refs} 条，建议至少 {min_refs} 条"})
            if ratio < 0.4 and not is_letter:
                issues.append({"severity": "P1", "type": "references_recency", "msg": f"近 5 年文献占比 {ratio:.0%}，建议至少 40%"})
    else:
        issues.append({"severity": "P1", "type": "references_count", "msg": "缺少 framework/references.md 参考文献清单"})

    # 6. 引用编号与文献一致性
    for it in citation_check(text, ref_entries):
        issues.append(it)

    # 7. 叙事结构（讲好一个故事）;综述无 Results/自己的实验结果,跳过结果类与 Discussion 回扣类检查
    narr_skip = set()
    if ptype == "review":
        # narrative_figorder 与 wb.py review-auto 保持一致不再跳过（综述无 Results，该检查天然不触发）
        narr_skip = {"narrative_results", "narrative_voice", "narrative_discussion"}
    if is_protocol:
        narr_skip |= {"narrative_abstract", "narrative_intro", "narrative_intro_open"}
    for it in narrative_check(text):
        if it.get("type") != "ok" and it.get("type") not in narr_skip:
            issues.append({"severity": it["severity"], "type": it["type"], "msg": it["msg"], "line": it.get("line")})

    # 8. 第四面墙：写作过程语言泄漏（内部规划/修改痕迹不得进入正文）
    process_words = ["针对审稿", "审稿意见", "导师反馈", "导师意见", "重新组织", "reorganized", "restructured",
                     "previous draft", "earlier draft", "according to the reviewer", "addressing the reviewer",
                     "按意见修改", "根据审稿", "initial draft", "first draft"]
    low_text = text.lower()
    for w in process_words:
        if w.lower() in low_text:
            issues.append({"severity": "P1", "type": "process_leak",
                           "msg": f"正文出现写作过程语言「{w}」——写作理由矩阵/修改痕迹是内部规划，不得进入正文（第四面墙规则）"})

    # 9. 贡献门禁（PaperSpine 关键：无 confirmed_contribution 不写正文）
    for it in contribution_check(project_dir):
        issues.append(it)

    # 10. Results 承诺-证据映射（每个 Results 小节验证一个贡献承诺）
    for it in results_validation_check(project_dir):
        issues.append(it)

    # 11. 章节经济性（顶层级章节 ≤6，薄节并入邻节）
    for it in section_economy_check(project_dir):
        issues.append(it)

    # 11.5 投稿要素 preflight（D3：Keywords/作者占位符/Data Availability）
    try:
        for it in submission_preflight_check(project_dir):
            issues.append(it)
    except Exception:
        pass  # 单项检查异常不影响其他项

    # 12. AI 味/人性化多维检测（D1-D5 阈值版）
    lung = "zh" if ("中文" in text or re.search(r"[一-鿿]", text)) else "en"
    if re.search(r"[一-鿿]", text):
        lung = "zh"
    human = humanize_check(text, lung)
    for f in human["findings"]:
        issues.append(f)

    # 14. 学术规范审查（E31–E45，移植自 review-gap-report.md）
    try:
        for it in academic_norm_check(text, ref_entries):
            issues.append(it)
    except Exception:
        pass  # 学术规范检查失败不阻塞主流程

    # 13. 统一附加定位信息(file + line), 供 Web 质量诊断直接跳转/标注
    # 13. 统一附加定位信息(file + line), 供 Web 质量诊断直接跳转/标注
    _file_of = {
        "missing_manuscript": "manuscript/main.md", "placeholder": "manuscript/main.md",
        "data_availability": "manuscript/main.md", "overclaim": "manuscript/main.md",
        "stats": "manuscript/main.md", "figure_legend": "manuscript/main.md",
        "figures_insert": "manuscript/main.md", "citations_missing": "manuscript/main.md",
        "citations_out_of_range": "manuscript/main.md", "citations_uncited": "manuscript/main.md",
        "references_count": "framework/references.md", "references_recency": "framework/references.md",
        "figures_plan": "framework/figures.md", "tables_plan": "framework/figures.md",
        "contribution_missing": "framework/contribution.md", "contribution_field": "framework/contribution.md",
        "contribution_soften": "framework/contribution.md", "results_validation_missing": "framework/results-validation.md",
        "results_validation_col": "framework/results-validation.md", "results_validation_hard": "framework/results-validation.md",
        "results_validation_gap": "framework/results-validation.md",
    }
    for it in issues:
        it.setdefault("file", _file_of.get(it.get("type"), "manuscript/main.md"))
    _attach_loc(project_dir, issues)
    if record:
        _record_quality_history(project, issues)
    return issues


def _attach_loc(project_dir, issues):
    """为缺失行号的问题补充 line 定位(在对应产物文件里找关键词所在行)。"""
    project = Path(project_dir)

    def _find(file_rel, needle, offset=0):
        p = project / file_rel
        if not p.exists() or not needle:
            return None
        for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if needle in ln:
                return max(1, i + offset)
        return None

    def _ref_line(file_rel):
        return _find(file_rel, "References") or _find(file_rel, "参考文献")

    for it in issues:
        if it.get("line"):
            continue
        t, f = it.get("type", ""), it.get("file", "manuscript/main.md")
        msg = it.get("msg", "")
        if t == "placeholder":
            it["line"] = _find(f, "___") or _find(f, "[TBD]") or _find(f, "[DATA REQUIRED]")
        elif t == "overclaim":
            m = re.search(r"[“\"]([^”\"]+)[”\"]", msg)
            if m:
                it["line"] = _find(f, m.group(1))
        elif t in ("narrative_abstract",):
            it["line"] = _find(f, "Abstract") or _find(f, "摘要") or 1
        elif t == "narrative_intro":
            it["line"] = _find(f, "Introduction") or _find(f, "引言")
        elif t == "narrative_results":
            it["line"] = _find(f, "Results") or _find(f, "结果")
        elif t in ("narrative_discussion", "narrative_voice"):
            it["line"] = _find(f, "Discussion") or _find(f, "讨论")
        elif t in ("narrative_conclusion",):
            it["line"] = _find(f, "Conclusion") or _find(f, "结论")
        elif t == "economy_stub":
            m = re.search(r"『([^』]+)』", msg)
            if m:
                it["line"] = _find(f, m.group(1))
        elif t in ("citations_uncited", "citations_out_of_range", "citations_missing"):
            it["line"] = _ref_line(f)
        elif t in ("figure_legend", "data_availability"):
            ln = _ref_line(f)
            it["line"] = (ln - 2) if ln else None
        elif t == "contribution_soften":
            it["line"] = _find(f, "缺失证据")
        elif t in ("vague_compare", "claim_no_evidence"):
            m = re.search(r"「([^」]+)」", msg)
            if m:
                it["line"] = _find(f, m.group(1))
        elif t in ("missing_test", "missing_sd"):
            pass  # audit_stats 已带 line, 这里兜底无关键词可找
        elif t == "process_leak":
            m = re.search(r"「([^」]+)」", msg)
            if m:
                it["line"] = _find(f, m.group(1))
    return issues


def originality_check(text, corpus_dir):
    """与本地语料做简单 n-gram 相似度检查（用于自查重复/自我抄袭）。"""
    import re
    from collections import Counter
    def shingles(t, n=10):
        words = re.findall(r"[a-zA-Z0-9\u4e00-\u9fff]+", t.lower())
        return set(" ".join(words[i:i+n]) for i in range(len(words) - n + 1))
    target = shingles(text)
    results = []
    corpus = Path(corpus_dir) if corpus_dir else None
    if corpus is None or not corpus.exists():
        return results
    if corpus.exists():
        for f in corpus.rglob("*.md"):
            if f.stat().st_size > 5_000_000:
                continue
            try:
                src = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            src_sh = shingles(src)
            if not target or not src_sh:
                continue
            overlap = len(target & src_sh) / len(target)
            if overlap > 0.05:
                results.append({"file": str(f), "overlap": round(overlap, 4), "shared_shingles": len(target & src_sh)})
    results.sort(key=lambda x: x["overlap"], reverse=True)
    return results[:20]


def _rewrite_image_paths(md_file):
    """把正文图片相对路径改为相对 md 文件所在目录的可解析路径。

    正文中的图片常写成「data/charts/x.png」(相对项目根)，而 pandoc
    以 md 文件所在目录(如 manuscript/)为基准解析，导致导出时图片
    被丢弃。这里在项目目录内查找真实文件，重写为绝对正斜杠路径
    （无 scheme 前缀、中文原样）。三个已实测的坑：
    1) 禁用 Path.as_uri()——百分号编码 pandoc 不解码；
    2) 禁用反斜杠 Windows 路径——点开头目录段如 .dsh 前的反斜杠
       会被 pandoc 吞掉致丢图；
    3) 禁用 file:/// URI——pandoc 3.9 对非 ASCII 路径段（如中文用户
       目录名）百分号编码后不解码，fetch 失败丢图（2026-08-21
       嵌图端到端实测：file:/// 丢图，绝对正斜杠路径嵌图成功）。
    返回 (实际使用的 md 路径, 是否生成临时文件)。
    """
    md_path = Path(md_file)
    base = md_path.parent
    candidates = [base, base.parent, base.parent.parent, base.parent.parent.parent]
    text = md_path.read_text(encoding="utf-8", errors="replace")

    def _fix(m):
        alt, rel = m.group(1), m.group(2)
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", rel.strip()):  # data:/http:/file: 等
            return m.group(0)
        target = None
        for cand in candidates:
            p = (cand / rel.strip()).resolve()
            if p.exists() and p.is_file():
                target = p
                break
        if target is None:
            return m.group(0)
        # 改写为绝对正斜杠路径（无 scheme）：中文原样保留。
        # 禁用 Path.as_uri()（百分号编码不解码）、反斜杠相对路径
        # （点开头目录段如 .dsh 前的反斜杠被 pandoc 吞掉致丢图）、
        # file:/// URI（pandoc 3.9 对非 ASCII 路径段百分号编码后不解码）。
        uri = str(target).replace("\\", "/")
        return "![{}]({})".format(alt, uri)

    new_text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _fix, text)
    if new_text == text:
        return str(md_path), False
    tmp_md = md_path.with_name(md_path.stem + "_export.md")
    tmp_md.write_text(new_text, encoding="utf-8")
    return str(tmp_md), True


def import_pdf(pdf_path, out=None, max_pages=None):
    """PDF → 规范化 Markdown(pdfplumber 直读: 文本 + 表格 + 双栏)。

    设计:
    - 每页按单词 x0 中位数分栏(双栏论文自动左右阅读序);
    - 表格用 pdfplumber extract_table 提取并转 GitHub md;
    - 过滤页眉/页脚(页码、期刊名、作者名等跨页重复行);
    - 行内连字符断词合并("pro-\\ntein" → "protein");
    - 标题启发式: 短行 + 数字编号 或 全部大写/首字母大写短语 → #/##/###。
    返回 (输出文件路径, 统计信息 dict)。
    """
    import pdfplumber
    pdf_path = str(Path(pdf_path).resolve())
    if out is None:
        out = str(Path(pdf_path).with_suffix(".md"))

    def extract_page(page):
        """返回 (文本行列表(含字号标记), 表格md列表)。行以 'SIZE:n|' 前缀标注字号, 供标题识别。"""
        tables_md = []
        try:
            for tb in page.extract_tables():
                rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in tb]
                rows = [r for r in rows if any(r)]
                if not rows:
                    continue
                ncols = max(len(r) for r in rows)
                rows = [r + [""] * (ncols - len(r)) for r in rows]
                head = rows[0]
                md = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * ncols) + "|"]
                for r in rows[1:]:
                    md.append("| " + " | ".join(r) + " |")
                tables_md.append("\n".join(md))
        except Exception:
            pass

        # 分栏: 按单词 x0 聚类, 若明显双峰则按列阅读
        words = page.extract_words(x_tolerance=1.5, y_tolerance=2.5)
        if not words:
            return [], tables_md
        xs = [w["x0"] for w in words]
        mid = (min(xs) + max(xs)) / 2
        left = [w for w in words if w["x0"] < mid]
        right = [w for w in words if w["x0"] >= mid]
        two_col = left and right and len(left) > 8 and len(right) > 8

        # 每行记录最大字号(用于标题识别)
        char_sizes = {}
        for ch in getattr(page, "chars", []):
            char_sizes.setdefault(round(ch["top"] / 3), []).append(ch.get("size", 0) or 0)

        def words_to_lines(wlist):
            lines = {}
            for w in wlist:
                key = round(w["top"] / 3)  # 3pt 容差聚行
                lines.setdefault(key, []).append(w)
            out_lines = []
            for k in sorted(lines):
                ws = sorted(lines[k], key=lambda w: w["x0"])
                line = " ".join(w["text"] for w in ws)
                line = re.sub(r"-\s+$", "", line)  # 行尾连字符合并
                sizes = char_sizes.get(k, [])
                sz = max(sizes) if sizes else 0
                out_lines.append(("SIZE:%.1f|" % sz) + line.strip())
            return out_lines

        lines = words_to_lines(left) + words_to_lines(right) if two_col else words_to_lines(words)
        return lines, tables_md

    # 页眉/页脚过滤: 出现 ≥2 页的相同短行视为页眉页脚
    all_pages = []
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        if max_pages:
            n = min(n, max_pages)
        for i in range(n):
            lines, tmd = extract_page(pdf.pages[i])
            all_pages.append(lines)
            # 表格紧跟其所在页文本之后
            all_pages[-1].extend(["__TABLE__" + t for t in tmd])

    from collections import Counter
    cnt = Counter()
    for lines in all_pages:
        for ln in lines:
            s = re.sub(r"^SIZE:[\d.]+\|", "", ln).strip()
            if 0 < len(s) <= 90 and not re.search(r"\d", s):
                cnt[s.lower()] += 1
    headers = {k for k, v in cnt.items() if v >= 2}

    # 正文基准字号: 众数(过滤页眉大字号)
    body_sizes = []
    for lines in all_pages:
        for ln in lines:
            m = re.match(r"^SIZE:([\d.]+)\|", ln)
            if m:
                body_sizes.append(float(m.group(1)))
    body_sizes = [s for s in body_sizes if s < 16]
    base_size = Counter(round(s, 1) for s in body_sizes).most_common(1)[0][0] if body_sizes else 10.0

    def clean_line(ln):
        return re.sub(r"^SIZE:[\d.]+\|", "", ln).strip()

    def is_heading(ln):
        s = clean_line(ln)
        if not s or len(s) > 90:
            return False
        # 排除引用/数值/括号行(如 "(2003)", "2003),", "0.7", "1 mL in 2 mL")
        if re.match(r"^\d+\)", s) or s.startswith("(") or re.match(r"^[\d.,\s%±]+$", s):
            return False
        if re.match(r"^\d+\s+\w+\s+\d", s):  # "1 mL in 2 mL tubes" 量纲行
            return False
        if re.search(r"EDITED BY|Edited by|REVIEWED BY|Reviewed by|SPECIALTY SECTION|ORIGINAL RESEARCH|ORIGINAL ARTICLE", s, re.I) and len(s.split()) <= 8:
            return False  # Frontiers 首页编辑部/类型标记
        m = re.match(r"^SIZE:([\d.]+)\|", ln)
        sz = float(m.group(1)) if m else 0
        if sz >= base_size + 3.0 and len(s.split()) <= 16:
            return True  # 明显大于正文 → 标题(含论文大标题)
        # 编号标题: 必须带点/顿号(1. 2.1. 3.2.1), 排除 "2003)" "1 mL" 等
        if re.match(r"^\d+(\.\d+)*[.．、]\s", s) and len(s.split()) <= 14:
            return True
        if re.match(r"^[A-Z][A-Z &\-]+$", s) and 3 <= len(s.split()) <= 10:
            return True
        if re.match(r"^(ABSTRACT|INTRODUCTION|METHODS|RESULTS|DISCUSSION|CONCLUSIONS?|REFERENCES|ACKNOWLEDGEMENTS?)$", s, re.I):
            return True
        return False

    def level_of(ln):
        s = clean_line(ln)
        m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", s)
        if m and m.group(1) and m.group(2) is not None:
            return min(1 + 1 + (1 if m.group(3) else 0), 4)
        if m and m.group(1):
            return 1
        sm = re.match(r"^SIZE:([\d.]+)\|", ln)
        sz = float(sm.group(1)) if sm else 0
        if sz >= base_size + 6:
            return 1
        if sz >= base_size + 3.5:
            return 2
        return 2  # 无编号章节名按二级

    out_lines = []
    for lines in all_pages:
        for ln in lines:
            s = clean_line(ln)
            if not s:
                continue
            if s.lower() in headers:
                continue
            if re.match(r"^\s*\d+\s*$", s):  # 页码
                continue
            if s.startswith("__TABLE__"):
                out_lines.append("")
                out_lines.append(s[len("__TABLE__"):])
                out_lines.append("")
                continue
            if is_heading(ln):
                out_lines.append("#" * level_of(ln) + " " + s)
            else:
                out_lines.append(s)

    # 相邻同级别标题合并: 大标题跨行断开时合并为一行(仅无编号标题)
    merged = []
    for l in out_lines:
        m = re.match(r"^(#{1,4})\s+(.*)$", l)
        if m and merged:
            pm = re.match(r"^(#{1,4})\s+(.*)$", merged[-1])
            if pm and pm.group(1) == m.group(1) and not re.match(r"^\d+(\.\d+)*[.．、]", m.group(2)) \
                    and not re.match(r"^\d+(\.\d+)*[.．、]", pm.group(2)):
                merged[-1] = pm.group(1) + " " + pm.group(2) + " " + m.group(2)
                continue
        merged.append(l)
    out_lines = merged
    md_text = "\n".join(out_lines)
    md_text = re.sub(r"\n{3,}", "\n\n", md_text).strip() + "\n"
    Path(out).write_text(md_text, encoding="utf-8")
    stats = {"chars": len(md_text), "pages": len(all_pages),
             "headings": sum(1 for l in out_lines if l.startswith("#")),
             "tables": sum(1 for l in out_lines if "|" in l and "---" in l)}
    return out, stats


def import_docx(docx_path, out=None, image_dir=None):
    """docx → 规范化 Markdown(供工作台导入稿件)。

    相比 pandoc 的差异与优势:
    - 零外部依赖(python-docx 直读),不需要安装 pandoc;
    - 无 pandoc 转义坑(引用编号 1\\.、表格头误转标题 ## 等);
    - 标题识别基于格式特征: Heading 样式 / 加粗 + 超大字号 / 居中 + 数字编号
      (真实稿件常全用 Normal 样式,靠格式区分标题);
    - 表格转 GitHub 风格 markdown 表格;图片提取到 image_dir 并引用。
    返回 (输出文件路径, 统计信息 dict)。
    """
    from docx import Document
    from docx.shared import Pt
    docx_path = str(Path(docx_path).resolve())
    doc = Document(docx_path)
    if out is None:
        out = str(Path(docx_path).with_suffix(".md"))
    if image_dir is None:
        image_dir = str(Path(docx_path).with_name(Path(docx_path).stem + "_images"))

    image_dir_p = Path(image_dir)
    image_dir_p.mkdir(parents=True, exist_ok=True)

    # 已知章节名(不分大小写; 含期刊常见 front/back matter)
    _KNOWN_HEADS = re.compile(
        r"^(abstract|summary|keywords|introduction|materials and methods|methods|results|"
        r"results and discussion|discussion|conclusions?|references|acknowledg?ements|funding|"
        r"conflict of interest|data availability|supplementary|附录|摘要|关键词|引言|前言|方法|"
        r"材料与方法|结果|讨论|结论|致谢|参考文献|数据可用性)", re.I)

    def para_is_heading(p):
        """格式特征识别标题:
        - Heading 样式 → 是;
        - 加粗 + (编号开头 或 已知章节名 或 居中) → 是(真实稿件常用纯加粗表达标题);
        - 加粗 + 超大字号(≥16pt) → 是(即使无编号)。
        正文里零散的加粗短语(如 *Italic* 标签、句内强调)不命中, 因为要求整段加粗。
        """
        if p.style and ("Heading" in p.style.name or "标题" in p.style.name):
            return True
        runs = [r for r in p.runs if r.text.strip()]
        if not runs:
            return False
        text = p.text.strip()
        if not text:
            return False
        bold = all(r.bold for r in runs)
        if not bold:
            return False
        sizes = [r.font.size.pt for r in runs if r.font.size]
        big = sizes and max(sizes) >= 16
        numbered = bool(re.match(r"^\d+(\.\d+)*[.．、)\s]", text))
        center = p.alignment is not None and str(p.alignment) == "CENTER (1)"
        known = bool(_KNOWN_HEADS.match(text))
        short = len(text) <= 110
        return (numbered and short) or known or center or big

    def heading_level(p):
        """标题级别: 编号深度优先(1.→2级, 1.1→3级, 1.1.1→4级), 否则按字号/样式。"""
        text = p.text.strip()
        m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", text)
        if m and m.group(1):
            depth = 1 + (1 if m.group(2) else 0) + (1 if m.group(3) else 0)
            return min(depth, 4)
        sizes = [r.font.size.pt for r in p.runs if r.font.size]
        sz = max(sizes) if sizes else 0
        if sz >= 20:
            return 1
        if sz >= 16:
            return 2
        if p.style and "Heading 1" in p.style.name:
            return 1
        if p.style and "Heading 2" in p.style.name:
            return 2
        return 2 if _KNOWN_HEADS.match(text) else 3

    def cell_text(cell):
        return " ".join(par.text.strip() for par in cell.paragraphs if par.text.strip()).strip()

    def table_to_md(table):
        rows = [[cell_text(c) for c in row.cells] for row in table.rows]
        if not rows:
            return ""
        ncols = max(len(r) for r in rows)
        rows = [r + [""] * (ncols - len(r)) for r in rows]
        head = rows[0]
        out_lines = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * ncols) + "|"]
        for r in rows[1:]:
            out_lines.append("| " + " | ".join(r) + " |")
        return "\n".join(out_lines)

    out_lines = []
    images_written = 0
    tables_written = 0
    # 文档正文段落与表格按出现顺序交错: 先收集 body 元素序列
    body = doc.element.body
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    def iter_block_items(parent):
        for child in parent.iterchildren():
            if child.tag.endswith("}p"):
                yield Paragraph(child, doc)
            elif child.tag.endswith("}tbl"):
                yield Table(child, doc)

    for block in iter_block_items(body):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text:
                out_lines.append("")
                continue
            if para_is_heading(block):
                lvl = heading_level(block)
                out_lines.append("#" * lvl + " " + text)
            else:
                out_lines.append(text)
        elif isinstance(block, Table):
            md = table_to_md(block)
            if md:
                out_lines.append(md)
                tables_written += 1
            out_lines.append("")

    # 图片提取(docx 内嵌图片 → image_dir)
    rels = doc.part.rels
    for rel_id, rel in rels.items():
        if "image" in rel.reltype:
            try:
                blob = rel.target_part.blob
                ext = rel.target_part.partname.split(".")[-1].lower() or "png"
                if ext not in ("png", "jpg", "jpeg", "gif", "bmp", "svg", "tif", "tiff"):
                    ext = "png"
                fname = f"img_{images_written + 1}.{ext}"
                (image_dir_p / fname).write_bytes(blob)
                images_written += 1
            except Exception:
                pass

    md_text = "\n".join(out_lines)
    md_text = re.sub(r"\n{3,}", "\n\n", md_text).strip() + "\n"
    Path(out).write_text(md_text, encoding="utf-8")
    stats = {"chars": len(md_text), "paragraphs": sum(1 for l in out_lines if l.strip() and not l.startswith("#") and "|" not in l),
             "headings": sum(1 for l in out_lines if l.startswith("#")), "tables": tables_written,
             "images": images_written, "image_dir": str(image_dir_p)}
    return out, stats


def export_markdown(md_file, fmt="latex", out=None, template=None, pdf_engine=None):
    """用 pandoc 导出 LaTeX / docx / pdf 等。

    template:
      - docx 时传 Word 参考模板（reference.docx），控制样式/字体/页边距；
      - latex/pdf 时传 LaTeX 模板文件，控制期刊版式。
    pdf_engine:
      默认自动探测 xelatex/pdflatex/lualatex；未安装时给出安装提示。
    """
    import pypandoc
    md_file, is_tmp = _rewrite_image_paths(md_file)
    md_file = str(Path(md_file).resolve())
    if out is None:
        suffix = ".tex" if fmt == "latex" else f".{fmt}"
        out = str(Path(md_file).with_suffix(suffix))
    extra = ["--standalone"]
    if template:
        if fmt == "docx":
            extra.append(f"--reference-doc={template}")
        else:
            extra.append(f"--template={template}")
    if fmt == "pdf":
        if not pdf_engine:
            import shutil
            for eng in ("xelatex", "pdflatex", "lualatex"):
                if shutil.which(eng):
                    pdf_engine = eng
                    break
        if not pdf_engine:
            raise RuntimeError(
                "未检测到 LaTeX 引擎（xelatex/pdflatex/lualatex）。"
                "请安装 TinyTeX 或 MiKTeX；或先用 --format docx 导出 Word，再在 Word 中另存为 PDF。"
            )
        extra.append(f"--pdf-engine={pdf_engine}")
        if pdf_engine == "xelatex":
            # 常见中文字体设置，可按期刊要求调整
            extra += ["-V", "CJKmainfont=Microsoft YaHei", "-V", "mainfont=Times New Roman"]
    try:
        pypandoc.convert_file(md_file, fmt, outputfile=out, extra_args=extra)
        return out
    finally:
        if is_tmp:
            try:
                Path(md_file).unlink()
            except Exception:
                pass


# ─────────────────────────── CLI ───────────────────────────

def main():
    ap = argparse.ArgumentParser(prog="toolbox", description="Paper Workbench Toolbox")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="文献检索")
    p.add_argument("query")
    p.add_argument("--sources", default="openalex,arxiv,crossref")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("fetch", help="按 DOI 获取元数据")
    p.add_argument("doi")
    p.set_defaults(fn=cmd_fetch)

    p = sub.add_parser("verify-bib", help="核验 BibTeX")
    p.add_argument("file")
    p.set_defaults(fn=cmd_verify_bib)

    p = sub.add_parser("used-refs", help="从正文 [n] 引用反推参考文献使用清单(收窄候选池)")
    p.add_argument("project", help="项目目录(含 manuscript/main.md 与 framework/references.md)")
    p.add_argument("--write", action="store_true", help="写入 manuscript/refs_used.md")
    p.set_defaults(fn=cmd_used_refs)

    p = sub.add_parser("stats", help="CSV 统计")
    p.add_argument("file")
    p.set_defaults(fn=cmd_stats)

    p = sub.add_parser("chart", help="生成图表")
    p.add_argument("file")
    p.add_argument("--type", default="bar", choices=["bar", "line", "scatter", "pie", "box", "violin", "hist", "area", "heatmap"])
    p.add_argument("--x", default=None)
    p.add_argument("--y", default=None)
    p.add_argument("--title", default="")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_chart)

    p = sub.add_parser("format-refs", help="格式化参考文献")
    p.add_argument("bibfile")
    p.add_argument("--style", default="springer-numeric", help="springer-numeric/ama/apa 或 CSL: crib/<name>.csl")
    p.set_defaults(fn=cmd_format_refs)

    p = sub.add_parser("lang-check", help="LanguageTool 语言质量检查(本地优先,无 Java 回退公共 API)")
    p.add_argument("file")
    p.add_argument("--lang", default="en-US")
    p.add_argument("--out", default=None, help="JSON 明细输出")
    p.add_argument("--max", type=int, default=40, help="报告明细条数上限")
    p.add_argument("--api-only", action="store_true", help="跳过本地,直接公共 API")
    p.set_defaults(fn=cmd_lang_check)

    p = sub.add_parser("ai-screen", help="AI 痕迹预筛查(统计红旗,非最终裁决)")
    p.add_argument("file")
    p.add_argument("--out", default=None, help="JSON 明细输出")
    p.set_defaults(fn=cmd_ai_screen)

    p = sub.add_parser("cn2bib", help="把 CNKI 导出的中文题录(GB/T 7714)解析为 BibTeX")
    p.add_argument("file")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_cn2bib)

    p = sub.add_parser("audit-stats", help="统计报告审计")
    p.add_argument("file")
    p.set_defaults(fn=cmd_audit_stats)

    p = sub.add_parser("originality", help="本地原创性/重复度检查")
    p.add_argument("file")
    p.add_argument("--corpus", default=None)
    p.set_defaults(fn=cmd_originality)

    p = sub.add_parser("build-refs", help="检索并生成/合并参考文献 BibTeX")
    p.add_argument("query")
    p.add_argument("--sources", default="openalex,arxiv,crossref,pubmed")
    p.add_argument("--limit", type=int, default=30, help="每个来源最多取多少条")
    p.add_argument("--min", type=int, default=80, help="目标最少文献数")
    p.add_argument("--out", default=None, help="输出 references.md 路径")
    p.add_argument("--append", default=None, help="已有 references.md 路径，合并去重")
    p.set_defaults(fn=cmd_build_refs)

    p = sub.add_parser("export", help="导出 LaTeX/Word/PDF")
    p.add_argument("file")
    p.add_argument("--format", default="latex", choices=["latex", "docx", "pdf", "html"])
    p.add_argument("--out", default=None)
    p.add_argument("--template", default=None, help="docx 用参考模板 reference.docx；latex/pdf 用期刊 LaTeX 模板")
    p.add_argument("--pdf-engine", default=None, choices=["xelatex", "pdflatex", "lualatex"], help="PDF 引擎（默认自动探测）")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("import-pdf", help="PDF → 规范化 Markdown(pdfplumber 直读,文本+表格+双栏)")
    p.add_argument("file")
    p.add_argument("--out", default=None)
    p.add_argument("--max-pages", type=int, default=None, help="最多读多少页")
    p.set_defaults(fn=cmd_import_pdf)

    p = sub.add_parser("import-docx", help="docx → 规范化 Markdown(python-docx 直读,零 pandoc 依赖)")
    p.add_argument("file")
    p.add_argument("--out", default=None)
    p.add_argument("--images", default=None, help="图片提取目录(默认 <docx 名>_images/)")
    p.set_defaults(fn=cmd_import_docx)

    p = sub.add_parser("quality-check", help="质量门禁检查")
    p.add_argument("project")
    p.set_defaults(fn=cmd_quality_check)

    p = sub.add_parser("data-gate", help="数据门禁检查(E2:SOURCES/核对)")
    p.add_argument("project")
    p.set_defaults(fn=cmd_data_gate)

    p = sub.add_parser("refs-ledger", help="引用账本(E3:生成 evidence/refs.jsonl)")
    p.add_argument("project")
    p.add_argument("--write", action="store_true", help="写入 evidence/refs.jsonl")
    p.set_defaults(fn=cmd_refs_ledger)

    p = sub.add_parser("contribution-check", help="贡献门禁检查(PaperSpine 对齐)")
    p.add_argument("project")
    p.set_defaults(fn=cmd_contribution_check)

    p = sub.add_parser("results-validation", help="Results 承诺-证据映射检查")
    p.add_argument("project")
    p.set_defaults(fn=cmd_results_validation)

    p = sub.add_parser("humanize", help="AI 味/人性化多维检测")
    p.add_argument("file")
    p.add_argument("--lang", default="zh", choices=["zh", "en"])
    p.set_defaults(fn=cmd_humanize)

    p = sub.add_parser("section-economy", help="章节经济性检查(顶层级≤6)")
    p.add_argument("project")
    p.set_defaults(fn=cmd_section_economy)

    p = sub.add_parser("mechanical", help="机械性检查(连标点/小写句首/超长句)")
    p.add_argument("file")
    p.set_defaults(fn=cmd_mechanical)

    p = sub.add_parser("mechanical-fix", help="机械性无损消毒(..→. ;;→; ,,→, 幂等)")
    p.add_argument("file")
    p.add_argument("--dry-run", action="store_true", help="只输出消毒后文本到 stdout，不写回")
    p.set_defaults(fn=cmd_mechanical_fix)

    args = ap.parse_args()
    args.fn(args)


def cmd_search(args):
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    res = search_literature(args.query, sources, args.limit)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    for i, r in enumerate(res, 1):
        print(f"{i}. [{r.get('source','?')}] {r.get('title','(无标题)')} ({r.get('year','?')})")
        if r.get("authors"):
            print(f"   作者: {', '.join(r['authors'][:5])}")
        if r.get("doi"):
            print(f"   DOI: {r['doi']}")
        if r.get("venue"):
            print(f"   出处: {r['venue']}")


def cmd_fetch(args):
    print(json.dumps(fetch_doi(args.doi), ensure_ascii=False, indent=2))


def cmd_build_refs(args):
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    existing = ""
    if args.append and Path(args.append).exists():
        existing = Path(args.append).read_text(encoding="utf-8", errors="replace")
    res = build_refs(args.query, sources, args.limit, args.min, out_file=args.out, existing_bib=existing)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_verify_bib(args):
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    print(json.dumps(verify_bibtex(text), ensure_ascii=False, indent=2))


def cmd_data_gate(args):
    print(json.dumps(data_gate(args.project), ensure_ascii=False, indent=2))


def cmd_refs_ledger(args):
    entries, summary, h = refs_ledger(args.project, args.write)
    print(json.dumps({"summary": summary, "hash": h, "count": len(entries),
                      "note": "已写入 evidence/refs.jsonl" if args.write else "未写入(--write 开启)"},
                     ensure_ascii=False, indent=2))


def cmd_used_refs(args):
    print(json.dumps(used_refs(args.project, args.write), ensure_ascii=False, indent=2))


def cmd_stats(args):
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    print(json.dumps(stats_csv(text), ensure_ascii=False, indent=2))


def cmd_chart(args):
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    res = make_chart(Path(args.file).name, text, args.type, args.x, args.y, args.title, args.out)
    print(json.dumps(res, ensure_ascii=False, indent=2))


def cmd_format_refs(args):
    style = args.style
    if style == "crib" or style.endswith(".csl") or style.startswith("csl:"):
        # CSL 渲染路径（citeproc-py，见 refs_pipeline.py）
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import refs_pipeline
        text = Path(args.bibfile).read_text(encoding="utf-8")
        bib = refs_pipeline.extract_bibtex(text)
        entries = refs_pipeline.parse_entries(bib)
        lines = refs_pipeline.format_refs_csl(bib, style.replace("csl:", ""), [e["id"] for e in entries])
        print("\n".join(lines))
        return
    print("\n".join(format_references(args.bibfile, args.style)))


def cmd_ai_screen(args):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ai_screen as _as
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    res = _as.ai_screen(text)
    print(_as.render_report(args.file, res))
    if args.out:
        Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"JSON 明细 → {args.out}")


def cmd_lang_check(args):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import lang_check as _lc
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    cleaned = _lc.strip_markdown(text)
    matches, mode = _lc.lang_check(cleaned, args.lang, prefer_local=not args.api_only)
    print(_lc.render_report(args.file, matches, mode, args.lang, args.max))
    if args.out:
        Path(args.out).write_text(json.dumps(matches, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"JSON 明细 → {args.out}")


def cmd_cn2bib(args):
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    refs = cn2bib(text)
    if not refs:
        print("未解析到题录。请确认是 CNKI 导出的 GB/T 7714 格式（含 [J]/[D]/[M] 类型标记）。")
        return
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n\n".join(refs) + "\n", encoding="utf-8")
        print(f"✔ 已生成 {len(refs)} 条 BibTeX → {out}")
    else:
        print("\n\n".join(refs))


def cmd_audit_stats(args):
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    print(json.dumps(audit_stats(text), ensure_ascii=False, indent=2))


def cmd_originality(args):
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    corpus = args.corpus or str(Path(args.file).resolve().parent)
    print(json.dumps(originality_check(text, corpus), ensure_ascii=False, indent=2))


def cmd_export(args):
    out = export_markdown(args.file, args.format, args.out, args.template, args.pdf_engine)
    print(out)


def cmd_import_docx(args):
    out, stats = import_docx(args.file, args.out, args.images)
    print(json.dumps({"file": out, **stats}, ensure_ascii=False, indent=2))


def cmd_import_pdf(args):
    out, stats = import_pdf(args.file, args.out, args.max_pages)
    print(json.dumps({"file": out, **stats}, ensure_ascii=False, indent=2))


def quality_score(issues):
    """把 quality_check 的问题列表换算为 0-100 分卡 + 等级。

    P0 每条 -30，P1 每条 -10，P2 每条 -3；满分 100，最低 0。
    等级: ≥90 可投 / 70-89 小修 / 50-69 大修 / <50 不可投。
    """
    weights = {"P0": 30, "P1": 10, "P2": 3}
    score = 100
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for i in issues:
        sev = i.get("severity", "P2")
        counts[sev] = counts.get(sev, 0) + 1
        score -= weights.get(sev, 5)
    score = max(0, min(100, score))
    if score >= 90:
        level = "可投/小修"
    elif score >= 70:
        level = "小修"
    elif score >= 50:
        level = "大修"
    else:
        level = "不可投"
    return {"score": score, "level": level, "p0": counts["P0"], "p1": counts["P1"], "p2": counts["P2"]}


def cmd_quality_check(args):
    issues = quality_check(args.project, record=True)
    print(json.dumps({"issues": issues, "score": quality_score(issues)}, ensure_ascii=False, indent=2))


def cmd_contribution_check(args):
    issues = contribution_check(args.project)
    print(json.dumps({"issues": issues}, ensure_ascii=False, indent=2))


def cmd_results_validation(args):
    issues = results_validation_check(args.project)
    print(json.dumps({"issues": issues}, ensure_ascii=False, indent=2))


def cmd_humanize(args):
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    print(json.dumps(humanize_check(text, args.lang), ensure_ascii=False, indent=2))


def cmd_section_economy(args):
    issues = section_economy_check(args.project)
    print(json.dumps({"issues": issues}, ensure_ascii=False, indent=2))


def cmd_mechanical(args):
    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    print(json.dumps({"issues": mechanical_check(text)}, ensure_ascii=False, indent=2))


def cmd_mechanical_fix(args):
    p = Path(args.file)
    text = p.read_text(encoding="utf-8", errors="replace")
    fixed = mechanical_fix(text)
    if args.dry_run:
        sys.stdout.write(fixed)
        return
    if fixed != text:
        p.write_text(fixed, encoding="utf-8")
    print(json.dumps({"file": str(p), "changed": fixed != text}, ensure_ascii=False))


# ============================================================
# 学术规范审查（E31–E45，移植自 review-gap-report.md）
# 以下检查覆盖内容逻辑/学术规范层面，不修改正文，只报告问题。
# 函数签名遵循 toolbox 约定：接收 md_text（部分再接收 ref_entries），
# 返回 list[dict]，元素形如 {"severity","type","msg","line"}。
# ============================================================

# 物种关系表（review-gap-report.md §5.4）；后续可扩展为外部 YAML
SPECIES_RELATIONS = {
    "H. sinensis": {"anamorph_of": "O. sinensis", "same_organism": True},
    "O. sinensis": {"teleomorph_of": "H. sinensis", "same_organism": True},
    "C. militaris": {"related_species": True, "same_organism": False},
}

# 已知基因/通路归属表（review-gap-report.md §5.4）
PATHWAY_ATTRIBUTION = {
    "Cns1": "C. militaris", "Cns2": "C. militaris",
    "Cns3": "C. militaris", "Cns4": "C. militaris",
    "purA": "C. militaris", "CmUGT1": "C. militaris",
}


def _line_of(text, pos):
    """将字符偏移转成行号（1-based）。"""
    if pos is None:
        return None
    return text.count("\n", 0, pos) + 1


def cross_species_inference_check(md_text):
    """E31 [P0] 跨物种推断无限定语。
    扫描正文中提及 *C. militaris* 研究结果的段落，检查同段是否含限定语
    （requires validation / may be adaptable / in *C. militaris* 等）；
    若 *C. militaris* 结果后紧跟 *H. sinensis* 的结论性表述且无限定语 → P0。
    """
    issues = []
    if not md_text:
        return issues
    # 排除 References 区块
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    # 按段落切分
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    qualifiers = re.compile(
        r"(?:requires?\s+validation|may\s+be\s+adaptable|in\s+\*?\.?\s*C\.\s*militaris\*?|"
        r"needs?\s+confirmation|further\s+studies?\s+|尚未|需验证|需进一步|在\s*\*?\s*C\.\s*militaris|"
        # 2026-08-20 扩充:已妥善 hedged 的表述不应再报(来自真实稿件误报)
        r"comparative\s+(?:C\.\s*militaris|fung|studies|lessons)|"
        r"(?:have|has)\s+not\s+(?:yet\s+)?been\s+shown|not\s+(?:an?\s+)?established|"
        r"not\s+been\s+shown\s+to\s+cause|remain(?:s)?\s+(?:premature|hypothetical|unproven)|"
        r"requires?\s+target-lineage|cannot\s+substitute|do(?:es)?\s+not\s+demonstrate|"
        r"rather\s+than\s+(?:a|an|substitute)|no\s+substitute)",
        re.I,
    )
    h_sin_pattern = re.compile(r"\*?H\.\s*sinensis\*?", re.I)
    for p in paras:
        if "C. militaris" not in p and "C.militaris" not in p.replace(" ", ""):
            continue
        # 段落含 C. militaris 结果表述但无限定语
        if not qualifiers.search(p):
            # 检查是否同时出现 H. sinensis 的结论性表述
            conclusion_words = re.compile(
                r"(?:therefore|thus|suggesting|indicating|implying|can\s+be|could\s+be|"
                r"因此|表明|说明|提示|可被|可应用于|suggests|indicates)",
                re.I,
            )
            if conclusion_words.search(p) and h_sin_pattern.search(p):
                line = _line_of(body, body.find(p))
                issues.append({
                    "severity": "P0", "type": "cross_species_inference",
                    "msg": "段落提及 *C. militaris* 结果后直接做 *H. sinensis* 结论性表述，缺少物种限定语（requires validation / may be adaptable / in *C. militaris* 等）",
                    "line": line,
                })
    return issues


def title_content_match_check(md_text, title_species=None):
    """E32 [P1] 标题与正文不匹配。
    若标题物种段落数 < 40% 而近缘物种段落 > 40% → P1。
    """
    issues = []
    if not md_text:
        return issues
    # 提取首个 # 一级标题作为标题
    m = re.search(r"^#\s+(.+?)$", md_text, re.M)
    if not m:
        return issues
    title = m.group(1).strip()
    # 从标题提取物种名（H. sinensis / O. sinensis / C. militaris 等）
    species_in_title = re.findall(r"([A-Z]\.\s*[a-z]+)", title)
    if not species_in_title:
        return issues  # 标题不含拉丁学名，无法判断
    primary = species_in_title[0]
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip() and len(p) > 60]
    if not paras:
        return issues
    primary_pat = re.compile(re.escape(primary), re.I)
    # 近缘物种：与 primary 不同名的其他 H./O./C. 开头物种
    relatives = []
    for p_name in ("H. sinensis", "O. sinensis", "C. militaris"):
        if p_name != primary:
            relatives.append(p_name)
    rel_pat = re.compile("|".join(re.escape(r) for r in relatives), re.I) if relatives else None
    primary_count = sum(1 for p in paras if primary_pat.search(p))
    rel_count = sum(1 for p in paras if rel_pat and rel_pat.search(p))
    total = len(paras)
    if total:
        p_ratio = primary_count / total
        r_ratio = rel_count / total
        if p_ratio < 0.4 and r_ratio > 0.4:
            issues.append({
                "severity": "P1", "type": "title_content_match",
                "msg": f"标题物种 {primary} 段落占比 {p_ratio:.0%}，近缘物种段落占比 {r_ratio:.0%}——标题承诺与正文实质内容不匹配",
            })
    return issues


def systematic_review_claim_check(md_text):
    """E33 [P0] 自称系统综述但方法不合格。
    若摘要/引言出现 "systematic review" / "systematically integrates" / "meta-analysis"，
    检查 Methods 是否含检索日期/数据库名/Boolean 检索式/纳排标准/筛选流程；缺任一项 → P0。
    """
    issues = []
    if not md_text:
        return issues
    claim_pat = re.compile(
        r"\b(?:systematic(?:ally)?\s+review|systematically\s+integrat|meta-analysis|系统综述|系统评价)\b",
        re.I,
    )
    # 只在正文(References 之前)找声称;参考文献标题里的 "systematic review" 不算
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    matches = list(claim_pat.finditer(body))
    if not matches:
        return issues
    # 排除否定语境("rather than a ... systematic review" / "not a systematic review" / 而非系统综述)
    neg_pat = re.compile(r"(?:not\s+(?:a|an|the)?\s*|rather\s+than\s+(?:a|an|the)?\s*|instead\s+of\s+(?:a|an)?\s*|non-|并非|而非)", re.I)
    real_claims = []
    for m in matches:
        window = body[max(0, m.start() - 80):m.start()]
        if not neg_pat.search(window):
            real_claims.append(m)
    if not real_claims:
        return issues
    # 提取 Methods 章节
    m = re.search(r"(?:^#+\s*(?:\d+[.．、]\s*)?(?:methods?|materials?\s+and\s+methods?|材料与方法|方法)\s*\n)(.*?)(?=\n\s*#+|\Z)",
                  md_text, re.M | re.S | re.I)
    methods = m.group(1) if m else ""
    required = [
        ("检索日期", re.compile(r"(?:search\s+date|date\s+of\s+search|检索日期|检索时间|as\s+of\s+\d{4})", re.I)),
        ("数据库名", re.compile(r"(?:database|web\s+of\s+science|pubmed|scopus|embase|google\s+scholar|arxiv|数据库)", re.I)),
        ("检索式或关键词", re.compile(r"(?:search\s+(?:string|query|term)|boolean|关键词|检索式|retrieval\s+formula)", re.I)),
        ("纳排标准", re.compile(r"(?:inclusion\s+criteri|exclusion\s+criteri|纳[入排]标准|筛选标准)", re.I)),
        ("筛选流程", re.compile(r"(?:screening\s+process|prisma|flow\s+diagram|筛选流程|两步筛选|title\s+and\s+abstract)", re.I)),
    ]
    missing = [name for name, pat in required if not pat.search(methods)]
    if missing:
        issues.append({
            "severity": "P0", "type": "systematic_review_claim",
            "msg": f"自称系统综述但 Methods 缺少：{', '.join(missing)}——不满足 PRISMA 最低要求，建议改称 narrative review",
        })
    return issues


def _body_without_refs(md_text):
    """去掉 References 区块后的正文。"""
    return re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]


# 数字引用语法:编号 1-3 位,区间分隔符支持 -- / - / – / —;编号后禁止紧跟数字(排除年份如 2019)
_NUM = r"\d{1,3}(?!\d)"
_SEP = r"(?:-{1,2}|[–—])"
_NUM_GROUP_LEAD = re.compile(
    rf"^({_NUM}(?:\s*{_SEP}\s*{_NUM})?(?:\s*,\s*{_NUM}(?:\s*{_SEP}\s*{_NUM})?)*)"
)


def _numeric_citation_groups(md_text):
    """提取正文数字引用组(括号开头为编号序列即算,容忍组内尾注如 "; Supplementary Table S4")。"""
    body = _body_without_refs(md_text)
    out = []
    for inner in re.findall(r"\(([^()]{1,160})\)", body):
        s = inner.strip()
        if not s or not s[0].isdigit():
            continue
        m = _NUM_GROUP_LEAD.match(s)
        if m:
            out.append(m.group(1))
    # 方括号数字引用 [n] / [1,2] / [1-3]
    out += re.findall(r"\[(\d{1,3}(?:\s*(?:-{1,2}|[–—]|,)\s*\d{1,3})*)\]", body)
    return out


def _citation_style(md_text):
    """识别正文引用风格:author-year / numeric / unknown(2026-08-20 新增,供引用类检查适配)。"""
    if not md_text:
        return "unknown"
    body = _body_without_refs(md_text)
    ay = len(re.findall(r"\([A-Z][a-z]+(?:\s+(?:et\s+al\.?|&|and)\s+[A-Z][a-z]+)?,\s*\d{4}[a-z]?\)", body))
    num = len(_numeric_citation_groups(md_text))
    if ay >= 3 and ay > num:
        return "author-year"
    if num >= 3 and num > ay:
        return "numeric"
    return "unknown"


def _cited_numbers(md_text):
    """数字引用风格下,正文引用到的文献编号集合(展开区间如 35--37)。"""
    nums = set()
    for g in _numeric_citation_groups(md_text):
        for part in re.split(r"\s*,\s*", g):
            part = part.strip()
            m = re.match(rf"({_NUM})\s*{_SEP}\s*({_NUM})$", part)
            if m:
                a, b = int(m.group(1)), int(m.group(2))
                if a <= b and b - a <= 200:
                    nums.update(range(a, b + 1))
            elif part.isdigit():
                nums.add(int(part))
    return nums


def citation_count_consistency_check(md_text, ref_entries=None):
    """E34a [P1] 正文引用数 vs 文末条目数不一致（差值 > 2 即报）。

    2026-08-20 起按引用风格适配:数字风格 (1--3, 9) / [n] 按"被引用到的编号去重数"
    对比条目数;作者-年风格按 (Author, Year) 去重对计数。风格不明时跳过,避免误报。
    """
    issues = []
    if not md_text or not ref_entries:
        return issues
    style = _citation_style(md_text)
    if style == "numeric":
        n_in = len(_cited_numbers(md_text))
    elif style == "author-year":
        pairs = set(re.findall(r"\(([A-Z][a-z]+(?:\s+(?:et\s+al\.?|&|and)\s+[A-Z][a-z]+)?,\s*\d{4}[a-z]?)\)",
                               _body_without_refs(md_text)))
        n_in = len(pairs)
    else:
        return issues
    n_ref = len(ref_entries)
    if abs(n_in - n_ref) > 2:
        issues.append({
            "severity": "P1", "type": "citation_count_consistency",
            "msg": f"正文引用的文献数 {n_in}（{style} 风格,去重）vs 文末条目数 {n_ref} 不一致（差值 > 2）",
        })
    return issues


def same_author_year_suffix_check(ref_entries, md_text=None):
    """E34b [P1] 同一第一作者同年多篇文献需加 a/b/c 后缀。

    仅对作者-年引用风格有意义;数字编号风格靠编号区分,无需后缀(2026-08-20 起按风格跳过)。
    """
    issues = []
    if not ref_entries:
        return issues
    if md_text is not None and _citation_style(md_text) == "numeric":
        return issues
    bucket = {}
    for e in ref_entries:
        author = (e.get("author") or e.get("authors") or "").split(",")[0].strip().lower()
        year = str(e.get("year", "")).strip()
        if author and year:
            bucket.setdefault((author, year), []).append(e)
    for (author, year), lst in bucket.items():
        if len(lst) > 1:
            # 检查是否已有 a/b/c 后缀
            keys = [e.get("id", "") for e in lst]
            has_suffix = any(re.search(r"[a-z]$", k) for k in keys)
            if not has_suffix:
                issues.append({
                    "severity": "P1", "type": "same_author_year_suffix",
                    "msg": f"同一第一作者 {author} {year} 有 {len(lst)} 篇文献，需在引用 key 与正文 (Author, Year) 后添加 a/b/c 后缀",
                })
    return issues


def overstatement_check(md_text):
    """E35 [P1] 过度论断。扫描绝对化词汇，排除引号内直接引语。"""
    issues = []
    if not md_text:
        return issues
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    over_words = [
        "first to", "directly transferable", "directly transferred",
        "guaranteed", "proven", "breakthrough", "首个", "首次", "可直接转移", "可保证",
    ]
    # 去除引号内的直接引语：用等长空格遮蔽而非删除，保持与 body 偏移一致，
    # 避免 _line_of(body, pos) 行号跨行漂移
    cleaned = re.sub("(?:\"[^\"]*\"|'[^']*'|“[^”]*”)",
                     lambda m: " " * len(m.group(0)), body)
    for w in over_words:
        for m in re.finditer(r"\b" + re.escape(w) + r"\b", cleaned, re.I):
            line = _line_of(body, m.start())
            issues.append({
                "severity": "P1", "type": "overstatement",
                "msg": f"过度论断词「{w}」——缺少直接实验或系统发育证据支撑，请加限定语或提供证据",
                "line": line,
            })
            break  # 每词报一条即可
    # strain improvement 用于描述培养基优化
    for m in re.finditer(r"strain\s+improvement", cleaned, re.I):
        ctx = cleaned[max(0, m.start() - 80):m.end() + 80]
        if re.search(r"medium|supplement|precursor|feeder|培养基|补料|前体", ctx, re.I):
            line = _line_of(body, m.start())
            issues.append({
                "severity": "P1", "type": "overstatement",
                "msg": "「strain improvement」用于描述培养基优化/前体添加——fermentation optimization ≠ strain improvement",
                "line": line,
            })
            break
    # B5 安全性绝对表述（GRAS / completely safe / no safety concerns 等）同句无限定语 → P1
    # 限定语窗口参照 cross_species_inference_check 的教训：限定表要够宽，宁可漏报不可误报已 hedged 的句子
    safety_abs = [
        (r"\bGRAS\b", "GRAS"),
        # regarded 变体：ai_client.py 提示词会教模型写 "generally regarded as safe"
        (r"generally\s+(?:recognized|regarded)\s+as\s+safe", "generally recognized/regarded as safe"),
        (r"completely\s+safe", "completely safe"),
        (r"entirely\s+safe", "entirely safe"),
        (r"absolutely\s+safe", "absolutely safe"),
        (r"totally\s+safe", "totally safe"),
        (r"perfectly\s+safe", "perfectly safe"),
        (r"no\s+safety\s+concerns?", "no safety concerns"),
        (r"without\s+(?:any\s+)?safety\s+concerns?", "without safety concerns"),
        (r"绝对安全|完全安全|无任何安全隐患", "绝对安全/完全安全"),
    ]
    safety_qual = re.compile(
        r"under\s+(?:defined|these|such|specific|certain|controlled|normal|specified)\s+conditions|"
        r"for\s+its\s+intended\s+use|as\s+intended|when\s+used|if\s+used|provided\s+that|as\s+long\s+as|"
        r"at\s+(?:the\s+)?(?:recommended|tested|studied|approved)\s+(?:doses?|levels?|concentrations?)|"
        r"in\s+(?:this|the)\s+(?:context|regard|range|study)|so\s+far|to\s+date|hitherto|"
        r"dose[- ]dependent|conditional(?:ly)?|within\s+(?:safe\s+)?limits?|"
        r"designated|certified|assessed|evaluated|reviewed\s+(?:as|by)|"
        r"status\s+for|for\s+(?:use\s+in|use\s+as)|"
        r"for\s+\w+(?:\s+\w+){0,2}\s+(?:production|applications?|uses?|purposes?)|"
        r"has\s+(?:a|an)\s+\w+\s+record|nuanced\s+record|"
        r"在一定条件下|在特定条件下|在预期用途下|按规定剂量|经评估|经认证|用于",
        re.I,
    )
    safety_neg = re.compile(r"\b(?:not|never|cannot|can't|isn't|aren't|hardly|rarely)\b|并非|不安全|并非没有", re.I)
    for pat, label in safety_abs:
        for m in re.finditer(pat, cleaned, re.I):
            # 取所在句作为限定语窗口（取不到时退化为 ±200 字符）
            window = cleaned[max(0, m.start() - 200):m.end() + 200]
            for st, s in _iter_sentences(cleaned):
                if st <= m.start() < st + len(s):
                    window = s
                    break
            if safety_qual.search(window) or safety_neg.search(window):
                continue  # 已 hedged（限定语/否定语境）
            if re.search(r"\[\d{1,3}(?!\d)", window):
                continue  # 同句带引文编号视为有出处
            issues.append({
                "severity": "P1", "type": "overstatement",
                "msg": f"安全性绝对表述「{label}」同句无限定语（under defined conditions / for its intended use / 引文编号等）——请加限定或引证",
                "line": _line_of(body, m.start()),
            })
            break  # 每模式报一条即可
    return issues


def review_depth_check(md_text):
    """E36 [P1] 综述性不足。检查统一单位换算/稳定性/重复性/工业放大讨论。"""
    issues = []
    if not md_text:
        return issues
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    # 含产量数据但无单位换算
    yield_pat = re.compile(r"\d+(?:\.\d+)?\s*(?:mg/L|g/L|μg/mL|mg/g|mg·g)", re.I)
    if yield_pat.search(body) and not re.search(r"unit\s+conversion|standardiz|单位换算|统一单位", body, re.I):
        issues.append({
            "severity": "P1", "type": "review_depth",
            "msg": "正文报告产量数据但无统一单位换算表——综述应做实验条件统一性分析与产量单位换算",
        })
    if yield_pat.search(body) and not re.search(r"stability|reproducibility|industrial\s+scal|稳定性|重复性|工业放大", body, re.I):
        issues.append({
            "severity": "P2", "type": "review_depth",
            "msg": "正文报告产量但缺少 stability/reproducibility/industrial scale 讨论",
        })
    return issues


def roadmap_timeline_check(md_text):
    """E37/E45 [P2] 路线图时间表无依据。"""
    issues = []
    if not md_text:
        return issues
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    time_pat = re.compile(r"(?:\d+\s*[-–~到]\s*\d+\s*years?|\d+\s*years?)", re.I)
    basis_pat = re.compile(r"(?:based\s+on|estimated\s+from|inferred\s+from|according\s+to|基于|依据|参考)", re.I)
    cross_species_pat = re.compile(r"C\.\s*militaris", re.I)
    for m in time_pat.finditer(body):
        ctx = body[max(0, m.start() - 200):m.end() + 100]
        if not basis_pat.search(ctx):
            line = _line_of(body, m.start())
            # 跨物种时间外推加重点提示
            if cross_species_pat.search(ctx):
                issues.append({
                    "severity": "P2", "type": "roadmap_timeline",
                    "msg": f"时间表「{m.group(0)}」缺少依据，且涉及跨物种（C. militaris）时间外推——依据有限，建议标注为作者主观估计或删除",
                    "line": line,
                })
            else:
                issues.append({
                    "severity": "P2", "type": "roadmap_timeline",
                    "msg": f"时间表「{m.group(0)}」缺少实证依据——建议标注为作者主观估计或删除",
                    "line": line,
                })
            break  # 报一条即可
    return issues


def latex_residue_check(md_text):
    """E38a/E44 [P1] LaTeX 转义残留 + 花括号。"""
    issues = []
    if not md_text:
        return issues
    patterns = [
        (re.compile(r"\\textit\{", re.I), "\\textit{}"),
        (re.compile(r"\\textbf\{", re.I), "\\textbf{}"),
        (re.compile(r"\\emph\{", re.I), "\\emph{}"),
        (re.compile(r"\\cite\{[^}]*\}", re.I), "\\cite{}"),
        (re.compile(r"\\ref\{[^}]*\}", re.I), "\\ref{}"),
        (re.compile(r"\{\\", re.I), "{\\"),
    ]
    total = 0
    for pat, label in patterns:
        n = len(pat.findall(md_text))
        if n:
            total += n
    # 参考文献列表中独立花括号（{ 或 }）统计
    brace_n = md_text.count("{") + md_text.count("}")
    # 阈值：参考文献区花括号 > 10 处多半是 BibTeX 残留
    if total > 0 or brace_n > 20:
        issues.append({
            "severity": "P1", "type": "latex_residue",
            "msg": f"LaTeX 转义残留 {total} 处，花括号 {brace_n} 处——投稿前需清理为纯 Markdown",
        })
    return issues


def bom_check(md_text):
    """E38b [P2] BOM 字符。"""
    issues = []
    if not md_text:
        return issues
    if md_text.startswith("\ufeff"):
        issues.append({
            "severity": "P2", "type": "bom",
            "msg": "文件首字节为 BOM (0xEF 0xBB 0xBF)——投稿前应保存为 UTF-8 无 BOM",
        })
    return issues


def cross_section_contradiction_check(md_text):
    """E39 [P0] 引言与正文矛盾。检测同一技术在引言 vs 正文中矛盾断言。"""
    issues = []
    if not md_text:
        return issues
    # 提取引言与摘要
    intro_m = re.search(r"(?:^#+\s*(?:\d+[.．、]\s*)?introduction\s*\n|^#+\s*引言\s*\n)(.*?)(?=\n\s*#+|\Z)",
                        md_text, re.M | re.S | re.I)
    abs_m = re.search(r"(?:^#+\s*(?:abstract|摘要)\s*\n)(.*?)(?=\n\s*#+|\Z)", md_text, re.M | re.S | re.I)
    intro_abs = (intro_m.group(1) if intro_m else "") + " " + (abs_m.group(1) if abs_m else "")
    if not intro_abs:
        return issues
    # 关键技术词
    tech_words = ["UV mutagenesis", "ARTP", "protoplast fusion", "CRISPR", "ATMT",
                  "Agrobacterium", "PEG transformation", "PEG-mediated",
                  "紫外", "原生质体融合", "基因编辑"]
    # 断言性陈述
    affirm_pat = re.compile(r"(?:have\s+been\s+applied|was\s+established|has\s+been\s+reported|are\s+available|已应用|已建立|已有报道|已报道)", re.I)
    deny_pat = re.compile(r"(?:has\s+not\s+been\s+reported|not\s+available|absent|尚未报道|未见报道|未在.*?报道|无报道)", re.I)
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    # 去掉引言/摘要
    body_only = body
    if intro_m:
        body_only = body_only.replace(intro_m.group(0), "", 1)
    for tech in tech_words:
        tech_pat = re.compile(re.escape(tech), re.I)
        in_intro_affirm = any(affirm_pat.search(intro_abs[i-80:i+80]) for i in [m.start() for m in tech_pat.finditer(intro_abs)])
        in_body_deny = any(deny_pat.search(body_only[max(0, m.start()-80):m.end()+80]) for m in tech_pat.finditer(body_only))
        if in_intro_affirm and in_body_deny:
            issues.append({
                "severity": "P0", "type": "cross_section_contradiction",
                "msg": f"引言/摘要称 {tech} 已应用，但正文明确说尚未报道——跨章节矛盾",
            })
            break  # 报一条即可，避免噪声
    return issues


def species_taxonomy_check(md_text):
    """E40 [P1] 物种/生活史阶段表述不严谨。"""
    issues = []
    if not md_text:
        return issues
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    # 同一物种不同阶段被当作不同物种
    wrong_pat = re.compile(r"(?:different\s+species|distinct\s+species|two\s+species|separate\s+species|两个不同物种|不同物种)", re.I)
    # 仅在同时出现 H. sinensis 和 O. sinensis 的语境下报
    for m in wrong_pat.finditer(body):
        ctx = body[max(0, m.start() - 300):m.end() + 300]
        if re.search(r"H\.\s*sinensis", ctx) and re.search(r"O\.\s*sinensis", ctx):
            line = _line_of(body, m.start())
            issues.append({
                "severity": "P1", "type": "species_taxonomy",
                "msg": "将 *H. sinensis* 与 *O. sinensis* 当作不同物种——*H. sinensis* 是 *O. sinensis* 的无性型（同一物种不同生活史阶段），首次提及时应标注 anamorph/teleomorph 关系",
                "line": line,
            })
            break
    return issues


def table_content_consistency_check(md_text):
    """E41 [P0] 表格技术状态描述错误。表格标 Established/Protocol available 但正文无对应实验报道。"""
    issues = []
    if not md_text:
        return issues
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    # 解析 Markdown 表格行
    table_rows = re.findall(r"^\|.*?\|$", body, re.M)
    if not table_rows:
        return issues
    # 提取表格中技术状态关键词
    state_pat = re.compile(r"(?:Established|Protocol\s+available|Not\s+available|Absent|已建立|方法可用|不可用)", re.I)
    tech_keywords = re.compile(r"(?:UV|ARTP|protoplast\s+fusion|CRISPR|ATMT|Agrobacterium|PEG|原生质体|基因编辑|农杆菌)", re.I)
    suspicious = 0
    for row in table_rows:
        if state_pat.search(row) and tech_keywords.search(row):
            # 在正文中搜索对应技术 + "H. sinensis" 的实验报道
            tech_match = tech_keywords.search(row)
            tech = tech_match.group(0)
            # 简化：检查正文是否有 H. sinensis + tech + 报道语
            report_pat = re.compile(
                rf"H\.\s*sinensis[^.]*?{re.escape(tech)}[^.]*?(?:was|has\s+been|were|demonstrated|reported|applied|报道|应用|建立)",
                re.I,
            )
            if not report_pat.search(body):
                suspicious += 1
    if suspicious > 0:
        issues.append({
            "severity": "P0", "type": "table_content_consistency",
            "msg": f"表格中 {suspicious} 项技术状态（Established/Protocol available）在正文中找不到对应实验报道——表格状态可能错误",
        })
    return issues


def pathway_attribution_check(md_text):
    """E42 [P0] 通路/基因归属物种外推。"""
    issues = []
    if not md_text:
        return issues
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    # 提及基因/通路但未标注物种来源
    for gene, src_species in PATHWAY_ATTRIBUTION.items():
        gene_pat = re.compile(r"\b" + re.escape(gene) + r"\b", re.I)
        for m in gene_pat.finditer(body):
            ctx = body[max(0, m.start() - 200):m.end() + 100]
            # 检查是否标注了物种来源
            if not re.search(rf"in\s*\*?\.?\s*{re.escape(src_species)}|{re.escape(src_species)}|identified\s+in|来自|源于", ctx, re.I):
                line = _line_of(body, m.start())
                issues.append({
                    "severity": "P0", "type": "pathway_attribution",
                    "msg": f"提及 {gene} 但未标注物种来源——{gene} 鉴定自 *{src_species}*，首次提及时应标注 \"identified in *{src_species}* by [citation]\"",
                    "line": line,
                })
                break  # 每基因报一条
    return issues


def citation_suffix_sync_check(md_text, ref_entries=None):
    """E43 [P1] 正文引文缺少 a/b/c 后缀。"""
    issues = []
    if not md_text or not ref_entries:
        return issues
    body = re.split(r"\n\s*#{1,6}\s*(?:references|参考文献)\s*\n", md_text, maxsplit=1, flags=re.I)[0]
    # 找出需要后缀的条目
    bucket = {}
    for e in ref_entries:
        author = (e.get("author") or e.get("authors") or "").split(",")[0].strip().lower()
        year = str(e.get("year", "")).strip()
        if author and year:
            bucket.setdefault((author, year), []).append(e)
    needs_suffix = {k: v for k, v in bucket.items() if len(v) > 1}
    if not needs_suffix:
        return issues
    # 检查正文中对应引用是否已加后缀
    for (author, year), lst in needs_suffix.items():
        # 提取 author 姓氏首字母大写形式（如 zhang → Zhang）
        surname = author.split()[-1].capitalize() if author.split() else author.capitalize()
        # 正文引用形如 (Zhang et al., 2020) 但缺后缀
        plain_pat = re.compile(rf"\({re.escape(surname)}\s+et\s+al\.?,?\s*{re.escape(year)}\)")
        # 已带后缀的形如 (Zhang et al., 2020a)
        suffixed_pat = re.compile(rf"\({re.escape(surname)}\s+et\s+al\.?,?\s*{re.escape(year)}[a-z]\)")
        if plain_pat.search(body) and not suffixed_pat.search(body):
            issues.append({
                "severity": "P1", "type": "citation_suffix_sync",
                "msg": f"参考文献 {surname} et al., {year} 需 a/b/c 后缀（同年多篇），但正文引用仍为 \"{surname} et al., {year}\" 无后缀",
            })
            break  # 报一条即可
    return issues


# ============================================================
# 确定性检查补盲（B1-B6 + D3，2026-08-21 新增）
# 约定同上：接收 md_text（部分再接收 ref_entries / 项目路径），
# 返回 list[dict]，只报告问题不修改正文（mechanical_fix 为专用无损消毒函数，供导出前调用）。
# ============================================================

# B3 通用停用词（仅长度 >=4 的 token 参与匹配，故只列常见长停用词与泛化词）
_STOPWORDS_EN = {
    "about", "above", "across", "after", "again", "against", "also", "although",
    "among", "another", "because", "been", "before", "being", "between", "both",
    "could", "down", "during", "each", "few", "for", "from", "further", "had",
    "has", "have", "having", "here", "hers", "him", "his", "how", "into", "its",
    "itself", "just", "more", "most", "not", "now", "off", "once", "only", "other",
    "our", "ours", "out", "over", "own", "same", "she", "should", "some", "such",
    "than", "that", "the", "their", "theirs", "them", "then", "there", "these",
    "they", "this", "those", "through", "thus", "under", "until", "upon", "very",
    "was", "were", "what", "when", "where", "which", "while", "who", "whom", "why",
    "will", "with", "within", "without", "would", "however", "based", "using",
    "used", "toward", "towards", "study", "studies", "paper", "approach", "novel",
    "from", "with", "into", "recent", "advances", "applications", "application",
    "based", "towards", "systems", "potential", "emerging", "perspectives",
    "strategies", "challenges", "opportunities", "insights", "overview",
}


def _strip_code_fences(md_text):
    """去掉 ``` 围栏代码块，避免对代码内容做机械/文本检查。"""
    return re.sub(r"```.*?```", "", md_text, flags=re.S)


# 句界字符集（正则字符类内部）：DEFAULT 为原有行为（仅半角 . ! ?）；
# FULL 追加半角分号与中文全角标点 。！？；——中文句子以 。 结尾，
# 不含半角句点，若沿用 DEFAULT 会把整行/整段吞成一个「句子」致窗口失真。
_SENT_BOUNDS_DEFAULT = r".!?"
_SENT_BOUNDS_FULL = r".!?;。！？；"


def _iter_sentences(text, bounds=_SENT_BOUNDS_DEFAULT):
    """逐句 yield (start_offset, 句子原文)：按行 + 句界字符集保守切分，不跨行。

    bounds 为句界字符集（正则字符类内部），默认 `. ! ?` 保持既有调用方行为；
    中文/中英混排文本应传 _SENT_BOUNDS_FULL。
    """
    pat = re.compile(r"[^{b}]+[{b}]*".format(b=bounds))
    for line_m in re.finditer(r"[^\n]*", text):
        line_start = line_m.start()
        for s in pat.finditer(line_m.group(0)):
            if s.group(0).strip():
                yield line_start + s.start(), s.group(0)


def mechanical_check(md_text):
    """B1 [P1/P2] 机械性缺陷检查。

    检测项：
      - `..` 恰好两个连续句点（不误伤 `...` 省略号与 e.g./i.e. 单点缩写）→ P1
      - `;;` / `,,` 连标点 → P1
      - 英文句首小写（排除 e.g./i.e./et al. 等缩写后）→ P2
      - 超长句（>40 词）→ P2
    """
    issues = []
    if not md_text:
        return issues
    text = _strip_code_fences(md_text)
    # 1) 恰好两个连续句点（三个及以上是省略号，保留不报）
    n_dots = len(re.findall(r"(?<!\.)\.{2}(?!\.)", text))
    if n_dots:
        issues.append({"severity": "P1", "type": "mechanical_punct",
                       "msg": f"检测到 {n_dots} 处连续双句点 `..`（非省略号应为单句点，年份后的 `..` 常为 `;`/`,` 之误）"})
    n_semi = len(re.findall(r";;", text))
    if n_semi:
        issues.append({"severity": "P1", "type": "mechanical_punct",
                       "msg": f"检测到 {n_semi} 处连续分号 `;;`"})
    n_comma = len(re.findall(r",,", text))
    if n_comma:
        issues.append({"severity": "P1", "type": "mechanical_punct",
                       "msg": f"检测到 {n_comma} 处连续逗号 `,,`"})
    body = _body_without_refs(text)
    # 2) 英文句首小写（排除单点缩写结尾；References 区块不参与，其续行本就小写开头）
    abbr_prefix = re.compile(
        r"(?:e\.g|i\.e|etc|et\s+al|vs|figs?|tabs?|cf|no|vol|pp|chap|eds?|approx|resp|inc|ltd)\.$", re.I)
    lower_starts = 0
    for m in re.finditer(r"""[.!?][)\]"']*\s+([a-z])""", body):
        prefix = body[max(0, m.start() - 16):m.start() + 1]
        if abbr_prefix.search(prefix):
            continue
        if re.search(r"\b(?:[A-Z]\.\s*)+$", prefix):
            continue  # 属名缩写续写（单大写字母+句点，如 H. sinensis / C. H. militaris）不视为句首小写
        if re.search(r"(?<![A-Za-z0-9])\d{1,2}\.$", prefix):
            continue  # 编号+句点（如 Fig. 2./Table 3.）后接小写多为列举续项，不报
        if re.search(r"\.{2,}$", prefix):
            continue  # 省略号后的小写续写不视为句首
        url_span = body[max(0, m.start() - 160):m.start() + 1]
        if re.search(r"https?://|\bdoi\.org\b", url_span, re.I):
            continue  # URL/DOI 内部句点（如 https://doi.org/10.1000/xyz. see）不视为句首
        lower_starts += 1
    if lower_starts:
        issues.append({"severity": "P2", "type": "mechanical_lowercase",
                       "msg": f"检测到 {lower_starts} 处英文句子疑似小写开头（已排除 e.g./i.e./et al. 等缩写）"})
    # 3) 超长句（>40 词）；排除表格行/标题行/图块
    body_lines = "\n".join(l for l in body.splitlines()
                            if not l.lstrip().startswith(("|", "#", ">", "![", "-", "*")))
    long_n = 0
    for _, sent in _iter_sentences(body_lines):
        words = re.findall(r"[A-Za-z][A-Za-z'\-]*", sent)
        if len(words) > 40:
            long_n += 1
    if long_n:
        issues.append({"severity": "P2", "type": "mechanical_long_sentence",
                       "msg": f"检测到 {long_n} 个超长句（>40 词），建议拆分"})
    return issues


def mechanical_fix(text):
    """B1 无损机械消毒（幂等，供导出前调用）：

    - 恰好两个连续句点 → 单句点（三个及以上保留为省略号）
    - `;;`（含连续分号串）→ `;`
    - `,,`（含连续逗号串）→ `,`
    返回修复后的文本。
    """
    if not text:
        return text
    out = re.sub(r"(?<!\.)\.{2}(?!\.)", ".", text)
    out = re.sub(r";{2,}", ";", out)
    out = re.sub(r",{2,}", ",", out)
    return out


def reference_completeness_check(ref_entries, md_text=None):
    """B2 [P1/P2] 文献条目字段完整性（按字段分级，按字段聚合报告）。

    分级策略：核心著录字段（author/year/title 同属 P1 档，本函数检测 author/year）
    缺失 → P1；形态性字段（volume/期号/页或文章号/DOI）缺失 → P2（观察期，不进门禁）。
    理由：许多期刊引文天然无期号、arXiv/书籍/在线优先出版天然缺卷期页，
    「71/100 缺期号」「30/100 缺 DOI」属大面积真实文献形态差异而非写作缺陷，
    统一 P1 会阻断 submit 门禁与 review 检查点 9 并把定稿评分打穿；形态性字段
    降为 P2 观察期，待误报清零与字段来源策略解决后再提级。
    et al 写法不统一（et al / et al. / et. al. 混用）→ P2。
    （"年份后紧跟 ;;" 的模式由 mechanical_check 对全文 `;;` 的检测覆盖。）
    """
    issues = []
    if not ref_entries:
        return issues

    def _key(e, idx):
        return str(e.get("id") or f"#{idx + 1}")

    # 核心著录字段（P1）与形态性字段（P2 观察期）分级：大面积缺卷期页/DOI 属真实
    # 文献形态差异（arXiv/书籍/在线优先出版），不应阻断定稿
    core_fields = ("author", "year", "title")
    form_fields = ("volume", "number", "pages", "doi")
    missing = {f: [] for f in core_fields + form_fields}
    # 书籍形态条目（@book/inbook/proceedings）天然无卷期页（2026-08-22）：
    # 只查 author/year/title/doi，不查 volume/number/pages（应为 publisher）
    _BOOK_TYPES = {"book", "inbook", "proceedings", "incollection", "monograph"}
    for i, e in enumerate(ref_entries):
        k = _key(e, i)
        is_book = str(e.get("type") or "").lower() in _BOOK_TYPES
        if not str(e.get("author") or e.get("authors") or "").strip():
            missing["author"].append(k)
        if not str(e.get("year") or "").strip():
            missing["year"].append(k)
        if not str(e.get("title") or "").strip():
            missing["title"].append(k)
        if is_book:
            continue
        if not str(e.get("volume") or "").strip():
            missing["volume"].append(k)
        if not str(e.get("number") or e.get("issue") or "").strip():
            missing["number"].append(k)
        if not (str(e.get("pages") or "").strip()
                or str(e.get("articleno") or e.get("article-number") or e.get("eid") or "").strip()):
            missing["pages"].append(k)
        if not str(e.get("doi") or "").strip():
            missing["doi"].append(k)
    labels = {"author": "作者", "year": "年份", "title": "标题", "volume": "卷(volume)",
              "number": "期(number)", "pages": "页码/文章号", "doi": "DOI"}
    for field, keys in missing.items():
        if keys:
            sev = "P1" if field in core_fields else "P2"
            shown = ", ".join(keys[:5]) + (" ..." if len(keys) > 5 else "")
            issues.append({"severity": sev, "type": "reference_completeness",
                           "msg": f"{len(keys)}/{len(ref_entries)} 条文献缺少{labels[field]}：{shown}"})
    # et al 写法一致性（只查 References 之前的正文）
    if md_text:
        body = _body_without_refs(md_text)
        variants = []
        if re.search(r"\bet\.\s*al", body):
            variants.append("et. al.")
        if re.search(r"\bet\s+al\.", body):
            variants.append("et al.")
        if re.search(r"\bet\s+al(?![\w.])", body):
            variants.append("et al")
        if len(variants) >= 2:
            issues.append({"severity": "P2", "type": "etal_consistency",
                           "msg": f"et al 用法不统一：{' / '.join(variants)} 同时出现，建议统一为 'et al.'"})
    return issues


def citation_context_check(md_text, ref_entries=None):
    """B3 [P1] 论述-引文匹配度：对每个数字引用 [n]，取其所在句与 ref n 标题
    做关键词重叠检查（停用词过滤、小写归一、长度 >=4 的 token）；零重叠 → P1。
    引用编号超出文献总数 → P1。

    保守策略：句子内容词 <3 时跳过（无法判断）；对应文献无标题时跳过；最多报 8 条。
    """
    issues = []
    if not md_text or not ref_entries:
        return issues
    body = _body_without_refs(md_text)
    # 句界含中文全角标点（。！？；）：中文句以 。 结尾，旧字符集会把整段吞没致重叠检查失真
    sents = list(_iter_sentences(body, _SENT_BOUNDS_FULL))
    # 预计算各段落真实 offset：re.split 带捕获组返回 [段, 分隔符, 段, ...]，
    # 偏移累加即可；body.find(p) 对重复段落恒返回首次位置，会致段落级兜底失效误报
    para_spans = []
    _off = 0
    for _piece in re.split(r"(\n\s*\n)", body):
        if _piece.strip():
            para_spans.append((_off, _piece))
        _off += len(_piece)

    def _tokens(s):
        # 连字符/斜杠切开（filamentous-fungi → filamentous + fungi），再按长度/停用词过滤
        raw = str(s).lower().replace("-", " ").replace("/", " ")
        return {t for t in re.findall(r"[a-z]{4,}", raw) if t not in _STOPWORDS_EN}

    def _para_of(pos):
        for idx, p in para_spans:
            if idx <= pos < idx + len(p):
                return p
        return None

    titles = {i + 1: _tokens(e.get("title", "")) for i, e in enumerate(ref_entries)}
    total = len(ref_entries)
    out_of_range = set()
    reported = 0
    for m in re.finditer(r"\[(\d{1,3})\]", body):
        n = int(m.group(1))
        if n == 0:
            continue
        if n > total:
            out_of_range.add(n)
            continue
        title_toks = titles.get(n)
        if not title_toks:
            continue  # 对应文献无可判标题
        pos = m.start()
        sent = next((s for st, s in sents if st <= pos < st + len(s)), None)
        if not sent:
            continue
        sent_toks = _tokens(sent)
        if len(sent_toks) < 3:
            continue
        if sent_toks & title_toks:
            continue
        # 保守化：句中零重叠时再看整段（段首主题句+段内挂引号是常见写法），
        # 段落级仍零重叠才报，宁可漏报不可误报
        para = _para_of(pos)
        if para and (_tokens(para) & title_toks):
            continue
        reported += 1
        if reported <= 8:
            issues.append({"severity": "P1", "type": "citation_context",
                           "msg": f"论述与引文疑似不匹配: [{n}] 所在段落与文献 {n} 标题关键词零重叠",
                           "line": _line_of(body, pos)})
    if out_of_range:
        issues.append({"severity": "P1", "type": "citation_context_range",
                       "msg": f"引用编号超出文献总数（共 {total} 条）：{sorted(out_of_range)[:10]}"})
    return issues


# B4 小节主题词表（保守：仅对明确的类别对做判断，宁可漏报不可误报）
_SCOPE_CATEGORIES = {
    "enzyme": {
        "label": "酶/enzyme",
        "heading": re.compile(r"\b(?:enzymes?|enzymatic|enzyme\s+production)\b|产酶|酶", re.I),
        "markers": re.compile(
            r"\b(?:amylase|protease|cellulase|pectinase|xylanase|lipase|glucoamylase|"
            r"laccase|phytase|invertase|tannase)\b|酶活|产酶", re.I),
    },
    "metabolite": {
        "label": "代谢产物/metabolite",
        "heading": re.compile(r"\b(?:metabolites?|organic\s+acids?|secondary\s+metabolites?)\b|代谢产物|有机酸", re.I),
        "markers": re.compile(
            r"\b(?:kojic\s+acid|xylitol|citric\s+acid|gluconic\s+acid|oxalic\s+acid|"
            r"itaconic\s+acid|malic\s+acid|fumaric\s+acid|isomaltulose|trehalose|"
            r"pullulan|scleroglucan|melanin|ethanol)\b", re.I),
    },
    "safety": {
        "label": "安全性/safety",
        "heading": re.compile(r"\b(?:safety|GRAS|pathogenicity|toxicity)\b|安全|毒性", re.I),
        "markers": re.compile(r"\b(?:mycotoxins?|ochratoxins?|fumonisins?|aflatoxins?|virulence|toxins?)\b|毒素", re.I),
    },
    "strain": {
        "label": "菌株改良/strain improvement",
        "heading": re.compile(r"\b(?:strain\s+improvement|strain\s+engineering|mutagenesis)\b|育种|菌株改良", re.I),
        "markers": re.compile(r"\b(?:UV\s+mutagenesis|ARTP|CRISPR|protoplast\s+fusion|ATMT|Agrobacterium)\b", re.I),
    },
}


def section_scope_check(md_text, outline_path=None):
    """B4 [P1] 小节标题-内容范围错位（启发式，务必保守）。

    若小节标题声明的类别（如 enzyme/enzymatic）在小节内容中零命中，
    而内容主导词来自另一类别词表（出现 >=3 次）→ P1「小节内容疑似归类不当」。
    outline_path 为可选参数（缺省时仅基于正文标题-内容一致性判断；提供时预留给
    与 outline.md 的标题交叉核对，当前不做额外报告以避免误报）。
    """
    issues = []
    if not md_text:
        return issues
    body = _body_without_refs(md_text)
    heads = list(re.finditer(r"^(#{2,3})\s+(.+?)\s*$", body, re.M))
    for i, h in enumerate(heads):
        level = len(h.group(1))
        title = h.group(2).strip()
        content_end = len(body)
        for nxt in heads[i + 1:]:
            if len(nxt.group(1)) <= level:
                content_end = nxt.start()
                break
        content = body[h.end():content_end]
        if len(content.strip()) < 200:
            continue  # 小节过短，证据不足不判
        for cat_a, va in _SCOPE_CATEGORIES.items():
            if not va["heading"].search(title):
                continue
            if va["heading"].search(content) or va["markers"].search(content):
                continue  # 内容确有本类词汇，不报
            best_b, best_n = None, 0
            for cat_b, vb in _SCOPE_CATEGORIES.items():
                if cat_b == cat_a:
                    continue
                n_b = len(vb["markers"].findall(content))
                if n_b > best_n:
                    best_b, best_n = cat_b, n_b
            if best_b and best_n >= 3:
                issues.append({
                    "severity": "P1", "type": "section_scope",
                    "msg": (f"小节内容疑似归类不当：标题 “{title}” 声明{_SCOPE_CATEGORIES[cat_a]['label']}主题，"
                            f"但内容中该主题词零命中、{_SCOPE_CATEGORIES[best_b]['label']}词占主导（{best_n} 处）"),
                    "line": _line_of(body, h.start()),
                })
    return issues


def figure_numbering_check(md_text):
    """B6 [P1/P2] 图表编号顺序检查。

    - Figure/Table 编号须按正文首次出现顺序递增（先 Figure 1 后 Figure 2；乱序/跳号 → P1）；
    - ![...](...) 图块与其后图注行之间不得隔其他段落（近旁能找到图注却不紧邻 → P2）。
    """
    issues = []
    if not md_text:
        return issues
    body = _body_without_refs(md_text)
    for label in ("Figure", "Table"):
        # 位置感知编号提取（2026-08-22 修复误报）：覆盖单数（Table 1 / 1a 子图）、
        # 复数列表（Tables 1, 2 and 3）与区间（Tables 1-3）；Supplementary Table
        # 不参与正文编号序列。此前仅识别单数形式，复数首次提及被漏记 → 首现序列
        # 失真 → 对实际 1,2,3,4 顺序误报 P1。
        _sup_guard = r"(?<![Ss]upplementary )"
        occur = []  # (pos, number)
        for m in re.finditer(rf"{_sup_guard}\b{label}s\s+(\d+(?:\s*[,–-]\s*\d+)*(?:\s+and\s+\d+)?)", body, re.I):
            pos, grp = m.start(), m.group(1)
            for tok in re.split(r"\s*,\s*|\s+and\s+", grp):
                tok = tok.strip()
                if re.fullmatch(r"\d+\s*[–-]\s*\d+", tok):
                    lo, hi = re.split(r"[–-]", tok)
                    occur.extend((pos, x) for x in range(int(lo.strip()), int(hi.strip()) + 1))
                elif tok.isdigit():
                    occur.append((pos, int(tok)))
        for m in re.finditer(rf"{_sup_guard}\b{label}\s+(\d+)[a-z]?(?![\dA-Za-z])", body, re.I):
            occur.append((m.start(), int(m.group(1))))
        first_appear = []
        for _, n in sorted(occur, key=lambda x: x[0]):
            if n not in first_appear:
                first_appear.append(n)
        expected = 1
        bad = None
        for n in first_appear:
            if n != expected:
                bad = n
                break
            expected += 1
        if bad is not None and first_appear:
            issues.append({"severity": "P1", "type": "figure_numbering",
                           "msg": f"{label} 编号未按首次出现顺序递增（乱序/跳号）：首次出现序列 {first_appear[:12]}"})
    # 图块与图注紧邻性：近旁 3 个非空行内有 Figure 图注但不紧邻 → P2（图注在前或集中在 Legends 节的写法不报）
    lines = md_text.splitlines()
    cap_pat = re.compile(r"^\s*(?:\*\*|\*)?\s*Figure\s*\d", re.I)
    gap_n = 0
    for i, l in enumerate(lines):
        if not re.search(r"!\[[^\]]*\]\([^)]*\)", l):
            continue
        j = i + 1
        nonblank = []
        while j < len(lines) and len(nonblank) < 3:
            if lines[j].strip():
                nonblank.append(lines[j])
            j += 1
        if not nonblank:
            continue
        if cap_pat.match(nonblank[0]):
            continue  # 图注紧邻，正常
        if any(cap_pat.match(x) for x in nonblank[1:]):
            gap_n += 1  # 图注在近旁但中间隔了其他内容
    if gap_n:
        issues.append({"severity": "P2", "type": "figure_caption_gap",
                       "msg": f"{gap_n} 处 ![...](...) 图块与其后图注行之间隔有其他段落——导出时可能被拆块"})
    return issues


def submission_preflight_check(project_dir):
    """D3 [P1/P2] 投稿要素 preflight（只查 manuscript/main.md）：

    - 缺 Keywords/Key words 节 → P1
    - 作者信息区块含明显占位符（___/TODO/Author Name/待填）→ P1
    - Data Availability 声明缺失 → P2
    """
    project = Path(project_dir)
    manuscript = project / "manuscript" / "main.md"
    if not manuscript.exists():
        return []
    text = manuscript.read_text(encoding="utf-8", errors="replace")
    issues = []
    # 1) Keywords 节（标题形式或加粗冒号行形式）
    kw_line = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?:keywords?|key\s*words?|关键词)\s*(?:\*\*|__)?\s*[:：]", re.I | re.M)
    kw_head = re.compile(r"^#{1,6}\s*(?:keywords?|key\s*words?|关键词)\s*$", re.I | re.M)
    if not (kw_line.search(text) or kw_head.search(text)):
        issues.append({"severity": "P1", "type": "submission_keywords",
                       "msg": "未找到 Keywords/Key words 节——投稿必备要素，请补充"})
    # 2) 作者信息区块占位符：一级标题与 Abstract 之间的区域
    lines = text.splitlines()
    title_idx = next((i for i, l in enumerate(lines) if re.match(r"^#\s+", l)), None)
    abs_idx = next((i for i, l in enumerate(lines)
                    if re.match(r"^\s*(?:#{1,6}\s*)?(?:abstract|摘要)\b", l, re.I)), None)
    if title_idx is not None:
        end_idx = abs_idx if (abs_idx is not None and abs_idx > title_idx) else min(title_idx + 40, len(lines))
        region = "\n".join(lines[title_idx + 1:end_idx])
        if re.search(r"___|\bTODO\b|\bAuthor\s+Name\b|待填|\[TBD", region, re.I):
            issues.append({"severity": "P1", "type": "submission_author_placeholder",
                           "msg": "作者信息区块仍含明显占位符（___/TODO/Author Name/待填）——投稿前须填写真实作者与单位信息"})
    # 3) Data Availability 声明（含常见变体：Data and materials availability / Availability of data and materials 等）
    if not re.search(r"data\s+availability|availability\s+of\s+(?:data|materials)|"
                     r"data\s+(?:and|or)\s+materials?\s+availability|"
                     r"availability\s+of\s+data\s+and\s+(?:materials|code)|"
                     r"数据(?:与材料)?可用性|数据可及性", text, re.I):
        issues.append({"severity": "P2", "type": "submission_data_availability",
                       "msg": "缺少 Data Availability 声明——多数期刊投稿要求该声明（无原始数据可写「不适用」）"})
    return issues


def species_italics_consistency_check(md_text):
    """F1 [P1] 同一拉丁学名同时存在斜体与正体两种写法。

    只报"同一双名(含缩写属名)两种写法并存"的确定性问题:
    普通英文短语(如 Random mutagenesis)因从未出现斜体写法, 不会被误判。
    来源: 2026-08-23 一篇真实综述稿件的人工审稿 F1(全文 153 处学名未斜体)。"""
    issues = []
    if not md_text:
        return issues
    italic = set(re.findall(r"\*([A-Z][a-z]{2,}|[A-Z]\.) ([a-z]{3,})\*", md_text))
    if not italic:
        return issues
    plain_pat = re.compile(r"(?<![\w*])([A-Z][a-z]{2,}|[A-Z]\.) ([a-z]{3,})(?![\w*])")
    for m in plain_pat.finditer(md_text):
        pair = (m.group(1), m.group(2))
        if pair in italic:
            phrase = f"{m.group(1)} {m.group(2)}"
            issues.append({
                "severity": "P1", "type": "species_italics_inconsistent",
                "msg": f"拉丁学名「{phrase}」在稿件中同时存在斜体与正体两种写法,请统一为斜体(全文)",
            })
            if len(issues) >= 8:
                issues.insert(0, {"severity": "P1", "type": "species_italics_inconsistent",
                                  "msg": f"学名斜体/正体不一致共多处(最多展示 8 处),建议全文统一处理"})
                break
    # 去重: 同一学名只报一次
    seen, dedup = set(), []
    for it in issues:
        key = it["msg"].split("「")[-1].split("」")[0] if "「" in it["msg"] else it["msg"]
        if key not in seen:
            seen.add(key)
            dedup.append(it)
    return dedup


def empty_citation_check(md_text):
    """F8 [P1] 空引用占位 () / (()) 与双括号引用 ((n)。

    引文绑定未完成的投稿硬伤, 必须清零。来源: 2026-08-23 真实审稿反馈 F8。"""
    issues = []
    if not md_text:
        return issues
    body = _body_without_refs(md_text)
    for m in re.finditer(r"\(\s*\)|\(\(\s*\)\)|\(\(\d", body):
        ctx = body[max(0, m.start() - 40):m.start() + 12].replace("\n", " ")
        issues.append({
            "severity": "P1", "type": "empty_citation",
            "msg": f"空/异常引用占位「{m.group(0)}」(引文绑定未完成): ...{ctx}...",
        })
    return issues


def citation_first_appearance_check(md_text):
    """F9 [P2 观察] 数字引用编号应按正文首次出现顺序递增(Vancouver 顺序编码制)。

    首现顺序非递增意味着编号与出现次序脱节(如后期插入文献未重排)。
    来源: 2026-08-23 真实审稿反馈 F9(105 条文献 15 处首现顺序违规)。"""
    issues = []
    if not md_text or _citation_style(md_text) != "numeric":
        return issues
    body = _body_without_refs(md_text)
    first = {}
    for m in re.finditer(r"\((\d+(?:\s*(?:-{1,2}|[–—]|,)\s*\d+)*)(?:\s*;[^)]*)?\)", body):
        for part in re.split(r"\s*,\s*", m.group(1)):
            part = part.strip()
            rm = re.match(rf"({_NUM})\s*{_SEP}\s*({_NUM})$", part)
            if rm:
                a, b = int(rm.group(1)), int(rm.group(2))
                if a <= b and b - a <= 200:
                    for n in range(a, b + 1):
                        first.setdefault(n, m.start())
            elif part.isdigit():
                first.setdefault(int(part), m.start())
    if not first:
        return issues
    order = [n for n, _ in sorted(first.items(), key=lambda kv: kv[1])]
    viol = [(order[i], order[i + 1]) for i in range(len(order) - 1) if order[i] > order[i + 1]]
    if viol:
        issues.append({
            "severity": "P2", "type": "citation_first_appearance_order",
            "msg": f"编号未按首次出现顺序递增({len(viol)} 处, 如 {viol[:5]}); "
                   f"顺序编码制要求首现顺序为 1,2,3,...,定稿时应统一重排",
        })
    return issues


def manuscript_ref_list_sync_check(md_text):
    """[P1] 正文引用编号集合 vs 文末 References 节条目一致性(稿内自洽)。

    与 citation_count_consistency_check(对 framework 池)互补: 本检查只看稿件自身,
    池未同步时稿内仍可自洽; 稿内不自洽则一定是引用或列表缺失。
    背景: 2026-08-23 HS 修订中重排编号后池未同步, 现有检查只报了池层。"""
    issues = []
    if not md_text or _citation_style(md_text) != "numeric":
        return issues
    cited = _cited_numbers(md_text)
    if not cited:
        return issues
    m = re.search(r"#{1,6}\s*(?:references|参考文献)\s*\n", md_text, re.I)
    if not m:
        return issues
    refs = re.split(r"\n#{1,6}\s", md_text[m.end():])[0]
    ids = set()
    for pm in re.finditer(r"^\s*\[?(\d{1,3})[\.\]]\s", refs, re.M):
        ids.add(int(pm.group(1)))
    if not ids:
        return issues
    if len(ids) != len(cited):
        missing = sorted(cited - ids)[:8]
        extra = sorted(ids - cited)[:8]
        msg = f"正文引用 {len(cited)} 条 vs 文末列表 {len(ids)} 条"
        if missing:
            msg += f"; 引用了但列表缺失: {missing}"
        if extra:
            msg += f"; 列表有但正文未引用: {extra}"
        issues.append({"severity": "P1", "type": "manuscript_ref_list_sync", "msg": msg})
    elif max(cited) > max(ids):
        issues.append({
            "severity": "P1", "type": "manuscript_ref_list_sync",
            "msg": f"正文最大引用编号 {max(cited)} 超出文末列表最大编号 {max(ids)}",
        })
    return issues


def academic_norm_check(md_text, ref_entries=None):
    """聚合 E31-E45 学术规范检查，供 quality_check / review-auto 统一调用。"""
    issues = []
    for fn in (
        # B1-B6 确定性检查补盲（2026-08-21）：text 类随主文本链，异常隔离同下
        mechanical_check,
        lambda t: reference_completeness_check(ref_entries, t) if ref_entries else [],
        lambda t: citation_context_check(t, ref_entries),
        section_scope_check,
        figure_numbering_check,
        cross_species_inference_check,
        lambda t: title_content_match_check(t),
        systematic_review_claim_check,
        lambda t: citation_count_consistency_check(t, ref_entries),
        lambda t: same_author_year_suffix_check(ref_entries, t) if ref_entries else [],
        overstatement_check,
        review_depth_check,
        roadmap_timeline_check,
        latex_residue_check,
        bom_check,
        cross_section_contradiction_check,
        species_taxonomy_check,
        table_content_consistency_check,
        pathway_attribution_check,
        lambda t: citation_suffix_sync_check(t, ref_entries),
        # 2026-08-23 审稿缺口补齐(F1/F8/F9 + 稿内一致性)
        species_italics_consistency_check,
        empty_citation_check,
        citation_first_appearance_check,
        manuscript_ref_list_sync_check,
    ):
        try:
            issues.extend(fn(md_text) or [])
        except Exception:
            continue  # 单项检查异常不影响其他项
    return issues




if __name__ == "__main__":
    main()
