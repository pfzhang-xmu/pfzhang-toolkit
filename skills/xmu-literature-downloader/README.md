# xmu-literature-downloader

A Claude Code skill for legally downloading, retrying, and reading academic PDFs through the user's own logged-in Xiamen University Library / WebVPN / Smart Gateway / publisher browser session.

中文简介：这是一个面向厦大图书馆/WebVPN/智能网关场景的文献下载、失败重试与全文读取 skill。它使用用户自己已经登录的浏览器会话，在授权范围内保存 PDF 和 supporting information，并对文件做页数、PDF 签名和文本可读性验证。适合"网页里能打开 PDF，但命令行下载 403/401/登录页""CAS SSO 中断批量下载""ScienceDirect 撞 Cloudflare 机器人验证"的情况。

中文快速使用教程：

1. **先启动浏览器和 CDP 代理**：
   ```bash
   bash ~/.claude/skills/xmu-literature-downloader/scripts/start_cdp.sh
   ```
   脚本会起一个带 `--remote-debugging-port=9223` 的 Edge（独立 profile），并启动 CDP 代理（:3456）。
   代理启动时会自动探测 Edge 真实端口，端口在监听但不是 CDP 端点的情况会被自动跳过。
2. **在 Edge 里登录 WebVPN**（这一步 agent 无法代劳，必须本人操作）：
   - 打开 `https://webvpn.xmu.edu.cn/`，用学号 + VPN 密码登录
   - 登录成功的标志是页面出现「XXX 欢迎您」字样
   - 忘记登录会导致所有文章都下成 HTML 错误页
3. **跑前置自检**，确认代理 / 端口 / 登录 / 授权四项全绿：
   ```bash
   python3 ~/.claude/skills/xmu-literature-downloader/scripts/preflight.py --probe
   ```
4. 告诉 Claude 你的文献清单（DOI 或完整引文），并说明输出文件夹。
5. Claude 会执行 `batch_download.py --tsv <清单> --out <输出目录>`，逐篇下载并做 PDF 真实性校验。
6. 如果网页要求验证码、Cloudflare、人机验证、扫码、短信/OTP 或二次认证，需要你本人在 Edge 里完成；agent 不绕过这些验证，也不自动点击出版商的人机验证。
   > **注意**：出版商的人机验证分两种，处理方式不同。
   > - **Cloudflare 的「Are you a robot?」交互挑战**（ScienceDirect 最典型）—— skill 会**自动**改走厦大 libproxy 智能网关，你不需要手动点。直连撞了 CF 属于预期内的正常降级，日志会打印 `[xxx] 撞 Cloudflare 机器人验证 → 自动改走后续路线`，随后自动取回 PDF。
   > - **真正的 CAPTCHA / 扫码 / 短信 OTP / 二次认证**（登录类）—— 这一类 agent 无法代办，必须你本人在浏览器里完成。skill 会停在当前 tab 并等你确认后继续。
7. 推荐小批量使用：一次 5-10 篇比较稳，最多 15-20 篇，并保留 manifest 记录。不要用它批量扫关键词结果、整期杂志或大量连续下载。
8. 如果 Claude Code 没有自动识别这个 skill，把仓库安装到 `~/.claude/skills/xmu-literature-downloader`。
9. 如果遇到厦大统一身份认证 / CAS SSO（`ids.xmu.edu.cn`），不要把账号密码发给 agent。若浏览器已经自动填好账号密码，你可以明确授权 agent 只点一次"登录/确认登录"；若出现扫码、短信/OTP、验证码、人机验证或安全提示，则需要你自己在浏览器里完成。
10. 遇到 Cloudflare 的「Are you a robot?」**不需要**你手动点 —— skill 会自动切换到厦大 libproxy 智能网关路线。只有当整条降级链（直连 → libproxy）都失败时，才需要你介入：那时请打开 Edge 看是不是需要登录/扫码/二次认证，或出版商对该出口 IP 临时限流（等几分钟或换网络再试）。

### 命令行直接跑（不走 agent）

```bash
SKILL=~/.claude/skills/xmu-literature-downloader
bash $SKILL/scripts/start_cdp.sh          # 启动 Edge + 代理
python3 $SKILL/scripts/preflight.py --probe   # 自检（必须全绿）
python3 $SKILL/batch_download.py --limit 0 --workers 3 --out ~/papers
```

> ⚠️ 排查本机端口时，`curl` 一定要加 `--noproxy '*'`。宿主注入的 `http_proxy`
> 会劫持对 `127.0.0.1` 的请求，报出误导性的 `upstream connect failed: Connection refused`，
> 让人误判「代理没启动」。脚本内部已用 `trust_env=False` 处理，但手工 curl 需手动加。

### 已验证支持的出版社（13 家横评，2026-10-21）

