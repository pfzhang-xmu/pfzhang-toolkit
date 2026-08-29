# PF Zhang 工具箱

[English](./README.md)

这是 [pfzhang-xmu](https://github.com/pfzhang-xmu) 维护的个人工具集合，用于存放日常使用、复用和持续迭代的插件、技能与项目：

- `plugins/`：提供工具、集成能力或宿主能力的插件。
- `skills/`：可被 AI Agent 复用的独立技能。
- `projects/`：完整应用及其他较大型项目。

## 内容索引

### 插件

- [`dsh-cowart`](./plugins/dsh-cowart)：tldraw 无限画布、图片标注和精修流程。
- [`dsh-github-workspace`](./plugins/dsh-github-workspace)：基于 GitHub CLI 的工作区集成。
- [`dsh-image-tools`](./plugins/dsh-image-tools)：兼容 OpenAI Images 的图片生成与编辑工具。

### Skills

[`skills/`](./skills) 目录用于存放可复用的 Agent 技能。每个技能可以包含自己的说明文档和辅助文件。

### 项目

- [`dsh-figureforge`](./projects/dsh-figureforge)：浏览器本地科研图片编辑器。
- [`paper-workbench`](./projects/paper-workbench)：论文写作、审查、数据、绘图和导出全流程工作台。

## Paper Workbench

Paper Workbench 提供本地 CLI、Web UI、桌面封装、MCP 服务、AI 辅助写作、科研绘图版本管理、参考图编辑以及 FigureForge 集成。

快速部署：

```bash
git clone https://github.com/pfzhang-xmu/pfzhang-toolkit.git
cd pfzhang-toolkit/projects/paper-workbench
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
./start-workbench.sh
```

然后访问 `http://127.0.0.1:8123`。

仓库中的 `app_config.example.json` 不含任何凭据。请复制为 `app_config.json`，再在本机配置 API Key，或使用环境变量注入。`data/` 和本地配置已通过 Git 忽略，不会被版本更新覆盖。

## 版本和更新

Paper Workbench 的开发分支是 `main`，稳定版本使用 `paper-workbench-v0.1.0` 等标签。已安装副本可运行 `./update-workbench.sh`：脚本会先备份本地配置和数据，再以 fast-forward 方式更新源码和依赖，保留运行数据。

各插件和项目目录内包含其各自的使用、迁移、测试和许可证说明。

## 许可证

每个目录保留自己的许可证。除非目录内另有说明，Paper Workbench 和 FigureForge 使用 MIT 许可证。
