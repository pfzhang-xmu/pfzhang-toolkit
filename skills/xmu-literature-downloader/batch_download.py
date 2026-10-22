#!/usr/bin/env python3
"""
批量 PDF 下载 — 通过已登录 WebVPN 的 Edge 浏览器并行下载。

v2 改进（2026-10-21）：
  1. 屏蔽宿主注入的 http_proxy —— 否则连本机 CDP 代理(:3456) 会被转发走而失败
  2. 统一走「页面内 fetch 取字节」路径，不再依赖 ~/Downloads 文件监控（消除并发竞态）
  3. 下载后强制校验：%PDF 魔数 + %%EOF 尾标 + 页数 + 首页标题关键词
  4. 失败自动重试（指数退避），并区分失败原因
  5. argparse 化 CLI，支持 --out/--tsv/--limit/--workers/--no-skip
  6. 已存在但校验不通过的文件会被重新下载，而不是被静默跳过

用法:
  python3 batch_download.py --limit 0 --workers 3
  python3 batch_download.py --tsv my.tsv --out ./pdfs
"""

import argparse
import base64
import csv
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

# ---- 关键修复 #1：必须在 import requests 之前清掉代理环境变量 ----
# WorkBuddy / Clash / 公司 VPN 会注入 http_proxy=127.0.0.1:55785，
# 它会拦截对 127.0.0.1:3456 的请求并报 "upstream connect failed"，让人误判代理没启动。
for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

import requests  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent
DEFAULT_TSV = SKILL_DIR / "manual_download.tsv"
DEFAULT_OUT = SKILL_DIR / "pdfs"

PROXY = os.environ.get("XMU_CDP_PROXY", "http://127.0.0.1:3456")
TIMEOUT_NAV = 60
TIMEOUT_EVAL = 180
CHUNK = 512 * 1024

_print_lock = threading.Lock()

# libproxy 智能网关的会话是**浏览器级共享状态**（cookie + CAS 单点登录）。
# 多个标签页同时走 libproxy 会触发统一认证重定向风暴 —— 表现为部分请求被
# 甩到「企业微信扫码登录」页，取字节失败，且失败是随机出现的（重跑就好了）。
# 实测：4 线程并发下 OUP/APS 失败，单线程重跑即通过。
# 解决：libproxy 路线必须**全局串行**。
_libproxy_lock = threading.Lock()


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False          # 双保险：即使 env 又被注入也不走代理
    return s


def safe_print(*args, **kwargs) -> None:
    with _print_lock:
        print(*args, **kwargs)


# ============================ CDP 代理调用 ============================

def proxy_new_tab(url: str = "about:blank") -> str:
    r = _session().get(f"{PROXY}/new", params={"url": url}, timeout=30)
    d = r.json()
    return d.get("targetId") or d.get("id")


def proxy_navigate(tid: str, url: str) -> dict:
    return _session().get(f"{PROXY}/navigate",
                          params={"target": tid, "url": url}, timeout=TIMEOUT_NAV).json()


def proxy_close(tid: str) -> None:
    try:
        _session().get(f"{PROXY}/close", params={"target": tid}, timeout=10)
    except Exception:
        pass


def proxy_eval(tid: str, js: str, timeout: int = TIMEOUT_EVAL) -> Any:
    """执行 JS 并把结果解包成 python 值。"""
    r = _session().post(f"{PROXY}/eval", params={"target": tid}, data=js, timeout=timeout)
    val = r.json().get("value", "")
    if isinstance(val, dict):
        val = val.get("value", val)
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            pass
    return val


# ============================ URL 处理 ============================

# 厦大「电子资源智能网关」——官方支持 Elsevier ScienceDirect 等 173 个平台。
# 走这个入口会带上机构凭据，Cloudflare 会放行（直连 sciencedirect.com 会被 Turnstile 拦）。
LIBPROXY_GO = "https://libproxy.xmu.edu.cn/go?url="


def is_elsevier(url: str) -> bool:
    u = (url or "").lower()
    return "sciencedirect.com" in u or "linkinghub.elsevier.com" in u


def libproxy_entry(url: str) -> str:
    """把目标 URL 包成 libproxy 智能网关入口。"""
    import urllib.parse
    return LIBPROXY_GO + urllib.parse.quote(url, safe="")


# ---------------------------------------------------------------
# 通用化：按 DOI 前缀硬编码 URL 的做法天花板很低（只覆盖 4-5 家）。
# 实测 10 家出版社后确认：**多数出版社的落地页上就有 PDF 链接**，
# 只要能拿到权威落地页 URL + 宽口径收集候选，就能覆盖绝大多数。
#
# 落地页来源优先级：
#   1. TSV 里给的 url（若看起来是落地页而非 PDF 直链）
#   2. doi.org 重定向后的真实落地页（最通用，Crossref 也直接给）
#   3. 按 DOI 前缀硬编码的 PDF 直链（对 ACS/Wiley/RSC 这类
#      "落地页会撞 Cloudflare、但 PDF 直链通" 的出版社是必需的）
# ---------------------------------------------------------------

# 已知出版社的 PDF 直链构造规则（前缀 → 函数）
# 只在"通用落地页路线失败"后作为兜底，因为部分出版社落地页有 CF 挑战
PDF_URL_RULES = {
    "10.1021/": lambda doi: f"https://pubs.acs.org/doi/pdf/{doi}",
    "10.1002/": lambda doi: f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}",
    "10.1126/": lambda doi: f"https://www.science.org/doi/pdf/{doi}",
    "10.1038/": lambda doi: f"https://www.nature.com/articles/{doi.split('/', 1)[1]}.pdf",
    "10.1007/": lambda doi: f"https://link.springer.com/content/pdf/{doi}.pdf",
}


