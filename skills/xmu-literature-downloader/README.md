# xmu-literature-downloader

A Claude Code skill for legally downloading, retrying, and reading academic PDFs through the user's own logged-in Xiamen University Library / WebVPN / Smart Gateway / publisher browser session.

中文简介：这是一个面向厦大图书馆/WebVPN/智能网关场景的文献下载、失败重试与全文读取 skill。它使用用户自己已经登录的 Chrome 会话，在授权范围内保存 PDF 和 supporting information，并对文件做页数、PDF 签名和文本可读性验证。适合"网页里能打开 PDF，但命令行下载 403/401/登录页""CAS SSO 中断批量下载""ScienceDirect/出版商人机验证后需要继续同一页下载"的情况。

中文快速使用教程：

1. 先在自己的 Chrome 里打开厦大图书馆、智能网关或 WebVPN，并用自己的账号登录。
   - 常用入口：`https://library.xmu.edu.cn/`
   - 智能网关（推荐）：`https://library.xmu.edu.cn/zy/dzzy/xwfw/znwg.htm`
   - WebVPN：`https://webvpn.xmu.edu.cn/`
2. 确认这个 Chrome 里能正常访问目标文献页面，最好能手动打开一次 PDF 或"在线全文"。
3. 在 Chrome 地址栏打开 `chrome://inspect/#remote-debugging`，勾选 `Allow remote debugging for this browser instance`。
4. 告诉 Claude 你的文献清单，例如 DOI、题目或 PMID，并说明输出文件夹。
5. agent 会通过你已经登录的 Chrome 会话检索、打开 PDF、保存主文和补充材料，并生成下载记录。
6. 如果网页要求验证码、Cloudflare、人机验证、扫码、短信/OTP 或二次认证，需要你本人在 Chrome 里完成；agent 不绕过这些验证，也不自动点击出版商的人机验证。
7. 推荐小批量使用：一次 5-10 篇比较稳，最多 15-20 篇，并保留 manifest 记录。不要用它批量扫关键词结果、整期杂志或大量连续下载。
8. 如果 Claude Code 没有自动识别这个 skill，把仓库安装到 `~/.claude/skills/xmu-literature-downloader`。
9. 如果遇到厦大统一身份认证 / CAS SSO（`ids.xmu.edu.cn`），不要把账号密码发给 agent。若 Chrome 已经自动填好账号密码，你可以明确授权 agent 只点一次"登录/确认登录"；若出现扫码、短信/OTP、验证码、人机验证或安全提示，则需要你自己在 Chrome 里完成。
10. 如果遇到 ScienceDirect 的 `Are you a robot?` 或其它出版商验证，让 agent 停在当前 tab，自己手动完成验证后再让 agent 从同一个页面继续。

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

It does not bypass paywalls, CAPTCHA, Cloudflare, two-factor authentication, publisher bot checks, DRM, or account restrictions. If a page asks for CAPTCHA, QR login, SMS/OTP, Cloudflare, "Are you a robot?", or publisher bot verification, the user must complete it in Chrome.

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

1. Chrome is open.
2. The user has personally logged in to Xiamen University Library / WebVPN / Smart Gateway in Chrome.
3. Chrome remote debugging is enabled at `chrome://inspect/#remote-debugging`.
4. The user has checked `Allow remote debugging for this browser instance`.
5. Node.js 22+ is available, or the Codex bundled Node runtime is available.
6. The `web-access-main` CDP proxy skill is installed.
7. The user has approved the target output folder.

## Installation

```bash
# Claude Code
git clone https://github.com/baihe26/xmu-literature-downloader.git ~/.claude/skills/xmu-literature-downloader

# From local
cp -r /path/to/xmu-literature-downloader ~/.claude/skills/xmu-literature-downloader
```

Optional Python helpers:

```bash
pip install -r ~/.claude/skills/xmu-literature-downloader/requirements.txt
```

## Usage

Tell Claude something like:

```text
Use xmu-literature-downloader to download these DOIs through my logged-in XMU WebVPN session, including supporting information, and make a manifest.
```

The skill instructs the agent to:

1. verify Chrome/WebVPN/remote-debugging prerequisites;
2. access the publisher through XMU Smart Gateway, WebVPN, or database navigation;
3. search by DOI or exact title within the publisher platform;
4. open the authorized PDF link in Chrome;
5. download bytes from the authenticated browser page context;
6. check supporting information links;
7. verify each file as a readable PDF or document;
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

Keep the batch small and auditable. Stop when login, CAPTCHA, WebVPN expiry, publisher security checks, or suspicious download prompts appear.

For CAS retries, use the richer template in `examples/cas-retry-template.tsv`.

For publisher verification queues, use `examples/publisher-verification-template.tsv`.

## Repository Layout

```text
xmu-literature-downloader/
├── LICENSE
├── README.md
├── requirements.txt
├── SKILL.md
├── agents/
│   └── openai.yaml
├── examples/
│   ├── manifest-template.tsv
│   ├── cas-retry-template.tsv
│   └── publisher-verification-template.tsv
└── scripts/
    ├── browser_pdf_downloader.mjs
    ├── cdp_open_url.mjs
    └── extract_pdf_text.py
```

## Credits

Adapted from [baihe26/zju-literature-downloader](https://github.com/baihe26/zju-literature-downloader) for Xiamen University (XMU). The ZJU original was verified with ACS publications through ZJU Summon. The XMU adaptation replaces ZJU-specific infrastructure (Summon, CAS domains, library URLs) with XMU equivalents: Smart Gateway, `ids.xmu.edu.cn`, `library.xmu.edu.cn`, `webvpn.xmu.edu.cn`.
