---
name: xmu-literature-downloader
description: Use this skill whenever the user wants to use their own logged-in Xiamen University Library, WebVPN, CAS SSO, or Edge/Chrome browser session to batch-download academic PDFs from ACS, Wiley, Elsevier (ScienceDirect), RSC, Science/AAAS, and other publishers. Trigger on requests like "用厦大图书馆下载文献", "批量下载论文PDF", "WebVPN下载", "通过机构访问下载期刊论文", or when the user provides a TSV/CSV of paper URLs and wants automated downloading.
metadata:
  compatibility: Requires Microsoft Edge (or Chrome) with remote debugging, Node.js 22+, Python 3.9+, and XMU WebVPN access. Uses only user-authorized institutional access. Tested on macOS + Edge 149.
---

# XMU Literature Downloader

批量下载学术论文 PDF 的完整方案。通过厦大 WebVPN + 浏览器 CDP 协议实现全自动下载，覆盖 ACS、Wiley、Elsevier、RSC、Science 五大出版社。

## 架构概览

```
batch_download.py ──HTTP──▶ CDP Proxy (:3456) ──WebSocket──▶ Edge (:9223)
     │                            │                              │
     │  DOI → 落地页解析          │  连接池复用                   │  已登录 WebVPN
     │  多路线降级链               │  持久 WS 连接                │  机构 IP
     │  页面内 JS fetch 取字节     │  端口自动探测                 │
     │  PDF 真实性校验            │                              │
     ▼                            ▼                              ▼
  pdfs/                    每个 tab 一个 WS               publisher sites
```

**核心原理**：不是绕过 Cloudflare，而是通过厦大 WebVPN 获得干净的机构 IP + 浏览器自动化登录，让 Cloudflare 只弹可自动解决的 JS 挑战（或根本不弹）。

### v4 通用化（2026-10-21 晚）

早期版本只支持 4 家出版社（ACS / Wiley / RSC / Elsevier），因为 `resolve_publisher_url()` 靠
**DOI 前缀硬编码** URL 规则。v4 改为「**DOI → 浏览器解析真实落地页 → 落地页上找 PDF**」的
通用路线，PDF 直链规则降级为兜底。**实测 13 家出版社零失败**（见下方实测数据）。

| 出版社 | 走通的路线 | 关键点 |
|--------|-----------|--------|
| ACS | 落地页 / PDF直链 | 落地页 `article-pdf` 链接 |
| Wiley | 落地页 / PDF直链 | `advanced.onlinelibrary.wiley.com` 子域 |
| RSC | 落地页 | 落地页同时挂主文与 SI，**必须排除 SI** |
| Elsevier | 落地页(libproxy) | PII 需从 doi.org 解析 |
| Science (AAAS) | 落地页 | `/doi/pdf/{DOI}?download=true` |
| Sci Reports | 落地页 | `citation_pdf_url` meta |
| Springer Link | 落地页 | `/content/pdf/{DOI}.pdf` |
| MDPI | 落地页 | 路径末段就是 `/pdf` |
| Frontiers | 落地页 | `/articles/{DOI}/pdf` |
| Taylor & Francis | 落地页 | `/doi/pdf/{DOI}` |
| APS | 落地页 | 期刊名在落地页 URL 里 |
| OUP | 落地页 | `/article-pdf/...` |
| IEEE | **IEEE-stampPDF** | PDF 藏在 stamp 包装页的 iframe 里 |

---

## ⛔ 下载前必读（务必按序执行，不要跳过）

> 这一节是 2026-10-21 实测复盘后新增的。跳过它会白跑一整轮下载。

### 步骤 0：先提醒用户登录 WebVPN（**必须主动提醒，不能等用户自己发现**）

在开始下载前，**主动告知用户**：

> 请先在 Edge 中打开 <https://webvpn.xmu.edu.cn/> 并登录（学号 + VPN 密码）。
> 登录成功的标志是页面出现「XXX 欢迎您」字样。
> 我会先跑一次自检确认登录态再开始下载。

**为什么必须提醒**：WebVPN 未登录时，页面停在 `/login`，所有出版社拿到的都是 HTML 错误页而不是 PDF。
本 skill 早期版本不提醒，用户是在"图书馆网站自己打开了但没登录"的情况下自行发现的 —— 这类静默失败最浪费时间。

### 步骤 1：跑前置自检

```bash
python3 ~/.claude/skills/xmu-literature-downloader/scripts/preflight.py --probe
```

自检 4 项（任一不过就别下载）：

| # | 检查项 | 通过判据 | 不通过怎么办 |
|---|--------|---------|-------------|
| 1 | CDP 代理 :3456 | `/health` 返回 `{"ok":true}` | `bash scripts/start_cdp.sh` |
| 2 | Edge CDP 端口 | `:9223/json/version` 返回含 `webSocketDebuggerUrl` | `bash scripts/start_cdp.sh` |
| 3 | WebVPN 登录态 | 页面文本含「欢迎您」且 URL 不含 `/login` | **提示用户手动登录** |
| 4 | 出版商授权 | 探针取到 `%PDF-` 魔数 | 重新登录 WebVPN，或改用 `--no-probe` 延后判定 |

退出码 `0` = 全部就绪；`1` = 有阻塞项，**按提示处理完再重跑**。

### 步骤 2：执行下载

```bash
python3 ~/.claude/skills/xmu-literature-downloader/batch_download.py --limit 0 --workers 3
```

### 登录到底提供了什么？（实测结论）

**答：登录是必要的准备条件，但授权机制是 IP-based 而非 cookie-based。**

实测证据：用 CDP `Target.createBrowserContext` 创建一个**零 cookie** 的隔离上下文，重新请求 ACS / Wiley / RSC 的 PDF，
仍全部返回 200 + 完整 PDF（6.3 MB / 6.0 MB / 6.1 MB），且 ACS PDF 请求的 `Cookie` 头为空。

结论拆成两层：
- **必须登录** —— 登录建立了通往机构资源的授权通道（`wengine_vpn_ticket` cookie + heartbeat + 校园网出口 IP）。
  退出登录后浏览器不再走机构出口，出版社看到的是校外 IP，直接给付费墙。
- **不必带 cookie** —— 出版社侧只认出口 IP，所以即使清了 cookie 也能拿到全文。
- **例外** —— Elsevier/ScienceDirect 直连会被 Cloudflare 机器人校验拦住，但**已通过厦大 libproxy 智能网关攻克**（见「坑 8」），无需人工交互。

