# Codex–Obsidian 通用文献管理系统

把 PDF 直接交给 Codex，自动生成可追溯的论文笔记、ML/AI 流程笔记和可复用知识卡，并更新本地 Chroma 检索索引。

## 快速开始

```bash
python3 scripts/setup.py
python3 scripts/doctor.py
python3 scripts/process_literature.py "/path/to/paper.pdf"
python3 scripts/research_search.py search --query "我的研究问题"
```

在 Codex 项目对话中发送 PDF 即可触发同一流程；文字可作为补充要求。详见 [DEPLOYMENT.md](DEPLOYMENT.md)。

目录：`00_系统`、`10_论文`、`20_知识`、`90_附件`。
