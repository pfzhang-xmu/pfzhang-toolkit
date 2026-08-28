# Paper Workbench｜论文写作与科研绘图工作台

Paper Workbench 是一个本地运行的论文工作台：覆盖文献检索、论文框架、分段写作、审查、引用核验、数据绘图、AI 科研绘图、FigureForge 图片编辑和导出。它不上传项目数据；配置、论文、图片和版本历史默认保存在本机。

## 快速安装

需要 Python 3.10+，推荐 Python 3.12。克隆仓库后执行：

```bash
cd dsh-plugins/projects/paper-workbench
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp app_config.example.json app_config.json
./start-workbench.sh
```

浏览器打开 <http://127.0.0.1:8123>。Windows 可使用 `web/start-workbench.bat` 或在项目目录运行对应的 Python 命令；桌面模式执行 `.venv/bin/python desktop.py`（Windows 为 `.venv\\Scripts\\python.exe desktop.py`）。

仓库已提交 FigureForge 的静态构建产物，普通使用不需要 Node/npm。只有修改 `frontend/figureforge` 时才需要：

```bash
cd frontend/figureforge
npm install
npm run build
```

## 配置 AI

编辑本机的 `app_config.json`（该文件已被 Git 忽略）：

- `ai.base_url`、`ai.api_key`、`ai.model`：论文写作使用的 OpenAI 兼容接口。
- `image.base_url`、`image.api_key`、`image.model`：图片生成/编辑接口；编辑固定使用 `/v1/images/edits`，服务不支持时会明确报错，不降级为文生图。
- `project_roots`：允许工作台发现的项目根目录。

不要把 API Key 写入 README、脚本、截图或 Git。也可以在部署环境中通过本机配置管理工具注入密钥。

## 两种使用模式

- **独立模式**：不选择项目即可使用 AI 科研绘图和 FigureForge，素材保存在工作台目录下的 `data/figures/_standalone/`。
- **项目模式**：选择论文项目后，素材保存在该项目的 `data/figures/<asset_id>/`，可与稿件和项目工作台联动。

AI 科研绘图支持新建、基于历史版本修改、参考图编辑。每次结果都生成不可变的新版本；生成后先在历史列表预览，再点击“设为当前”。

## FigureForge

FigureForge 提供 PNG 合成、裁剪、擦除、缩放、旋转、翻转、文字标注、撤销/重做、PNG/TIFF 导出和工程 JSON 保存。可从 AI 科研绘图的任意版本点击“用 FigureForge 编辑”，保存后会产生下一个版本，不覆盖旧图。若版本没有工程 JSON，则以 PNG 底图开始编辑。

## 数据目录与更新

运行数据不进入 Git，典型目录包括：

```text
data/figures/<asset_id>/versions/   # v001.png、FigureForge 工程 JSON
data/figures/<asset_id>/references/ # 参考图
data/                               # 项目、台账和生成产物
app_config.json                     # 本机配置和密钥
```

更新前请在项目目录执行：

```bash
./update-workbench.sh
```

脚本会备份 `app_config.json`、`data/` 和会话状态，再执行 `git fetch --tags` 与 `git pull --ff-only`，最后更新 Python 依赖。失败时原始文件仍保留在备份目录。稳定部署可切换到标签，例如 `paper-workbench-v0.1.0`；回滚前先停止服务并备份数据。

建议生产部署将源码、运行数据和备份分离：`source/`、`runtime/`、`backups/`。单目录部署也安全，因为 `app_config.json`、`data/`、虚拟环境和缓存均被忽略。

## 常见故障

- **浏览器无法访问**：确认服务进程仍在运行、端口未被占用，并访问 `127.0.0.1:8123`；可用 `./start-workbench.sh 8124` 更换端口。
- **AI 不可用**：检查 Base URL 是否包含 `/v1`、密钥和模型名是否正确，并查看终端错误。
- **图片编辑失败**：当前服务或模型可能未实现 OpenAI 兼容的 `/images/edits`；原版本不会被覆盖。
- **FigureForge 资源缺失**：开发构建后确认 `web/static/figureforge/figureforge.js` 和 `figureforge.css` 存在。

## 开发与测试

```bash
.venv/bin/python -m py_compile *.py web/*.py
.venv/bin/python -m unittest -v test_sci_figure_versions.py test_figureforge_integration.py
python smoke_test.py
cd frontend/figureforge && npm install && npm run build
```

## 安全与许可证

Paper Workbench 只接受项目内上传的参考图，不允许通过接口读取任意服务器路径；路径、素材 ID 和版本号都会校验。请自行备份论文和图片数据。项目采用 [MIT License](LICENSE)。
