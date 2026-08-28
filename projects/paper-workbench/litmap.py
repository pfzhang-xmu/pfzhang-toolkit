# -*- coding: utf-8 -*-
"""litmap.py — 选题文献地图（主题聚类 + 年份分布 + 研究缺口候选）

定位:
- flow ①确定方向 / ③检索文献 的可选前置增强：基于项目已有文献池
  （默认 framework/references.md 的 ```bibtex 块）做纯标准库的主题聚类、
  年份分布与缺口候选识别，为 research/literature.md 撰写与选题判断提供输入；
- 聚类：标题（+abstract 若有）分词去停用词 → TF 向量 → 余弦相似 → 凝聚聚类
  （average-linkage，n≤120 秒级）；
- 无被引数据可用：作者/期刊共现仅按出现频次给出，显式标注「近似」；
- 缺口候选全部为确定性规则产出，每条标「待人判，人工裁定为准」；
- 增量缓存 research/litmap-cache.json（DOI→向量/簇标签），新增条目增量归簇，
  池变更 >20% 或 --rebuild 全量重算。

CLI:
    python litmap.py [--refs PATH] [--query KW] [--limit N] [--standalone]
                     [--rebuild] [--dir PROJ] [--out PATH]

产物（只写新文件，不覆盖 literature.md）:
    research/litmap.md          主题聚类（簇 1..N）+ 研究空白
    research/litmap-cache.json  增量缓存

退出码: 0=成功；1=输入缺失/检索失败/参数错误。纯标准库，零第三方依赖。
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

WORKBENCH_DIR = Path(__file__).resolve().parent
CACHE_SCHEMA = 1
MERGE_THRESHOLD = 0.13     # 凝聚聚类平均链接相似度阈值
MAX_CLUSTERS = 8           # 簇数上限（超出继续强制合并）
POOL_CHANGE_LIMIT = 0.20   # 池变更比例上限，超过则全量重算

# 内置小停用词表（学术标题常见虚词/泛义词）
STOPWORDS = set("""
a an the and or of for in on to with by from as at is are was were be been being
this that these those it its we our us they their you your he she his her not no
nor so such into over under between across per via using use used uses based study
studies paper papers work works research novel new towards toward approach more most
less than then also can could may might will would should do does did done have has
had having effect effects role case application applications review survey overview
analysis method methods model models data framework
""".split())

REVIEW_TITLE_RE = re.compile(
    r"\b(review|survey|overview|advances|progress|perspective|tutorial)\b", re.I)
EMPIRICAL_RE = re.compile(
    r"\b(experiment(al|s)?|empirical|benchmark|dataset|implementation|demonstrate|clinical|field trial)\b", re.I)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-']+")


# ─────────────────────────── 文本向量化 ───────────────────────────


def _stem(w):
    """极轻量词干化：复数/ies→y，仅用于聚类归并（确定性）。"""
    if w.endswith("us"):  # 拉丁词尾保留（aspergillus 等学名不截断）
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def tokenize(text):
    """小写分词 → 去停用词/短词/纯数字 → 轻量词干化，返回 Counter。"""
    c = Counter()
    for w in _TOKEN_RE.findall((text or "").lower()):
        w = w.strip("-'")
        if len(w) < 3 or w.isdigit() or w in STOPWORDS:
            continue
        c[_stem(w)] += 1
    return c


def doc_vector(entry):
    """标题（权重 2）+ abstract（若有，权重 1）→ 稀疏 TF 向量。"""
    v = Counter()
    for tok, n in tokenize(entry.get("title") or "").items():
        v[tok] += 2 * n
    for tok, n in tokenize(entry.get("abstract") or "").items():
        v[tok] += n
    return dict(v)


def cosine(a, b):
    if not a or not b:
        return 0.0
    num = sum(a[t] * b[t] for t in a.keys() & b.keys())
    da = math.sqrt(sum(x * x for x in a.values()))
    db = math.sqrt(sum(x * x for x in b.values()))
    return num / (da * db) if da and db else 0.0


# ─────────────────────────── 凝聚聚类 ───────────────────────────


def agglomerate(vecs, threshold=MERGE_THRESHOLD, max_clusters=MAX_CLUSTERS):
    """average-linkage 凝聚聚类 → 成员索引列表（按簇大小降序）。"""
    n = len(vecs)
    if n == 0:
        return []
    if n == 1:
        return [[0]]
    csim = {}
    for i in range(n):
        for j in range(i + 1, n):
            csim[(i, j)] = cosine(vecs[i], vecs[j])
    members = {i: [i] for i in range(n)}
    sizes = {i: 1 for i in range(n)}
    active = list(range(n))
    nxt = n
    while len(active) > 1:
        best = None
        for x in range(len(active)):
            for y in range(x + 1, len(active)):
                a, b = active[x], active[y]
                s = csim[(min(a, b), max(a, b))]
                if best is None or s > best[0]:
                    best = (s, a, b)
        s, a, b = best
        if s < threshold and len(active) <= max_clusters:
            break
        if len(active) <= 2:
            break
        sa, sb = sizes[a], sizes[b]
        new = nxt
        nxt += 1
        for z in active:
            if z in (a, b):
                continue
            ka, kb = (min(a, z), max(a, z)), (min(b, z), max(b, z))
            csim[(min(new, z), max(new, z))] = (csim[ka] * sa + csim[kb] * sb) / (sa + sb)
        members[new] = members[a] + members[b]
        sizes[new] = sa + sb
        active = [x for x in active if x not in (a, b)] + [new]
    return sorted((members[a] for a in active), key=lambda c: -len(c))


def centroid(vecs, indices):
    c = Counter()
    for i in indices:
        c.update(vecs[i])
    return dict(c)


def cluster_label(indices, entries):
    """簇标签：簇内标题高频词 top3；无有效词时回退说明。"""
    c = Counter()
    for i in indices:
        c.update(tokenize(entries[i].get("title") or ""))
    # 平票时按词形字典序升序二级排序，保证簇标题确定性（增量与全量路径一致）
    top = [t for t, _n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
    return " / ".join(top) if top else "(非英文标题或无有效词)"


# ─────────────────────────── 文献池装载 ───────────────────────────


def load_refs_md(path):
    """解析 references.md 的 ```bibtex 块（复用 toolbox.parse_bibtex，仿 refs_pipeline）。"""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    m = re.search(r"```bibtex\s*(.*?)```", text, re.S)
    bib = m.group(1) if m else text
    sys.path.insert(0, str(WORKBENCH_DIR))
    import toolbox
    entries = toolbox.parse_bibtex(bib)
    if entries and isinstance(entries[0], dict) and "error" in entries[0]:
        raise ValueError(f"BibTeX 解析失败: {entries[0]['error']}")
    return entries


def load_query(query, limit):
    """--query 在线检索补充池（走 toolbox.search_literature；失败明确报错）。"""
    sys.path.insert(0, str(WORKBENCH_DIR))
    try:
        import toolbox
        hits = toolbox.search_literature(query, limit=limit)
    except Exception as e:
        raise RuntimeError(f"在线检索失败: {e}") from e
    ok = []
    for i, h in enumerate(hits or [], 1):
        if not h or h.get("error") or not (h.get("title") or "").strip():
            continue
        ok.append({
            "id": f"q{i}",
            "title": h.get("title", ""),
            "year": str(h.get("year") or ""),
            "doi": h.get("doi", "") or "",
            "journal": h.get("venue") or "",
            "author": " and ".join(h.get("authors") or []) or "",
            "type": "article",
        })
    if not ok:
        raise RuntimeError(f"在线检索无有效结果（query={query!r}）：网络不可达或全部源报错")
    return ok


def entry_key(e):
    doi = (e.get("doi") or "").strip().lower()
    return f"doi:{doi}" if doi else f"key:{(e.get('id') or '').strip()}"


# ─────────────────────────── 缺口候选（确定性规则） ───────────────────────────


def _is_review(e):
    return ((e.get("type") or "").lower() in ("review", "incollection")
            or bool(REVIEW_TITLE_RE.search(e.get("title") or "")))


def find_gaps(clusters, entries, labels):
    """三类确定性规则 → 缺口候选，每条带「待人判，人工裁定为准」标注。"""
    gaps = []
    years = [int(e.get("year") or 0) for e in entries]
    valid = [y for y in years if 1900 < y <= 2100]
    if not valid or len(entries) < 3:
        return gaps
    ymin, ymax = min(valid), max(valid)
    mid = (ymin + ymax) // 2
    recent_cut = ymax - 2

    for ci, members in enumerate(clusters):
        yrs = [int(entries[i].get("year") or 0) for i in members]
        early = sum(1 for y in yrs if 1900 < y <= mid)
        recent = sum(1 for y in yrs if y >= recent_cut)
        # 规则1：年份×主题矩阵空区——早期有积累、近 3 年无新文献
        if len(members) >= 3 and early >= 2 and recent == 0:
            gaps.append(f"簇 {ci + 1}（{labels[ci]}）早期文献 {early} 篇、近 3 年（≥{recent_cut}）"
                        f"无新增——年份×主题矩阵空区")
        # 规则3：仅 review/survey 型文献、无实证跟进
        n_rev = sum(1 for i in members if _is_review(entries[i]))
        n_emp = sum(1 for i in members if EMPIRICAL_RE.search(entries[i].get("title") or ""))
        if len(members) >= 2 and n_rev / len(members) >= 0.5 and n_emp == 0:
            gaps.append(f"簇 {ci + 1}（{labels[ci]}）{n_rev}/{len(members)} 篇为 review/survey 型，"
                        f"未见实证类文献跟进")

    # 规则2：近年衰退关键词（前半期高频、后半期骤减）
    early_tok, late_tok = Counter(), Counter()
    for e in entries:
        y = int(e.get("year") or 0)
        if not (1900 < y <= 2100):
            continue
        toks = tokenize(e.get("title") or "")
        (early_tok if y <= mid else late_tok).update(toks)
    declining = sorted(t for t, c in early_tok.items()
                       if c >= 3 and late_tok.get(t, 0) <= max(1, c // 4))
    if declining:
        gaps.append("近年衰退关键词: " + ", ".join(declining[:8])
                    + f"（前半期高频、{mid} 年后骤减）")
    return gaps


# ─────────────────────────── 缓存 ───────────────────────────


def load_cache(path):
    if not path.exists():
        return None
    try:
        c = json.loads(path.read_text(encoding="utf-8"))
        if c.get("schema") == CACHE_SCHEMA and isinstance(c.get("entries"), dict):
            return c
    except Exception:
        pass
    return None


def save_cache(path, entries, vecs, clusters):
    idx2cid = {}
    for cid, members in enumerate(clusters, 1):
        for i in members:
            idx2cid[i] = cid
    payload = {
        "schema": CACHE_SCHEMA,
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "threshold": MERGE_THRESHOLD,
        "n": len(entries),
        "entries": {
            entry_key(entries[i]): {"tokens": vecs[i], "cluster": idx2cid[i]}
            for i in range(len(entries))
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ─────────────────────────── 渲染 ───────────────────────────


def _first_author(e):
    a = (e.get("author") or "").strip()
    if not a or a.lower() == "anonymous":
        return "Anonymous"
    return a.split(",")[0].split(" and ")[0].strip() or "Anonymous"


def _year(e):
    y = str(e.get("year") or "").strip()
    return int(y) if y.isdigit() else 0


def render_litmap(entries, clusters, labels, gaps, sources, cache_note):
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    lines = [
        "# 选题文献地图（litmap.md）",
        "",
        f"> 生成: {now} | 来源: {'; '.join(sources)} | 工具: litmap.py（纯标准库，标题词频聚类）",
        f"> {cache_note}",
        "> 与 research/literature.md 互补，不覆盖之；缺口候选均为规则初筛，**待人判，人工裁定为准**。",
        "> 无被引数据——作者/期刊共现轴仅按频次统计，显式标注「近似」。",
        "",
        "## 1. 主题聚类",
        "",
    ]
    for ci, members in enumerate(clusters):
        lines.append(f"### 簇 {ci + 1} — {labels[ci]}（{len(members)} 篇）")
        for i in sorted(members, key=lambda k: -_year(entries[k])):
            e = entries[i]
            doi = (e.get("doi") or "").strip()
            yr = _year(e) or "?"
            jr = (e.get("journal") or "").strip()
            lines.append(f"- **{_first_author(e)} {yr}**"
                         + (f", *{jr}*" if jr else "")
                         + f"：{(e.get('title') or '').strip()}"
                         + (f"（{doi}）" if doi else ""))
        lines.append("")
    # 年份直方图
    years = sorted(y for y in (_year(e) for e in entries) if y)
    if years:
        hist = Counter(years)
        scale = max(1, max(hist.values()) // 30)
        lines += ["## 2. 年份分布", "", "```"]
        for y in range(min(years), max(years) + 1):
            n = hist.get(y, 0)
            lines.append(f"{y} {'█' * max(1, n // scale) if n else ''} {n}" if n else f"{y}")
        lines += ["```", ""]
        # 簇×年份矩阵
        all_years = sorted(hist)
        lines += ["| 簇 | " + " | ".join(str(y) for y in all_years) + " |",
                  "|---|" + "---|" * len(all_years)]
        for ci, members in enumerate(clusters):
            ys = Counter(_year(entries[i]) for i in members)
            lines.append(f"| 簇{ci + 1} " + "".join(f"| {ys.get(y, 0)} " for y in all_years) + "|")
        lines.append("")
    # 共现轴（近似）
    journals = Counter((e.get("journal") or "").strip() for e in entries if (e.get("journal") or "").strip())
    authors = Counter(_first_author(e) for e in entries if _first_author(e) != "Anonymous")
    lines += ["## 3. 共现轴（近似：无被引数据，仅频次）", ""]
    if journals:
        lines.append("- 期刊频次（近似）: " + "; ".join(f"{j}×{c}" for j, c in journals.most_common(5)))
    if authors:
        lines.append("- 第一作者频次（近似）: " + "; ".join(f"{a}×{c}" for a, c in authors.most_common(5)))
    if not journals and not authors:
        lines.append("- （池中无可统计的期刊/作者信息）")
    lines.append("")
    # 研究空白
    lines += ["## 4. 研究空白（Research Gaps · 缺口候选）", ""]
    if gaps:
        for gi, g in enumerate(gaps, 1):
            lines.append(f"{gi}. {g}。**【待人判，人工裁定为准】**")
    else:
        lines.append("- （规则未识别出缺口候选；池规模较小或主题过于集中时属正常）")
    return "\n".join(lines) + "\n"