def crossref_landing(doi: str) -> str:
    """取权威落地页 URL（DOI 的标准解析入口，公开 API，无需密钥）。

    坑：Crossref 返回的 URL 几乎总是 `https://doi.org/{DOI}` —— 这是**跳转地址**，
    不是出版社落地页。直接导航过去虽然会 302 过去，但落地页是 SPA/延迟渲染，
    在拿到最终 URL 前 body 可能还是空的，导致收集不到 PDF 候选。
    所以这里只把 Crossref 当"DOI 有效性的确认"，最终落地页用
    doi_landing_via_browser() 在浏览器里解析。
    """
    if not doi:
        return ""
    import urllib.parse
    try:
        url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
        r = _session().get(url, timeout=20, headers={
            "User-Agent": "XmuLitBot/1.0 (mailto:xmu-literature-downloader@users.noreply.github.com)"})
        if r.status_code != 200:
            return ""
        msg = r.json().get("message", {})
        return (msg.get("URL") or "").strip()
    except Exception:
        return ""


def doi_landing_via_browser(doi: str) -> str:
    """用浏览器导航 doi.org，读取重定向后的真实落地页 URL。

    这是**最通用的落地页解析方式**：不需要为每家出版社硬编码文章页 URL 规则。
    实测 MDPI → www.mdpi.com/2079-9292/14/19/3892，
    Elsevier → www.sciencedirect.com/science/article/pii/S0926337324011597?via=ihub
    """
    if not doi:
        return ""
    tid = None
    try:
        tid = proxy_new_tab()
        proxy_navigate(tid, f"https://doi.org/{doi}")
        time.sleep(5)
        for _ in range(4):
            href = str(proxy_eval(tid, "location.href") or "")
            if href and "doi.org" not in href and href.startswith("http"):
                return href
            time.sleep(2)
        return ""
    except Exception:
        return ""
    finally:
        if tid:
            proxy_close(tid)


def build_pdf_url(doi: str) -> str:
    """按 DOI 前缀构造 PDF 直链（无法构造则返回空）。"""
    if not doi:
        return ""
    d = doi.lower()
    for prefix, fn in PDF_URL_RULES.items():
        if d.startswith(prefix):
            return fn(doi)
    # RSC: DOI 形如 10.1039/d3ee04338j → 年份=2020+第2位数字，期刊码=第3-4位
    if d.startswith("10.1039/"):
        code = doi.split("/", 1)[1]
        if len(code) > 4:
            year = 2020 + int(code[1]) if code[1].isdigit() else 2024
            return f"https://pubs.rsc.org/en/content/articlepdf/{year}/{code[2:4]}/{code}"
    # IEEE: 需要 arnumber，DOI 无法推导 → 留空，交给落地页路线
    if d.startswith("10.1016/"):
        # Elsevier: PII 无法从 DOI 推导，这里给 doi 形式，
        # download_one 会用 resolve_elsevier_pii 换成真实 PII
        return f"https://www.sciencedirect.com/science/article/doi/{doi}/pdfft"
    return ""


def resolve_publisher_url(row: dict) -> str:
    """把 TSV 的 url 修正为可直接取字节的 PDF 地址（保留原有行为）。"""
    url = (row.get("url") or "").strip()
    doi = (row.get("doi") or "").strip()

    # RSC: /articlepdf/{code} → /articlepdf/{year}/{journal}/{code}
    if "pubs.rsc.org/en/content/articlepdf/" in url:
        tail = url.split("/articlepdf/", 1)[1].split("?")[0].split("#")[0]
        if "/" not in tail and len(tail) > 4:
            year = 2020 + int(tail[1]) if tail[1].isdigit() else 2024
            journal = tail[2:4]
            url = f"https://pubs.rsc.org/en/content/articlepdf/{year}/{journal}/{tail}"

    # Wiley: /doi/pdf/ → /doi/pdfdirect/
    if "onlinelibrary.wiley.com/doi/pdf/" in url:
        url = url.replace("/doi/pdf/", "/doi/pdfdirect/")

    # ACS: 去掉 ?download=true（我们走页面内 fetch，不需要触发浏览器下载）
    if "pubs.acs.org/doi/pdf/" in url and "?download=true" in url:
        url = url.split("?")[0]

    # Elsevier: 文章页 URL → PDF 直链（ScienceDirect 的 PDF 必须用导航打开，fetch 会被 CF 拦）
    if "sciencedirect.com/science/article/" in url and "/pdfft" not in url and "/pdf" not in url:
        url = url.rstrip("/") + "/pdfft"

    # 兜底：TSV 没给 url 就按 DOI 猜出版社
    if not url and doi:
        d = doi.lower()
        if d.startswith("10.1021/"):
            url = f"https://pubs.acs.org/doi/pdf/{doi}"
        elif d.startswith("10.1002/"):
            url = f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi}"
        elif d.startswith("10.1039/"):
            code = doi.split("/", 1)[1]
            year = 2020 + int(code[1]) if code[1].isdigit() else 2024
            url = f"https://pubs.rsc.org/en/content/articlepdf/{year}/{code[2:4]}/{code}"
        elif d.startswith("10.1126/"):
            url = f"https://www.science.org/doi/pdf/{doi}"
        elif d.startswith("10.1016/"):
            # Elsevier 的 PII 无法从 DOI 推导，必须走 doi.org 重定向解析（见 resolve_elsevier_pii）
            url = f"https://www.sciencedirect.com/science/article/doi/{doi}/pdfft"

    return url


def resolve_elsevier_pii(doi: str) -> str:
    """把 Elsevier DOI 解析成真实 PII。

    坑：Elsevier 的 PII（如 S0926337324011597）无法从 DOI 推导，
    只能通过 doi.org 的 302 重定向拿到最终 URL 后解析。
    """
    import re
    if not doi.lower().startswith("10.1016/"):
        return ""
    tid = None
    try:
        tid = proxy_new_tab()
        proxy_navigate(tid, f"https://doi.org/{doi}")
        time.sleep(5)
        # 重定向可能需要几跳，最多等 4 次
        for _ in range(4):
            val = proxy_eval(tid, "location.href")
            u = str(val or "")
            m = re.search(r"/pii/([A-Z0-9]{6,})", u)
            if m:
                return m.group(1)
            if "doi.org" not in u:
                break
            time.sleep(2)
        # 兜底：从页面内所有链接里找 pii
        val = proxy_eval(tid, """(() => {
            const m = location.href.match(/\\/pii\\/([A-Z0-9]{6,})/);
            if (m) return m[1];
            const a = [...document.querySelectorAll('a[href]')]
              .map(x => x.getAttribute('href'))
              .find(h => h && /\\/pii\\/[A-Z0-9]{6,}/.test(h));
            return a ? (a.match(/\\/pii\\/([A-Z0-9]{6,})/)||[])[1] || '' : '';
        })()""")
        return str(val or "")
    except Exception:
        return ""
    finally:
        if tid:
            proxy_close(tid)


