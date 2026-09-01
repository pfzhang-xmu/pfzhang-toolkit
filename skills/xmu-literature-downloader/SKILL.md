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
batch_download.py ──HTTP──▶ CDP Proxy (:3456) ──WebSocket──▶ Edge (:9222)
     │                            │                              │
     │  URL修复                   │  连接池复用                   │  已登录 WebVPN
     │  文件监控/JS fetch          │  持久 WS 连接                │  已过 Cloudflare
     ▼                            ▼                              ▼
  pdfs/                    每个 tab 一个 WS               publisher sites
```

**核心原理**：不是绕过 Cloudflare，而是通过厦大 WebVPN 获得干净的机构 IP + 浏览器自动化登录，让 Cloudflare 只弹可自动解决的 JS 挑战（或根本不弹）。

---

## 前置条件

### 1. 浏览器
- macOS：Microsoft Edge 或 Google Chrome（Chromium 内核）
- 启动时加 `--remote-debugging-port=9222`
- 使用持久化 profile：`--user-data-dir=$HOME/.xmu-literature-edge-profile`
- **不需要安装 Chrome**，Edge 完全够用

### 2. CDP 代理
- 位置：`~/.claude/skills/xmu-literature-downloader/scripts/cdp_proxy.mjs`
- 依赖：Node.js 22+, `ws` 包
- 启动：`node scripts/cdp_proxy.mjs`（监听 3456 端口）

### 3. 认证
- 用户需登录 `https://webvpn.xmu.edu.cn/`（学号 + VPN 密码）
- 首次使用需在 Edge 中手动登录一次，后续持久化 profile 保持登录状态
- 对于 Wiley：还需要通过 CARSI 做一次厦大 CAS 认证

### 4. Python
- Python 3.9+（注意：类型注解用 `Optional[X]` 不用 `X | None`，因为 macOS 自带 Python 3.9）
- 依赖：`requests`

---

## 各出版社下载方案

以下方案均基于 **WebVPN 已登录 + Edge 远程调试已开启** 的前提。

### ACS Publications (pubs.acs.org)

| 项目 | 说明 |
|------|------|
| **URL 模式** | `https://pubs.acs.org/doi/pdf/{DOI}?download=true` |
| **URL 修复** | 无需修复，TSV 中的 URL 直接可用 |
| **Cloudflare** | WebVPN 下不弹 CF 或秒过 |
| **下载方式** | **文件监控** — 浏览器将 PDF 作为文件直接下载到 `~/Downloads`，脚本监听文件系统并移动 |
| **速度** | ~1秒/篇，极快 |
| **可靠性** | 几乎 100% |

**关键行为**：导航到 PDF URL 时，Edge 返回 `isDownload: true`，页面被 abort（`net::ERR_ABORTED`），PDF 直接出现在 Downloads 目录。**不会产生 .crdownload 文件** — 下载太快了。

**易踩的坑**：
- ❌ 试图用 JS fetch 获取 ACS PDF — 页面已经被 abort，无法 eval
- ❌ 等待 .crdownload 文件 — ACS 下载不产生中间文件
- ✅ 正确做法：导航后立即监控 `~/Downloads` 的新增 .pdf 文件

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
| **URL 修复** | **关键！** TSV 中的 PII 格式是错的（如 `j.apcatb.2024.123744`），必须用 `doi.org` 重定向获取真实 PII（如 `S0926337324000559`） |
| **Cloudflare** | **首次 ~30 秒**，之后 cookie 复用秒过 |
| **下载方式** | **JS fetch** — PDF 内联显示 |
| **速度** | ~5秒/篇（首次 ~30秒 CF 等待） |
| **可靠性** | ~100% |

**PII 修复方法**：
```python
# 通过 doi.org 重定向获取真实 PII
# 导航到 https://doi.org/{DOI}
# 浏览器重定向到 https://www.sciencedirect.com/science/article/pii/{REAL_PII}
# 提取 PII，构造 /pdfft URL
```

**易踩的坑**：
- ❌ TSV 中的 `j.apcatb.2024.123744` 格式的 PII 是错的
- ❌ `/pdfft` URL 需要正确的 PII 才返回 PDF
- ❌ 不使用 WebVPN 的话 CF Turnstile 不会自解（需要手动点击）
- ✅ doi.org 重定向 → 提取 PII → 构建正确 URL
- ✅ 第一篇走完 CF 后，后续论文秒下

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

### 连接池设计（v2，经验教训）