| 出版社 | Domain | 走通的路线 |
|--------|--------|-----------|
| ACS | `pubs.acs.org` | 落地页 / PDF 直链 |
| Wiley | `onlinelibrary.wiley.com` | 落地页 / PDF 直链 |
| RSC | `pubs.rsc.org` | 落地页 |
| Elsevier | `sciencedirect.com` | 落地页（经 libproxy 智能网关） |
| Science | `science.org` | 落地页 |
| Scientific Reports | `nature.com` | 落地页 |
| Springer Link | `link.springer.com` | 落地页 |
| MDPI | `mdpi.com` | 落地页 |
| Frontiers | `frontiersin.org` | 落地页 |
| Taylor & Francis | `tandfonline.com` | 落地页 |
| APS | `journals.aps.org` | 落地页 |
| Oxford Academic | `academic.oup.com` | 落地页 |
| IEEE | `ieeexplore.ieee.org` | `stampPDF/getPDF.jsp` |

**清单只需要给 DOI**，不必给 PDF 链接 —— skill 会在浏览器里解析 `doi.org` 拿到
出版社真实落地页，再从落地页上找 PDF 链接。实测 13 家出版社、只给 DOI，
**13/13 零失败，123 秒**。

不在上表中的出版社也值得一试（走通用落地页路线），但未实测保证。
已知不通的：**SAGE**（`journals.sagepub.com` 站点级不可达，连接超时）。

可以这样对 agent 说：

```text
请使用 xmu-literature-downloader，通过我已经登录的厦大图书馆/WebVPN/智能网关 Chrome 会话，下载下面这些 DOI 的 PDF 和补充材料，并生成 manifest。
```

## What It Solves

- XMU Library / WebVPN / Smart Gateway can open a paper, but direct `curl` or `wget` returns 403.
- A DOI/title list needs small-batch PDF and supporting information collection.
- CAS SSO interrupts a batch and the failed papers need to be retried after the user manually authenticates in Chrome.
- ScienceDirect or publisher verification interrupts a batch and needs manual browser handoff before retrying the same tab.
- The user wants a manifest recording DOI, source URL, download status, SI status, and local paths.
- PDFs need to be verified before an agent reads, summarizes, or cites them.
- Zotero can import metadata, but the user still wants local project-folder PDFs.

## Boundaries

This skill only uses user-authorized institutional access.

It does not bypass paywalls, CAPTCHA, Cloudflare, two-factor authentication, publisher bot checks, DRM, or account restrictions.

Two kinds of challenge need to be distinguished:

- **Cloudflare's interactive "Are you a robot?" check** (most typical on ScienceDirect) is handled automatically. The skill detects the challenge and switches to the XMU libproxy smart gateway route on its own — no manual clicking is required. A direct-connect CF hit is an expected fallback, and the log prints `[route] hit Cloudflare bot check → switching to the next route`.
- **Genuine login challenges** — CAPTCHA, QR login, SMS/OTP, two-factor authentication — cannot be automated. The skill stops at the current tab and waits for you to complete them in the browser.

Do not paste school account passwords, CAS passwords, SMS codes, OTP codes, QR login results, cookies, or session tokens into chat. The intended workflow is browser handoff: the agent opens the page, the user completes authentication in Chrome, and the agent resumes after the user confirms. If the XMU CAS page is already filled by Chrome's password manager and you explicitly authorize it, the agent may click the visible login/confirm button once without reading or typing credentials.

Small batches are supported when the user provides a definite DOI/title/PMID list. Avoid broad keyword-result scraping, whole-issue downloads, large automated runs, repeated challenge retries, or parallel ScienceDirect tab bursts.

## XMU Institutional Access

Xiamen University provides several routes for off-campus access to library resources:

| Route | URL | Notes |
|---|---|---|
| Smart Gateway（智能网关） | `https://library.xmu.edu.cn/zy/dzzy/xwfw/znwg.htm` | Recommended — one-click access, no VPN needed |
| WebVPN | `https://webvpn.xmu.edu.cn/` | Library resources only (since 2023-07) |
| Campus VPN | `https://vpn.xmu.edu.cn/` | Full campus network |
| CARSI | Shibboleth via `ids.xmu.edu.cn` | Supported by many publishers |
| CAS/SSO | `https://ids.xmu.edu.cn/authserver/login` | Unified identity authentication |

Login uses student/staff ID (学号/工号) with VPN/XMUNET+ password.

## Preconditions

Before using the skill:

1. Edge (or Chrome) is open.
2. The user has personally logged in to Xiamen University Library / WebVPN / Smart Gateway in that browser.
3. The browser was launched with `--remote-debugging-port=9223` (the bundled `scripts/start_cdp.sh` does this).
4. The CDP proxy (`scripts/cdp_proxy.mjs`) is running on port 3456.
5. Node.js 22+ is available.
6. The `ws` package is installed (`npm install` in the skill directory — `node_modules/` is git-ignored and therefore **not** shipped in the repository).
7. The user has approved the target output folder.