# ============================ 页面内取字节 ============================

# 在页面上下文中执行：定位真实 PDF 链接 → fetch → 存到 window.__xmuPDFBytes
#
# 坑（实测 10 家出版社后总结）：候选来源必须覆盖 4 类，缺一不可：
#   1. a[href]          —— 最常见（Science / MDPI / Springer / Frontiers / T&F / APS）
#   2. meta[citation_pdf_url] —— MDPI / IEEE 用这个
#   3. iframe/embed[src] —— IEEE 的 stamp 包装页把真 PDF 放在 iframe 里
#   4. 页面自身就是 PDF   —— ScienceDirect / IEEE getPDF 导航后
# 只扫 a[href] 会在 6/10 家上误报 "no-pdf-candidate"。
#
# ⛔ 极隐蔽的坑：这是 JS **正则字面量**，里面的每个 `/` 都必须写成 `\/`。
# 未转义时 JS 解析器会在第一个 `/` 处结束正则字面量，后面的内容变成语法错误 ——
# 整个 FETCH_JS 抛 SyntaxError，表现为「所有落地页路线全部失败、
# 只有走 PDF 直链的能成功」，极具迷惑性。
#
# ⛔ 另一个坑：必须**排除补充材料**链接。RSC 落地页同时挂主文 PDF 与
# Supporting Information PDF（如 .../article-supplement/.../d3ee04338j2_suppl/），
# 命中补充材料会得到 54 页的 SI 而不是 12 页主文 —— 文件合法但内容不是目标。
#
# ⛔ 但排除规则不能太宽：OUP 的正常文章路径里就含 `Supplement_1`
# （如 /pch/article-pdf/29/Supplement_1/e32/.../pxae067.pdf —— 那是**增刊期号**
# 不是补充材料），按 "supplement" 粗匹配会误杀主文。
# 因此只匹配**路径中表示补充材料文件的特征**：article-supplement / _suppl / suppl 后缀 /
# mmc / supporting-information / attachment。
PDF_LINK_RE = r"(\.pdf(\?|$|\/)|\/pdf\b|\/pdfdirect|article-pdf|articlepdf|\/pdfft|stamp\.jsp|stampPDF|getPDF|\/content\/pdf|\/pdf$|\/pdf\?)"

# 补充材料 / 附件类链接的特征（命中即跳过）
# 注意：不要匹配裸的 "supplement" —— OUP 增刊期号路径里含它
SI_MARK_RE = r"(article-supplement|article_supplement|supplement\/|\/suppl\b|_suppl\b|suppl\/|downloadSupplement|supporting-?info|supplementary-?material|mmc\d|attachment|\/esi\/|\bsi\.pdf|-sup-?\d|\bsup-\d)"

FETCH_JS = """(async () => {
    const PDFRE = /""" + PDF_LINK_RE + r"""/i;
    const SIRE = /""" + SI_MARK_RE + r"""/i;
    const cands = [];
    const push = h => {
        if (!h) return;
        if (SIRE.test(h)) return;          // 跳过补充材料
        try { cands.push(new URL(h, location.href).href); } catch(e) {}
    };

    for (const a of document.querySelectorAll('a[href]')) {
        const h = a.getAttribute('href') || '';
        if (PDFRE.test(h)) push(h);
    }
    for (const m of document.querySelectorAll('meta[content]')) {
        const nm = (m.getAttribute('name') || '').toLowerCase();
        if (nm === 'citation_pdf_url') push(m.getAttribute('content'));
        else if (PDFRE.test(m.getAttribute('content') || '')) push(m.getAttribute('content'));
    }
    for (const e of document.querySelectorAll('iframe[src],embed[src],object[data]')) {
        push(e.getAttribute('src') || e.getAttribute('data'));
    }
    for (const e of document.querySelectorAll('[data-url],[data-href],[data-pdf],[onclick]')) {
        for (const a of ['data-url','data-href','data-pdf','onclick']) {
            const v = e.getAttribute(a) || '';
            if (v && PDFRE.test(v)) { push(v); break; }
        }
    }
    if (PDFRE.test(location.href)) cands.unshift(location.href);

    const uniq = [...new Set(cands)];

    // 排序：主文优先（URL 里含主文 DOI 的排前面），再按原始顺序
    const dnorm = (document.querySelector('meta[name="citation_doi"]')||{}).content || '';
    const dkey = dnorm.replace(/^10\.\d{4,9}\//i,'').toLowerCase();
    const isMain = u => {
        const s = u.toLowerCase();
        if (SIRE.test(u)) return false;
        if (dkey && s.includes(dkey)) return true;
        return !/article-supplement|_suppl\b|suppl\/|supporting-?info|mmc\d/i.test(s);
    };
    uniq.sort((a,b) => (isMain(b)?1:0) - (isMain(a)?1:0));

    for (const u of uniq) {
        try {
            const r = await fetch(u, {credentials: 'include'});
            if (!r.ok) continue;
            // 探针模式：只读首个数据块即中止，不下载整篇
            if (typeof __PROBE__ !== 'undefined' && __PROBE__) {
                const rd = r.body.getReader();
                const {value} = await rd.read();
                if (!value) continue;
                const head = String.fromCharCode(...value.slice(0, 5));
                if (head !== '%PDF-') continue;
                return JSON.stringify({ok: true, status: r.status,
                                       ct: r.headers.get('content-type'), url: u});
            }
            const ab = await r.arrayBuffer();
            if (ab.byteLength < 4096) continue;
            const head = String.fromCharCode(...new Uint8Array(ab.slice(0, 5)));
            if (head !== '%PDF-') continue;
            window.__xmuPDFBytes = new Uint8Array(ab);
            return JSON.stringify({ok: true, size: ab.byteLength, url: u});
        } catch (e) { /* 试下一个候选 */ }
    }
    return JSON.stringify({ok: false, why: 'no-pdf-bytes', n: uniq.length,
                           cands: uniq.slice(0, 4),
                           title: document.title,
                           text: document.body.innerText.replace(/\\s+/g,' ').slice(0, 200)});
})()"""

