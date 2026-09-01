# Skills Catalog

这里集中维护可复用的 Codex Skills。每个 skill 通常包含一个 `SKILL.md`，以及可选的脚本、参考资料、模板和许可证文件。使用前请先阅读对应的 `SKILL.md`。

## 快速开始

### 安装单个 skill

克隆仓库后，将目标 skill 目录复制到你的 agent skills 目录：

```bash
git clone https://github.com/pfzhang-xmu/pfzhang-toolkit.git
cp -R pfzhang-toolkit/skills/<skill-name> <your-agent-skills-directory>/
```

例如：

```bash
cp -R pfzhang-toolkit/skills/aihot <your-agent-skills-directory>/
```

### 使用方式

1. 将 skill 目录放入 agent 可发现的 skills 目录。
2. 阅读该目录下的 `SKILL.md`，了解触发条件、限制和工作流。
3. 在对话中直接描述任务；当请求命中 skill 的适用场景时，agent 会按其中的规则执行。
4. 如果 skill 附带脚本、模板或参考资料，按 `SKILL.md` 中的命令和路径调用。

> 技能名称、触发词和参数以各自的 `SKILL.md` 为准。本页是导航和快速参考，不替代技能原文。

## 通用与创作类 Skills

| Skill | 适用场景 | 基本使用方法 |
| --- | --- | --- |
| [`grilling`](./grilling) | 对计划、决策或想法进行持续、结构化的压力测试，逐轮追问直到形成共同理解。 | 直接提出“帮我 grill 这个计划/方案”或要求对某个决定进行全面质询；agent 会按设计树分轮提问。 |
| [`aihot`](./aihot) | 查询最新中文 AI 资讯、模型发布、产品动态、AI 论文和行业热点。 | 直接问“今天 AI 圈有什么”“AI 日报”“最近的模型发布”；skill 会调用 AI HOT 公开 API 并整理成中文简报。 |
| [`ass-LLM`](./ass-LLM) | 明确要求把任务转交给外部 LLM（GPT、Gemini 等）处理。 | 明确说“用外部模型”“调用 GPT/Gemini”“代理模式”，并指定模型或让 skill 按可用模型转发；启用后会原样转发外部模型回答。 |
| [`frontend-design`](./frontend-design) | 创建高质量、具有独特视觉风格的网页、Landing Page、Dashboard、React 组件或 HTML/CSS 界面。 | 描述页面目标、内容和风格，或提供现有 UI 要求美化；skill 会生成生产级前端代码并避免模板化 AI 风格。 |
| [`imagen-skill`](./imagen-skill) | 使用 GPT Image-2 进行文生图，或根据参考图进行编辑、换背景、修图和生成变体。 | 直接描述要生成的图片，或附图并说明修改内容；skill 会判断文生图/参考图编辑模式并执行对应工作流。 |
| [`image2ppt`](./image2ppt) | 将 PNG、JPG、截图或照片中的幻灯片还原为可编辑 PPTX，尽量保持原图布局和视觉效果。 | 提供图片并说“图片转 PPT/截图转可编辑 PPT/还原幻灯片”；可配合 `assets/blank.pptx`、脚本和参考资料使用。 |
| [`mcp-builder`](./mcp-builder) | 从零创建或改进 MCP（Model Context Protocol）服务器，将外部 API 或服务封装成 LLM 可调用工具。 | 说明要接入的服务、接口和目标语言（Python/FastMCP 或 Node/TypeScript）；按 skill 指引设计工具、资源、错误处理并验证。 |
| [`skill-creator`](./skill-creator) | 创建新 skill、修改已有 skill、优化触发描述，或运行评测/基准测试衡量 skill 效果。 | 提供目标能力和示例任务；可进一步要求运行 eval、benchmark、描述优化或生成 skill 包。 |
| [`xmu-literature-downloader`](./xmu-literature-downloader) | 使用厦门大学图书馆、WebVPN、CAS SSO 或已登录浏览器会话，批量下载 ACS、Wiley、Elsevier、RSC、Science/AAAS 等论文 PDF。 | 提供论文 URL 清单（TSV/CSV）或说明机构访问方式，提出“批量下载论文 PDF”；skill 会按其验证、重试和记录流程执行。 |

## 办公与文档 Skills

办公类 skill 位于 [`office/`](./office)，适合处理 Word、PowerPoint、Excel 和 PDF 文件。涉及相应文件类型时，优先使用对应 skill。

| Skill | 适用场景 | 基本使用方法 |
| --- | --- | --- |
| [`docx`](./office/docx) | 创建、读取、编辑、转换和校验 Word（`.docx`）文档，包括目录、页码、批注和修订。 | 提供文档或说明目标格式；创建/编辑后按 skill 要求渲染、校验并迭代，确保版式正确。 |
| [`pptx`](./office/pptx) | 创建、读取、编辑、拆分、合并和验证 PowerPoint（`.pptx`）演示文稿。 | 提供 PPTX、模板或内容要求；按读取、编辑/生成、渲染和验证流程处理。 |
| [`xlsx`](./office/xlsx) | 创建、读取、清洗、分析和编辑 Excel/CSV/TSV 表格，包含公式、格式、图表和重算。 | 提供表格文件与目标变更；使用公式和现有模板约定，完成后检查公式错误并重算验证。 |
| [`pdf`](./office/pdf) | 读取、提取、合并、拆分、旋转、创建、填写表单、OCR 和验证 PDF。 | 提供 PDF 或目标操作；根据任务选择 pypdf、pdfplumber、reportlab、OCR 和渲染检查流程。 |
| [`markitdown`](./office/markitdown) | 将 Word、PPT、Excel、PDF、图片、HTML、EPUB、ZIP 等内容提取为 Markdown/纯文本，供 LLM 分析。 | 使用“转成 Markdown/提取文档内容/批量转换”等请求；单文件或批量调用 `markitdown`，再进行总结、翻译或分析。 |

## 推荐工作流

- 需要“内容提取、总结、翻译、分析”时，先用 [`markitdown`](./office/markitdown) 统一转换为 Markdown。
- 需要创建或编辑最终文件时，使用对应的 [`docx`](./office/docx)、[`pptx`](./office/pptx)、[`xlsx`](./office/xlsx) 或 [`pdf`](./office/pdf) skill。
- 需要从图片还原演示文稿时，使用 [`image2ppt`](./image2ppt)；需要生成或修改位图时，使用 [`imagen-skill`](./imagen-skill)。
- 需要扩展 agent 能力时，使用 [`skill-creator`](./skill-creator)；需要接入外部服务时，使用 [`mcp-builder`](./mcp-builder)。

## 目录结构

```text
skills/
├── aihot/
├── ass-LLM/
├── frontend-design/
├── grilling/
├── image2ppt/
├── imagen-skill/
├── mcp-builder/
├── office/
│   ├── docx/
│   ├── markitdown/
│   ├── pdf/
│   ├── pptx/
│   └── xlsx/
├── skill-creator/
└── xmu-literature-downloader/
```

各目录中的 `SKILL.md` 是权威说明；脚本、参考资料、模板和许可证均以目录内文件为准。
