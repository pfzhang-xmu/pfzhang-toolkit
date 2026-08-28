export const COWART_SKILL = {
  name: 'cowart',
  source: 'runtime',
  whenToUse:
    '用户要求打开/使用 Cowart 画布，或收到以 [cowart-request: 开头的消息（画布内点击生成/按标注修改），或需要针对画布选中形状生成/编辑图片并插回画布。',
  description:
    'Cowart 无限画布：在 DSH Web GUI 内打开 tldraw 画布，读取画布选中状态，用 image-tools（generate_image / edit_image）与 modlens 完成「生成 → 标注 → 按标注精修」闭环，并把结果插回画布。',
  content: `# Cowart 画布（DSH 适配版）

Cowart 是一个嵌入 DSH Web GUI 的 tldraw 无限画布。画布数据保存在当前项目目录的 \`canvas/\` 下（\`canvas/pages/<page-id>/cowart-canvas.json\` + 图片资源），与对话工作区一致。

## 画布工具（全部以 cowart_ 开头）

- \`cowart_open_canvas\`：打开画布 iframe（projectDir 缺省为当前会话工作区）。用户说「打开 Cowart 画布」时调用。
- \`cowart_get_selection\`：读取画布当前选中形状（含图片资源元数据）。要针对画布选中内容操作时先调用它。
- \`cowart_get_canvas_state\` / \`cowart_save_canvas_state\`：读写整个 tldraw snapshot（一般不直接用，插入工具会自动保存）。
- \`cowart_insert_image\`：把本地图片复制进画布 assets 并创建 image 形状；默认替换选中的「AI 图片」框，否则放在锚点旁边。
- \`cowart_insert_html_draft\`：保存单文件 HTML 并创建 embed 形状（AI HTML 框 / AI Slides 页）。
- \`cowart_save_reference_image\`：把 dataUrl/base64 图片存入画布 page assets（画布自身也在用）。
- \`cowart_read_page_asset\`：读取画布资产（图片/HTML）的 base64 与路径。
- \`cowart_save_selection_state\` / \`cowart_save_view_state\`：画布内部维护用，agent 一般不需要。
- \`cowart_download_file\`：把画布请求的文件存入 \`<projectDir>/Downloads/\`。

图片生成与编辑请使用 image-tools 插件的 \`generate_image\` / \`edit_image\`：\`edit_image\` 直接以本地图片路径为输入（编辑模型能看到参考图本身），适合「以参考图为底图做编辑」；需要自己检查图片内容时（如 ai_html/ai_slides 的参考图、标注截图）用 \`modlens_read_image\`。

## 打开画布

用户要求打开/使用画布时：

1. 调用 \`cowart_open_canvas\`（不传 projectDir 时默认当前会话工作区）。
2. 工具卡片处会出现画布 iframe，等待用户操作。
3. 不要手动去读 canvas/ 文件或做校验；画布自会保存。

## 收到 [cowart-request:*] 消息

用户在画布内点击生成/按标注修改时，会以 \`[cowart-request:类型] …提示词…\` 的形式发来一条用户消息，提示词文本里包含形状 id、目标尺寸、参考图或标注截图路径等自包含信息。按下面流程处理。

### AI 图片框生成（cowart-request:ai_image）

1. 提示词里给出 AI 图片框 id、目标宽高与宽高比、参考图本地路径（如有）。
2. **有参考图时：这是图片编辑任务，不是从零生成**——调用 image-tools 的 \`edit_image\`，以第一张参考图的本地路径为 \`image\`、把用户 Prompt 作为编辑指令（在参考图基础上修改：换背景/服装/发型等，保留人物长相与身份）；\`size\` 按目标宽高比选择（如 3:4 → 1024x1365），保证结果填满画布槽位且不裁剪拉伸。编辑工具能直接打开参考图路径，不需要先用 modlens 读图。
3. **没有参考图时**：才用 \`generate_image\` 按目标宽高比生成新图。
4. 用 \`cowart_insert_image\` 插入：\`imagePath\` = 生成/编辑结果路径（.dsh-images/ 下），\`anchorShapeId\` = 框 id，\`replaceAiImageHolder\` 默认 true 把框替换为图片；多张图时第一张替换框，后续用上一张的 shapeId 作 anchor、\`replaceAiImageHolder:false, matchAnchor:false, placement:"right"\` 平铺。
5. 不要把参考图文件名或任何界面元素画进最终图片。

### 按标注修改（cowart-request:annotation_edit）

1. 提示词里给出「Annotation screenshot local path」——这是画布导出的含标注（箭头/文字）的截图。
2. 先用 \`modlens_read_image\` 读截图，理解标注意图。
3. 用 \`edit_image\` 以截图本身为 image、prompt 描述「去除所有标注痕迹，按标注意图生成干净新图」。
4. 用 \`cowart_insert_image\` 把结果放到原图旁边（提示词里给了原图 shape id；\`annotationScreenshot\` 传截图路径），不删除、不移动原图与标注。

### AI HTML 框（cowart-request:ai_html）

如提示词附带了参考图本地路径，先用 \`modlens_read_image\` 读取参考图再设计（DSH 下参考图只以路径文本到达）。生成完整可运行的单文件 HTML（CSS/JS 尽量内联），调用 \`cowart_insert_html_draft\`，\`draftShapeId\` = 框 id，\`htmlContent\` = 完整 HTML，fileName 用简短描述。多份 HTML 时逐个插入并横向平铺。

### AI Slides（cowart-request:ai_slides）

如提示词附带了参考图本地路径，先用 \`modlens_read_image\` 读取参考图再设计（DSH 下参考图只以路径文本到达）。按提示词页数逐页生成独立 16:9（1024x576）单文件 HTML，每页一次 \`cowart_insert_html_draft\`，\`draftShapeId\` = Slides 框 id，\`replaceDraftHolder:false, updateExistingDraft:false, matchAnchor:false, displayWidth:1024, displayHeight:576\`，shapeMeta 里带 \`cowartAiSlidesParentShapeId\`、页号等。

## 约束

- 画布数据只通过 cowart_ 工具读写，不要手工编辑 canvas/ 下的 JSON。
- 不要生成 bitmap 去满足 AI HTML/Slides 请求。
- 插入结果由画布 SSE 自动刷新显示，无需额外确认。
- projectDir 缺省即当前会话工作区；多项目时以提示词/用户说明为准。`,
}

export function registerCowartSkill(ctx) {
  ctx.skills.register(COWART_SKILL)
}