# 同源取字节：当前页面本身就是 PDF（document.contentType === 'application/pdf'）时，
# fetch(location.href) 是同源请求，Cloudflare 不会拦。
#
# 坑：ScienceDirect 的 PDF **必须用「导航」打开**。若在文章页里用 fetch 请求 pdfft 链接，
# CF 会按 Sec-Fetch-Dest 判定为非导航请求（empty）→ 直接返回 CF 挑战 HTML（content-type: text/html，~52 KB）。
# 正确姿势：导航到 PDF URL（Sec-Fetch-Dest: document）→ 浏览器内联渲染 →
# 此时 location.href 已在 pdf.sciencedirectassets.com 域内 → 同源 fetch 取字节。
SELF_FETCH_JS = """(async () => {
    try {
        const r = await fetch(location.href, {credentials: 'include'});
        if (!r.ok) return JSON.stringify({ok:false, why:'http-'+r.status,
                                          ct:r.headers.get('content-type')});
        const ab = await r.arrayBuffer();
        if (ab.byteLength < 4096) return JSON.stringify({ok:false, why:'too-small',
                                                       len:ab.byteLength});
        const head = String.fromCharCode(...new Uint8Array(ab.slice(0, 5)));
        if (head !== '%PDF-') return JSON.stringify({ok:false, why:'not-pdf', head,
                                                    ct:r.headers.get('content-type'),
                                                    len:ab.byteLength});
        window.__xmuPDFBytes = new Uint8Array(ab);
        return JSON.stringify({ok: true, size: ab.byteLength, url: location.href});
    } catch (e) {
        return JSON.stringify({ok: false, why: 'fetch-failed', err: String(e).slice(0, 100)});
    }
})()"""

# 收集页面上的所有 PDF 候选链接（4 类来源：a[href] / meta[citation_pdf_url] / iframe / data-*）
HARVEST_JS = """(() => {
    const PDFRE = /""" + PDF_LINK_RE + r"""/i;
    const SIRE = /""" + SI_MARK_RE + r"""/i;
    const out = [];
    const push = h => {
        if (!h) return;
        if (SIRE.test(h)) return;          // 跳过补充材料
        try { out.push(new URL(h, location.href).href); } catch(e) {}
    };
    for (const a of document.querySelectorAll('a[href]')) {
        const h = a.getAttribute('href') || '';
        if (PDFRE.test(h)) push(h);
    }
    for (const m of document.querySelectorAll('meta[content]')) {
        const nm = (m.getAttribute('name') || '').toLowerCase();
        if (nm === 'citation_pdf_url') push(m.getAttribute('content'));
        else if (PDFRE.test(m.getAttribute('content') || '')) push(m.getAttribute('content'));
    }
    for (const e of document.querySelectorAll('iframe[src],embed[src],object[data]')) {
        push(e.getAttribute('src') || e.getAttribute('data'));
    }
    for (const e of document.querySelectorAll('[data-url],[data-href],[data-pdf],[onclick]')) {
        for (const a of ['data-url','data-href','data-pdf','onclick']) {
            const v = e.getAttribute(a) || '';
            if (v && PDFRE.test(v)) { push(v); break; }
        }
    }
    if (PDFRE.test(location.href)) out.unshift(location.href);
    const uniq = [...new Set(out)];
    // 主文优先：含主文 DOI 的排前面
    const dnorm = (document.querySelector('meta[name="citation_doi"]')||{}).content || '';
    const dkey = dnorm.replace(/^10\.\d{4,9}\//i,'').toLowerCase();
    const isMain = u => {
        const s = u.toLowerCase();
        if (SIRE.test(u)) return false;
        if (dkey && s.includes(dkey)) return true;
        return !/article-supplement|_suppl\b|suppl\/|supporting-?info|mmc\d/i.test(s);
    };
    uniq.sort((a,b) => (isMain(b)?1:0) - (isMain(a)?1:0));
    return JSON.stringify({ct: document.contentType, title: (document.title||'').slice(0,60),
                           nlinks: document.querySelectorAll('a[href]').length,
                           cands: uniq.slice(0, 6)});
})()"""

# 判断当前页是否已被 Cloudflare 机器人验证拦住
CF_BLOCK_JS = """JSON.stringify({
    ct: document.contentType,
    title: document.title,
    nlinks: document.querySelectorAll('a[href]').length,
    text: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0, 220)
})"""

CHUNK_JS = """(() => {
    const bytes = window.__xmuPDFBytes.slice(__START__, __END__);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 0x8000)
        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin);
})()"""


def _chunk_js(start: int, end: int) -> str:
    """生成取块 JS。

    坑：JS 代码里全是 { }，绝不能用 str.format() 注入参数 ——
    Python 会把 JS 的花括号当占位符，报 "ValueError: unexpected '{' in field name"。
    改用显式的 __START__ / __END__ 占位符做字面替换。
    """
    return CHUNK_JS.replace("__START__", str(start)).replace("__END__", str(end))


