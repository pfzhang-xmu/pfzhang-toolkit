# DSH 插件集合

[English](./README.md)

这是一个面向 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的插件集合仓库。

## 当前插件

| 插件 | 说明 |
| --- | --- |
| [`dsh-github-workspace`](./dsh-github-workspace) | 基于本机 GitHub CLI 的工作区，可在 DSH Web GUI 中浏览仓库并提交文本文件修改。 |
| [`dsh-image-tools`](./dsh-image-tools) | Host 端插件，为 DSH agent 注册 OpenAI Images 兼容的 `generate_image` / `edit_image` 工具，输出保存到项目目录。 |
| [`dsh-cowart`](./dsh-cowart) | 嵌入 DSH Web GUI 的 tldraw 无限画布，项目存储 + 常驻悬浮窗 + 「生成 → 标注 → 按标注精修」agent 工作流。 |
| [`dsh-figureforge`](./dsh-figureforge) | 基于 React + TypeScript 的浏览器本地科研图片编辑器，支持 PNG/TIFF 导出和兼容工程 JSON。 |

每个插件都位于独立目录中，并应提供安装、配置、安全边界和验证说明。后续新增插件请作为根目录下的同级目录添加，并同步更新本索引。

## dsh-cowart + dsh-image-tools 组合使用

两个插件搭配使用，可以把 DSH 变成可视化图片工作台：**生成 → 标注 → 按标注精修**，全程在聊天窗口旁常驻的无限画布上完成。

```
┌─────────────────────────────────────────────┬──────────────────┐
│  DSH 对话                                    │  Cowart 画布     │
│                                             │  （悬浮/固定）    │
│  [cowart-request:ai_image] prompt...        │  ┌────────────┐  │
│      ↓ generate_image                       │  │ AI 图片框  │  │
│      ↓ cowart_insert_image ────────────────►│  │ + prompt   │  │
│                                             │  └────────────┘  │
│  [cowart-request:annotation_edit]           │  ┌────────────┐  │
│      ↓ modlens_read_image（读标注）          │  │ 标注后的图 │  │
│      ↓ edit_image（按标注修改）              │  └────────────┘  │
│      ↓ cowart_insert_image ────────────────►│  结果放到原图旁  │
└─────────────────────────────────────────────┴──────────────────┘
```

### 示例流程

1. **打开画布** —— 说「打开 Cowart 画布」，agent 调用 `cowart_open_canvas`，画布出现在悬浮窗（可拖拽/缩放/📌 固定到右侧）。
2. **生成** —— 在画布上创建「AI 图片」框并输入 prompt（如「一只戴宇航头盔的柴犬，赛博朋克风」）。请求以 `[cowart-request:ai_image]` 到达 agent，按框的宽高比调用 `generate_image`，再用 `cowart_insert_image` 替换框——画布通过 SSE 即时刷新。
3. **标注** —— 在图片上画箭头、写批注（如「把模糊的飞鸟换成一行清晰可见的白鹭」）。
4. **精修** —— 选中图片点「按标注修改」，标注截图存入画布资源并以 `[cowart-request:annotation_edit]` 发送；agent 用视觉桥读标注、`edit_image` 按标注重绘，把干净的新图放到原图旁。原图与标注原样保留，方便对比迭代。

所有画布数据（画布 JSON、图片、标注、参考截图）都在 `<工作区>/canvas/` 下，随项目进 git。

## 文档要求

每个插件必须同时提供以下两份文档：

- 英文文档：`README.md`
- 中文文档：`README.zh.md`

当插件的功能、安装方式、配置、安全边界或验证步骤发生变化时，两份文档必须同步更新。
