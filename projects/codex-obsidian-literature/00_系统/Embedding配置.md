# External Embedding配置

检索脚本使用 OpenAI-compatible embedding API。不要把 API key 写入 vault、Markdown、`.obsidian`、`.claudian` 或聊天记录中的笔记。

## 推荐：本机配置文件

脚本会自动读取 vault 外部的：

```text
~/.config/obsidian-codex/embedding.env
```

创建目录并限制权限：

```bash
mkdir -p ~/.config/obsidian-codex
chmod 700 ~/.config/obsidian-codex
chmod 600 ~/.config/obsidian-codex/embedding.env
```

文件内容格式：

```text
EMBEDDING_BASE_URL=https://your-provider.example/v1
EMBEDDING_API_KEY=your-api-key
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_TIMEOUT_S=60
EMBEDDING_BATCH_SIZE=32
```

脚本读取优先级为：进程环境变量 > 本机配置文件 > 缺失配置。配置文件不会写入 Chroma metadata，也不会出现在检索结果中。

如确实需要指定其他位置，可设置：

```bash
export EMBEDDING_ENV_FILE="/absolute/path/to/embedding.env"
```

不建议把该文件放在 vault 内。若为了个人项目方便仍放入 vault，必须确保它不会被同步、备份或提交到任何仓库。

## 环境变量

```bash
export EMBEDDING_BASE_URL="https://your-provider.example/v1"
export EMBEDDING_API_KEY="your-api-key"
export EMBEDDING_MODEL="your-embedding-model"
export EMBEDDING_TIMEOUT_S="60"
export EMBEDDING_BATCH_SIZE="32"
```

`EMBEDDING_BASE_URL` 可以带或不带 `/embeddings`；脚本会自动补齐。API 必须接受：

```json
{"model":"your-embedding-model","input":["text"]}
```

并返回包含 `data[].index` 和 `data[].embedding` 的 JSON。

## 初始化和查询

```bash
python3 scripts/research_search.py index
python3 scripts/research_search.py search --query "哪些论文适合我的电催化小数据问题？"
```

使用外部配置文件后，Obsidian 从图形界面启动也能由脚本自行读取配置，不依赖 GUI 进程继承终端环境变量。修改配置文件后，下一次执行索引或检索即可生效。

## 安全与故障处理

- API key 只通过 `EMBEDDING_API_KEY` 或 vault 外部配置文件读取。
- 外部服务只接收结构化 Markdown 片段，不接收 PDF 原文或本地路径。
- API 失败时，检索会降级到关键词/Properties；索引不会写入不完整向量。
- Chroma 只在本地保存向量、文本块和非敏感 metadata。
