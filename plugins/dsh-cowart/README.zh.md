# dsh-cowart — Cowart 无限画布 for DeepSeek Harness

面向 DeepSeek Harness 的插件：在 DSH Web GUI 中嵌入 [tldraw](https://github.com/tldraw/tldraw) 无限画布（改编自 [zhongerxin/Cowart](https://github.com/zhongerxin/Cowart)，MIT 协议）。

- **画布**：tldraw 画布由 DSH 同源托管于 `/cowart/`，配套项目存储 API 与 SSE 实时刷新。
- **存储**：画布数据保存在会话工作区的 `canvas/` 目录（`canvas/pages/<page-id>/cowart-canvas.json` + 图片资源），随项目进 git。
- **Agent 工具**：11 个 `cowart_*` 工具（打开画布、读写状态、插入图片、插入 HTML、选中、参考图、下载）。搭配任意 OpenAI Images 兼容的图片工具（`generate_image` / `edit_image`，如本仓库的 `dsh-image-tools`）与视觉桥（`modlens_read_image`），构成完整的「生成 → 标注 → 按标注精修」闭环。
- **常驻悬浮窗**：画布在悬浮窗中打开（可拖拽、可缩放、可固定到右侧成为侧边栏），不随对话轮数滚动消失。

## 目录结构

```
dsh-cowart/
  index.js              host 插件入口（工具、路由、技能）
  lib/                  存储 + 插入逻辑 + webServer 路由 + skill
  client/client.js      web 客户端：悬浮画布窗 + 消息桥
  canvas/               vendor 的 Cowart tldraw 应用（已打 DSH 补丁），懒构建
  scripts/              构建 / 验证 / 重启脚本
```

## 安装

需要 Node.js ≥ 20；首次构建需要网络（画布应用与 `tldraw` 按需安装）。

```bash
# 1) 安装到 DSH web profile（客户端半区必须作为包安装）
cd /path/to/your/workspace
dsh plugin --profile web add link:/absolute/path/to/dsh-cowart

# 2) 完全退出并重启 dsh web，然后刷新 http://127.0.0.1:3080
```

bundle 自带的 `cordis.patch.yml` 会自动插入 `cowart` 插件行；`dsh.client.platform: web` + `exports["./client"]` 让 Web 前端自动加载客户端半区。

首次使用时 host 插件会自动构建画布应用（`canvas/` 内 `npm install` + `vite build`）到 `dist/cowart/`。也可以手动预构建：

```bash
cd dsh-cowart && npm run build:canvas
```

## 使用

1. 说「打开 Cowart 画布」→ agent 调用 `cowart_open_canvas`，画布出现在悬浮窗（拖标题栏移动、拖右下角握把缩放、点 📌 固定到右侧成为侧边栏）。
2. 创建「AI 图片」框并输入 prompt → 请求以 `[cowart-request:ai_image]` 到达 agent：附带参考图时以第一张参考图为底图调用 `edit_image`（prompt 作为编辑指令，保留主体身份）；无参考图时按框的宽高比用 `generate_image` 生成 → `cowart_insert_image` 替换框，画布 SSE 自动刷新。
3. 在图上标注（箭头/文字）后点「按标注修改」→ 标注截图存入 canvas assets → agent 读标注（视觉）→ 编辑图片（`edit_image`）→ 结果插到原图旁。
4. AI HTML 框与 AI Slides 使用 `cowart_insert_html_draft`。

所有画布数据均在 `<工作区>/canvas/` 下。

## 安全边界

- 画布存储路径经校验，限制在 `<projectDir>/canvas` 内；page-asset URL 均做路径检查。
- 画布 iframe 同源；消息桥只把 `dsh-cowart` 标记的消息转发到当前会话。
- 本插件不处理任何 API Key 或凭据；图片生成委托给你的图片工具服务商。
- 上游画布的分析埋点在 DSH 嵌入模式下已禁用。

## 验证

```bash
cd dsh-cowart && npm run check          # 语法检查
node scripts/verify.mjs --port=3080     # 对运行中的实例做预检
```

## 致谢

画布能力基于 [tldraw/tldraw](https://github.com/tldraw/tldraw)；画布应用改编自 [zhongerxin/Cowart](https://github.com/zhongerxin/Cowart)（MIT）。

## 使用示例

一次典型会话（需要图片生成/编辑工具，如 `dsh-image-tools`；建议配合视觉桥 `modlens_read_image`）：

1. **打开** —— 说「打开 Cowart 画布」（或点输入栏的 🖼 画布 按钮）。画布在悬浮窗中打开：拖标题栏移动、拖右下角握把缩放、点 📌 固定到右侧成为侧边栏；位置、大小、固定状态与项目都会记住。
2. **生成** —— 创建「AI 图片」框并输入 prompt，例如：

   ```text
   画一幅动漫风的山水风景图
   ```

   框的请求以 `[cowart-request:ai_image]` 到达 agent（含框 id、目标尺寸与宽高比）。agent 按比例调用 `generate_image`，再用 `cowart_insert_image`（anchorShapeId = 框 id，replaceAiImageHolder 默认）把框替换为普通图片形状，画布自动刷新。
3. **标注与精修** —— 在图片上画箭头/写批注（如「把模糊的飞鸟换成一行清晰可见的白鹭」），选中后点「按标注修改」。标注截图存入 page assets，并以 `[cowart-request:annotation_edit]` 发送；agent 读标注（视觉桥）→ `edit_image` 重绘 → `cowart_insert_image` 带 `annotationScreenshot` 元数据把新图放到原图旁。原图与标注原样保留。
4. **AI HTML 与 AI Slides** —— 「AI HTML」框或「AI Slides」框会发送 `[cowart-request:ai_html]` / `[cowart-request:ai_slides]`；agent 生成单文件 HTML 后调用 `cowart_insert_html_draft` 嵌入画布。

所有资源（画布 JSON、图片、标注截图、HTML 草稿）都在 `<工作区>/canvas/` 下，随项目进 git。