---

## 六大实测坑位（复盘新增，每条都真实踩过）

### 坑 1：`http_proxy` 劫持本机请求 → 误判"代理没启动"

**现象**：`curl http://127.0.0.1:3456/health` 返回
`upstream connect failed: Connection refused (os error 61)`，明明代理进程活着。

**根因**：WorkBuddy / Clash / 公司 VPN 会注入 `http_proxy=http://127.0.0.1:55785`，
它把对 `127.0.0.1` 的请求也劫持转发，导致本机 CDP 代理的请求走不通。

**解法（三层）**：
```python
# 1) 必须在 import requests 之前清掉环境变量
for _v in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","all_proxy"):
    os.environ.pop(_v, None)
os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"]  = "localhost,127.0.0.1,::1"

# 2) 用 trust_env=False 的 session 双保险
s = requests.Session(); s.trust_env = False

# 3) 命令行 curl 必须加 --noproxy '*'
curl -s --noproxy '*' http://127.0.0.1:3456/health
```

**副作用注意**：沙箱内 `lsof -ti :3456` 看不到宿主进程（返回空），会进一步加深误判。
**不要**因为 `lsof` 看不到就断定代理没起 —— 用 `curl --noproxy '*'` 探活才准。

### 坑 2：CDP 端口错配 9222 vs 9223 → 代理"活着但连不上"

**现象**：9222 端口在监听，但 `/json/version` 返回 **404**（不是 CDP 端点）；9223 才返回 200。

**根因**：`start_cdp.sh` 把 Edge 起在 9223，`cdp_proxy.mjs` 默认连 9222，两边没对齐。
9222 常常被别的进程占着，看起来"端口通了"其实根本不是 CDP。

**解法（已内置）**：
- `cdp_proxy.mjs` 启动时依次探测 `CDP_BROWSER_PORT` → 9223 → 9222，
  命中含 `webSocketDebuggerUrl` 的那个端口，并在日志打印 `[cdp-proxy] 浏览器 CDP 端口自动探测命中: 9223`
- `start_cdp.sh` 补上了 `export CDP_BROWSER_PORT=$CDP_PORT`（之前只是 shell 局部变量，子进程读不到）

**排查口诀**：端口在监听 ≠ 端口可用。一定要看 `/json/version` 的 JSON 体。

### 坑 3：重复启动代理 → EADDRINUSE 直接崩

**现象**：`Error: listen EADDRINUSE: address already in use :::3456`

**解法**：`start_cdp.sh` 现在会先探活，已健康则**直接复用并退出**，不重复启动。
手工重启时先 `kill $(lsof -ti :3456)`。

### 坑 4：后台进程随 shell 退出被回收

**现象**：`nohup node cdp_proxy.mjs &` 起的代理，下一条命令就没了。

**根因**：非交互 shell 退出时回收子进程；`nohup`/`setsid` 也挡不住宿主的进程组清理。

**解法**：用工具的后台任务机制启动（`run_in_background=true`），别用 `&`。

### 坑 5：`~/Downloads` 文件监控的并发竞态

**现象**：多线程同时下载时，A 线程会把 B 线程刚下载的文件当成自己的搬走。

**解法**：**已废弃文件监控路径**。现在统一走「页面内 fetch 取字节」——
在页面上下文中 `fetch(url, {credentials:'include'})` → `arrayBuffer()` → 存 `window.__xmuPDFBytes` →
分块 base64 经 CDP 传回 Python。全程不碰文件系统，无竞态。

### 坑 6：下到 HTML 却当成 PDF

**现象**：文件名叫 `xxx.pdf`，实际是出版商的错误页 / 付费墙 HTML。

**解法**：`validate_pdf()` 四重校验，任一不过就判定失败并自动重试：
1. `len(data) >= 2048`
2. 魔数 `data.startswith(b"%PDF-")`
3. 尾标 `b"%%EOF" in data[-4096:]`（检测截断）
4. PyMuPDF 解析页数 + 首页文本命中 TSV `title` 的关键词（防下错文章）

**同时**：已存在但校验不通过的文件会被**重新下载**，而不是被静默跳过。

### 坑 7：`str.format()` 撞上 JS 花括号

**现象**：所有文章统一报
`ValueError: unexpected '{' in field name`，一个都下不下来。

**根因**：用 `CHUNK_JS.format(start=..., end=...)` 注入参数，
但 `CHUNK_JS` 是 JS 代码，里面全是 `{ }`（`() => { ... }`），
Python 的 `str.format()` 把 `=> {` 当成替换字段的开头，直接抛 `ValueError`。

**解法**：JS 模板**不要用 `.format()`**。改用显式占位符做字面替换：
```python
CHUNK_JS = """(() => {
    const bytes = window.__xmuPDFBytes.slice(__START__, __END__);
    ...
})()"""

def _chunk_js(start: int, end: int) -> str:
    return CHUNK_JS.replace("__START__", str(start)).replace("__END__", str(end))
```

**教训**：这坑在 v2 重写时是**真实发生**的 —— 改完代码必须真跑一遍才算验证过。
"逻辑看着对"不等于"跑得通"。

### 坑 8：Elsevier / ScienceDirect 的 Cloudflare 机器人验证 —— 已用 libproxy 智能网关攻克

**现象**：直连 `www.sciencedirect.com` 时页面标题是「请稍候…」，
正文是 `Are you a robot? Please confirm you are a human by completing the captcha challenge below`，
8 轮 40 秒都不自解（需要交互点击）。

**穷举过的所有路径与结论**：

| 尝试 | 结果 |
|------|------|
| 直连文章页 | CF Turnstile 交互挑战，40 s 不自解 |
| 裸 curl 访问 | 403 |
| 在文章页内 fetch `pdfft` 链接 | **200 + text/html ~52 KB（CF 挑战页，不是 PDF）** |
| `api.elsevier.com` + `Accept: application/pdf` | 406 `Accept header value 'application/pdf' is restricted` |
| `api.elsevier.com` + `httpAccept=text/xml` | 200，但**只有 412 字符元数据**（无 abstract/section/参考文献）→ `view=FULL` 需 API key |
| `linkinghub.elsevier.com/retrieve/pii/...` | CF 挑战 |
| **`libproxy.xmu.edu.cn/go?url=<目标URL>`** | **✓ 成功** |