def selfcheck_js() -> list[str]:
    """校验内嵌 JS。返回错误列表（空 = 全部通过）。

    为什么必须有：内嵌 JS 里的正则字面量若含未转义/双重转义的 `/` `\\b` `\\d`，
    JS 解析器会提前结束正则字面量或产生非法语法，整段代码抛 SyntaxError。
    症状极具迷惑性 —— **只有走 FETCH_JS 的「落地页路线」全挂，
    而走 SELF_FETCH_JS 的「PDF 直链路线」照常成功**，很容易误判成「出版社不给 PDF」。

    两道检查：
      1. `node --check`  —— 静态语法
      2. 在 mock DOM 里真跑一遍 —— 捕获运行时异常（静态检查查不出双重转义）
    """
    errors: list[str] = []
    node = None
    for cand in ("/Users/zhang/.workbuddy/binaries/node/versions/22.22.2-6/bin/node",
                 "/opt/homebrew/bin/node", "/usr/local/bin/node", "node"):
        import shutil
        found = shutil.which(cand)
        if found:
            node = found
            break
    if not node:
        return ["未找到 node，跳过 JS 语法自检"]

    import subprocess
    import tempfile

    # mock DOM：够用来触发正则定义、正则构造、querySelectorAll 等运行时错误
    harness = """
const MOCK_LINKS = [
  {getAttribute: () => '/content/pdf/10.1140/epjc/s10052-024-12995-0.pdf'},
  {getAttribute: () => '/ee/article-supplement/807781/pdf/d3ee04338j2_suppl/'},
  {getAttribute: () => '/pch/article-pdf/29/Supplement_1/e32/60009587/pxae067.064.pdf'},
];
globalThis.location = {href: 'https://example.org/article/x'};
globalThis.document = {
  contentType: 'text/html', title: 'mock',
  body: {innerText: 'mock body text'},
  querySelectorAll: (sel) => {
    if (sel === 'a[href]') return MOCK_LINKS;
    if (sel === 'meta[content]') return [{getAttribute: (a) =>
        a === 'name' ? 'citation_doi' : '10.1140/epjc/s10052-024-12995-0'}];
    return [];
  },
};
globalThis.fetch = async () => ({ok: false});
globalThis.window = {};
"""

    for name, js in (("FETCH_JS", FETCH_JS), ("SELF_FETCH_JS", SELF_FETCH_JS),
                     ("CF_BLOCK_JS", CF_BLOCK_JS), ("CHUNK_JS", _chunk_js(0, 512))):
        # 1) 静态语法检查
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as f:
                f.write("const __x = " + js + ";\n")
                path = f.name
            r = subprocess.run([node, "--check", path], capture_output=True, text=True, timeout=20)
            os.unlink(path)
            if r.returncode != 0:
                errors.append(f"{name} 语法错误: {r.stderr.strip()[:300]}")
                continue
        except Exception as e:
            errors.append(f"{name} 语法自检异常: {type(e).__name__}: {str(e)[:80]}")
            continue

        # 2) 运行时检查（在 mock DOM 里真跑）
        #    CHUNK_JS 依赖页面里已存在 window.__xmuPDFBytes，需单独 mock
        extra = ("globalThis.window.__xmuPDFBytes = new Uint8Array([37,80,68,70,45]);\n"
                 if name == "CHUNK_JS" else "")
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as f:
                f.write(harness + extra + "\ntry {\n  const __r = " + js + ";\n"
                        "  console.log('RUN_OK');\n} catch (e) {\n"
                        "  console.log('RUN_ERR: ' + String(e));\n}\n")
                path = f.name
            r2 = subprocess.run([node, path], capture_output=True, text=True, timeout=20)
            os.unlink(path)
            if "RUN_OK" not in (r2.stdout or ""):
                msg = (r2.stdout or "").strip() or (r2.stderr or "").strip()
                errors.append(f"{name} 运行时错误: {msg[:260]}")
        except Exception as e:
            errors.append(f"{name} 运行自检异常: {type(e).__name__}: {str(e)[:80]}")

    return errors


def fetch_pdf_bytes(tid: str) -> tuple[Optional[bytes], str]:
    """在页面内 fetch PDF 字节（落地页找链接模式）。返回 (bytes, 说明)。"""
    res = proxy_eval(tid, FETCH_JS)
    if not isinstance(res, dict) or not res.get("ok"):
        why = res.get("why") if isinstance(res, dict) else str(res)
        txt = res.get("text", "") if isinstance(res, dict) else ""
        return None, f"{why} | {txt[:120]}"

    return _drain(tid, int(res["size"]), res.get("url", "")[:80])


# ============================ 校验 ============================

def validate_pdf(data: bytes, row: dict) -> tuple[bool, str]:
    """校验 PDF 真实性：魔数 / 尾标 / 页数 / 标题关键词。"""
    if len(data) < 2048:
        return False, f"文件过小 ({len(data)}B)"
    if not data.startswith(b"%PDF-"):
        head = data[:120].decode("utf-8", "ignore").replace("\n", " ")
        return False, f"不是 PDF（内容像 HTML/JS）: {head[:80]}"
    if b"%%EOF" not in data[-4096:]:
        return False, "缺少 %%EOF 尾标（文件被截断）"

    pages = None
    title_text = ""
    try:
        import pymupdf
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            pages = doc.page_count
            title_text = doc[0].get_text()[:6000] if doc.page_count else ""
    except ImportError:
        try:
            from pypdf import PdfReader
            import io
            reader = PdfReader(io.BytesIO(data))
            pages = len(reader.pages)
        except Exception:
            pass
    except Exception as e:
        return False, f"PDF 解析失败: {type(e).__name__}: {str(e)[:60]}"

    if pages is not None and pages < 1:
        return False, "页数为 0"

    # 标题关键词校验：TSV 里的 title 抽几个长词，命中其一即可
    title = (row.get("title") or "").strip()
    if title and title_text:
        import re
        words = [w for w in re.findall(r"[A-Za-z一-鿿]{4,}", title)][:4]
        if words:
            norm = title_text.replace("’", "'").replace("–", "-")
            if not any(w.lower() in norm.lower() for w in words):
                return False, f"首页未命中标题关键词 {words[:2]}（可能下错文章）"

    return True, f"{pages} 页" if pages else "页数未知"


# ============================ 单篇下载 ============================

