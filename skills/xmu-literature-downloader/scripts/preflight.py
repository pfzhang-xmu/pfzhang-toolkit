#!/usr/bin/env python3
"""
下载前置自检 —— 在跑 batch_download.py 之前先跑这个，避免白跑一轮。

检查 5 件事（第 5 项为选检）：
  1. CDP 代理（:3456）是否在跑
  2. Edge 远程调试端口（:9222 / :9223）哪个真正可用
  3. WebVPN 是否已登录（读页面文本 + cookie 双判据）
  4. 出版商是否真的授了权（拿一篇公开可用的文献做探针）
  5. [选检] Elsevier / libproxy 智能网关通路是否可用

用法:
  python3 preflight.py                # 全量检查
  python3 preflight.py --probe        # 额外做一次真实取字节探针（需联网，更慢）
退出码: 0 = 全部就绪；1 = 有阻塞项（按提示处理）
"""

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Optional

# ---- 关键：屏蔽宿主注入的 http_proxy，否则连本机 3456 会被转发到 55785 而失败 ----
for _v in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

import requests  # noqa: E402

PROXY = "http://127.0.0.1:3456"
CANDIDATE_PORTS = [9222, 9223]

# 探针用一篇公开可下载的 ACS 文章（只取首块，不落盘）
PROBE_URL = "https://pubs.acs.org/doi/10.1021/jacs.3c05556"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = RESET = ""


def _session() -> requests.Session:
    """不受环境代理影响的 session —— 访问本机 CDP 代理必须用它。"""
    s = requests.Session()
    s.trust_env = False
    return s


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET} {msg}")


def bad(msg: str) -> None:
    print(f"  {RED}✗{RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}!{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {DIM}·{RESET} {msg}")


def head(title: str) -> None:
    print(f"\n{title}")


# ---------------------------------------------------------------- 1. CDP 代理
def check_proxy() -> bool:
    head("[1/5] CDP 代理 (:3456)")
    try:
        r = _session().get(f"{PROXY}/health", timeout=4)
        if r.status_code == 200:
            ok(f"代理存活  {r.text.strip()}")
            return True
        bad(f"代理返回 HTTP {r.status_code}")
    except Exception as e:
        bad(f"连不上代理: {type(e).__name__}: {str(e)[:80]}")
    print(f"    {DIM}启动: node ~/.claude/skills/xmu-literature-downloader/scripts/cdp_proxy.mjs{RESET}")
    return False


# ------------------------------------------------------------ 2. Edge CDP 端口
def check_browser() -> Optional[int]:
    head("[2/5] Edge 远程调试端口")
    for port in CANDIDATE_PORTS:
        try:
            r = _session().get(f"http://127.0.0.1:{port}/json/version", timeout=4)
            if r.status_code == 200 and "webSocketDebuggerUrl" in r.text:
                ok(f":{port} 可用  ({r.json().get('Browser', '?')})")
                return port
        except Exception:
            continue
    bad("9222 / 9223 都没有可用的 CDP 端点")
    print(f"    {DIM}启动: bash ~/.claude/skills/xmu-literature-downloader/scripts/start_cdp.sh{RESET}")
    return None


# -------------------------------------------------------------- 3. WebVPN 登录
def check_webvpn() -> bool:
    head("[3/5] WebVPN 登录态")
    try:
        r = _session().get(f"{PROXY}/targets", timeout=8)
        targets = r.json()
    except Exception as e:
        bad(f"拿不到标签页列表: {type(e).__name__}")
        return False

    webvpn_tabs = [t for t in targets if t.get("type") == "page" and "webvpn.xmu.edu.cn" in t.get("url", "")]
    if not webvpn_tabs:
        warn("没找到 webvpn.xmu.edu.cn 标签页（可能没开或已关闭）")
        print(f"    {DIM}在 Edge 中打开 https://webvpn.xmu.edu.cn/ 登录后重跑{RESET}")
        return False

    tid = webvpn_tabs[0]["id"]
    try:
        r = _session().post(
            f"{PROXY}/eval",
            params={"target": tid},
            data="JSON.stringify({url:location.href,title:document.title,"
                 "text:document.body.innerText.replace(/\\s+/g,' ').slice(0,400),"
                 "cookie:document.cookie})",
            timeout=30,
        )
        val = r.json().get("value", "")
        info = json.loads(val) if isinstance(val, str) else val
    except Exception as e:
        bad(f"读取 WebVPN 页面失败: {type(e).__name__}")
        return False

    url = str(info.get("url", ""))
    text = str(info.get("text", ""))
    cookie = str(info.get("cookie", ""))

    # 判据：登录成功页会显示「XXX 欢迎您」；未登录会停在 /login 且有用户名输入框
    logged_in = ("欢迎您" in text) and ("/login" not in url)
    if logged_in:
        who = text.split("资源搜索")[0].strip()
        ok(f"已登录  {who[:40]}")
    else:
        bad(f"未登录（当前 {url}）")
        print(f"    {DIM}请在 Edge 中手动登录 https://webvpn.xmu.edu.cn/（学号 + VPN 密码），"
              f"登录后应能看到「欢迎您」{RESET}")
        return False

    if "wengine_vpn_ticket" in cookie or "heartbeat=1" in cookie:
        ok("会话 cookie 正常")
    else:
        warn("没找到 wengine_vpn_ticket / heartbeat cookie，可能已过期，建议重新登录")
    return True