**解法（三步，缺一不可）**：

```
1. 导航到 https://libproxy.xmu.edu.cn/go?url=<PDF_URL>
   └─ 厦大「电子资源智能网关」做机构认证 + URL 重写，请求带机构凭据 → CF 放行
   └─ 页面显示 "Brought to you by: Xiamen University"

2. 必须用「导航」打开 PDF，不能用 fetch
   └─ CF 按 Sec-Fetch-Dest 区分：导航=document（放行），fetch=empty（拦成 HTML）
   └─ 导航到 pdfft → 302 到 pdf.sciencedirectassets.com → 浏览器内联渲染 PDF
   └─ 实测 contentType=application/pdf，URL 已落在 PDF CDN 域内

3. 导航后 location.href 已在 PDF 域内 → 同源 fetch(location.href, {credentials:'include'})
   └─ 200 + application/pdf，拿到完整字节（实测 8.5 MB / 9.8 MB）
```

**关键坑**：
- ❌ 在文章页里用 fetch 请求 `pdfft` 链接 → 必然拿到 CF 的 HTML（52 KB），因为 fetch 不是导航
- ❌ 以为 `api.elsevier.com` 200 就是拿到全文 → 实际只有元数据
- ✅ 判断当前页是不是 PDF：`document.contentType === 'application/pdf'`
- ✅ Elsevier 的 PII（如 `S0926337324011597`）**无法从 DOI 推导**，必须走 `doi.org` 的 302 重定向解析

**集成方式**：`download_one()` 现在是**多路线降级链**——落地页 → libproxy 落地页 → PDF 直链 → libproxy 直链。
`resolve_elsevier_pii()` 负责 DOI→PII 解析（仅 DOI、无 url 的清单也能跑）。

---

### 坑 9：JS 正则字面量里的 `/` 未转义 —— 整段 FETCH_JS 静默失效

**现象**：v4 改用「落地页上找 PDF 链接」路线后，**所有落地页路线全部失败**，
只有走 PDF 直链的（ACS/Wiley/RSC）能成功。报错是 `no-pdf-bytes`，看不出任何语法问题。

**根因**：`PDF_LINK_RE` 里写的是未转义的 `/`：

```javascript
const PDFRE = /(\.pdf(\?|$|/)|\/pdf\b|...)/i;
//                    ↑ 这个 / 提前结束了正则字面量
```

JS 解析器在第一个未转义的 `/` 处结束正则字面量，后面的 `/i` 被当成除法运算符 →
整段 FETCH_JS 抛 **SyntaxError** → 页面内 fetch 永远返回空。

**为什么极具迷惑性**：
- 只有走 `FETCH_JS` 的**落地页路线**会挂，走 `SELF_FETCH_JS`（无正则）的 **PDF 直链路线照常成功**
- 报错信息是业务层面的「没找到 PDF」，完全没有语法错误提示
- 极易误判成「这些出版社不给 PDF」或「页面没渲染完」

**修法**：正则字面量里每个 `/` 都写成 `\/`。

**防复发**：`batch_download.py` 启动时用 `node --check` 自动校验所有内嵌 JS 的语法
（`selfcheck_js()`），有错直接拒绝运行。这一个自检换来一整类静默失败的免疫。

---

### 坑 10：doi.org 跳转地址 ≠ 出版社落地页

**现象**：用 Crossref 拿到的 `URL` 字段当落地页，导航过去后页面 body 是空的，
收集不到任何 PDF 候选。

**根因**：Crossref 的 `URL` 几乎总是 `https://doi.org/{DOI}` —— 这是**跳转地址**。
即使浏览器会 302 过去，落地的出版社页面多为 SPA，在拿到最终 URL 前 body 还没渲染。

**修法**：`doi_landing_via_browser()` —— 在浏览器里导航 `https://doi.org/{DOI}`，
轮询 `location.href` 直到不再是 doi.org，拿到的就是出版社真实落地页：

```
10.3390/electronics14193892 → https://www.mdpi.com/2079-9292/14/19/3892
10.1016/j.apcatb.2024.124845 → https://www.sciencedirect.com/science/article/pii/S0926337324011597?via=ihub
10.1109/toh.2023.3269086     → https://ieeexplore.ieee.org/document/10109195
```

这一步是**通用化的关键** —— 不必为每家出版社硬编码文章页 URL 规则。

---

### 坑 11：落地页同时挂主文与补充材料 → 下到 SI 而不是主文

**现象**：RSC 那篇 EES 下到的是 **54 页的 Supporting Information**，而主文只有 12 页。
文件完全合法（魔数、尾标、页数都对），标题校验也过了 —— 但内容不是要的东西。

**根因**：RSC 落地页同时挂两个 PDF：
```
https://pubs.rsc.org/ee/article-pdf/17/12/4044/9092280/d3ee04338j.pdf   ← 主文
https://pubs.rsc.org/ee/article-supplement/807781/pdf/d3ee04338j2_suppl/ ← SI
```

**修法**（两道）：
1. `SI_MARK_RE` 排除含 `supplement|suppl|supporting|mmc\d|attachment` 的链接
2. 候选**主文优先排序** —— URL 含主文 DOI（`citation_doi` meta）��排前面

**教训**：PDF 校验通过 ≠ 内容正确。页数异常（54 vs 12）是个强信号，值得单独看。

**⚠️ 排除规则不能太宽**：OUP 的正常文章路径里就含 `Supplement_1`
（`/pch/article-pdf/29/Supplement_1/e32/.../pxae067.pdf`）—— 那是**增刊期号**，
不是补充材料。按裸 `supplement` 匹配会把主文一起误杀（实测 OUP 因此全败）。

最终规则只匹配**表示补充材料文件的特征**：
`article-supplement | _suppl | suppl/ | downloadSupplement | supporting-info | mmc\d | attachment | /esi/ | si.pdf | -sup-N`

---

### 坑 12：libproxy 会话是浏览器级共享状态 —— 并发访问会触发认证风暴

**现象**：4 线程并发跑 13 篇，OUP 和 APS 随机失败，错误页是
「账号登录 / 企业微信扫码登录 / 严禁在本互联网非涉密平台传输…」—— 厦大统一认证页。
**单线程重跑同一批立刻全过**，且 ACS 等非 libproxy 路线完全正常。

