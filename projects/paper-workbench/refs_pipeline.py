# -*- coding: utf-8 -*-
"""refs_pipeline.py — 参考文献核验/修复/CSL 格式化管线

开源集成（2026-08-22 从 GitHub 生态引入）：
- habanero      CrossRef 官方 Python 客户端（标题反查/元数据补全）
- citeproc-py   CSL 引文格式化引擎
- pybtex        BibTeX 底层解析（citeproc-py 的 bibtex source 依赖）

CLI:
    python refs_pipeline.py fix <references.md> [--apply] [--report PATH]
    python refs_pipeline.py format <references.md> --style crib [--out PATH]
    python refs_pipeline.py verify <references.md> [--json]

说明:
- fix: 全量 DOI 核验 → 对 no_doi/mismatch/缺年份条目按标题反查 CrossRef，
       生成修订报告；--apply 时写回 ```bibtex 块（保留条目顺序与键名）。
- format: 用 CSL 样式渲染参考文献列表。--style 可传 .csl 路径或短名
       （短名在 templates/csl/<name>.csl 查找，缺失时尝试从
       citation-style-language/styles 下载；crib 无官方样式时回退 vancouver）。
- verify: 复用 toolbox.verify_bibtex 的只读核验。

网络限速：CrossRef 请求间隔 >= 0.4s（polite pool）。
"""
from __future__ import annotations

import difflib
import io
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

WORKBENCH_DIR = Path(__file__).resolve().parent
CSL_DIR = WORKBENCH_DIR / "templates" / "csl"
ZOTERO_STYLES = "https://www.zotero.org/styles/"
GITHUB_API_CONTENTS = "https://api.github.com/repos/citation-style-language/styles/contents/"
CROSSREF_API = "https://api.crossref.org"
USER_AGENT = "paper-workbench/1.1 (refs_pipeline; mailto:research@example.com)"
REQUEST_GAP = 1.0  # CrossRef 限速 1 请求/秒（规范要求）
RETRY_TIMES = 3    # 429/5xx/超时指数退避重试

_last_request = 0.0


def _throttle():
    global _last_request
    wait = REQUEST_GAP - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


# ---------------------------------------------------------------- 解析层


def extract_bibtex(text: str) -> str:
    """从 references.md 提取 ```bibtex 围栏内容；无围栏则视全文为 BibTeX。"""
    m = re.search(r"```bibtex\s*(.*?)```", text, re.S)
    return m.group(1) if m else text


def parse_entries(bib_text: str) -> list[dict]:
    """复用 toolbox 的解析（含 fallback），返回条目列表。"""
    sys.path.insert(0, str(WORKBENCH_DIR))
    import toolbox
    entries = toolbox.parse_bibtex(bib_text)
    if entries and isinstance(entries[0], dict) and "error" in entries[0]:
        raise ValueError(f"BibTeX 解析失败: {entries[0]['error']}")
    return entries


def _norm_title(s: str) -> str:
    """标题归一化：去 HTML/CSL 标记（<i>/<scp> 等，替换为空格防词汇黏连）、去非字母数字。"""
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def title_similarity(a: str, b: str) -> float:
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    return max(jaccard, seq)


# ---------------------------------------------------------------- CrossRef 层


def _cr_get(path: str, params: dict | None = None, timeout: int = 25) -> dict | None:
    url = f"{CROSSREF_API}{path}"
    if params:
        from urllib.parse import urlencode
        url += "?" + urlencode(params)
    for attempt in range(RETRY_TIMES):
        _throttle()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as ex:
            if ex.code in (429, 500, 502, 503, 504) and attempt < RETRY_TIMES - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return None
        except Exception:
            if attempt < RETRY_TIMES - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return None
    return None


_HB_CLIENT = "_uninit"


def _hb():
    """habanero（CrossRef 官方客户端）延迟初始化；不可用返回 None（回退直连 REST）。"""
    global _HB_CLIENT
    if _HB_CLIENT == "_uninit":
        try:
            from habanero import Crossref
            _HB_CLIENT = Crossref(mailto="research@example.com", ua_string=USER_AGENT)
        except Exception:
            _HB_CLIENT = None
    return _HB_CLIENT