# ------------------------------------------------------------ 4. 出版商授权探针
def check_entitlement(do_probe: bool) -> bool:
    head("[4/5] 出版商授权")
    if not do_probe:
        info("跳过真实探针（加 --probe 开启）")
        info("提示：授权是按出口 IP + 浏览器会话判定的，纯 IP 检查无法替代真实取字节")
        return True

    try:
        r = _session().get(f"{PROXY}/new", params={"url": "about:blank"}, timeout=20)
        tid = r.json().get("targetId") or r.json().get("id")
    except Exception as e:
        bad(f"无法新建标签页: {type(e).__name__}")
        return False

    # 探针的候选选择器必须与 batch_download.py 的 FETCH_JS 保持一致，
    # 否则会出现「下载能成但探针说没权限」的假阴性。
    #
    # 关键改进：**直接从 batch_download.py 导入 FETCH_JS**，不再各自维护一份。
    # 坑（真实踩过）：早期版本两边各写一份选择器，改了下载端忘了改探针端 →
    # 自检报「未就绪」而实际下载完全正常。两份逻辑必然漂移。
    try:
        import importlib.util
        _bd_path = Path(__file__).resolve().parent.parent / "batch_download.py"
        _spec = importlib.util.spec_from_file_location("_xmu_bd", _bd_path)
        _bd = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_bd)
        FETCH_JS = _bd.FETCH_JS
        # 复用同一套候选逻辑，但开启探针模式（只读首块即中止，不下载整篇）
        PROBE_JS = "const __PROBE__ = true;\n" + FETCH_JS
    except Exception as e:
        bad(f"无法加载 batch_download.py 的取字节逻辑: {type(e).__name__}: {str(e)[:60]}")
        return False

    try:
        _session().get(f"{PROXY}/navigate",
                       params={"target": tid, "url": f"https://pubs.acs.org/doi/10.1021/jacs.3c05556"},
                       timeout=40)

        # 出版商落地页是 SPA / 有 Cloudflare 挑战，链接可能延迟渲染 → 轮询重试
        last = {}
        for attempt in range(1, 5):
            time.sleep(5 if attempt == 1 else 4)
            r = _session().post(f"{PROXY}/eval", params={"target": tid}, data=PROBE_JS, timeout=90)
            val = r.json().get("value", "")
            res = json.loads(val) if isinstance(val, str) else val
            if isinstance(res, dict) and res.get("ok"):
                ok(f"授权正常  (探针 {res.get('status')} {res.get('ct')})")
                return True
            last = res if isinstance(res, dict) else {"why": str(res)}
            # 已经找到候选链接但取不到字节，是真失败，不必再等
            if last.get("why") == "no-pdf-bytes" and last.get("cands"):
                break

        bad(f"探针未取到 PDF: {last.get('why')} | {str(last.get('text',''))[:100]}")
        if last.get("why") == "no-pdf-bytes" and not last.get("cands"):
            print(f"    {DIM}落地页没有 PDF 候选 —— 可能是 Cloudflare 挑战未过或页面未渲染完；"
                  f"也可在 Edge 中直接打开该文章确认{RESET}")
        else:
            print(f"    {DIM}若为 403/HTML，多为未登录或未走通 WebVPN；先完成第 3 步{RESET}")
        return False
    except Exception as e:
        bad(f"探针异常: {type(e).__name__}: {str(e)[:80]}")
        return False
    finally:
        try:
            _session().get(f"{PROXY}/close", params={"target": tid}, timeout=10)
        except Exception:
            pass


