#!/usr/bin/env python3
"""
批量 PDF 下载 — 通过已登录 WebVPN 的 Edge 浏览器多线程并行下载。

用法: python3 batch_download.py [--limit 10] [--workers 3] [--delay 2]
"""

import csv
import os
import sys
import time
import shutil
import threading
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

# ===================== 配置 =====================
PROXY = "http://127.0.0.1:3456"
TSV_FILE = Path(__file__).parent / "manual_download.tsv"
OUT_DIR = Path(__file__).parent / "pdfs"
DOWNLOADS_DIR = Path.home() / "Downloads"
TIMEOUT = 60
MAX_WORKERS = 3      # 并行下载数
TEST_LIMIT = 10      # 0 = 全部
# ===============================================


def read_urls(limit: int = 0) -> list[dict]:
    rows = []
    with open(TSV_FILE, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            rows.append(row)
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def proxy_new_tab() -> str:
    r = requests.get(f"{PROXY}/new", params={"url": "about:blank"}, timeout=30)
    data = r.json()
    return data.get("targetId") or data.get("id")


def proxy_navigate(target_id: str, url: str) -> dict:
    r = requests.get(f"{PROXY}/navigate", params={"target": target_id, "url": url}, timeout=30)
    return r.json()


def proxy_close(target_id: str):
    try:
        requests.get(f"{PROXY}/close", params={"target": target_id}, timeout=10)
    except Exception:
        pass


# ---- 线程共享状态 ----
_print_lock = threading.Lock()
_before_lock = threading.Lock()
_before_files: set = set()


def safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)


def snap_files() -> set:
    """获取当前 Downloads 文件快照"""
    return set(f.name for f in DOWNLOADS_DIR.iterdir() if f.is_file())


def wait_for_new_download(before: set, timeout: int = TIMEOUT) -> Optional[Path]:
    """
    等待 before 快照之后出现的新下载完成。
    Edge 通过 WebVPN 下载 PDF 极快，通常没有 .crdownload 阶段。
    """
    start = time.time()

    while time.time() - start < timeout:
        time.sleep(0.3)
        current = set(f.name for f in DOWNLOADS_DIR.iterdir() if f.is_file())

        # 检查新增的 .pdf（最常见情况：Edge 直接秒下）
        new_pdfs = [f for f in current if f.lower().endswith(".pdf") and f not in before]
        if new_pdfs:
            for pdf_name in new_pdfs:
                full = DOWNLOADS_DIR / pdf_name
                s1 = full.stat().st_size
                time.sleep(0.2)
                if full.exists():
                    s2 = full.stat().st_size
                    if s1 == s2 and s1 > 1024:
                        return full

        # 检查 .crdownload（大文件可能有中间状态）
        crdownloads = [f for f in current if f.endswith(".crdownload") and f not in before]
        if crdownloads:
            crd = crdownloads[0]
            # 等待 .crdownload 消失
            while time.time() - start < timeout:
                time.sleep(0.3)
                current2 = set(f.name for f in DOWNLOADS_DIR.iterdir() if f.is_file())
                if crd not in current2:
                    base = crd.replace(".crdownload", "")
                    # 查找对应的完成文件
                    for nf in current2:
                        if nf == base or (nf.startswith(base[:40]) and nf.lower().endswith(".pdf")):
                            full = DOWNLOADS_DIR / nf
                            if full.stat().st_size > 1024:
                                return full
                    break
            continue

    return None


def fetch_pdf_via_js(tab: str, out_path: Path) -> Optional[int]:
    """
    在浏览器页面内通过 fetch() 获取 PDF 字节并保存到文件。
    用于 PDF 在浏览器中内联显示（而非自动下载）的情况（如 Science、Wiley）。
    返回文件大小，失败返回 None。
    """
    fetch_js = """(
        async () => {
            const r = await fetch(location.href, {credentials: "include"});
            if (!r.ok) return JSON.stringify({ok: false, status: r.status});
            const ct = r.headers.get("content-type") || "";
            const ab = await r.arrayBuffer();
            if (ab.byteLength < 1024) return JSON.stringify({ok: false, status: r.status, reason: "too small"});
            window.__xmuPDFBytes = new Uint8Array(ab);
            return JSON.stringify({ok: true, status: r.status, contentType: ct, size: ab.byteLength});
        }
    )()"""

    r = requests.post(f"{PROXY}/eval", params={"target": tab}, data=fetch_js, timeout=120)
    val = r.json().get("value", "")
    if isinstance(val, dict):
        # 处理嵌套 value
        inner = val.get("value", val)
        if isinstance(inner, (dict, str)):
            val = inner
    if isinstance(val, str):
        import json
        val = json.loads(val)

    if not val or not val.get("ok"):
        return None

    size = val["size"]
    import base64
    with open(out_path, "wb") as f:
        for start in range(0, size, 262144):
            end = min(start + 262144, size)
            chunk_js = f"""(
                () => {{
                    const bytes = window.__xmuPDFBytes.slice({start}, {end});
                    let bin = "";
                    for (let i = 0; i < bytes.length; i += 0x8000)
                        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
                    return btoa(bin);
                }}
            )()"""
            cr = requests.post(f"{PROXY}/eval", params={"target": tab}, data=chunk_js, timeout=120)
            cv = cr.json().get("value", "")
            if isinstance(cv, dict):
                cv = cv.get("value", cv)
            f.write(base64.b64decode(cv))

    return out_path.stat().st_size


# Elsevier PII 缓存
_elsevier_pii = {}
_elsevier_lock = threading.Lock()