def verify_doi(doi: str) -> dict | None:
    """DOI → CrossRef 元数据（不存在返回 None）。habanero 优先，直连回退，均带重试。"""
    hb = _hb()
    if hb is not None:
        for attempt in range(RETRY_TIMES):
            _throttle()
            try:
                data = hb.works(ids=doi)
                if data and data.get("status") == "ok":
                    return _msg_to_meta(data.get("message", {}))
                return None  # 明确的 404：条目不存在，无需重试/回退
            except Exception:
                if attempt < RETRY_TIMES - 1:
                    time.sleep(2 ** attempt * 2)
                    continue
                break  # 重试耗尽 → 回退直连（部分代理环境下 habanero 行为不一致）
    data = _cr_get(f"/works/{urllib.request.quote(doi, safe='()')}")
    if not data or data.get("status") != "ok":
        return None
    return _msg_to_meta(data.get("message", {}))


def search_by_title(title: str, first_author: str = "", year: str = "") -> list[dict]:
    """按标题反查 CrossRef，返回候选元数据列表（按相关度排序）。habanero 优先。"""
    q = title if not first_author else f"{title} {first_author}"
    items = None
    hb = _hb()
    if hb is not None:
        _throttle()
        try:
            data = hb.works(query_bibliographic=q, rows=8)
            if data and data.get("status") == "ok":
                items = data.get("message", {}).get("items", [])
        except Exception:
            items = None
    if items is None:
        data = _cr_get("/works", {"query.bibliographic": q, "rows": "8"})
        items = data.get("message", {}).get("items", []) if data and data.get("status") == "ok" else []
    out = []
    for msg in items:
        meta = _msg_to_meta(msg)
        meta["score"] = title_similarity(title, meta.get("title", ""))
        if year and meta.get("year"):
            meta["year_match"] = str(year).strip() == str(meta["year"]).strip()
        out.append(meta)
    out.sort(key=lambda m: m["score"], reverse=True)
    return out


def _msg_to_meta(msg: dict) -> dict:
    issued = msg.get("issued", {}).get("date-parts", [[None]])
    year = issued[0][0] if issued and issued[0] else None
    authors = []
    for a in msg.get("author", []):
        fam, giv = a.get("family", ""), a.get("given", "")
        if fam:
            authors.append(f"{fam}, {giv}" if giv else fam)
    return {
        "doi": msg.get("DOI", ""),
        "title": (msg.get("title") or [""])[0],
        "year": year,
        "journal": (msg.get("container-title") or [""])[0],
        "volume": msg.get("volume", ""),
        "number": msg.get("issue", ""),
        "pages": msg.get("page", "") or msg.get("article-number", ""),
        "authors": authors,
        "type": msg.get("type", ""),
        "publisher": msg.get("publisher", ""),
    }


# ---------------------------------------------------------------- fix 流程

# 视为需修复的触发条件
EDITION_JOURNAL_RE = re.compile(r"^\d+(st|nd|rd|th)\s*ed(ition)?\.?$", re.I)


def _entry_problems(e: dict) -> list[str]:
    probs = []
    if not e.get("doi"):
        probs.append("no_doi")
    if not e.get("year"):
        probs.append("missing_year")
    if e.get("type") == "article" and EDITION_JOURNAL_RE.match(e.get("journal", "") or ""):
        probs.append("book_as_article")
    if not e.get("title"):
        probs.append("missing_title")
    return probs


