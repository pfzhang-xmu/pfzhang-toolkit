# DSH 生态仓库

[English](./README.md)

本仓库统一存放 DeepSeek Harness 生态中的三类内容：

- `plugins/`：DSH 插件，负责注册工具、界面集成或宿主能力。
- `skills/`：可被 Agent 复用的独立技能。
- `projects/`：完整应用和非插件项目。

## 内容索引

### 插件

- [`dsh-cowart`](./plugins/dsh-cowart)：tldraw 无限画布、图片标注和按标注精修流程。
- [`dsh-github-workspace`](./plugins/dsh-github-workspace)：基于本机 GitHub CLI 的工作区集成。
- [`dsh-image-tools`](./plugins/dsh-image-tools)：OpenAI Images 兼容的图片生成与编辑工具。

### 项目

- [`dsh-figureforge`](./projects/dsh-figureforge)：浏览器本地科研图片编辑器。
- [`paper-workbench`](./projects/paper-workbench)：论文写作、审查、数据、绘图和导出全流程工作台。

`dsh-figureforge` 是项目，不是 DSH 插件；它同时作为编辑器内置到 Paper Workbench。

## Paper Workbench

Paper Workbench 提供本地 CLI、Web UI、桌面封装、MCP 服务、AI 辅助写作、科研绘图版本管理、参考图编辑以及 FigureForge 集成。

快速部署：

```bash
git clone https://github.com/pfzhang-xmu/dsh-plugins.git
cd dsh-plugins/projects/paper-workbench
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
./start-workbench.sh
```

然后访问 `http://127.0.0.1:8123`。

仓库中的 `app_config.example.json` 不含任何凭据。请复制为 `app_config.json`，再在本机配置 API Key，或使用环境变量注入。`data/` 和本地配置已通过 Git 忽略，不会被版本更新覆盖。

## 版本和更新

Paper Workbench 的开发分支是 `main`，稳定版本使用 `paper-workbench-v0.1.0` 等标签。已安装副本可运行 `./update-workbench.sh`：脚本会先备份本地配置和数据，再以 fast-forward 方式更新源码和依赖，保留运行数据。

详细部署、迁移、回滚、安全和测试说明请参阅项目文档。

## 许可证

每个目录保留自己的许可证。除非目录内另有说明，Paper Workbench 和 FigureForge 使用 MIT 许可证。