def _page_state(tid: str) -> dict:
    """读当前页状态：content-type / 标题 / 文本片段（用于识别 CF 挑战）。"""
    val = proxy_eval(tid, CF_BLOCK_JS, timeout=30)
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            import json as _json
            d = _json.loads(val)
            return d if isinstance(d, dict) else {}
        except Exception:
            pass
    return {}


def _is_cf_challenge(state: dict) -> bool:
    """判断当前页是不是 Cloudflare 机器人验证页。"""
    txt = (state.get("text") or "").lower()
    title = (state.get("title") or "").lower()
    markers = ("are you a robot", "captcha challenge", "请稍候", "checking your browser",
               "enable javascript and cookies", "just a moment")
    return any(m in txt or m in title for m in markers)


def _is_publisher_error(state: dict) -> str:
    """识别出版商自己的错误页（区别于 CF 挑战与付费墙）。

    实测：ScienceDirect 在出口 IP 被临时限流时，返回
      "There was a problem providing the content you requested ...
       Reference number: xxxx  IP Address: 1.2.3.4"
    这类页面**看起来像 HTML 错误页，但不是本地 skill 的问题** ——
    请求已到达出版商，是对方拒绝了该 IP / 该会话。
    识别出来才能给出正确提示，否则会被误报成「没有 PDF 链接」。
    """
    txt = (state.get("text") or "")
    low = txt.lower()
    if "problem providing the content you requested" in low:
        import re
        ref = re.search(r"reference number:\s*(\w+)", txt, re.I)
        ip = re.search(r"ip address:\s*([\d.]+)", txt, re.I)
        bits = ["ScienceDirect 拒绝了该请求"]
        if ref:
            bits.append(f"参考号 {ref.group(1)}")
        if ip:
            bits.append(f"出口 IP {ip.group(1)}")
        return " ".join(bits) + " —— 通常是出版商对该出口 IP 的临时限流（等几分钟或换网络再试）"
    if "access denied" in low or "403 forbidden" in low:
        return "出版商返回 403（机构授权未生效，检查 WebVPN 登录态）"
    if "sign in" in low and "purchase" in low and "subscribe" in low:
        return "落地页显示需订阅/购买（该刊可能不在机构订阅范围内）"
    # 厦大统一认证登录页 —— libproxy 会话被打断时被甩到这里
    if "企业微信扫码登录" in txt or "账号登录" in txt or "统一身份认证" in txt:
        return ("落到厦大统一认证登录页 —— libproxy 会话被打断。"
                "通常是并发访问 libproxy 引起（已用全局锁串行化）；"
                "若持续出现需在浏览器重新登录 libproxy 智能网关")
    return ""


def _pull_bytes(tid: str) -> tuple[Optional[bytes], str]:
    """从当前 tab 取回 PDF 字节（先试同源自取，再试找链接）。"""
    # 1) 当前页本身已是 PDF → 同源 fetch(location.href)
    state = _page_state(tid)
    if (state.get("ct") or "").lower().startswith("application/pdf"):
        res = proxy_eval(tid, SELF_FETCH_JS, timeout=TIMEOUT_EVAL)
        if isinstance(res, dict) and res.get("ok"):
            return _drain(tid, int(res["size"]), res.get("url", "")[:90])
        return None, f"同源自取失败: {res.get('why') if isinstance(res, dict) else res}"

    # 2) 文章页 → 找 PDF 链接再 fetch
    return fetch_pdf_bytes(tid)


def _drain(tid: str, size: int, note: str) -> tuple[Optional[bytes], str]:
    """分块取回 window.__xmuPDFBytes。"""
    buf = bytearray()
    for start in range(0, size, CHUNK):
        end = min(start + CHUNK, size)
        part = proxy_eval(tid, _chunk_js(start, end))
        if not isinstance(part, str) or not part:
            return None, f"分块传输中断 @ {start}"
        try:
            buf += base64.b64decode(part)
        except Exception as e:
            return None, f"base64 解码失败: {e}"
    return bytes(buf), note


def _wait_ready(tid: str, max_wait: float = 14.0) -> dict:
    """等页面渲染就绪，返回页面状态。

    坑：落地页多为 SPA，导航后 body 可能还没渲染（link 数为 0），
    此时直接收集候选会得到空列表，误判成"没有 PDF 链接"。
    这里轮询到「有链接」或超时为止。
    """
    deadline = time.time() + max_wait
    state: dict = {}
    while time.time() < deadline:
        state = _page_state(tid)
        text = state.get("text") or ""
        title = (state.get("title") or "")
        nlinks = int(state.get("nlinks") or 0)
        # 就绪判据：页面有实质内容且有链接；或已经是 PDF
        if (state.get("ct") or "").lower().startswith("application/pdf"):
            break
        if nlinks > 5 and len(text) > 80 and "请稍候" not in title:
            break
        time.sleep(1.5)
    return state


def _harvest_candidates(tid: str) -> list[str]:
    """收集页面上的所有 PDF 候选链接（4 类来源）。"""
    val = proxy_eval(tid, HARVEST_JS, timeout=60)
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return []
    if isinstance(val, dict):
        return [c for c in (val.get("cands") or []) if c]
    return []


def _try_route(url: str, wait: float) -> tuple[Optional[bytes], str, bool]:
    """在独立 tab 里走一条路径取字节。返回 (bytes, 说明, 是否命中CF挑战)。

    三层取字节策略（层层降级）：
      1. 当前页已是 PDF            → 同源 fetch(location.href)
      2. 落地页内找链接后 fetch     → 页面内 fetch(候选 URL)
      3. 导航到候选链接再同源 fetch  → 兜底，用于「fetch 被拦但导航放行」的出版社
         （OUP / ScienceDirect / IEEE 属于这类）
    """
    tid = None
    try:
        tid = proxy_new_tab()
        proxy_navigate(tid, url)
        time.sleep(wait)
        state = _wait_ready(tid)
        if _is_cf_challenge(state):
            txt = (state.get("text") or "")[:90]
            return None, f"Cloudflare 机器人验证拦截: {txt}", True
        pub_err = _is_publisher_error(state)
        if pub_err:
            return None, pub_err, False

        data, why = _pull_bytes(tid)
        if data is not None:
            return data, why, False

        # ---- 策略 3：导航到候选 PDF 链接 → 同源取字节 ----
        cands = _harvest_candidates(tid)
        for cu in cands[:3]:
            try:
                proxy_navigate(tid, cu)
                time.sleep(5)
                st = _page_state(tid)
                if "pdf" in (st.get("ct") or "").lower():
                    res = proxy_eval(tid, SELF_FETCH_JS, timeout=TIMEOUT_EVAL)
                    if isinstance(res, dict) and res.get("ok"):
                        d2, n2 = _drain(tid, int(res["size"]), f"导航同源 {cu[:60]}")
                        if d2:
                            return d2, n2, False
            except Exception:
                continue

        return None, why, False
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:80]}", False
    finally:
        if tid:
            proxy_close(tid)


