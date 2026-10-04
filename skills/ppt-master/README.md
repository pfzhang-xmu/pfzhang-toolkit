# PPT Master（收录副本）

> **本目录是第三方 skill 的镜像副本，非本仓库原创。**
> 上游原地址：**[hugohe3/ppt-master](https://github.com/hugohe3/ppt-master)**
> 除本 `README.md`（收录说明）外，目录内所有文件与上游对应路径**逐字节一致、未作任何改动**。

| 项目 | 值 |
| --- | --- |
| 原仓库 | https://github.com/hugohe3/ppt-master |
| 上游路径 | `skills/ppt-master/` |
| Skill 版本 | **6.6.0**（见 `SKILL.md` frontmatter `metadata.version`） |
| 收录时上游 commit | `44c10ed` |
| 作者 | [Hugo He](https://www.hehugo.com/)（heyug3@gmail.com） |
| 许可证 | **MIT** — 见 [`LICENSE`](./LICENSE)（版权归原作者，转载保留原始声明） |
| 收录日期 | 2026-10-04 |

## 这是什么

PPT Master 是一个 **agent 工作流 skill**（不是普通应用）：把「素材 → 原生可编辑 PPTX」的流程写成权威 Markdown 流程 + 一批 Python 工具脚本，由任意具备读写文件与执行命令能力的 AI 宿主（DSH、Claude Code、Codex 等）驱动。产出是真正的 DrawingML 原生形状、图表、转场与动画，而非图片版幻灯片。

三条顶层路线：

- **Generate PPTX** — 主流程；Default（含 Stage1/Stage2 人工确认）或显式 Quick（无确认直出）
- **Create Template** — 从参考物蒸馏 Brand / Style / Layout / Deck 可复用工作区
- **Edit Native PPTX** — 原生 PPTX 往返编辑，未改动页面字节级保留

完整说明以 [`SKILL.md`](./SKILL.md) 为唯一权威入口；上游文档见 [原仓库 docs](https://github.com/hugohe3/ppt-master/tree/main/docs)。

## 安装为 agent skill

```bash
git clone https://github.com/pfzhang-xmu/pfzhang-toolkit.git
pip install -r pfzhang-toolkit/skills/ppt-master/requirements.txt

# 复制到你的 agent skills 目录（保持目录名 ppt-master）
cp -R pfzhang-toolkit/skills/ppt-master ~/.dsh/skills/
```

视觉审查（把幻灯片渲染成 PNG 做自检）还需要 Playwright 浏览器：

```bash
pip install playwright && python3 -m playwright install chromium
```

可选外部工具：ImageMagick（PPTX 内嵌 EMF/WMF 转换）、ffmpeg（旁白混音与字幕）、pandoc（`.doc`/`.odt`/`.rtf`/`.tex` 等旧格式）。

### ⚠️ 路径约定（从本仓库复制时必须知道）

skill 脚本按**自身文件位置**推导工作区根目录：`REPO_ROOT = <skill目录>/../..`，`projects/` 落在 `REPO_ROOT/projects`。因此：

- 复制到 `~/.dsh/skills/ppt-master` → 产出落在 `~/.dsh/projects/`
- 放在 `<工作区>/skills/ppt-master` → 产出落在 `<工作区>/projects/`（上游推荐布局）

另：`.env` 查找顺序为 `./.env` → `<skill目录>/.env` → `<REPO_ROOT>/.env` → `~/.ppt-master/.env`（只读第一个存在的文件）。建议把配置放在用户级 `~/.ppt-master/.env`，既不影响仓库，也不随 skill 更新丢失。

## 不要修改本目录内的文件

skill 自带完整性门 [`scripts/attribution_guard.py`](./scripts/attribution_guard.py)，会校验 `SKILL.md` 的版权/许可/仓库元数据、`LICENSE` 摘要以及 8 个关键脚本中的完整性调用。**任何改动都会导致 skill 以 exit 78 直接拒绝运行**（只读、不可绕过，且脚本自身不会提示具体原因）。配置请一律写入 `~/.ppt-master/.env`。

## 更新

本目录是**镜像副本**，不是 git 子模块，因此：

```bash
# 从上游重新同步（推荐：按需覆盖，保留你自己的配置）
git clone --depth 1 https://github.com/hugohe3/ppt-master.git
rsync -a --delete --exclude='__pycache__/' --exclude='*.pyc' \
  ppt-master/skills/ppt-master/ pfzhang-toolkit/skills/ppt-master/
```

> 请勿在本仓库内运行上游的 `scripts/update_repo.py`：它会在 `REPO_ROOT`（即本仓库根）执行 `git pull`，拉取的是 `pfzhang-toolkit` 而非上游 ppt-master。

## 第三方依赖的许可证提示

`requirements.txt` 中的 **PyMuPDF 采用 AGPL-3.0**（Artifex 另提供商业许可），仅被 PDF 转换脚本使用；不转换 PDF 源可跳过该依赖。若要连同依赖一起再分发，请先核对 AGPL 条款。