def build_fix_plan(entries: list[dict], max_lookups: int = 60) -> list[dict]:
    """对全部条目核验，输出修订计划列表。

    每条计划: {key, action, changes:{field: (old, new)}, reason, confidence}
    action: verified / auto_fix / manual / not_found
    """
    plan = []
    lookups = 0
    for e in entries:
        key = e.get("id", "?")
        probs = _entry_problems(e)
        doi = (e.get("doi") or "").strip()

        # 1) 有 DOI：先核验
        meta = None
        if doi:
            meta = verify_doi(doi)
            if meta:
                title_ok = title_similarity(e.get("title", ""), meta["title"]) >= 0.80
                year_ok = (not e.get("year")) or (str(e["year"]).strip() == str(meta.get("year") or "").strip())
                if title_ok and year_ok:
                    # DOI 核验通过：仅补全缺失的形态字段（卷/期/页），已有字段不动
                    fills = _fill_missing(e, meta)
                    if "missing_year" in probs and meta.get("year"):
                        fills["year"] = ("", str(meta["year"]))
                    if fills:
                        plan.append({"key": key, "action": "auto_fix", "changes": fills,
                                     "reason": "DOI 核验通过，补全缺失字段",
                                     "confidence": "high"})
                        continue
                    if not probs:
                        plan.append({"key": key, "action": "verified", "changes": {}, "reason": "DOI 核验通过"})
                        continue
                # DOI 指向真实文献但本地字段有误 → 以 CrossRef 为准修正
                if title_ok and lookups < max_lookups:
                    changes = _diff_fields(e, meta)
                    if changes:
                        plan.append({"key": key, "action": "auto_fix", "changes": changes,
                                     "reason": "DOI 有效但本地字段与 CrossRef 不一致",
                                     "confidence": "high"})
                        continue
            if meta is None and probs and lookups < max_lookups:
                meta = None  # DOI 失效，走标题反查
            elif meta is not None:
                # DOI 有效但标题对不上：挂名引用嫌疑，需人工
                plan.append({"key": key, "action": "manual", "changes": {},
                             "reason": f"DOI 指向其他文献（CrossRef 标题: {meta['title'][:80]}...）",
                             "crossref": meta})
                continue

        # 2) 需标题反查（no_doi / mismatch / 缺字段）
        if lookups >= max_lookups:
            plan.append({"key": key, "action": "manual", "changes": {}, "reason": "超出反查预算，待人工"})
            continue
        lookups += 1
        if not e.get("title"):
            plan.append({"key": key, "action": "manual", "changes": {}, "reason": "无标题，无法反查"})
            continue
        first_author = (e.get("author", "") or "").split(",")[0].split(" and ")[0]
        cands = search_by_title(e["title"], first_author, e.get("year", ""))
        best = cands[0] if cands and cands[0]["score"] >= 0.80 else None
        if not best:
            plan.append({"key": key, "action": "not_found", "changes": {},
                         "reason": "CrossRef 标题反查无高置信匹配（可能为书籍/灰色文献）",
                         "candidates": [c["title"][:80] for c in cands[:2]]})
            continue
        best["doi"] = _clean_doi(best.get("doi", ""))
        changes = _diff_fields(e, best)
        # 书籍条目类型修正
        if e.get("type") == "article" and best.get("type") in ("book", "monograph", "book-section"):
            changes["ENTRYTYPE"] = ("article", "book")
        conf = "high" if best["score"] >= 0.92 else "medium"
        year_now = str(e.get("year") or "").strip()
        if year_now and best.get("year") and year_now != str(best["year"]):
            conf = "medium"  # 年份变化需人工留意
        plan.append({"key": key, "action": "auto_fix", "changes": changes,
                     "reason": f"标题反查命中（相似度 {best['score']:.2f}）",
                     "confidence": conf, "crossref": best})
    return plan


def _fill_missing(e: dict, meta: dict) -> dict:
    """仅补全本地缺失的形态字段（卷/期/页），不动已有字段。"""
    fills = {}
    for local, src in (("volume", "volume"), ("number", "number"), ("pages", "pages")):
        new = str(meta.get(src) or "").strip()
        if new and not str(e.get(local) or "").strip():
            fills[local] = ("", new)
    return fills


def _clean_doi(doi: str) -> str:
    """去除 ACS 补充材料后缀（.s001/.s002…）：反查可能命中 supporting info 条目。"""
    return re.sub(r"\.s\d{3}$", "", doi or "", flags=re.I)