# ------------------------------------------------- 5. Elsevier / libproxy 通路
def check_elsevier_route() -> bool:
    """检查 Elsevier 通路：libproxy 智能网关是否已登录 + 能否拿到 PDF 字节。

    坑：Elsevier 直连必撞 Cloudflare Turnstile，必须走 libproxy 入口。
    这两项若不通，Elsevier 类的下载会全部失败，而其他出版社不受影响 ——
    所以单独列一项，避免用户以为「自检全绿就什么都能下」。
    """
    head("[5/5] Elsevier / libproxy 通路（选检）")
    import urllib.parse

    PII = "S0926337324011597"
    PDF = f"https://www.sciencedirect.com/science/article/pii/{PII}/pdfft"
    entry = "https://libproxy.xmu.edu.cn/go?url=" + urllib.parse.quote(PDF, safe="")

    try:
        r = _session().get(f"{PROXY}/new", params={"url": "about:blank"}, timeout=20)
        tid = r.json().get("targetId") or r.json().get("id")
    except Exception as e:
        bad(f"无法新建标签页: {type(e).__name__}")
        return False

    try:
        _session().get(f"{PROXY}/navigate", params={"target": tid, "url": entry}, timeout=60)
        time.sleep(12)

        js = """(async () => {
            const ct = document.contentType || '';
            const txt = (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,200);
            if (ct.toLowerCase().indexOf('application/pdf') !== 0)
                return JSON.stringify({ok:false, why:'not-pdf-page', ct:ct, text:txt,
                                       url:location.href});
            try {
                const r = await fetch(location.href, {credentials:'include'});
                const ab = await r.arrayBuffer();
                const head = String.fromCharCode(...new Uint8Array(ab.slice(0,5)));
                return JSON.stringify({ok: head==='%PDF-', head:head, len:ab.byteLength,
                                       ct:r.headers.get('content-type'), url:location.href});
            } catch(e) { return JSON.stringify({ok:false, why:'fetch-failed', err:String(e).slice(0,80)}); }
        })()"""
        r = _session().post(f"{PROXY}/eval", params={"target": tid}, data=js, timeout=150)
        val = r.json().get("value", "")
        res = json.loads(val) if isinstance(val, str) else val
        if res.get("ok"):
            ok(f"Elsevier 通路正常  (经 libproxy，取到 {int(res.get('len',0))/1024/1024:.1f} MB PDF)")
            return True
        bad(f"Elsevier 通路未通: {res.get('why') or res.get('head')} | {str(res.get('text',''))[:80]}")
        if "log in" in str(res.get("text", "")).lower() or "登录" in str(res.get("text", "")):
            print(f"    {DIM}libproxy 需要登录 —— 请在 Edge 中打开 https://libproxy.xmu.edu.cn/ 完成厦大统一身份认证{RESET}")
        else:
            print(f"    {DIM}若报 robot/captcha，说明 libproxy 会话未生效或该文章未订阅{RESET}")
        return False
    except Exception as e:
        bad(f"Elsevier 探针异常: {type(e).__name__}: {str(e)[:80]}")
        return False
    finally:
        try:
            _session().get(f"{PROXY}/close", params={"target": tid}, timeout=10)
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="XMU 文献下载前置自检")
    ap.add_argument("--probe", action="store_true", help="额外做一次真实取字节探针（更慢但最可靠）")
    ap.add_argument("--check-elsevier", action="store_true",
                    help="额外检查 Elsevier/libproxy 通路（选检，不阻塞；下 Elsevier 时建议开）")
    args = ap.parse_args()

    print("=" * 60)
    print("  XMU 文献下载 · 前置自检")
    print("=" * 60)

    results = {
        "proxy": check_proxy(),
        "browser": check_browser() is not None,
        "webvpn": check_webvpn(),
        "entitlement": check_entitlement(args.probe),
    }

    # Elsevier 通路是「选检」：不通只影响 Elsevier 类下载，不该阻塞其他出版社
    if args.check_elsevier or args.probe:
        elsevier_ok = check_elsevier_route()
        if not elsevier_ok:
            print(f"    {DIM}↑ 选检项：不影响 ACS/Wiley/RSC 下载；仅当你要下 Elsevier 时才需处理{RESET}")

    print("\n" + "=" * 60)
    blockers = [k for k, v in results.items() if not v]
    if blockers:
        print(f"  {RED}未就绪{RESET} —— 阻塞项: {', '.join(blockers)}")
        print("  处理完上面的提示后重跑本脚本，再执行 batch_download.py")
    else:
        print(f"  {GREEN}全部就绪{RESET} —— 可以执行 batch_download.py")
    print("=" * 60)
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
