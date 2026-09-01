# Imagen Skill — GPT Image-2 图像生成与编辑

让 AI Agent 在对话中直接生成和编辑图片，无需切换到其他工具。封装 gpt-image-2 模型，用 Python 3 调用 OpenAI 兼容 Images API。

> 跨 Claude Code · Codex CLI · Cursor · Gemini CLI · GitHub Copilot 等任意支持 SKILL.md 格式的 Agent 平台。

## 两大功能

| 功能 | 说明 | 端点 / 请求方式 | 用法示例 |
|------|------|-----------------|----------|
| **模式 A：纯文生图** | 给文字描述，AI 直接生成图片 | `POST /v1/images/generations` (`application/json`) | "帮我画一只猫咪在太空站里漂浮" |
| **模式 B：参考图编辑** | 给参考图 + 修改指令，AI 在原图基础上精细修改 | `POST /v1/images/edits` (`multipart/form-data`) | "把这张图的背景换成海滩日落" |

---

## 为什么模式 B（参考图编辑）更稳定？

相较于传统的文生图模型改图，本 Skill 针对改图模式做了以下深度改进：

1. **专用的 `/v1/images/edits` 接口**：直接将参考图作为 `multipart/form-data` 的 `image` 字段发送，避免常规接口静默退化为纯文本生图的问题。
2. **“三段式”编辑 Prompt 组装**：Agent 会先分析参考图的主体特征与风格，组装包含 `[原图描述] + [具体修改指令] + [需保持不变元素]` 的结构化 Prompt，确保模型精准把握原图细节。
3. **原图比例自动贴合 (Aspect Ratio Preservation)**：内建尺寸智能检测，自动匹配 `1792x1024` (16:9)、`1024x1792` (9:16) 或 `1024x1024` (1:1)，防止图片裁剪或拉伸变形。

---

## 安装

### 方式 A：让 Agent 自动装

在你的 Agent 里说：

```
帮我安装 imagen-skill
```

### 方式 B：手动安装

```bash
mkdir -p ~/.claude/skills/imagen-skill
```

将本仓库的 `SKILL.md` 和 `README.md` 复制到 `~/.claude/skills/imagen-skill/` 目录下。

---

## 触发示例

### 模式 A：文生图

- 帮我画一只猫咪在太空站里漂浮
- 生成一张未来城市的 16:9 高清图片
- 画一张透明背景的卡通人物
- generate an image of a serene Japanese garden at sunset

### 模式 B：图片编辑

- （贴一张照片）把背景换成海滩日落
- （贴一张图）把这只猫的毛色改成橘色，保持姿势不变
- 修改 `/path/to/photo.jpg`，加一个复古滤镜效果
- edit this image to make it look like a watercolor painting

---

## 支持的用户参数

| 参数 | 可选项 | 默认值 |
|------|--------|--------|
| 数量 (n) | 1-10 张 | 1 |
| 尺寸 (size) | auto (自动贴合原图比例), 1024x1024, 1792x1024, 1024x1792, 2048x2048, 自定义 WxH | 模式A默认 1024x1024，模式B为 auto |
| 质量 (quality) | auto, high, low | auto |
| 格式 (output_format) | png, jpeg, webp | png |
| 背景 (background) | auto, transparent, opaque | auto |
| 内容审核 (moderation) | auto, low | auto |

---

## 输出与保存

生成的图片默认保存在当前工作目录，文件名由 prompt 前 40 个字符 + 时间戳组成：

```
A_cat_floating_in_a_space_station_20260725_153000.png
```

Agent 会在对话中直接展示生成的图片，并汇报文件绝对路径、使用的英文 Prompt 及 Token 用量。

---

## 依赖

- **Python 3**（系统自带）
- 使用标准库及 `Pillow` (PIL) 进行图像尺寸解析与转换。

---

## License

MIT