def _diff_fields(e: dict, meta: dict) -> dict:
    """比较本地条目与 CrossRef 元数据，返回需更新字段 {field: (old, new)}。"""
    changes = {}
    cand = {
        "doi": meta.get("doi", ""),
        "title": meta.get("title", ""),
        "year": str(meta.get("year") or ""),
        "journal": meta.get("journal", ""),
        "volume": meta.get("volume", ""),
        "number": meta.get("number", ""),
        "pages": meta.get("pages", ""),
    }
    for field, new in cand.items():
        old = str(e.get(field, "") or "").strip()
        if new and (not old or old.lower() != str(new).lower()):
            # 标题只在大改时替换（相似度 <0.95 视为差异但保留人工判断空间，仍更新）
            changes[field] = (old, str(new))
    if meta.get("authors"):
        old_a = str(e.get("author", "") or "").strip()
        new_a = " and ".join(meta["authors"])
        if not old_a or "et al" in old_a.lower() or old_a.count(",") == 0 and old_a.count(" ") <= 3:
            changes["author"] = (old_a, new_a)
        elif title_similarity(old_a.replace(" and ", " "), new_a.replace(" and ", " ")) < 0.5:
            changes["author"] = (old_a, new_a)
    return changes


# ---------------------------------------------------------------- 写回层


