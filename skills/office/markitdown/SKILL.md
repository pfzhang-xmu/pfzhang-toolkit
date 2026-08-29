---
name: markitdown
description: >-
  Use this skill whenever the user wants to convert office files or documents into Markdown/plain text for LLM
  consumption — Word (.docx), PowerPoint (.pptx), Excel (.xlsx/.csv), PDF, images (OCR), HTML, EPUB, or ZIP archives
  containing such files. Triggers include: "转成 Markdown", "提取文档内容", "把文件内容拿出来", "转成纯文本",
  "批量转换文件", "统一转成 markdown", "整理成语料", "convert to markdown", "extract text from this docx",
  "summarize/translate/analyze this pptx/pdf" (conversion is the prerequisite step). This skill is a one-way converter
  (file → Markdown); use it BEFORE summarization, translation, analysis, or knowledge-base building tasks, and when
  mixed-format files need a uniform Markdown output. Do NOT use for creating, editing, or high-fidelity formatting of
  documents — that is the docx/pptx/xlsx/pdf skills' job. Make sure to use this skill whenever the user mentions
  converting any office file to markdown/text for AI processing, even if they don't explicitly say "markitdown".
---

# MarkItDown 转换 Skill

## 定位

markitdown 是 Microsoft 出品的**单向**文件→Markdown 转换器，输出面向 LLM 消费，优先保留文档结构（标题、列表、表格、链接），不做高保真排版。

**与办公 skill 的分工**（用户没明说时要自己判断意图）：
- 本 skill：**提取**内容为 Markdown —— 是"总结 / 翻译 / 分析 / 建语料 / 知识库"任务的前置步骤
- docx / pptx / xlsx / pdf skill：创建、编辑、高保真格式化
- 判断标准：用户要"内容"（转文本、提取、分析、汇总）→ 本 skill；用户要"文件"（编辑、排版、生成、合并）→ 对应编辑 skill

## 前提

- markitdown 0.1.7 已安装，直接调用 `markitdown` 命令即可（无需激活虚拟环境）
- 常用格式依赖已装齐：docx (mammoth)、pptx (python-pptx)、xlsx (openpyxl)、pdf (pypdfium2)、html (markdownify)、csv/json/xml
- **已知限制**：
  - 音频转写 (wav/mp3) 依赖 ffmpeg，本机**未安装** → 音频转换会失败，提前告知用户，不要盲目尝试
  - YouTube 字幕需要网络；Azure 云转换需额外配置（默认不用）
  - stderr 可能出现 `Couldn't find ffmpeg` 警告 —— 对办公文件无害，忽略
  - 扫描版 PDF 只提取到文字层；无文字层的扫描件需先用 pdf skill 的 OCR 处理

## 核心命令

### 单个文件

```bash
markitdown "输入文件.docx" -o "输出.md"
```

- 不带 `-o` 时输出到 stdout
- 输出文件名默认与输入同名、扩展名改为 `.md`；用户指定了路径就用 `-o`，不要在没问的情况下乱放

### 批量转换（目录内多个文件）

```bash
mkdir -p "输出目录"
for f in "输入目录"/*.docx "输入目录"/*.pptx "输入目录"/*.pdf "输入目录"/*.xlsx; do
  [ -f "$f" ] && markitdown "$f" -o "输出目录/$(basename "${f%.*}").md"
done
```

### ZIP 一次转换整批文件

```bash
markitdown archive.zip -o all.md
```

一次调用遍历 zip 内所有支持的文件，输出按文件名分段。适合"一批不同格式的文件统一转成一个 markdown"。

### 管道输入

```bash
cat 文件 | markitdown
```

## 支持格式

PDF / PowerPoint (.pptx) / Word (.docx) / Excel (.xlsx, .xls) / 图片 (EXIF 元数据 + OCR 文字) / HTML / CSV / JSON / XML / ZIP / EPUB

## 使用流程

1. 确认输入路径存在（路径含空格或中文时**必须加引号**）
2. 按需求选择最合适的方式：单个 / 目录批量 / ZIP 打包
3. 转换后检查输出：非空且包含预期结构（标题、表格等）；批量任务抽查 1-2 个结果
4. 输出为空或报错时：看 stderr 定位原因；确认格式在支持列表内；`markitdown --help` 可查参数

## 输出约定

- 单个文件默认输出到源文件同目录、同名 `.md`；批量转换建独立输出目录，避免与源文件混放
- 转换完成后向用户报告输出位置和内容概要（首个标题/行数），让用户确认是否符合预期

## 安全注意

markitdown 以当前进程权限访问文件。只处理用户明确提供的文件，不处理不可信来源的任意输入。
