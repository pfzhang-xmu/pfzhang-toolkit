# 部署指南

## 自动部署

将仓库下载为 Obsidian vault 后，在项目根目录执行：

```bash
python3 scripts/setup.py
python3 scripts/doctor.py
```

脚本会检查 Python、安装 `requirements.txt`、创建目录、检查配置并运行测试。Embedding 配置缺失时不会写入密钥，也不会阻止本地笔记处理。

## Embedding 配置

在 vault 外创建 `~/.config/obsidian-codex/embedding.env`：

```text
EMBEDDING_BASE_URL=https://your-provider.example/v1
EMBEDDING_API_KEY=your-key
EMBEDDING_MODEL=your-embedding-model
```

不要把 API key 写入 Git、Markdown、`.obsidian` 或聊天记录。

## Codex 自动部署

把 GitHub 项目地址发送给 Codex，并说明“按 DEPLOYMENT.md 部署为 Obsidian vault”。Codex 应运行 setup、doctor，询问唯一必须人工提供的 embedding 配置，并验证示例 PDF 流程。

## 使用

将 PDF 放入 `90_附件/论文/`，或在 Codex 对话中直接发送 PDF。Codex 自动生成 `10_论文/` 下的 Paper Note 和 ML Workflow，并按需生成 `20_知识/` 原子卡；完成后更新索引并返回简报。

## 故障处理

- 缺依赖：重新运行 `python3 scripts/setup.py`。
- PDF 无法解析：确认文件完整且为真实 PDF。
- 索引失败：检查 embedding.env；笔记仍会保留，可稍后运行 `python3 scripts/research_search.py index`。
- 权限不足：确认 vault 和配置目录可写。