def apply_plan(bib_text: str, plan: list[dict]) -> tuple[str, list[str]]:
    """把 auto_fix 计划应用到 BibTeX 文本；返回 (新文本, 应用日志)。"""
    applied = []
    out = bib_text
    for p in plan:
        if p["action"] != "auto_fix" or not p["changes"]:
            continue
        key = p["key"]
        # 类型变更（@ 前缀在块外，先在全文替换再定位块）
        if "ENTRYTYPE" in p["changes"]:
            old_t, new_t = p["changes"]["ENTRYTYPE"]
            out = re.sub(r"@" + re.escape(old_t) + r"(\s*\{\s*" + re.escape(key) + r"\s*,)",
                         "@" + new_t + r"\1", out, count=1, flags=re.I)
        block_re = re.compile(r"(@\w+)\s*\{\s*" + re.escape(key) + r"\s*,", re.I)
        m = block_re.search(out)
        if not m:
            applied.append(f"[skip] {key}: 未找到条目块")
            continue
        # 定位条目块结尾（配平花括号）
        start = m.start()
        i = out.index("{", m.start(1))
        depth, j = 0, i
        while j < len(out):
            if out[j] == "{":
                depth += 1
            elif out[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block = out[i:j + 1]
        new_block = block
        for field, (old, new) in p["changes"].items():
            if field == "ENTRYTYPE":
                continue
            fpat = re.compile(r"(\b" + field + r"\s*=\s*[\{\"])([^\}\"]*)([\}\"])", re.I)
            fm = fpat.search(new_block)
            if fm:
                new_block = new_block[:fm.start(2)] + new + new_block[fm.end(2):]
            else:
                # 字段不存在 → 追加（在最后一个 } 前，先去掉尾部多余逗号）
                core = new_block[:-1].rstrip().rstrip(",")
                new_block = core + f",\n  {field} = {{{new}}}\n}}"
        out = out[:i] + new_block + out[j + 1:]
        applied.append(f"[fix] {key}: {', '.join(p['changes'].keys())}")
    return out, applied


# ---------------------------------------------------------------- CSL 格式化


def _http_get(url: str, timeout: int = 30) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def resolve_csl(style: str) -> Path:
    """解析 CSL 样式：短名 → templates/csl/<name>.csl；缺失则在线下载。

    下载源优先级：Zotero 样式库 → GitHub API（raw.githubusercontent 在部分网络不可达）。
    crib 无专属 CSL，映射到 Taylor & Francis NLM 数字制（CRIB 投稿实际格式）。
    """
    import base64
    CSL_DIR.mkdir(parents=True, exist_ok=True)
    name = style.lower().strip()
    if name.endswith(".csl") and Path(name).exists():
        return Path(name)
    aliases = {
        "crib": "taylor-and-francis-national-library-of-medicine",
        "vancouver": "vancouver",
    }
    name = aliases.get(name, name.replace(".csl", ""))
    local = CSL_DIR / f"{name}.csl"
    if local.exists():
        return local
    # 源 1：Zotero 样式库（覆盖广、国内可达）
    data = _http_get(ZOTERO_STYLES + name)
    # 源 2：GitHub API（base64 编码内容）
    if not data:
        raw = _http_get(GITHUB_API_CONTENTS + f"{name}.csl")
        if raw:
            try:
                data = base64.b64decode(json.loads(raw)["content"])
            except Exception:
                data = None
    if data and b"<style" in data[:2000]:
        local.write_bytes(data)
        return local
    if name != "vancouver":
        print(f"[warn] CSL 样式 {name} 获取失败，回退 vancouver", file=sys.stderr)
        return resolve_csl("vancouver")
    raise RuntimeError("无法获取任何 CSL 样式（含 vancouver 回退），请检查网络")


def format_refs_csl(bib_text: str, style: str, keys_order: list[str] | None = None) -> list[str]:
    """用 citeproc-py 渲染参考文献列表，返回字符串列表（与 keys_order 对应）。"""
    from citeproc import CitationStylesStyle, CitationStylesBibliography, Citation, CitationItem
    from citeproc.formatter import plain as plain_formatter
    from citeproc.source.bibtex import BibTeX as CiteprocBibTeX

    csl_path = resolve_csl(style)
    tmp_bib = WORKBENCH_DIR / ".tmp_refs_pipeline.bib"
    tmp_bib.write_text(bib_text, encoding="utf-8")
    try:
        source = CiteprocBibTeX(str(tmp_bib))
        bib_style = CitationStylesStyle(str(csl_path), validate=False)
        bibliography = CitationStylesBibliography(bib_style, source, plain_formatter)
        keys = keys_order or [e["id"] for e in parse_entries(bib_text)]
        valid = set(source.keys())
        for k in keys:
            if k in valid:
                bibliography.register(Citation([CitationItem(k)]))
        lines = [str(s) for s in bibliography.bibliography()]
        idx = 0
        out = []
        for k in keys:
            if k not in valid:
                out.append(f"[{k}] (解析失败，请检查条目)")
            else:
                out.append(lines[idx])
                idx += 1
        return out
    finally:
        if tmp_bib.exists():
            tmp_bib.unlink()


# ---------------------------------------------------------------- 报告


def render_report(plan: list[dict], applied: list[str], refs_path: str) -> str:
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        "# 参考文献修订报告（refs_pipeline）",
        "",
        f"> 生成: {now} | 目标: {refs_path}",
        f"> 工具: CrossRef API（habanero 同源）+ 标题反查 | 限速 {REQUEST_GAP}s/请求",
        "",
        "## 汇总",
        "",
        f"- 条目总数: {len(plan)}",
        f"- verified: {sum(1 for p in plan if p['action'] == 'verified')}",
        f"- auto_fix: {sum(1 for p in plan if p['action'] == 'auto_fix')}",
        f"- manual/not_found: {sum(1 for p in plan if p['action'] in ('manual', 'not_found'))}",
        "",
        "## 修订明细",
        "",
        "| 条目 | 动作 | 置信 | 原因 | 字段变更 |",
        "|------|------|------|------|---------|",
    ]
    for p in plan:
        if p["action"] == "verified":
            continue
        ch = "; ".join(f"{f}: {o[:40]} → {n[:40]}" for f, (o, n) in p["changes"].items()) or "-"
        lines.append(f"| {p['key']} | {p['action']} | {p.get('confidence', '-')} | {p['reason'][:60]} | {ch[:120]} |")
    if applied:
        lines += ["", "## 应用日志", ""] + [f"- {a}" for a in applied]
    manual = [p for p in plan if p["action"] in ("manual", "not_found")]
    if manual:
        lines += ["", "## 待人工处理", ""]
        for p in manual:
            lines.append(f"- **{p['key']}**: {p['reason']}")
            for c in p.get("candidates", []):
                lines.append(f"  - 候选: {c}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- CLI


def cmd_fix(args):
    text = Path(args.file).read_text(encoding="utf-8")
    # 预清理：ACS 补充材料 DOI 后缀（.s001…）不应作为正式引用 DOI（2026-08-22）
    n_sfx = len(re.findall(r"doi = \{[^}]*\.s\d{3}\}", text, re.I))
    if n_sfx:
        text = re.sub(r"(doi = \{[^}]*)\.s\d{3}(\})", r"\1\2", text, flags=re.I)
        Path(args.file).write_text(text, encoding="utf-8")
        print(f"预清理：去除 {n_sfx} 个 .s00x 补充材料 DOI 后缀")
    bib = extract_bibtex(text)
    entries = parse_entries(bib)
    print(f"解析到 {len(entries)} 条，开始全量核验与标题反查（限速 {REQUEST_GAP}s）...")
    plan = build_fix_plan(entries, max_lookups=args.max)
    applied = []
    if args.apply:
        new_bib, applied = apply_plan(bib, plan)
        if "```bibtex" in text:
            text = re.sub(r"(```bibtex\s*).*?(```)", lambda m: m.group(1) + new_bib + "\n" + m.group(2), text, count=1, flags=re.S)
        else:
            text = new_bib
        Path(args.file).write_text(text, encoding="utf-8")
    report = render_report(plan, applied, str(args.file))
    rpt = Path(args.report) if args.report else Path(args.file).parent.parent / "review" / "ref-fixes.md"
    rpt.parent.mkdir(parents=True, exist_ok=True)
    rpt.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n报告已写入: {rpt}")
    if not args.apply:
        print("（预览模式；确认后用 --apply 写回）")


def cmd_format(args):
    text = Path(args.file).read_text(encoding="utf-8")
    bib = extract_bibtex(text)
    entries = parse_entries(bib)
    lines = format_refs_csl(bib, args.style, [e["id"] for e in entries])
    # CSL numeric 样式自带编号，不重复追加；无编号时补序号
    numbered = any(re.match(r"^\[?\d+\]", ln.strip()) for ln in lines[:3] if ln.strip())
    if numbered:
        out_text = "\n".join(lines)
    else:
        out_text = "\n".join(f"[{i + 1}] {ln}" for i, ln in enumerate(lines))
    if args.out:
        Path(args.out).write_text(out_text + "\n", encoding="utf-8")
        print(f"已渲染 {len(lines)} 条 → {args.out}")
    else:
        print(out_text)


def cmd_verify(args):
    sys.path.insert(0, str(WORKBENCH_DIR))
    import toolbox
    text = Path(args.file).read_text(encoding="utf-8")
    results = toolbox.verify_bibtex(extract_bibtex(text))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return
    bad = [r for r in results if r.get("status") not in ("verified",)]
    print(f"共 {len(results)} 条，异常 {len(bad)} 条：")
    for r in bad:
        print(f"  [{r['status']}] {r['id']}: {r.get('note', '')}")


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="refs_pipeline", description="参考文献核验/修复/CSL 格式化管线")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("fix", help="全量核验 + 标题反查修复")
    p.add_argument("file", help="references.md 路径")
    p.add_argument("--apply", action="store_true", help="写回修复（默认预览）")
    p.add_argument("--report", default=None, help="报告输出路径")
    p.add_argument("--max", type=int, default=60, help="标题反查上限")
    p.set_defaults(fn=cmd_fix)

    p = sub.add_parser("format", help="CSL 样式渲染参考文献")
    p.add_argument("file", help="references.md 路径")
    p.add_argument("--style", default="crib", help="CSL 短名或 .csl 路径")
    p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_format)

    p = sub.add_parser("verify", help="只读核验（复用 toolbox.verify_bibtex）")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_verify)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