# ─────────────────────────── 主流程 ───────────────────────────


def resolve_project(dir_arg):
    if dir_arg:
        p = Path(dir_arg).resolve()
        if (p / "state.json").exists():
            return p
        raise SystemExit(f"✗ 指定目录不是论文项目(缺 state.json): {p}")
    cur = Path.cwd().resolve()
    for d in [cur] + list(cur.parents):
        if (d / "state.json").exists():
            return d
    return None


def cmd_litmap(args):
    """供 wb.py 委托与自带 main() 共用的入口。"""
    standalone = getattr(args, "standalone", False)
    proj = None
    if not standalone:
        proj = resolve_project(getattr(args, "dir", None))
        if proj is None:
            print("✗ 未定位到论文项目：请用 --dir 指定，或加 --standalone 独立运行", file=sys.stderr)
            sys.exit(1)

    # 文献池装载
    entries, sources = [], []
    refs_path = getattr(args, "refs", None)
    if refs_path is None and proj is not None:
        cand = proj / "framework" / "references.md"
        refs_path = str(cand) if cand.exists() else None
    if refs_path:
        try:
            entries = load_refs_md(refs_path)
        except Exception as e:
            print(f"✗ 解析 references 失败: {e}", file=sys.stderr)
            sys.exit(1)
        sources.append(refs_path)
    if getattr(args, "query", None):
        try:
            qentries = load_query(args.query, getattr(args, "limit", 20))
        except RuntimeError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)
        entries += qentries
        sources.append(f"--query {args.query!r}")
    if not entries:
        print("✗ 文献池为空：请提供 --refs 或 --query（--standalone 模式不依赖项目）", file=sys.stderr)
        sys.exit(1)
    # 键去重（同 DOI 只保留一条）
    seen, dedup = set(), []
    for e in entries:
        k = entry_key(e)
        if k in seen:
            continue
        seen.add(k)
        dedup.append(e)
    entries = dedup

    # 输出位置
    if getattr(args, "out", None):
        out_md = Path(args.out)
        out_dir = out_md.parent
    elif proj is not None:
        out_dir = proj / "research"
        out_md = out_dir / "litmap.md"
    else:
        out_dir = Path.cwd()
        out_md = out_dir / "litmap.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "litmap-cache.json"

    vecs = [doc_vector(e) for e in entries]
    cache = None if getattr(args, "rebuild", False) else load_cache(cache_path)
    cache_note = "全量重算（--rebuild 或无缓存）"
    if cache is not None:
        cached_keys = set(cache["entries"])
        pool_keys = {entry_key(e) for e in entries}
        change_ratio = len(pool_keys ^ cached_keys) / max(len(pool_keys), 1)
        if change_ratio > POOL_CHANGE_LIMIT:
            cache_note = f"全量重算（池变更 {change_ratio:.0%} > {POOL_CHANGE_LIMIT:.0%}）"
        else:
            # 增量：复用缓存簇分配，新条目归入最近簇心（或新开簇）
            cache_note = f"增量归簇（池变更 {change_ratio:.0%} ≤ {POOL_CHANGE_LIMIT:.0%}）"
            cid_members = {}
            for i, e in enumerate(entries):
                cinfo = cache["entries"].get(entry_key(e))
                if cinfo:
                    cid_members.setdefault(cinfo.get("cluster", 0), []).append(i)
            centroids = {cid: centroid(vecs, mem) for cid, mem in cid_members.items() if mem}
            clusters_map = {cid: list(mem) for cid, mem in cid_members.items() if mem}
            next_cid = max(clusters_map, default=0) + 1
            for i, e in enumerate(entries):
                if entry_key(e) in cache["entries"]:
                    continue
                best_cid, best_s = None, -1.0
                for cid, c in centroids.items():
                    s = cosine(vecs[i], c)
                    if s > best_s:
                        best_s, best_cid = s, cid
                if best_cid is not None and best_s >= MERGE_THRESHOLD:
                    clusters_map[best_cid].append(i)
                else:
                    clusters_map[next_cid] = [i]
                    next_cid += 1
            clusters = sorted(clusters_map.values(), key=lambda c: -len(c))
            labels = [cluster_label(m, entries) for m in clusters]
            gaps = find_gaps(clusters, entries, labels)
            save_cache(cache_path, entries, vecs, clusters)
            out_md.write_text(render_litmap(entries, clusters, labels, gaps, sources, cache_note),
                              encoding="utf-8")
            print(f"✔ litmap（{len(entries)} 条 → {len(clusters)} 簇，{len(gaps)} 条缺口候选，{cache_note}）")
            print(f"  → {out_md}")
            print(f"  → {cache_path}")
            return
    # 全量聚类
    clusters = agglomerate(vecs)
    labels = [cluster_label(m, entries) for m in clusters]
    gaps = find_gaps(clusters, entries, labels)
    save_cache(cache_path, entries, vecs, clusters)
    out_md.write_text(render_litmap(entries, clusters, labels, gaps, sources, cache_note),
                      encoding="utf-8")
    print(f"✔ litmap（{len(entries)} 条 → {len(clusters)} 簇，{len(gaps)} 条缺口候选，{cache_note}）")
    print(f"  → {out_md}")
    print(f"  → {cache_path}")


def main():
    # GBK 控制台兼容：重定向/非 UTF-8 终端下避免 非GBK字符 抛 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser(prog="litmap",
                                 description="选题文献地图：主题聚类 + 年份分布 + 缺口候选（纯标准库）")
    ap.add_argument("--refs", default=None, help="references.md 路径（缺省读项目 framework/references.md）")
    ap.add_argument("--query", default=None, help="在线检索关键词补充池（默认关；失败明确报错）")
    ap.add_argument("--limit", type=int, default=20, help="--query 最大条数（默认 20）")
    ap.add_argument("--standalone", action="store_true", help="独立模式（不依赖论文项目）")
    ap.add_argument("--rebuild", action="store_true", help="忽略增量缓存，全量重算")
    ap.add_argument("--out", default=None, help="litmap.md 输出路径（缺省按项目/当前目录）")
    ap.add_argument("--dir", default=None, help="论文项目目录（默认自动查找）")
    args = ap.parse_args()
    cmd_litmap(args)


if __name__ == "__main__":
    main()