**根因**：`libproxy.xmu.edu.cn/go?url=` 的会话（cookie + CAS 单点登录）是**整个浏览器共享**的。
多个标签页同时走网关 → 触发 CAS 重定向风暴 → 部分请求被甩到登录页。

**修法**：libproxy 路线用**全局锁串行化**（`_libproxy_lock`）。非 libproxy 路线仍可并发。

**顺带**：`libproxy` 本身也会话过期，表现为所有出版社一起失败 —— 此时需在浏览器
重新登录一次 `https://libproxy.xmu.edu.cn/`。

---

### 坑 13：正则双重转义 —— `\\b` 在 JS 里不是词边界

**现象**：SI 规则修好后，OUP/APS 反而全败，报错是 `FETCH_JS → {}`（空对象）。

**根因**：用 Python 脚本做字符串替换时，把 `\\b` 写成了 `\\\\b`，
Python 字符串里输出的是 `\\b` —— **JS 正则里 `\\b` 匹配的是「字面反斜杠 + b」**，
不是词边界。更糟的是 `suppl\\/` 里的 `\\` 让正则提前闭合，
后面的 `|supporting-?info|mmc\\d/i` 全部变成语法错误 → 整段 JS 抛异常。

**关键点**：`node --check` 只做**静态语法检查，查不出双重转义**
（`\\b` 在语法上是合法的）。必须**真跑一遍**才能发现。

**修法**：`selfcheck_js()` 做成**两道**：
1. `node --check` —— 静态语法
2. 在 **mock DOM** 里用 node 真执行一遍，捕获运行时异常

第 2 道检查在本次直接抓出了 3 个真 bug（`\\b` / `suppl\\/` / `mmc\\d`），
全部是「静态检查通过但运行时炸」的典型。

---

### 1. 浏览器
- macOS：Microsoft Edge 或 Google Chrome（Chromium 内核）
- 启动时加 `--remote-debugging-port=9223`，并同步设 `CDP_BROWSER_PORT=9223`（见 start_cdp.sh 和 cdp_proxy.mjs 说明）
- 使用持久化 profile：`--user-data-dir=$HOME/.xmu-literature-edge-profile`
- **不需要安装 Chrome**，Edge 完全够用

### 2. CDP 代理
- 位置：`~/.claude/skills/xmu-literature-downloader/scripts/cdp_proxy.mjs`
- 依赖：Node.js 22+, `ws` 包（仓库不分发 `node_modules/`，首次使用需 `npm install`）
- 启动：`node scripts/cdp_proxy.mjs`（监听 3456 端口，浏览器端口自动探测）

### 3. 认证
- 用户需登录 `https://webvpn.xmu.edu.cn/`（学号 + VPN 密码）—— **这一步必须由用户手动完成，skill 只能检测不能代劳**
- 首次使用需在 Edge 中手动登录一次，后续持久化 profile 保持登录状态
- 对于 Wiley：还需要通过 CARSI 做一次厦大 CAS 认证

### 4. Python
- Python 3.9+（注意：类型注解用 `Optional[X]` 不用 `X | None`，因为 macOS 自带 Python 3.9）
- 依赖：`requests`；PDF 校验建议装 `pymupdf`（可选，缺失时降级用 `pypdf`，再降级只做魔数+尾标校验）

---

## 各出版社下载方案

以下方案均基于 **WebVPN 已登录 + Edge 远程调试已开启** 的前提。

### ACS Publications (pubs.acs.org)

| 项目 | 说明 |
|------|------|
| **URL 模式** | `https://pubs.acs.org/doi/pdf/{DOI}`（去掉 `?download=true`） |
| **URL 修复** | 去掉 `?download=true` —— 我们走页面内 fetch，不需要触发浏览器下载 |
| **Cloudflare** | WebVPN 下不弹 CF 或秒过；裸 curl 访问会 403 "Just a moment..."（非付费墙） |
| **下载方式** | **页面内 JS fetch** — 落地页找 PDF 链接 → `fetch()` → base64 分块 |
| **速度** | ~8秒/篇（含页面加载等待） |
| **可靠性** | ~100%（实测 3 篇 JACS 全部成功） |

**易踩的坑**：
- ❌ 裸 curl 访问 pubs.acs.org → 403 + "Just a moment..."，这是 Cloudflare 挑战**不是**付费墙，别误判为"没权限"
- ❌ 依赖 `~/Downloads` 文件监控 → 多线程下会互相抢文件（见坑 5）
- ✅ 正确做法：导航到落地页 → 页面内 fetch PDF 字节 → 分块 base64 传回 → 校验落盘

### Wiley Online Library (onlinelibrary.wiley.com)

| 项目 | 说明 |
|------|------|
| **URL 模式** | `https://onlinelibrary.wiley.com/doi/pdfdirect/{DOI}` |
| **URL 修复** | `/doi/pdf/` → `/doi/pdfdirect/`（关键！原 `/doi/pdf/` 会返回 Error 页） |
| **Cloudflare** | 首次可能弹 CF，5-10 秒自解 |
| **下载方式** | **JS fetch** — 页面内联显示 PDF，通过 `fetch()` 获取字节 |
| **认证要求** | 需要 CARSI 机构登录（见下文"Wiley 认证流程"） |
| **速度** | ~5秒/篇（不含首次 CF 等待） |
| **可靠性** | ~100% |

**Wiley 认证流程**：
1. 导航到 `https://onlinelibrary.wiley.com/action/ssostart`
2. 等待 CF 自解 → 看到 "Institutional Login"
3. 选择 "China CERNET Federation (CARSI)"
4. 找到并点击 "Xiamen University"
5. 跳转到 `ids.xmu.edu.cn/authserver/login` — 输入学号和 VPN 密码
6. 完成后回到 Wiley 首页，显示 "Xiamen University" 即成功

**易踩的坑**：
- ❌ `/doi/pdf/` 返回 Error — 必须用 `/doi/pdfdirect/`
- ❌ 直接用 curl 下载 403 — 需要浏览器 session
- ❌ Wiley 新网站是 SPA — 文章摘要页没有静态 PDF 链接，不能爬页面
- ❌ `/doi/pdfdirect/` 也不支持直接下载（isDownload=false），需要 JS fetch
- ✅ 认证一次后 cookie 全局有效，所有 Wiley 论文都能下

### Elsevier / ScienceDirect (sciencedirect.com)