## Installation

```bash
# Claude Code
git clone https://github.com/pfzhang-xmu/pfzhang-toolkit.git /tmp/pfzhang-toolkit
cp -r /tmp/pfzhang-toolkit/skills/xmu-literature-downloader ~/.claude/skills/xmu-literature-downloader
rm -rf /tmp/pfzhang-toolkit

# From local
cp -r /path/to/xmu-literature-downloader ~/.claude/skills/xmu-literature-downloader
```

Node and Python dependencies:

```bash
cd ~/.claude/skills/xmu-literature-downloader
npm install                       # installs the single dependency: ws
pip install -r requirements.txt   # requests + pymupdf (PDF verification) + fallbacks
```

## Usage

Tell Claude something like:

```text
Use xmu-literature-downloader to download these DOIs through my logged-in XMU WebVPN session, including supporting information, and make a manifest.
```

The skill instructs the agent to:

1. run `scripts/preflight.py` to verify proxy / browser port / WebVPN login / publisher entitlement;
2. access the publisher through XMU Smart Gateway, WebVPN, or database navigation;
3. resolve the DOI to the publisher landing page and locate the PDF link;
4. open the authorized PDF link in the browser;
5. download bytes from the authenticated browser page context;
6. check supporting information links;
7. verify each file as a readable PDF (magic bytes + `%%EOF` + page count + first-page title);
8. record all results in a manifest.

For ScienceDirect or other sensitive publisher platforms, keep the workflow slower and more manual:

- Start from Smart Gateway / WebVPN / library database navigation when possible.
- Process one publisher article at a time.
- Do not open many ScienceDirect tabs in parallel.
- Do not repeatedly refresh or retry a bot-check page.
- If verification appears, pause and ask the user to handle it in Chrome, then continue from the same tab.

## Helper Scripts

Open a publisher URL through the CDP proxy:

```bash
node ~/.claude/skills/xmu-literature-downloader/scripts/cdp_open_url.mjs \
  --url "https://pubs.acs.org/doi/abs/10.1021/acs.biomac.4c00102" \
  --wait
```

Download a PDF that opens in Chrome but fails from shell:

```bash
node ~/.claude/skills/xmu-literature-downloader/scripts/browser_pdf_downloader.mjs \
  --url "https://pubs.acs.org/doi/pdf/10.1021/acs.biomac.4c00102" \
  --out ~/papers/paper.pdf \
  --close
```

Extract and verify PDF text:

```bash
python3 ~/.claude/skills/xmu-literature-downloader/scripts/extract_pdf_text.py \
  --pdf ~/papers/paper.pdf \
  --pages 3
```

## Batch Manifest

For multi-paper work, create a manifest with at least:

```text
id	title	doi	year	venue	status	pdf_path	si_status	si_paths	source_url	notes
```

Keep the batch small and auditable. Stop when login, CAPTCHA, WebVPN expiry, publisher security checks, or suspicious download prompts appear. A Cloudflare bot check on the direct route is not a stop condition — the skill falls back to the libproxy route by itself.

For CAS retries, use the richer template in `examples/cas-retry-template.tsv`.

For publisher verification queues, use `examples/publisher-verification-template.tsv`.

## Repository Layout

```text
xmu-literature-downloader/
├── LICENSE
├── README.md
├── requirements.txt
├── SKILL.md
├── manual_download.tsv          ← 默认下载清单
├── agents/
│   └── openai.yaml
├── examples/
│   ├── manifest-template.tsv
│   ├── cas-retry-template.tsv
│   └── publisher-verification-template.tsv
├── scripts/
│   ├── preflight.py             ← ★ 前置自检（代理/端口/登录/授权）
│   ├── cdp_proxy.mjs            ← CDP 代理（连接池 + 端口自动探测）
│   ├── cdp_open_url.mjs
│   ├── browser_pdf_downloader.mjs
│   ├── extract_pdf_text.py
│   └── start_cdp.sh             ← 一键启动 Edge + 代理
└── batch_download.py            ← ★ 核心：批量下载（页面内 fetch + 四重校验）
```

## Credits

Adapted from [baihe26/zju-literature-downloader](https://github.com/baihe26/zju-literature-downloader) for Xiamen University (XMU). The ZJU original was verified with ACS publications through ZJU Summon. The XMU adaptation replaces ZJU-specific infrastructure (Summon, CAS domains, library URLs) with XMU equivalents: Smart Gateway, `ids.xmu.edu.cn`, `library.xmu.edu.cn`, `webvpn.xmu.edu.cn`.