**v1 的问题**：每次 `/navigate` 或 `/eval` 都打开新的 WebSocket，用完就关。频繁握手导致间歇性超时。

**v2 的修复**：
- 每个 tab 维护一个持久 WebSocket 连接
- 连接缓存到 `pool` Map（key = targetId）
- 2 分钟空闲自动回收
- `Runtime.evaluate` 和 `Page.navigate` 共用同一连接

**v2 的关键 bug**（花了几小时才找到）：
```javascript
// ❌ 错误：conn 声明在 on("open") 闭包内，on("message") 拿不到
ws.on("open", () => {
    const conn = { ws, pending, ref: Date.now() };  // 局部变量！
    pool.set(targetId, conn);
});
ws.on("message", (data) => {
    if (conn?.pending.has(...))  // conn 是 undefined！消息全部丢失！
});

// ✅ 正确：conn 声明在 Promise 回调顶层
let conn;
ws.on("open", () => {
    conn = { ws, pending, ref: Date.now() };  // 赋值给外层变量
    pool.set(targetId, conn);
});
ws.on("message", (data) => {
    if (conn?.pending.has(...))  // 正确引用
});
```

### WebSocket 消息格式

CDP 协议要求 `id` 必须是**整数**（不能是字符串），否则返回 `"Message must have integer 'id' property"`。

---

## 下载方式对比

| 方式 | 适用出版社 | 原理 | 优点 | 缺点 |
|------|-----------|------|------|------|
| **文件监控** | ACS | Edge 自动下载 PDF 到 ~/Downloads，脚本监听新文件并移动 | 极快（1s），不消耗浏览器资源 | 只能用于浏览器拦截下载的出版社 |
| **JS fetch** | Science, Wiley, RSC, Elsevier | 在页面内执行 `fetch(location.href)` 获取 PDF 字节，base64 分块传出 | 适用于 PDF 内联显示的出版社 | 慢（需要 CDP 传 base64），大文件分块多 |

### JS fetch 的实现细节

```javascript
// Step 1: 在页面内 fetch PDF
const r = await fetch(location.href, { credentials: "include" });
const ab = await r.arrayBuffer();
window.__xmuPDFBytes = new Uint8Array(ab);

// Step 2: 分块 base64 传输（每块 256KB）
const bytes = window.__xmuPDFBytes.slice(start, end);
let bin = "";
for (let i = 0; i < bytes.length; i += 0x8000)
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
return btoa(bin);
```

**坑**：`String.fromCharCode.apply(null, arr)` 有栈溢出风险 — 需要按 0x8000 (32KB) 分块处理。

---

## 批量下载脚本

### 使用方法

```bash
# 1. 启动 Edge + CDP 代理
bash ~/.claude/skills/xmu-literature-downloader/scripts/start_cdp.sh
# 在 Edge 中登录 webvpn.xmu.edu.cn

# 2. 下载（测试模式 10 篇）
python3 batch_download.py --limit 10 --workers 3

# 3. 全量下载
python3 batch_download.py --limit 0 --workers 3
```

### 脚本结构

```
batch_download.py
├── fix_url(row)          # URL 修复（RSC/Wiley/Elsevier）
├── resolve_elsevier_pii() # Elsevier PII 懒解析（缓存到字典）
├── snap_files()           # Downloads 目录快照
├── wait_for_new_download()# 文件监控（ACS）
├── fetch_pdf_via_js()     # JS fetch（其他出版社）
├── download_one(row)      # 单篇下载
│   ├── fix_url()          # 修复 URL
│   ├── proxy_navigate()   # 导航
│   ├── wait_for_new_download(timeout=8)  # 尝试文件监控
│   ├── Cloudflare 等待     # 轮询 document.title 直到不是"请稍候…"
│   └── fetch_pdf_via_js() # 尝试 JS fetch
└── main()                 # 线程池并行调度
```

### download_one 执行流程

```
打开新 tab
  │
  ├─ 修复 URL（RSC/Wiley/Elsevier）
  ├─ 导航到 PDF URL
  ├─ 等待 8 秒（文件监控模式 — ACS）
  │   └─ 有新 .pdf？→ 移动文件 → 完成 ✓
  │
  ├─ 等待 Cloudflare 自解（轮询 title，最多 30 秒）
  │   └─ 标题 ≠ "请稍候…" → 继续
  │
  └─ JS fetch 模式（其他出版社）
      └─ 成功 → 保存文件 → 完成 ✓
      └─ 失败 → 记录失败 ✗
```

---

## 常见故障排查

### Edge 远程调试无法连接