| 项目 | 说明 |
|------|------|
| **URL 模式** | `https://www.sciencedirect.com/science/article/pii/{PII}/pdfft` |
| **PII 来源** | **无法从 DOI 推导**，必须走 `doi.org` 的 302 重定向解析（`resolve_elsevier_pii()`） |
| **Cloudflare** | 直连必撞 Turnstile 交互验证；**必须走 `libproxy.xmu.edu.cn/go?url=` 智能网关** |
| **下载方式** | 导航到 PDF URL → 浏览器内联渲染 → 同源 `fetch(location.href)` 取字节 |
| **速度** | ~10 秒/篇（含 PII 解析与 libproxy 跳转） |
| **可靠性** | ~100%（2026-10-21 实测 2/2 成功） |

**解法详解见「坑 8」**。核心三点：
1. 入口用 `https://libproxy.xmu.edu.cn/go?url=<urlencode后的目标URL>`（厦大电子资源智能网关，官方支持 Elsevier ScienceDirect）
2. **必须用「导航」打开 PDF**（CF 按 `Sec-Fetch-Dest` 区分导航与 XHR，fetch 会被拦成 HTML）
3. 导航后 `location.href` 已在 `pdf.sciencedirectassets.com` 域内 → **同源** `fetch(location.href)` 取字节

**易踩的坑**：
- ❌ 直连 sciencedirect.com → CF Turnstile 交互挑战，脚本无法自解
- ❌ 在文章页里 fetch `pdfft` 链接 → 200 但 content-type 是 `text/html`（CF 挑战页，~52 KB）
- ❌ 以为 `api.elsevier.com` 返回 200 就是全文 → 实际只有 412 字符元数据
- ❌ 用 TSV 里的假 PII（如 `j.apcatb.2024.123744`）→ 必须用 `doi.org` 解析出的真 PII
- ✅ 页面显示 "Brought to you by: Xiamen University" = 机构凭据已生效，CF 已放行

### RSC Publishing (pubs.rsc.org)

| 项目 | 说明 |
|------|------|
| **URL 模式** | `https://pubs.rsc.org/en/content/articlepdf/{year}/{journal}/{doi_code}` |
| **URL 修复** | TSV 中的 URL 缺少 year 和 journal 段：`/articlepdf/d4ee02970d` → `/articlepdf/2024/ee/d4ee02970d` |
| **Cloudflare** | WebVPN 下几乎不弹 |
| **下载方式** | **JS fetch** — PDF 内联显示 |
| **速度** | ~8秒/篇 |
| **可靠性** | ~90% |

**RSC DOI 解析规则**：
```
DOI: 10.1039/d4ee02970d
      └─ d4ee02970d
         d = 前缀
         4 = 年份 (2020 + 4 = 2024)
         ee = 期刊代码 (Energy & Environmental Science)
         02970d = 文章 ID
```

```python
code = doi.split('/')[1]  # d4ee02970d
year = 2020 + int(code[1])
journal = code[2:4]
correct_url = f"https://pubs.rsc.org/en/content/articlepdf/{year}/{journal}/{code}"
```

**易踩的坑**：
- ❌ 直接使用 TSV 中的 URL → 404
- ❌ 期刊代码长度固定为 2 字符（ta, ee, nj, gc, sc 等）
- ❌ 年份是 `2020 + digit`（不是 `2000 + digit`！坑过无数次）
- ✅ URL 加上 `/year/journal/` 段即可

### Science / AAAS (science.org)

| 项目 | 说明 |
|------|------|
| **URL 模式** | `https://www.science.org/doi/pdf/{DOI}` |
| **URL 修复** | 无需修复 |
| **Cloudflare** | WebVPN 下不弹或秒过 |
| **下载方式** | **JS fetch** — PDF 内联显示 |
| **速度** | ~5秒/篇 |
| **可靠性** | ~100% |

---

## CDP 代理架构

### API 端点

| 端点 | 方法 | 参数 | 说明 |
|------|------|------|------|
| `/new?url=` | GET/PUT | url | 创建新标签页 |
| `/navigate?target=&url=` | GET | target, url | 导航已有标签页 |
| `/eval?target=` | POST | body=JS | 在标签页执行 JavaScript |
| `/info?target=` | GET | target | 获取标签页信息 |
| `/close?target=` | GET | target | 关闭标签页 |
| `/targets` | GET | — | 列出所有标签页 |
| `/health` | GET | — | 健康检查 + 连接池大小 |

### 连接池设计

连接池契约：每个 tab 维护一个持久 WebSocket 连接，缓存到 `pool` Map（key = targetId），2 分钟空闲自动回收；`Runtime.evaluate` 和 `Page.navigate` 共用同一连接。按请求新建连接会因频繁握手导致间歇性超时，不要改回按请求建连。

### WebSocket 消息格式

CDP 协议要求 `id` 必须是**整数**（不能是字符串），否则返回 `"Message must have integer 'id' property"`。

---

## 下载方式对比

> **v2 起统一为「页面内 JS fetch」**，文件监控路径已废弃（并发竞态，见坑 5）。

| 方式 | 适用出版社 | 原理 | 优点 | 缺点 |
|------|-----------|------|------|------|
| **页面内 JS fetch**（现行） | ACS, Wiley, RSC, Science | 落地页找 PDF 链接 → 页面内 `fetch()` → `arrayBuffer()` → base64 分块传回 | 无并发竞态、不依赖文件系统、可校验 | 需 CDP 传 base64，大文件分块多 |
| **libproxy + 导航 + 同源 fetch** | Elsevier | 智能网关入口 → 导航打开 PDF → 同源 `fetch(location.href)` | 自动过 CF，无并发竞态 | 需先解析 PII；多一次跳转 |
| ~~文件监控~~（已废弃） | ~~ACS~~ | Edge 自动下载到 `~/Downloads`，脚本监听移动 | 极快 | **多线程互相抢文件**，已弃用 |

### 页面内 fetch 的实现细节

