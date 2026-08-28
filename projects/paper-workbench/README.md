# Paper Workbench｜论文写作审查全流程工作台

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Cross-ecosystem MCP](https://img.shields.io/badge/MCP-跨生态-green.svg)](#跨生态-mcp-服务)

> **English summary** · An end-to-end academic writing & review workbench that automates the
> mechanical 80% of paper production (literature search, outlining, drafting, rendering,
> polishing, typesetting, unpacking reviewer comments) while keeping the human-in-the-loop for
> the judgment-critical 20% (direction, storyline, scientific claims, figure conclusions,
> terminology, journal fit, response strategy). Ships with a CLI state machine, a desktop-style
> local Web UI, a cross-ecosystem MCP server, and optional integration with the **DSH Agent**
> for skill-orchestrated AI delegation (falls back to direct LLM calls when DSH is absent).

---

## 定位声明 / Positioning

> 工作台定位 = **AI 干 80% 体力活、人守 20% 核心判断**的效率倍增器——检索/搭架/起草/渲染/润色/排版/拆审稿意见交给工作台，方向取舍/故事线/学术观点/图表结论/术语把关/投稿核对/回复策略由人守住。可投稿的文章 = 人的判断 + AI 的体力；AI 是效率倍增器，不是自动出稿机。分工总纲（环节级分工表 + 八步人工判断点）唯一权威源在 [`flow.md`](flow.md)「总纲」节。

---

## 特性一览 / Features

| 能力 | 说明 | Description |
| --- | --- | --- |
| 六阶段状态机 | `research → journal → framework → draft → review → submit`，含阶段门禁 | CLI state machine with quality gates |
| 分批生成管线 | 契约先行 + 分段生成 + 段级门禁（`staged_gen.py`） | Contract-first staged generation |
| 并行分段写作 | 依赖图拓扑分波 + 会话池并发（`parallel_gen.py`） | Dependency-graph parallel writing |
| 审查流水线 | review 阶段 8 步 + P0/P1/P2 分级 | 8-step review pipeline with severity tiers |
| 三路绘图路由 | data→matplotlib/NPG；schematic→PPT 路由；origin→Origin COM（`figure_router.py`） | Deterministic figure routing |
| 参考文献管线 | Crossref/arXiv 核验、修复、CSL 格式化（`refs_pipeline.py`） | Reference verification & CSL formatting |
| 文献检索工具 | OpenAlex/arXiv/Crossref/Pubmed（`toolbox.py search`） | Multi-source literature search |
| 跨生态 MCP | 任意支持 MCP 的 agent 可调用全部工具（`workbench_mcp.py`） | Cross-ecosystem MCP server |
| Web 桌面 UI | 项目/技能/数据分析/AI 助手/质量诊断/可视化预览 | Local web + desktop shell |
| 科研绘图迭代 | 文生图版本历史、整图参考编辑、参考图素材库、显式当前版本 | Versioned image generation & `/images/edits` editing |
| 可选 DSH 集成 | AI 生成优先委托 Agent 利用技能系统，离线自动回退直连 LLM | Optional DSH Agent delegation |

---

## 安装 / Install

```bash
# 方式一：核心 CLI（推荐先跑通状态机）
pip install -r requirements.txt
python wb.py --help

# 方式二：完整能力（Web UI + 数据图表 + MCP）
#   已在 requirements.txt 中；最大化安装：
python -m pip install matplotlib pandas numpy scipy seaborn openpyxl \
    habanero citeproc-py pybtex bibtexparser language-tool-python \
    python-docx pdfplumber reportlab pypandoc python-pptx pywebview mcp
```

可选依赖（缺省自动降级，不影响主流程）：

- **DSH Agent**（端口 3080）：`dsh_bridge.py` 自动探测；在线时 AI 生成走技能编排委托，离线回退直连 LLM。
- **matplotlib** 缺失：图表自动降级为 SVG；**LLM API** 未配置：AI 功能不可用但 CLI 全流程可用。
- **pywin32**（仅 Windows）：启用 Origin COM 桥与 PPT COM 导出。
- **language-tool-python**：语言检查本地模式需 Java，无 Java 时自动回退公共 API。

---

## 快速开始 / Quick Start

```bash
python wb.py init "大语言模型多智能体协作机制研究" --journal "Nature Machine Intelligence"
python wb.py next        # 看当前阶段要做什么
python wb.py check research 1    # 完成一项勾一项
python wb.py stage journal       # 产物齐全后推进
python wb.py review-book         # 审查阶段：生成任务工作簿
```

任意目录运行需 `--dir <项目路径>`；未指定时自动发现配置根目录下的唯一项目。

---

## 状态机 / State Machine

```
research → journal → framework → draft → review → submit
```

依赖严格单向推进；每个阶段切换前有质量门禁，`draft→review` 含文本质量检查，`submit` 前自动跑质量门禁（存在 P0/P1 默认拒绝，`--force` 需带理由）。

---

## 审查流水线（review 阶段，8 步）

1. `verify-claims` — Claim-证据映射审计
2. `verify-citations` + `nature-ref-verifier` — 引用真实性逐条核验
3. `stats-reporting-audit` — 统计报告审计
4. `simulate-reviewers` / `nature-reviewer` / `paper-reviewer` — 模拟审稿 2-3 视角
5. `refactor-structure` / `reflect-paper` — 结构与叙事审计
6. `nature-polishing` / `polish-prose` — 语言润色与 AI 味去除
7. 对应期刊 submission skill / `preflight-check` — 格式 preflight
8. `check-originality` — 原创性检查

问题按 P0(学术诚信/致命)/P1(强烈建议)/P2(润色)分级，全部 P0/P1 关闭后进入 submit。

---

## 全部命令 / Commands

```bash
python wb.py init "方向" [--journal 期刊] [--dir 父目录] [--lang zh|en] [--type article|letter|review]
python wb.py status | next | summary | doctor
python wb.py stage <research|journal|framework|draft|review|submit> [--force --reason ...]
python wb.py new <stage>
python wb.py check <stage> <n> [--uncheck]
python wb.py recommend "研究方向" [--dir 项目目录]
python wb.py review-book        # 生成审查任务工作簿(8 步)
python wb.py review-auto        # 半自动执行可代码化的审查步骤
python wb.py import 稿件.docx|.pdf    # 导入外部稿件 → manuscript/imported_*.md
python wb.py generate init            # 分批生成：初始化 draft/ 结构
python wb.py generate contract [--ai] # 生成契约(范围/大纲/图表契约/引文分配/术语), 人工锁定
python wb.py generate section --sid S1 [--via normal|subagent] [--dry-run|--accept]
python wb.py generate parallel [--concurrency N] [--timeout S]  # 并行分段写作
python wb.py generate status
python wb.py generate assemble        # 拼装(自动按契约插入已渲染表格、前置摘要产物)
python wb.py generate tables [--extract|--gen --tid "Table 1"]
python wb.py generate abstract [--dry-run]   # 摘要后置生成+论断对齐校验
python wb.py generate delegate       # 委托 DSH Agent 编排全流程
python wb.py litmap "方向" [--dir 项目目录]   # 选题文献地图
python wb.py rebuttal [--dir 项目目录]        # 审稿回复逐条草稿
python integration_qc.py <项目> [--apply-refs] [--json]   # 整合质检
```

AI 科研绘图现作为独立工作区提供，可选择“新建图片”“基于上一版本修改”或“使用参考图编辑”；工具箱保留文献、数据和导出等通用入口。
每次生成保存为 `data/figures/<asset_id>/versions/vNNN.*`，不会覆盖历史版本；
参考图保存到同一素材的 `references/` 目录。参考图编辑严格调用 OpenAI 兼容的
`/v1/images/edits`，当前模型/服务不支持时会直接提示，不降级为普通文生图。

**工具箱（toolbox.py）常用入口：**

```bash
python toolbox.py search "大语言模型 多智能体" --sources openalex,arxiv,crossref,pubmed --limit 20
python toolbox.py build-refs "protoplast fusion fungi" --out framework/references.md
python toolbox.py fetch 10.1038/nature14539
python toolbox.py verify-bib framework/references.md
python toolbox.py used-refs 项目目录 [--write]      # 正文引用反推使用清单
python toolbox.py format-refs framework/references.md --style crib   # CSL 渲染
python toolbox.py audit-stats manuscript/main.md
python toolbox.py originality manuscript/main.md --corpus .
python toolbox.py cn2bib cnki题录.txt --out 中文文献.bib   # CNKI GB/T 7714 → BibTeX
python toolbox.py quality-check 项目目录            # 0-100 打分卡
python data2paper.py fill .     # 自动扫描 data/ 生成 Results 统计/图表/表
```

---

## 质量体系 / Quality System

- `quality_rules.md` — 从核心写作/审稿 skill 提炼的写作与审稿准则。
- `skill_routing.md` — 技能路由规则，避免多套写作/审稿风格冲突。
- 质量门禁：`python toolbox.py quality-check <项目目录>`，P0/P1 清零后才进入 submit；检查占位符、图表规划、参考文献数量（默认 ≥80，常见 80-120）与近 5 年占比。
- 门禁输出 0-100 打分卡（P0=-30/P1=-10/P2=-3；≥90 可投 / 70-89 小修 / 50-69 大修 / <50 不可投），Web 工具箱页直接显示。
- 质量收敛历史写入 `<项目>/review/quality-history.json`，首页项目卡绘制 0→可投 收敛趋势线。
- 冒烟测试：`python smoke_test.py`（验证编译、状态机、图表、质量门禁、数据填稿、Web API）。

---

## 跨生态 MCP 服务 / Cross-ecosystem MCP

工作台核心能力可作为标准 MCP 服务被任何支持 MCP 的 agent（Claude Code / Cursor / Codex / Qoder / TRAE / DSH）自主调用：

```bash
# 一键注册(Qoder/Cursor/Codex 自动写入配置；Claude Code/TRAE 打印配置片段)
python register_mcp.py
```

核心工具：`search_skills` / `read_skill` / `record_skill_use` / `quality_check` / `mechanical_fix`
/ `used_refs` / `lang_check` / `figure_render`(三路绘图路由) / `export_docx` / `process_audit`
/ `ledger_query` / `list_projects` / `project_status`。

**防"阳奉阴违"机制**：所有工具调用记录到 `data/tool_ledger.jsonl` 台账；`process_audit` 比对稿件修改时间与台账——修改晚于最近 `quality_check` 判 P1，晚于一切后处理工具判 P0。

---

## Web 桌面端 / Web & Desktop UI

- 项目工作台：建项目、推荐期刊、阶段状态、检查点勾选、产物查看、intake 信息收集、`?sel=项目路径` 直达。
- 质量诊断：运行门禁后逐条显示 严重级 + 文件:行号 + 一键修复；需人工的「定位」在渲染稿件中红色高亮。
- 可视化预览：直接查看项目进度、自动生成的图表、稿件 Markdown 渲染。
- 技能管理 / 数据分析 / AI 助手（OpenAI 兼容 API）/ 工具箱模式。
- 深色/浅色主题切换。

启动：

```bash
python web/server.py            # 本地 HTTP(默认 8123)
# 或 Windows 双击 web/start-workbench.bat
# 或桌面版
python desktop.py               # 独立桌面窗口(pywebview)
```

---

## 打包独立 exe（桌面版）/ Build standalone exe

```bash
python build_exe.py    # PyInstaller → dist/PaperWorkbench/PaperWorkbench.exe
```

- onedir 模式，含 Python 运行时 + 全部依赖，双击即用，无需本机 Python。
- 打包后引擎自动切换：`run_wb` 函数直调、模板从资源目录读取、最近项目写用户目录。

---

## 目录结构 / Layout

```
workbench/
├── wb.py                  # 状态机引擎(纯 Python 标准库, 零依赖)
├── staged_gen.py          # 分批生成管线(契约先行+分段生成+段间门禁)
├── parallel_gen.py        # 依赖图驱动的并行分段写作
├── subagent_writer.py     # 经 DSH 子代理会话的章节写作通道
├── skill_channel.py       # 技能引用区块 + 执行者:模型解析
├── toolbox.py             # 开源工具封装(检索/引用/质检/统计)
├── dsh_bridge.py          # DSH Agent JSON-RPC 桥 + 会话池(可选)
├── workbench_mcp.py       # 跨生态 MCP 服务(stdio)
├── register_mcp.py        # 一键注册/注销 MCP 到各 agent 生态
├── refs_pipeline.py       # 引文核验/修复/CSL 格式化管线
├── data2paper.py          # 数据→Results 统计/图表/表格填稿
├── integration_qc.py      # 整合质检(机械/确定性, 零 LLM)
├── litmap.py / rebuttal.py# 文献地图 / 审稿回复草稿(可选增强)
├── figure_router.py       # 绘图三路确定性分发
├── figure_origin.py / origin_mcp_server.py   # Origin COM 桥 + MCP 服务
├── figure_pptx.py / figure_styles.py         # PPT 绘图路由 + 科研配色
├── ai_screen.py / lang_check.py              # AI 粗筛 / 语言质量检查
├── runner.py              # 任务派发层(dsh/claude/codex 执行者)
├── checklists/            # 6 阶段检查清单
├── templates/             # 21 个阶段产物模板 + CSL 样式
├── skills/                # 工作台附带的写作/审查 skill
└── web/                   # 本地 Web UI + HTTP API
    ├── server.py          # HTTP 服务(默认 8123)
    ├── index.html         # 桌面风格前端
    ├── charts.py          # 数据图表(matplotlib PNG, 缺失自动 SVG)
    ├── ai_client.py       # OpenAI 兼容 API 客户端
    ├── pdf_export.py      # PDF 导出(reportlab)
    └── requirements.txt   # 精简依赖
```

> 工作台在用户配置根目录（默认 `~/.dsh/papers`，可用 `app_config.json` 的 `project_roots` 扩展）下逐项目建立 `research/ → journal/ → framework/ → draft/ → review/ → submit/` 六阶段目录。

---

## 配置 / Configuration

复制 `app_config.example.json` 为 `app_config.json`：

```json
{
  "ai": {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "temperature": 0.7,
    "tier": "flash",
    "use_dsh_delegate": true,
    "dsh_delegate_timeout": 600
  },
  "skills": {
    "disabled": []
  },
  "project_roots": ["~/papers"]
}
```

- `ai.*`：OpenAI 兼容 API（Base URL / Key / Model）；`api_key` 留空或经环境变量注入，配置不入库。
- `ai.use_dsh_delegate`：DSH 在线时优先委托 Agent 技能编排，失败/超时自动回退直连 LLM。
- `skills.disabled`：禁用某些技能（默认全启用）。

---

## 目录维护约定（Contributors）

- 一次性脚本归档到 `scripts/_archive/`，参数化、可复用脚本留 `scripts/` 顶层。
- 禁用 `.bak` 备份，历史快照走 `git commit`。
- 新增能力优先独立模块，通过 `wb.py` / `toolbox.py` 注册命令接入。

---

## License

[MIT](LICENSE) © 2026 Paper Workbench contributors

## Distribution and updates

This project is published under `projects/paper-workbench` in the DSH ecosystem repository. The Web UI includes AI scientific drawing and the bundled FigureForge editor. The checked-in `web/static/figureforge` assets are ready to use; Node/npm is only needed when developing the FigureForge frontend.

Create `app_config.json` from `app_config.example.json` and configure AI/image API settings locally. Never commit `app_config.json` or API keys. Runtime projects, generated figures, references, FigureForge versions, and settings are stored under ignored local paths and preserved by the update scripts.

Stable snapshots use Git tags such as `paper-workbench-v0.1.0`; use `git checkout <tag>` for a pinned deployment. Run the update script from this project directory to back up local data, fast-forward the repository, and refresh Python dependencies.