def resolve_elsevier_pii(doi: str) -> Optional[str]:
    """通过 doi.org 重定向获取 Elsevier 真实 PII"""
    with _elsevier_lock:
        if doi in _elsevier_pii:
            return _elsevier_pii[doi]

    try:
        tab = proxy_new_tab()
        requests.get(f"{PROXY}/navigate",
                     params={"target": tab, "url": f"https://doi.org/{doi}"},
                     timeout=30)
        pii = None
        for _ in range(10):
            time.sleep(2)
            try:
                r = requests.post(f"{PROXY}/eval", params={"target": tab},
                                data="location.href", timeout=10)
                url = r.json().get("value", "")
                if url and "/pii/" in url:
                    pii = url.split("/pii/")[1].split("?")[0].split("#")[0]
                    break
            except Exception:
                pass
        proxy_close(tab)
        if pii:
            with _elsevier_lock:
                _elsevier_pii[doi] = pii
            return pii
    except Exception:
        pass
    return None


def fix_url(row: dict) -> str:
    """修复已知的 URL 模式问题。"""
    url = row["url"].strip()

    # RSC: /articlepdf/d4ee02970d → /articlepdf/2024/ee/d4ee02970d
    if "pubs.rsc.org/en/content/articlepdf/" in url:
        parts = url.split("/articlepdf/")
        if len(parts) == 2:
            code = parts[1].split("?")[0].split("#")[0]
            rest = parts[1]
            if "/" not in rest and len(code) > 4:
                year = 2020 + int(code[1]) if code[1].isdigit() else 2024
                journal = code[2:4]
                url = f"https://pubs.rsc.org/en/content/articlepdf/{year}/{journal}/{code}"

    # Wiley: /doi/pdf/ → /doi/pdfdirect/
    if "onlinelibrary.wiley.com/doi/pdf/" in url:
        url = url.replace("/doi/pdf/", "/doi/pdfdirect/")

    # Elsevier: 错误的 PII → 正确 PII（通过 doi.org 解析）
    if "sciencedirect" in url:
        doi = row.get("doi", "").strip()
        if doi:
            pii = resolve_elsevier_pii(doi)
            if pii:
                url = f"https://www.sciencedirect.com/science/article/pii/{pii}/pdfft"

    return url


def download_one(row: dict, idx: int, total: int) -> tuple:
    """
    下载单篇。先尝试文件监控（ACS 行为），后尝试 JS fetch（Science/Wiley 行为）。
    """
    url = fix_url(row)
    filename = row["filename"].strip()
    out_path = OUT_DIR / filename

    if out_path.exists() and out_path.stat().st_size > 1024:
        return ("skip", filename, out_path.stat().st_size)

    tab = proxy_new_tab()
    my_snapshot = snap_files()

    try:
        proxy_navigate(tab, url)

        # 先等几秒看是否有文件下载（ACS 模式）
        dl_file = wait_for_new_download(my_snapshot, timeout=8)
        if dl_file and dl_file.exists():
            if out_path != dl_file:
                shutil.move(str(dl_file), str(out_path))
            return ("ok", filename, out_path.stat().st_size)

        # 没有文件下载，等待 Cloudflare 自解（Elsevier/ScienceDirect 需要 ~30s）
        for cf_wait in range(15):
            time.sleep(2)
            try:
                r = requests.post(f"{PROXY}/eval", params={"target": tab},
                    data="document.title", timeout=10)
                title = r.json().get("value", "")
                if title and "请稍候" not in title and "Just a moment" not in title:
                    break
            except Exception:
                pass

        # 尝试 JS fetch（Science/RSC/Wiley/Elsevier 模式）
        size = fetch_pdf_via_js(tab, out_path)
        if size and size > 1024:
            return ("ok", filename, size)
        else:
            return ("fail", filename, 0)

    except Exception as e:
        return ("fail", filename, 0)
    finally:
        proxy_close(tab)


def main():
    limit = TEST_LIMIT
    workers = MAX_WORKERS
    for i, a in enumerate(sys.argv[1:]):
        if a == "--limit" and i + 1 < len(sys.argv) - 1:
            limit = int(sys.argv[i + 2])
        elif a == "--workers" and i + 1 < len(sys.argv) - 1:
            workers = int(sys.argv[i + 2])

    print("=" * 55)
    print(f"  批量 PDF 下载 (WebVPN + CDP, {workers} 线程)")
    print("=" * 55)

    rows = read_urls(limit=limit)
    total = len(rows)
    print(f"\n共 {total} 篇{'（测试模式）' if limit else ''}")
    print(f"输出: {OUT_DIR.resolve()}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 初始化基准文件列表
    global _before_files
    _before_files = set(f.name for f in DOWNLOADS_DIR.iterdir() if f.is_file())

    stats = {"ok": 0, "skip": 0, "fail": 0}
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(download_one, row, i, total): (i, row)
            for i, row in enumerate(rows)
        }

        completed = 0
        for future in as_completed(futures):
            status, filename, size = future.result()
            stats[status] += 1
            completed += 1
            elapsed = time.time() - start_time
            speed = completed / elapsed if elapsed > 0 else 0
            emoji = {"ok": "✓", "skip": "⏭", "fail": "✗"}
            size_str = f"({size/1024:.0f}KB)" if size > 0 else ""
            safe_print(
                f"[{completed}/{total}] {emoji[status]} {filename} {size_str}"
                f" | {elapsed:.0f}s | {speed:.2f}/s"
            )

    elapsed = time.time() - start_time
    print(f"\n{'='*45}")
    print(f"完成 | {elapsed:.0f}s | "
          f"下载 {stats['ok']} | 跳过 {stats['skip']} | 失败 {stats['fail']}")


if __name__ == "__main__":
    main()