```javascript
// Step 1: 在页面内找候选 PDF 链接并 fetch
const hrefs = [...new Set(
    [...document.querySelectorAll('a[href]')]
        .map(a => a.getAttribute('href'))
        .filter(h => h && /\.pdf|article-pdf|pdfdirect|articlepdf/i.test(h))
        .map(h => new URL(h, location.href).href)
)];
const cands = [...hrefs];
if (location.href.match(/\.pdf|article-pdf|pdfdirect|articlepdf/i)) cands.unshift(location.href);

for (const u of cands) {
    const r = await fetch(u, { credentials: 'include' });
    if (!r.ok) continue;
    const ab = await r.arrayBuffer();
    const head = String.fromCharCode(...new Uint8Array(ab.slice(0, 5)));
    if (head !== '%PDF-') continue;      // ← 关键：魔数校验，别把 HTML 当 PDF
    window.__xmuPDFBytes = new Uint8Array(ab);
    return JSON.stringify({ ok: true, size: ab.byteLength, url: u });
}

// Step 2: 分块 base64 传输（每块 512KB）
const bytes = window.__xmuPDFBytes.slice(start, end);
let bin = '';
for (let i = 0; i < bytes.length; i += 0x8000)
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
return btoa(bin);
```

**坑**：`String.fromCharCode.apply(null, arr)` 有栈溢出风险 — 需要按 0x8000 (32KB) 分块处理。

**坑**：各出版社的 PDF 链接位置不一致（RSC 用 `/en/content/articlepdf/{year}/{journal}/{code}`，
Wiley 用 `/doi/pdfdirect/`，ACS 用 `/doi/pdf/`），**不要**试图用一条统一 URL 规则覆盖所有出版社 — 实测 RSC 统一 fetch 路径失败。
每家保留各自已验证的 URL 模式，靠 `resolve_publisher_url()` 分派。

---

## 批量下载脚本

### 使用方法

```bash
SKILL=~/.claude/skills/xmu-literature-downloader

# 1. 启动 Edge + CDP 代理（后台任务方式，别用 & ）
bash $SKILL/scripts/start_cdp.sh

# 2. 【必须】提醒用户在 Edge 中登录 https://webvpn.xmu.edu.cn/
#    登录成功标志：页面出现「XXX 欢迎您」

# 3. 【必须】跑前置自检，不通过就别下载
python3 $SKILL/scripts/preflight.py --probe

# 4. 下载
python3 $SKILL/batch_download.py --limit 0 --workers 3
```

### CLI 参数

```
batch_download.py
  --tsv PATH      清单 TSV（默认 skill 目录下的 manual_download.tsv）
  --out DIR       PDF 输出目录（默认 skill 目录下的 pdfs/）
  --limit N       最多下载 N 篇（0 = 全部）
  --workers N     并发线程数（默认 3）
  --retries N     单篇重试次数（默认 2，指数退避）
  --no-skip       即使已存在也强制重新下载
```

**退出码**：`0` = 全部成功；`1` = 环境问题（代理没起等）；`2` = 有下载失败。

### 脚本结构（v2）

```
batch_download.py
├── _session()                  # trust_env=False 的 requests session
├── resolve_publisher_url(row)  # 各出版社 URL 模式分派 + DOI 兜底
├── FETCH_JS / CHUNK_JS         # 页面内取字节 + 分块 base64
├── fetch_pdf_bytes(tid)        # 分块取回完整 PDF 字节
├── validate_pdf(data, row)     # ★ 魔数 + 尾标 + 页数 + 标题关键词 四重校验
├── download_one(row, ...)      # 单篇下载（重试 + 退避 + finally 关 tab）
└── main()                      # 线程池并行调度 + 失败明细汇总
```

### download_one 执行流程（v4 多路线降级链）

```
已有文件？→ validate_pdf → 通过则 skip；不通过则重下
  │
├─ 组装候选路线（4 条，按优先级）
│   ├─ 落地页          = doi_landing_via_browser(doi)  ★ 通用化的关键
│   ├─ 落地页(libproxy) = 同上 URL 经 libproxy 包装
│   ├─ PDF直链         = build_pdf_url(doi)（按 DOI 前缀硬编码，兜底用）
│   ├─ PDF直链(libproxy)
│   └─ [IEEE 专用] IEEE-stampPDF（arnumber 从落地页 URL 提取）
│
├─ 若是 Elsevier → resolve_elsevier_pii() 解析 PII（替换必须在 libproxy 包装之前）
│
└─ for attempt in 1..retries:
    for (label, url) in 路线:
        ├─ libproxy 路线加全局锁串行（会话是浏览器级共享状态）
        ├─ 导航 → _wait_ready()（等 SPA 渲染）
        ├─ _is_cf_challenge() → 换下一条路线
        ├─ _is_publisher_error() → 换下一条路线（如 ScienceDirect 限流）
        ├─ _pull_bytes() 三层降级：
        │   ├─ ① contentType 是 application/pdf？→ 同源 fetch(location.href) ★
        │   ├─ ② 落地页找 PDF 链接（4 类来源：a[href]/meta/iframe/data-*）→ fetch
        │   └─ ③ 导航到候选链接 → 同源 fetch（OUP/Elsevier/IEEE 属这类）
        └─ validate_pdf → 通过则落盘 ✓
```

★ 同源 fetch 是关键：CF 按 Sec-Fetch-Dest 区分导航与 XHR。
  导航打开的 PDF（location.href 已在 PDF 域内）用 fetch(location.href) 才不会被拦。

### v4 实测数据（2026-10-21 晚，13 家出版社横评）

清单只给 DOI（不给 url），4 线程并发，**13/13 零失败，123 s**：

| 出版社 | 走的路线 | 页数 | 大小 |
|--------|---------|------|------|
| Science (AAAS) | 落地页 | 2 | 0.84 MB |
| Sci Reports | 落地页 | 15 | 4.86 MB |
| Springer Link | 落地页 | 22 | 1.77 MB |
| MDPI | 落地页 | 15 | 14.0 MB |
| Frontiers | 落地页 | 11 | 6.91 MB |
| Taylor & Francis | 落地页 | 4 | 0.40 MB |
| APS | 落地页 | 17 | 0.99 MB |
| OUP | 落地页 | 2 | 1.29 MB |
| ACS | 落地页 / PDF直链 | 12 | 9.18 MB |
| Wiley | 落地页 / PDF直链 | 11 | 5.77 MB |
| RSC | 落地页 | 12 | 5.79 MB |
| Elsevier | 落地页(libproxy) | 12 | 8.15 MB |
| IEEE | IEEE-stampPDF | 8 | 5.90 MB |

回归测试：原有 7 篇 Ru/Co₃O₄ 文献 **7/7 零失败，70 s**。
独立校验（PyMuPDF 复核，不信脚本自报）：13/13 全部 PASS，标题与目标文献逐一对应。

