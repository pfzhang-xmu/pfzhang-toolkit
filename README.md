# DSH Plugins

[中文文档](./README.zh.md)

A collection of plugins for [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness).

## Available plugins

| Plugin | Description |
| --- | --- |
| [`dsh-github-workspace`](./dsh-github-workspace) | Local GitHub CLI workspace for browsing repositories and committing text-file changes from the DSH Web GUI. |
| [`dsh-image-tools`](./dsh-image-tools) | Host plugin registering OpenAI Images-compatible `generate_image` / `edit_image` tools for DSH agents, with project-local output storage. |
| [`dsh-cowart`](./dsh-cowart) | tldraw infinite canvas embedded in the DSH Web GUI with project-backed storage, a persistent floating window, and agent workflows for generate → annotate → refine. |
| [`dsh-figureforge`](./dsh-figureforge) | Browser-local React + TypeScript scientific figure editor with PNG/TIFF export and compatible project JSON files. |

Each plugin is self-contained in its own directory with installation, configuration, security, and verification instructions. Add future plugins as sibling directories and list them here.

## Combining dsh-cowart + dsh-image-tools

Used together, the two plugins turn DSH into a visual image-workbench: **generate → annotate → refine**, all inside an infinite canvas that stays open beside the chat.

```
┌─────────────────────────────────────────────┬──────────────────┐
│  DSH chat                                   │  Cowart canvas   │
│                                             │  (floating/pin)  │
│  [cowart-request:ai_image] prompt...        │  ┌────────────┐  │
│      ↓ generate_image                       │  │  AI frame  │  │
│      ↓ cowart_insert_image ────────────────►│  │  + prompt  │  │
│                                             │  └────────────┘  │
│  [cowart-request:annotation_edit]           │  ┌────────────┐  │
│      ↓ modlens_read_image (annotation)      │  │ annotated  │  │
│      ↓ edit_image (apply annotation)        │  └────────────┘  │
│      ↓ cowart_insert_image ────────────────►│  result beside   │
└─────────────────────────────────────────────┴──────────────────┘
```

### Example workflow

1. **Open the canvas** — say "Open the Cowart canvas"; the agent calls `cowart_open_canvas` and a floating canvas window appears (drag / resize / pin 📌 to the right edge).
2. **Generate** — create an "AI image" frame on the canvas and type a prompt (e.g. "一只戴宇航头盔的柴犬，赛博朋克风"). The request arrives as `[cowart-request:ai_image]`; the agent calls `generate_image` at the frame's aspect ratio, then `cowart_insert_image` replaces the frame — the canvas refreshes instantly via SSE.
3. **Annotate** — draw arrows / write notes on the image (e.g. "把模糊的飞鸟换成一行清晰可见的白鹭").
4. **Refine** — select the image and click "按标注修改"; the annotated screenshot is saved into the canvas assets and sent as `[cowart-request:annotation_edit]`. The agent reads the annotation with a vision bridge, edits the image with `edit_image`, and inserts the clean result beside the original — original and annotations stay untouched, so you can compare and iterate.

Everything (canvas JSON, images, annotations, reference shots) is stored under `<workspace>/canvas/`, versioned with your project.

## Contribution requirements

Every plugin must include both an English `README.md` and a Chinese `README.zh.md`. Keep the two documents aligned when functionality, installation, configuration, security boundaries, or verification steps change.
