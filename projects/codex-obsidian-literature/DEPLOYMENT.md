# 部署指南

## 新用户

```bash
git clone https://github.com/pfzhang-xmu/pfzhang-toolkit.git
cd pfzhang-toolkit/projects/codex-obsidian-literature
python3 scripts/setup.py
python3 scripts/doctor.py
```

项目框架由 Git 管理；`10_论文/`、`20_知识/`、`90_附件/` 和 `.research_index/` 是本地用户数据目录，默认被 `.gitignore` 忽略。

## 使用

把 PDF 放入 `90_附件/论文/`，或在 Codex 对话中直接发送 PDF。系统会生成论文笔记、ML Workflow 和必要的知识卡。

## 升级

```bash
python3 scripts/update.py --target main
```

升级前自动备份用户数据。框架版本使用语义化版本；稳定版本通过 GitHub Release 发布，可用 `git checkout v0.2.0` 固定或回退版本。升级不会删除论文、PDF、知识卡或索引。

## 索引与故障处理

配置 vault 外的 `~/.config/obsidian-codex/embedding.env` 后运行 `python3 scripts/research_search.py index`。没有 embedding 配置时，笔记处理仍可用，索引可稍后重建。