### TSV 格式

制表符分隔，**必须**包含 `filename` 与 `doi` 或 `url`：

```
filename	url	doi	title	journal	year
2023_JACS_Zhu_Ru_Co3O4.pdf	https://pubs.acs.org/doi/pdf/10.1021/jacs.3c05556	10.1021/jacs.3c05556	Selective Ru Incorporation...	JACS	2023
```

`title` 字段会被用于下载后的**标题关键词校验**（防下错文章），建议填英文原标题。
`url` 可以留空（只给 DOI 也能跑，Elsevier 会自动解析 PII）。
**列名大小写不敏感，且兼容 UTF-8 BOM**（Excel/WPS 导出的 TSV 天然带 BOM）。

---

## 常见故障排查

### 第一步永远是 preflight

```bash
python3 ~/.claude/skills/xmu-literature-downloader/scripts/preflight.py --probe
```

它会逐项告诉你是哪一环没通，比盲目重试快得多。

### Edge 远程调试无法连接

```bash
# ★ 必须带 --noproxy '*'，否则会被宿主的 http_proxy 劫持
curl -s --noproxy '*' http://127.0.0.1:9223/json/version

# 如果没有响应，重启 Edge
kill $(lsof -ti :9223)
"/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
  --remote-debugging-port=9223 \
  --user-data-dir="$HOME/.xmu-literature-edge-profile" &
```

**注意**：端口在监听 ≠ 端口可用。9222 常常被别的进程占着并返回 404。
`cdp_proxy.mjs` 会自动探测，但手工排查时务必看 `/json/version` 的 JSON 体。

### CDP 代理报 timeout / 连不上

```bash
curl -s --noproxy '*' http://127.0.0.1:3456/health

# 如果 conns 很多（>20），重启代理：
kill $(lsof -ti :3456)
cd ~/.claude/skills/xmu-literature-downloader && node scripts/cdp_proxy.mjs
```

启动日志应打印 `[cdp-proxy] 浏览器 CDP 端口自动探测命中: 9223`。

### 标签页堆积

Edge 不会主动关闭旧的 about:blank 标签页。如果积累太多：
```python
import requests
s = requests.Session(); s.trust_env = False
targets = s.get("http://127.0.0.1:3456/targets").json()
for t in targets:
    if t["type"] == "page":
        s.get(f"http://127.0.0.1:3456/close", params={"target": t["id"]})
```

### 下载的 PDF 是 HTML 页面

v2 起 `validate_pdf()` 会自动拦截这类文件并重试，理论上不会落盘。
若仍遇到，手工检查：

```bash
head -c 8 paper.pdf   # 正常：%PDF-1.4   异常：<!DOCTYP
```

原因通常是：Cookie 过期 / WebVPN 未登录 / URL 模式错误。

### Cloudflare "请稍候…" 卡住不消失

- ACS/Science/RSC/Wiley：WebVPN 下 CF 应 5-10 秒自解。如果超过 60 秒，检查 WebVPN 是否登录。
- Elsevier：首次可能 30-60 秒。**不需要你手动点复选框** —— skill 会自动改走 libproxy 智能网关路线（见「坑 8」），日志会打印
  `[落地页] 撞 Cloudflare 机器人验证 → 自动改走后续路线`，随后自动取回 PDF。
  只有当 libproxy 路线也失败时，才需要你在 Edge 里看是不是要登录/扫码，或出版商对该出口 IP 临时限流。
- 如果所有出版社都卡 CF：WebVPN session 可能过期，重新登录。

### Elsevier 返回 CF 机器人验证

直连 `sciencedirect.com` 会撞上 Cloudflare Turnstile 交互挑战（页面显示「Are you a robot?」）。
**这不是付费墙，也不是无法自动化** —— 改走厦大 libproxy 智能网关即可自动放行，
详见「坑 8」。**这一步由 skill 自动完成，不需要用户手动点复选框。**

只有当整条降级链（直连落地页 → libproxy 落地页 → PDF 直链 → libproxy 直链）全部失败时，
才需要用户介入。那时的典型原因是：厦大统一身份认证未登录（`libproxy.xmu.edu.cn` 首页有「登录」入口）、
或出版商对该出口 IP 临时限流（等几分钟或换网络）。此时日志会明确指出
`（落地页 撞了 Cloudflare 机器人验证；libproxy 智能网关路线也已尝试）`。

### Python 类型注解兼容性

`batch_download.py` 可能被 `/usr/bin/python3`（3.9）或 Homebrew `python3`（3.14+）调用，所以类型注解统一用 `Optional[X]` 以兼容两者：
```python
from typing import Optional
def foo() -> Optional[str]:  # ✅
def foo() -> str | None:      # ❌ 3.9 报错，3.10+ 才行
```

### 并发下载的竞态条件

v2 已通过「统一页面内 fetch + 每线程独立 Session + 每线程独立标签页 + `safe_print` 锁」消除竞态。
`~/Downloads` 文件监控路径已废弃（见坑 5）。

---

## 已验证信息

### 已确认可用的 URL 模式

```
ACS:       https://pubs.acs.org/doi/pdf/{DOI}
Wiley:     https://onlinelibrary.wiley.com/doi/pdfdirect/{DOI}
Elsevier:  https://www.sciencedirect.com/science/article/pii/{PII}/pdfft   ← 必须经 libproxy 入口，见「坑 8」
RSC:       https://pubs.rsc.org/en/content/articlepdf/{YEAR}/{JOURNAL}/{CODE}
Science:   https://www.science.org/doi/pdf/{DOI}
```

### 2026-10-21 实测验证（5 篇 Ru/Co₃O₄ 核心文献）

**v1（旧版，文件监控 + JS fetch 混合）**：5/5 成功，总耗时 **84 s**。

**v2（改进版，统一页面内 fetch + 四重校验）**：5/5 成功，总耗时 **23 s**，3 线程并发：

| # | 文献 | 出版社 | 页数 | 大小 | 结果 |
|---|------|--------|------|------|------|
| 1 | Zhu 2023 JACS `10.1021/jacs.3c05556` | ACS | 12 | 9.63 MB | ✓ |
| 2 | Huo 2025 JACS `10.1021/jacs.5c00665` | ACS | 12 | 10.07 MB | ✓ |
| 3 | Jiang 2025 AFM `10.1002/adfm.202509656` | Wiley | 11 | 6.05 MB | ✓ |
| 4 | Hao 2024 EES `10.1039/D3EE04338J` | RSC | 12 | 6.08 MB | ✓ |
| 5 | Zhu 2025 JACS `10.1021/jacs.5c12381` | ACS | 11 | 7.21 MB | ✓ |

