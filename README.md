# DSH Ecosystem

[中文文档](./README.zh.md)

This repository contains three kinds of reusable content for the DeepSeek Harness ecosystem:

- `plugins/` — DSH plugins that register tools, UI integrations, or host capabilities.
- `skills/` — standalone skills reusable by agents.
- `projects/` — complete applications and non-plugin projects.

## Contents

### Plugins

- [`dsh-cowart`](./plugins/dsh-cowart) — tldraw infinite canvas and image annotation workflows.
- [`dsh-github-workspace`](./plugins/dsh-github-workspace) — local GitHub CLI workspace integration.
- [`dsh-image-tools`](./plugins/dsh-image-tools) — OpenAI Images-compatible image generation/editing tools.

### Projects

- [`dsh-figureforge`](./projects/dsh-figureforge) — browser-local scientific figure editor.
- [`paper-workbench`](./projects/paper-workbench) — end-to-end academic writing, review, data, figure, and export workbench.

`dsh-figureforge` is a project rather than a DSH plugin. It is also bundled into Paper Workbench as the FigureForge editor.

## Paper Workbench

Paper Workbench provides a local CLI, Web UI, desktop wrapper, MCP server, AI-assisted writing workflow, versioned AI scientific figure generation, reference-image editing, and FigureForge integration.

Quick deployment:

```bash
git clone https://github.com/pfzhang-xmu/dsh-plugins.git
cd dsh-plugins/projects/paper-workbench
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
./start-workbench.sh
```

Then open `http://127.0.0.1:8123`.

The checked-in `app_config.example.json` contains no credentials. Copy it to `app_config.json` and configure API keys locally or through environment variables. Runtime data under `data/` and local settings are intentionally ignored by Git.

## Versioning and updates

Paper Workbench development happens on `main`. Stable snapshots use tags such as `paper-workbench-v0.1.0`. From an installed checkout, run `./update-workbench.sh` to back up local configuration/data, fast-forward the source tree, refresh dependencies, and preserve runtime data.

See the project documentation for migration, rollback, security, and testing details.

## License

Each directory keeps its own license. Paper Workbench and FigureForge are MIT-licensed unless noted otherwise.