```bash
# 检查 Edge 是否在监听
curl -s http://127.0.0.1:9222/json/version

# 如果没有响应，重启 Edge
kill $(lsof -ti :9222)
"/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.xmu-literature-edge-profile" &
```

### CDP 代理报 timeout

```bash
# 检查代理健康
curl -s http://127.0.0.1:3456/health

# 检查连接池
# 如果 conns 很多（>20），重启代理：
kill $(lsof -ti :3456)
cd ~/.claude/skills/xmu-literature-downloader && node scripts/cdp_proxy.mjs &
```

### 标签页堆积

Edge 不会主动关闭旧的 about:blank 标签页。如果积累太多：
```python
import requests
targets = requests.get("http://127.0.0.1:3456/targets").json()
for t in targets:
    if t["type"] == "page":
        requests.get(f"http://127.0.0.1:3456/close", params={"target": t["id"]})
```

### 下载的 PDF 是 HTML 页面

用 `head -c 8` 检查：
```bash
head -c 8 paper.pdf  # 正常：%PDF-1.4  异常：<!DOCTYP
```

原因通常是：Cookie 过期 / WebVPN 未登录 / URL 模式错误。

### Cloudflare "请稍候…" 卡住不消失

- ACS/Science/RSC/Wiley：WebVPN 下 CF 应 5-10 秒自解。如果超过 60 秒，检查 WebVPN 是否登录。
- Elsevier：首次可能 30-60 秒。如果超过 120 秒，可能是 Turnstile 交互验证 —— 需要在 Edge 中手动点击复选框。
- 如果所有出版社都卡 CF：WebVPN session 可能过期，重新登录。

### Python 3.9 类型注解兼容性

macOS 自带的 Python 3.9 不支持 `X | None` 语法。所有类型注解用 `Optional[X]`：
```python
from typing import Optional
def foo() -> Optional[str]:  # ✅
def foo() -> str | None:      # ❌ Python 3.9 报错
```

### 并发下载的竞态条件

- 文件监控模式：每个线程用**独立的** Downloads 快照（`snap_files()` 在导航前调用），避免多线程看到同一个新文件
- Elsevier PII 解析：用 `threading.Lock()` 保护共享的 PII 缓存字典
- `.crdownload` 追踪：Edge 通过 WebVPN 下载 PDF **通常不产生 .crdownload 文件**——文件直接秒写完。不要依赖 .crdownload 检测

---

## 已验证信息

### 已确认可用的 URL 模式

```
ACS:       https://pubs.acs.org/doi/pdf/{DOI}?download=true
Wiley:     https://onlinelibrary.wiley.com/doi/pdfdirect/{DOI}
Elsevier:  https://www.sciencedirect.com/science/article/pii/{PII}/pdfft
RSC:       https://pubs.rsc.org/en/content/articlepdf/{YEAR}/{JOURNAL}/{CODE}
Science:   https://www.science.org/doi/pdf/{DOI}
```

### 验证过的测试用例

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
├── scripts/
│   ├── cdp_proxy.mjs               ← CDP 代理 v2（连接池复用）
│   ├── cdp_open_url.mjs            ← 辅助：打开 URL
│   ├── browser_pdf_downloader.mjs  ← 辅助：下载 PDF（旧方案，可弃用）
│   ├── extract_pdf_text.py         ← 辅助：提取 PDF 文本验证
│   └── start_cdp.sh               ← 一键启动 Edge + 代理
├── batch_download.py               ← **核心**：批量下载脚本
├── preprocess_elsevier.py          ← 预处理：Elsevier PII 映射
├── examples/
│   ├── manifest-template.tsv
│   ├── cas-retry-template.tsv
│   └── publisher-verification-template.tsv
└── agents/openai.yaml
```

---

## 在其他项目中使用

1. 复制 `scripts/cdp_proxy.mjs` 和 `batch_download.py` 到新项目
2. 准备一个 TSV 文件，包含 `url` 和 `filename` 列
3. 确保 Edge 已启动 + 远程调试 + WebVPN 已登录
4. 启动 CDP 代理：`node scripts/cdp_proxy.mjs`
5. 运行：`python3 batch_download.py`

如果出版社不在支持列表中，遵循以下调试流程：
1. 在浏览器中手动打开 PDF URL，观察行为（直接下载？内联显示？跳转？）
2. 根据行为选择文件监控或 JS fetch
3. 如果 403/CF，检查是否需要机构认证或修改 URL
4. 测试单个 URL 后再批量运行