**加速 3.6 倍的原因**：废弃了 `~/Downloads` 文件监控（省掉轮询等待），
页面内一次 fetch 直接拿全量字节，省掉浏览器的下载落盘环节。

**校验结论**（独立于脚本自检，用 PyMuPDF 复核）：
5 篇全部 `%PDF-` 魔数 + `%%EOF` 尾标 + 文字层可解析 + 首页标题与目标文献逐一对应。
与 v1 下载的副本逐字比对，**正文文本哈希完全一致**（md5 差异仅来自出版商内嵌文档 ID / 时间戳元数据）。

**幂等性**：重跑 → 5 篇全部 `⏭ 已存在且有效`（0 s）。
**容错性**：故意把某篇替换成 54 字节的 HTML 错误页 → 自动识别「存在但无效」并重新下载成功。

### v3 实测验证（Elsevier 支持 + BOM 修复）

**v3 引入**：Elsevier/ScienceDirect 支持（libproxy 智能网关路径）、TSV 的 UTF-8 BOM 兼容。

**测试 1：2 篇 Elsevier + 2 篇对照（4 篇混跑）** —— 4/4 成功，**9 秒**：

| # | 文献 | 出版社 | 页数 | 大小 | 结果 |
|---|------|--------|------|------|------|
| 1 | Zhang 2025 Appl. Catal. B `10.1016/j.apcatb.2024.124845` | Elsevier | 12 | 8.34 MB | ✓ 经 libproxy |
| 2 | Wang 2026 Appl. Catal. B `10.1016/j.apcatb.2025.126341` | Elsevier | 11 | 9.61 MB | ✓ 经 libproxy |
| 3 | Hao 2024 EES `10.1039/D3EE04338J` | RSC | 12 | 5.94 MB | ✓ 直连 |
| 4 | Jiang 2025 AFM `10.1002/adfm.202509656` | Wiley | 11 | 5.91 MB | ✓ 直连 |

**测试 2：仅给 DOI、不给 url** —— 2/2 成功，13 秒，PII 自动解析
（`PII 解析成功: S0926337324011597` / `S0926337325013244`）。

**测试 3：回归** —— 原 5 篇（ACS×3 + Wiley + RSC）5/5 成功，21 秒，v3 改动未破坏既有路径。

**独立校验**：4/4 全部 `%PDF-` + `%%EOF` + 文字层可解析 + 首页标题与目标文献逐一对应。

**同批发现的 BOM 缺陷**：Excel/WPS 导出的 TSV 默认带 UTF-8 BOM，首列列名变成 `﻿url`，
`row.get("url")` 永远为 None。已修：用 `utf-8-sig` 打开 + 清洗列名（`read_rows()`）。

### 更早的测试用例

| DOI | 出版社 | 文件大小 |
|-----|--------|---------|
| 10.1021/acscatal.3c01914 | ACS Catalysis | 4.4 MB |
| 10.1002/aenm.202301948 | Adv. Energy Mater. (Wiley) | 4.8 MB |
| 10.1016/j.apcatb.2024.123744 | Appl. Catal. B (Elsevier) | 3.4 MB |
| 10.1039/d4ee02970d | Energy Environ. Sci. (RSC) | 3.3 MB |
| 10.1126/science.aec5465 | Science | 1.0 MB |

---

## 项目文件结构

```
xmu-literature-downloader/
├── SKILL.md                         ← 本文件
├── README.md                        ← 快速使用教程
├── requirements.txt                 ← Python 依赖
├── manual_download.tsv              ← 默认清单（filename/url/doi/title/journal/year）
├── scripts/
│   ├── preflight.py                 ← ★ 前置自检（代理/端口/登录/授权 四项）
│   ├── cdp_proxy.mjs                ← CDP 代理 v2（连接池复用 + 端口自动探测）
│   ├── cdp_open_url.mjs             ← 辅助：打开 URL
│   ├── browser_pdf_downloader.mjs   ← 辅助：下载 PDF（旧方案，可弃用）
│   ├── extract_pdf_text.py          ← 辅助：提取 PDF 文本验证
│   └── start_cdp.sh                 ← 一键启动 Edge + 代理（自动探测 + 复用已有代理）
├── batch_download.py                ← ★ 核心：批量下载脚本（v2）
├── preprocess_elsevier.py           ← 预处理：Elsevier PII 映射
├── package.json                     ← Node 依赖（仅 ws 一个）
├── examples/
│   ├── manifest-template.tsv
│   ├── cas-retry-template.tsv
│   ├── publisher-verification-template.tsv
│   └── multipublisher_benchmark.tsv  ← 13 家出版社回归基线（复现测试用）
└── agents/openai.yaml
```

> 安装后需先 `npm install`（拉取 `ws`）与 `pip install -r requirements.txt`；
> `node_modules/` 被仓库 `.gitignore` 忽略，不随仓库分发。

---

## 在其他项目中使用

1. 复制 `scripts/cdp_proxy.mjs`、`scripts/preflight.py`、`batch_download.py` 到新项目
2. 准备一个 TSV 文件，包含 `filename` 和 `doi`/`url` 列（建议也带 `title` 用于校验）
3. 启动 Edge（带 `--remote-debugging-port=9223`）+ 代理
4. **提醒用户手动登录 WebVPN** —— 这一步无法自动化
5. 跑 `preflight.py --probe` 确认就绪
6. 运行 `batch_download.py --tsv your.tsv --out ./pdfs`

如果出版社不在支持列表中，遵循以下调试流程：
1. 在浏览器中手动打开 PDF URL，观察行为（内联显示？跳转？直接下载？）
2. 按行为在 `resolve_publisher_url()` 里补该出版社的 URL 模式（**不要**试图统一所有出版社的 URL 规则）
3. 如果 403/CF，检查是否需要机构认证或修改 URL
4. 测试单个 URL 后再批量运行
5. 新增出版商后记得更新 `validate_pdf()` 的标题关键词校验与 SKILL.md 的「已确认可用的 URL 模式」表