def _save(out_path: Path, data: bytes, row: dict) -> tuple[Optional[str], str]:
    """校验并落盘。返回 (说明, 错误)。说明为 None 表示成功。"""
    good, reason = validate_pdf(data, row)
    if not good:
        return None, f"校验不通过: {reason}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return reason, ""


def download_one(row: dict, out_dir: Path, retries: int, force: bool) -> tuple[str, str, int, str]:
    filename = (row.get("filename") or "").strip()
    if not filename:
        return ("fail", filename, 0, "TSV 缺 filename")

    out_path = out_dir / filename

    # 已有文件：先校验，通过就跳过；不通过则重下
    if out_path.exists() and not force:
        existing = out_path.read_bytes()
        good, why = validate_pdf(existing, row)
        if good:
            return ("skip", filename, len(existing), f"已存在且有效（{why}）")
        safe_print(f"    {filename} 存在但无效（{why}）→ 重新下载")

    doi = (row.get("doi") or "").strip()
    tsv_url = (row.get("url") or "").strip()

    # ---------- 组装候选路线（按优先级）----------
    # 实测结论（14 家出版社横评）：
    #   路线 1 落地页(带 PDF 链接)   → 覆盖 Science/SciRep/Springer/MDPI/Frontiers/T&F/APS/OUP/IEEE
    #   路线 2 PDF 直链               → 覆盖 ACS/Wiley/RSC（落地页会撞 CF，直链通）
    #   路线 3 libproxy + 落地页      → 覆盖 Elsevier 等 CF 拦截型
    routes: list[tuple[str, str]] = []   # (标签, 原始 URL，未做 libproxy 包装)
    tried: set[tuple[str, str]] = set()

    def add(label: str, url: str) -> None:
        # 去重键必须含标签 —— 同一 URL 的「直连」与「libproxy」是两条不同路线
        key = (label, url)
        if url and key not in tried:
            tried.add(key)
            routes.append((label, url))

    # 1) 落地页路线：优先用 TSV 给的 url（若不像 PDF 直链），否则用浏览器解析 doi.org
    landing = ""
    if tsv_url and not re.search(r"\.pdf($|\?)|/pdfdirect|/pdfft|/articlepdf|stamp", tsv_url, re.I):
        landing = tsv_url
    if not landing and doi:
        # 关键：必须在浏览器里解析 doi.org 跳转，拿到出版社真实落地页
        # （Crossref 给的 https://doi.org/... 是跳转地址，直接用会落空）
        landing = doi_landing_via_browser(doi)
        if landing:
            safe_print(f"    {filename} 落地页: {landing[:78]}")
    if landing:
        add("落地页", landing)
        add("落地页(libproxy)", landing)

    # 2) PDF 直链路线
    direct = tsv_url if tsv_url and re.search(r"\.pdf($|\?)|/pdfdirect|/pdfft|/articlepdf|stamp", tsv_url, re.I) else ""
    if not direct and doi:
        direct = build_pdf_url(doi)
    if direct:
        add("PDF直链", direct)
        add("PDF直链(libproxy)", direct)

    # 3) IEEE 专用：PDF 藏在 stamp 包装页的 iframe 里，getPDF.jsp 才是真 PDF。
    #    坑：arnumber 不能用 DOI 顶替 —— doi.org 解析出的落地页是
    #    /document/{arnumber}，两者是不同的编号体系。
    if doi.lower().startswith("10.1109/"):
        arn = ""
        if landing:
            mm = re.search(r"/document/(\d+)", landing)
            if mm:
                arn = mm.group(1)
        if arn:
            add("IEEE-stampPDF", f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arn}")
            add("IEEE-stamp", f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={arn}")
        else:
            add("IEEE落地页", f"https://ieeexplore.ieee.org/document/{doi}")

    if not routes:
        return ("fail", filename, 0, "无法确定任何可用 URL（缺 doi 或 url）")

    # Elsevier 的 PII 解析（DOI 推不出 PII，必须先换成真实 PII）
    pii_cache: dict[str, str] = {}
    if doi.lower().startswith("10.1016/"):
        pii = resolve_elsevier_pii(doi)
        if pii:
            pii_cache[doi] = pii
            safe_print(f"    {filename} PII 解析成功: {pii}")
        else:
            return ("fail", filename, 0, "Elsevier PII 解析失败（doi.org 重定向未拿到 PII）")

    last_err = ""
    cf_routes: list[str] = []   # 记录哪些路线撞了 CF 挑战（用于最终归因）
    for attempt in range(1, retries + 1):
        for label, raw_url in routes:
            target = raw_url
            # PII 替换必须在 libproxy 包装之前做
            if pii_cache and f"/doi/{doi}" in target:
                target = target.replace(f"/doi/{doi}", f"/pii/{pii_cache[doi]}")
            if "libproxy" in label:
                target = libproxy_entry(target)

            wait = 12 if "libproxy" in label else 7
            if "libproxy" in label:
                # libproxy 会话是浏览器级共享状态，必须串行访问
                with _libproxy_lock:
                    data, why, cf_hit = _try_route(target, wait)
            else:
                data, why, cf_hit = _try_route(target, wait)
            if data is None:
                if cf_hit:
                    cf_routes.append(label)
                    # 直连撞 CF 是**预期情况**，不是故障 —— libproxy 路线会自动接手。
                    # 但要让用户看见，否则会误以为「整条链都挂了」。
                    safe_print(f"    {filename} [{label}] 撞 Cloudflare 机器人验证 → 自动改走后续路线")
                last_err = f"[{label}] {why[:110]}"
                continue

            reason, err = _save(out_path, data, row)
            if reason is not None:
                return ("ok", filename, len(data), f"{reason}（{label}）")
            last_err = f"[{label}] {err}"

        if attempt < retries:
            time.sleep(5 * attempt)

    # 归因：全部路线失败时，明确告诉用户「哪些撞了 CF、哪些是别的原因」，
    # 否则最后一根路线的错误会覆盖掉真正的关键信息。
    if cf_routes:
        cf_desc = "、".join(cf_routes)
        hint = (f"（{cf_desc} 撞了 Cloudflare 机器人验证；"
                f"libproxy 智能网关路线也已尝试）")
        return ("fail", filename, 0, f"{hint} 最后一次错误: {last_err or '未知错误'}")
    return ("fail", filename, 0, last_err or "未知错误")


# ============================ 主流程 ============================

# TSV 必须包含的列（大小写不敏感）
REQUIRED_COLS = ("filename",)


def _clean_key(k: Optional[str]) -> str:
    """清洗列名：剥 BOM、去空白、转小写。

    坑：Excel / WPS 导出的 TSV 默认带 UTF-8 BOM，
    首列列名会变成 '\\ufeffurl'，于是 row.get("url") 永远返回 None，
    表现为「明明填了 url 却提示无法确定 PDF URL」。
    """
    return (k or "").lstrip("﻿").strip().lower()


def read_rows(tsv: Path, limit: int = 0) -> list[dict]:
    """读 TSV 清单。

    用 utf-8-sig 打开：它会自动剥离 BOM（有 BOM 按 BOM 解，无 BOM 按普通 utf-8 解，两者都安全）。
    同时把所有列名统一清洗成小写无 BOM，让 'DOI' / 'doi' / 'DOI ' 都能认。
    """
    rows: list[dict] = []
    # newline="" 是 csv 模块的官方要求，否则 Windows 下空行会被当成坏行
    with open(tsv, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not reader.fieldnames:
            return rows
        # 表头清洗
        field_map = {_clean_key(k): k for k in reader.fieldnames if k}
        if "filename" not in field_map:
            print(f"✗ TSV 缺少 filename 列（现有列: {list(field_map.keys())}）")
            return rows

        for raw in reader:
            row: dict[str, Any] = {}
            for k, v in raw.items():
                if k is None:
                    continue
                row[_clean_key(k)] = (v or "").strip() if isinstance(v, str) else v
            if not (row.get("doi") or row.get("url")):
                continue
            if not row.get("filename"):
                continue
            rows.append(row)
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="批量 PDF 下载（WebVPN + CDP）")
    ap.add_argument("--tsv", default=str(DEFAULT_TSV), help="清单 TSV 路径")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="PDF 输出目录")
    ap.add_argument("--limit", type=int, default=0, help="最多下载几篇（0=全部）")
    ap.add_argument("--workers", type=int, default=3, help="并发数")
    ap.add_argument("--retries", type=int, default=2, help="单篇重试次数")
    ap.add_argument("--no-skip", action="store_true", help="即使已存在也重新下载")
    args = ap.parse_args()

    tsv = Path(args.tsv)
    out_dir = Path(args.out)
    if not tsv.exists():
        print(f"✗ 找不到 TSV: {tsv}")
        return 1

    rows = read_rows(tsv, args.limit)
    total = len(rows)

    # 内嵌 JS 语法自检（能提前抓出正则未转义这类静默失败）
    js_errors = selfcheck_js()
    if js_errors:
        print("✗ 内嵌 JS 语法自检未通过：")
        for e in js_errors:
            print(f"    {e}")
        print("  → 修正后重试。这类错误会导致「落地页路线」全部失败且无明确报错。")
        return 1

    print("=" * 58)
    print(f"  批量 PDF 下载 (WebVPN + CDP, {args.workers} 线程)")
    print("=" * 58)
    print(f"共 {total} 篇{'（测试模式）' if args.limit else ''}")
    print(f"输出: {out_dir.resolve()}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 前置自检：代理 / 浏览器 / WebVPN 登录 —— 任一不过就别白跑
    try:
        h = _session().get(f"{PROXY}/health", timeout=5)
        if h.status_code != 200:
            print(f"✗ CDP 代理异常 ({h.status_code})，请先跑 scripts/preflight.py")
            return 1
    except Exception as e:
        print(f"✗ 连不上 CDP 代理 {PROXY}: {type(e).__name__}")
        print("  请先跑 scripts/preflight.py 查看具体缺什么")
        return 1

    stats = {"ok": 0, "skip": 0, "fail": 0}
    failures: list[tuple[str, str]] = []
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(download_one, r, out_dir, args.retries, args.no_skip): r for r in rows}
        done = 0
        for fut in as_completed(futs):
            status, filename, size, note = fut.result()
            stats[status] += 1
            done += 1
            if status == "fail":
                failures.append((filename, note))
            icon = {"ok": "✓", "skip": "⏭", "fail": "✗"}[status]
            sz = f"{size/1024:.0f}KB" if size else ""
            safe_print(f"[{done}/{total}] {icon} {filename} {sz}  {note}")

    el = time.time() - t0
    print(f"\n{'=' * 52}")
    print(f"完成 | {el:.0f}s | 下载 {stats['ok']} | 跳过 {stats['skip']} | 失败 {stats['fail']}")
    if failures:
        print("\n失败明细：")
        for fn, why in failures:
            print(f"  ✗ {fn}\n      {why}")
        print("\n建议：先跑 scripts/preflight.py 确认 WebVPN 已登录，再重跑本脚本")
    return 0 if stats["fail"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
