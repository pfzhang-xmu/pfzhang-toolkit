# DSH Image Tools

面向 DeepSeek Harness 的 Host 端 Cordis 插件：为 DSH agent 注册 OpenAI Images 兼容的 `generate_image` / `edit_image` 工具，输出保存到 `<会话工作区>/.dsh-images/`。

本插件可对接任意 OpenAI Images 兼容服务。**源码中的接口地址、密钥、模型均为占位符，需自行配置。**

## 配置

在 host 插件入口（如 profile 的 `cordis.patch.yml`）加入 `dsh-image-tools/index.js`：

```yaml
- insert:
    - id: image-tools
      name: /absolute/path/to/dsh-image-tools/index.js
      config:
        baseURL: https://your-openai-compatible-host/v1   # 必填 —— 替换为你的服务地址
        apiKeyEnv: IMAGE_API_KEY                           # 存放 API Key 的环境变量名
        model: gpt-image-1                                 # 你服务的图片模型 id
        timeoutMs: 180000
        maxRetries: 1
```

API Key 存入 DSH 凭据库（名称即 `apiKeyEnv`，如 `IMAGE_API_KEY`），或在启动 DSH 前导出该环境变量。**切勿把 Key 写进 Cordis 组合配置。**

> 源码默认值为占位符：`baseURL: https://api.example.com/v1`、`apiKeyEnv: IMAGE_API_KEY`、`model: gpt-image-1`。配置真实地址与密钥前，请求会以明确的错误提示失败。

## 工具

- `generate_image`：向 `<baseURL>/images/generations` 发送 JSON。
- `edit_image`：向 `<baseURL>/images/edits` 发送 multipart 表单，含 `image` 与可选 `mask` 文件。

两个工具只接受服务商支持的参数。本地编辑输入必须位于当前会话工作区内（或 `modlens-dsh-paste-*` 临时目录），也支持 HTTPS 图片输入。响应可包含 `b64_json` 或 HTTPS `url` 字段。

每个输出旁会生成一份 JSON 元数据：操作、prompt、模型、参数、编辑来源路径、MIME、字节数与 SHA-256。凭据按请求解析，绝不写入输出元数据。

## 安全边界

- API Key 每次请求时从 DSH 凭据库或环境变量读取，绝不写入文件、元数据或组合配置。
- 本地文件输入经路径校验，限制在当前会话工作区（或 Modlens 粘贴目录）内。
- 输出有大小限制（默认输入 25 MB / 输出 50 MB），并按 MIME 嗅探校验为 PNG/JPEG/WebP/GIF。

## 验证

```bash
node --test dsh-image-tools/index.test.js
```

修改 Host profile 后需重启 DSH Web 进程并刷新 `http://127.0.0.1:3080`。本插件仅 Host 端，无需重建客户端 bundle。

## 用法

两个工具都是普通 agent 工具：模型在对话中直接调用，结果以本地路径返回（宿主支持时同时内联展示图片）。

**生成图片**

```text
生成一张 16:9 的赛博朋克城市夜景插画，霓虹蓝紫配色，无文字。
```

agent 会调用 `generate_image({ prompt, size: "1536x1024" })`，并把保存路径（`.dsh-images/` 下，如 `generate-20260101T000000Z-xxxx-1.png`）返回，可直接 `modlens_read_image` 查看或继续编辑。

**编辑/精修图片**

```text
把 /Users/me/project/.dsh-images/xxx.png 里的红色箭头全部去掉，保持其余内容不变
```

agent 会调用 `edit_image({ image: <路径>, prompt: "…" })`，新结果保存到原图旁；`mask` 可选，用于限定区域修改。

**与画布（dsh-cowart）配合的典型流程**

1. `generate_image` 按目标宽高比出图。
2. `cowart_insert_image`（来自 `dsh-cowart` 插件）把图片复制进画布 page assets 并替换选中的 AI 图片框。
3. 标注后 `edit_image` 依据标注截图重绘，`cowart_insert_image` 把结果放到原图旁。

完整「生成 → 标注 → 按标注精修」示例见仓库根 README。
