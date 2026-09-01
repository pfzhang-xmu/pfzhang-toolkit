---
name: imagen-skill
description: |
  GPT Image-2 图像生成与编辑 Skill。使用 gpt-image-2 模型进行文生图和图片编辑。

  两大使用场景：
  模式 A（纯文生图）：用户只给描述词，无参考图，直接生成图片。
  模式 B（参考图编辑）：用户给参考图 + 描述/修改意见，基于参考图生成或修改图片。

  中文触发词："生成图片"、"画一张"、"画个"、"生成一张"、"文生图"、"文字生成图片"、"帮我画"、"创建图片"、"生成图像"、"帮我生成"、"做一张图"、"画图"、"生成插图"、"AI画图"、"AI绘画"、"AI生成图片"、"修改这张图"、"把这张图改成"、"基于这张图生成"、"编辑这张图"、"改下图"、"修图"、"P一下"、"换个背景"、"把图片里的"、"图片编辑"、包含"生成"+"图/画/图像/图片/插画/海报"等组合。

  English triggers: "generate image", "create an image", "text to image", "make an image", "draw", "image generation", "AI image", "generate a picture", "create a picture", "generate art", "visualize", "illustrate", "generate a diagram", "edit this image", "modify this image", "based on this image", "change the background", "remove the background", "add to the image", "retouch", "image edit".

  只要用户在话里表达了"用文字生成图片"或"用描述修改/编辑图片"的意图就应该触发。不要 undertrigger——用户想生成/编辑图片而你不调用本 Skill 就是让用户需求落空。
---

# Imagen Skill — GPT Image-2 图像生成与编辑

封装 gpt-image-2 模型，支持文生图（text-to-image）和参考图编辑（image editing）。通过 Python 3 调用 OpenAI 兼容的 Images API。

**API 端点配置：**
- Base URL: `https://api.zetatechs.com`
- Auth: `Bearer sk-nToLs7Dqydl2YYHAbjjaYJZahgkN8C9dQ7YlfLL6TdmFJfPS`
- **模式 A（纯文生图）：** `POST /v1/images/generations`（`application/json`）
- **模式 B（参考图编辑）：** `POST /v1/images/edits`（`multipart/form-data`，专用于参考图修改）

---

## 核心原则

### 原则一：所有 Prompt 必须翻译成英文，模式 B 采用“三段式”编辑 Prompt

gpt-image-2 对英文指令遵循能力明显更强。**无论用户用什么语言，都必须先将 prompt 翻译成英文。**

**模式 B（参考图编辑）Prompt 构造规范（核心重点）：**
生图模型在编辑图片时需要明确的画面上下文，不能仅给抽象短句（如 `"Same subject but orange"`）。 Agent 必须构建包含以下 3 部分结构的英文 Prompt：

1. **原图视觉特征描述 (Context & Style)**：简要描述原图主体、构图、颜色和艺术风格（如 *"A studio photo of a white cat sitting on a wooden stool, soft lighting"*）。
2. **具体修改指令 (Specific Changes)**：点明要更改的具体细节/背景/颜色/元素（如 *"Change the cat's fur color to vibrant orange tabby pattern, add small freckles"*）。
3. **保持不变要素 (Preservation)**：明确指出需与原图保持一致的元素（如 *"Keep the cat's eyes, posture, wooden stool, and studio lighting identical to the reference image"*）。

**不同意图类型的翻译策略：**

| 意图类型 | 判断依据 | 翻译与构建策略 |
|----------|----------|----------------|
| **艺术创作** | 用户要生成画作、插画、壁纸、概念图 | 适度丰富细节（如补充风格词、光影、构图） |
| **技术/精确图** | 用户要生成架构图、流程图、UI原型、示意图 | **严格忠实翻译**，不添加任何艺术修饰词 |
| **精确编辑 (模式 B)** | 用户基于参考图修改细微细节/背景/色彩 | **必须使用“三段式” Prompt 结构**，清晰说明[原图描述]+[修改点]+[保留项] |

翻译完成后向用户展示：
> 已将 prompt 翻译/构建为英文：「English prompt here」

### 原则二：先判断模式再执行

- 用户消息中有图片（贴图/文件路径/URL/对话上文图片）→ **模式 B（参考图编辑）**
- 用户消息中只有文字描述 → **模式 A（纯文生图）**

### 原则三：模式 B 自动匹配宽高比 (Aspect Ratio Preservation)

模式 B 改图时，如果强制指定 `1024x1024`，非正方形原图会被拉伸或严重截断。
Python 脚本会自动读取原图尺寸，智能选择最贴近的 API 支持尺寸：
- 宽高比 ≥ 1.4 (横屏/16:9 视界) → `1792x1024`
- 宽高比 ≤ 0.7 (竖屏/9:16 壁纸) → `1024x1792`
- 宽高比接近 1.0 (正方形) → `1024x1024`
- 4:3 比例 (1.15 ~ 1.4) → `1360x1024`
- 3:4 比例 (0.7 ~ 0.85) → `1024x1360`
- 用户显式指定了尺寸（如 "16:9"、"2048x2048"）时，优先尊重用户设置。

---

## 模式判断

读取用户消息，检查是否包含图片：

- 用户在对话中**贴了图片**（message 中包含 image 附件）→ **模式 B**
- 用户消息中包含了**文件路径**（如 `/path/to/image.png`）并说"修改/编辑/改"等 → **模式 B**
- 用户消息中包含了**图片 URL** 并说"基于这张图/修改这张图"等 → **模式 B**
- 用户只给了文字描述，没有任何图片 → **模式 A**
- 用户说"换背景/去掉背景/修图/P一下"且对话上文有图片 → **模式 B**（使用上文最新的图片）

---

## 参数检测

从用户的话中提取以下参数覆盖默认值：

| 用户说 | 参数设置 |
|--------|----------|
| 默认（无特别说明） | n=1, size=auto (模式A默认1024x1024，模式B自动检测原图比例), quality=auto, output_format=png, background=auto |
| "高清" / "高质量" / "HQ" | quality=high |
| "低质量" / "draft" | quality=low |
| "N张" / "N variations" | n=N |
| "透明背景" / "去背" | background=transparent, output_format=png |
| "白底" / "白色背景" | background=opaque |
| "JPEG" / "JPG" | output_format=jpeg |
| "WEBP" | output_format=webp |
| "PNG" | output_format=png |
| "16:9" / "宽屏" / "横屏" | size=1792x1024 |
| "9:16" / "竖屏" / "壁纸" | size=1024x1792 |
| "4:3" | size=1360x1024 |
| "1:1" / "正方形" | size=1024x1024 |
| "4K" / "超清" | size=2048x2048 |
| WxH 形式（如 "800x600"） | 直接使用，宽高向上取整到 16 的倍数 |

---

## 工作流

### 模式 A：纯文生图

#### Step A1 — 翻译 Prompt
将用户的描述词翻译为英文。如果是简短描述，可适度丰富表达。展示给用户：
> 已将 prompt 翻译为英文：「English prompt here」

#### Step A2 — 执行 Python 脚本

使用 `POST /v1/images/generations` (`application/json`) 执行：

```python
import json, urllib.request, sys, base64, os, re
from datetime import datetime

# === AGENT: 替换以下变量 ===
PROMPT = "翻译后的英文 prompt"
N = 1
SIZE = "1024x1024"
QUALITY = "auto"
BACKGROUND = "auto"
OUTPUT_FORMAT = "png"
MODERATION = "auto"
OUTPUT_DIR = "."
# ==========================

if len(PROMPT) > 32000:
    print(f"ERROR: Prompt is {len(PROMPT)} chars (max 32000)", file=sys.stderr)
    sys.exit(1)

body = json.dumps({
    "model": "gpt-image-2",
    "prompt": PROMPT,
    "n": N,
    "size": SIZE,
    "quality": QUALITY,
    "background": BACKGROUND,
    "output_format": OUTPUT_FORMAT,
    "moderation": MODERATION
}).encode("utf-8")

req = urllib.request.Request(
    "https://api.zetatechs.com/v1/images/generations",
    data=body,
    headers={
        "Authorization": "Bearer sk-nToLs7Dqydl2YYHAbjjaYJZahgkN8C9dQ7YlfLL6TdmFJfPS",
        "Content-Type": "application/json"
    }
)

print("正在生成图片，请耐心等待（可能需要 10-60 秒）...", file=sys.stderr)

try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode())
except urllib.error.HTTPError as e:
    error_body = e.read().decode()
    print(f"HTTP_ERROR:{e.code}:{error_body}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"NETWORK_ERROR:{str(e)}", file=sys.stderr)
    sys.exit(2)

if "data" not in data or len(data["data"]) == 0:
    print("ERROR: No images in API response.", file=sys.stderr)
    sys.exit(3)

safe_prompt = re.sub(r'[^a-zA-Z0-9_-]', '_', PROMPT[:40]).strip('_') or "image"
safe_prompt = re.sub(r'_+', '_', safe_prompt)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

saved_files = []
for i, img in enumerate(data["data"]):
    fname = f"{safe_prompt}_{timestamp}_{i+1:02d}.{OUTPUT_FORMAT}" if N > 1 else f"{safe_prompt}_{timestamp}.{OUTPUT_FORMAT}"
    fpath = os.path.join(OUTPUT_DIR, fname)
    raw = img.get("b64_json") or img.get("url")
    if not raw: continue
    if img.get("b64_json"):
        with open(fpath, "wb") as f:
            f.write(base64.b64decode(raw))
    else:
        urllib.request.urlretrieve(raw, fpath)
    saved_files.append(os.path.abspath(fpath))

result = {"files": saved_files}
if "usage" in data: result["usage"] = data["usage"]
print(json.dumps(result, indent=2, ensure_ascii=False))
```

#### Step A3 — 展示结果
展示文件路径、Prompt、参数和 Token 用量，并用 Read 工具显示内联图片。

---

### 模式 B：参考图编辑

#### Step B1 — 获取参考图
寻找用户提供的参考图路径（上下文贴图/本地绝对路径/图片 URL 下载到临时文件）。

#### Step B2 — 构建“三段式”英文 Prompt
Agent 识别/分析参考图画风与主体，构建格式为：
`[原图主体特征与画风描述] + [具体修改内容] + [需保持不变的要素]` 的英文 Prompt。

展示给用户：
> 已将编辑指令构建为英文：「English edit prompt」

#### Step B3 — 执行 Python 脚本 (POST /v1/images/edits multipart/form-data)

替换变量并直接用 `python3` 执行以下脚本：

```python
import json, urllib.request, sys, os, re, uuid, base64
from datetime import datetime
from PIL import Image

# === AGENT: 替换以下变量 ===
PROMPT = "三段式结构的英文编辑 prompt"
IMAGE_PATH = "/path/to/reference_image.png"  # 参考图路径
N = 1
SIZE = "auto"  # 'auto' 或用户指定的尺寸（如 "1792x1024"）
QUALITY = "auto"
BACKGROUND = "auto"
OUTPUT_FORMAT = "png"
MODERATION = "auto"
OUTPUT_DIR = "."
# ==========================

if not os.path.isabs(IMAGE_PATH):
    IMAGE_PATH = os.path.abspath(IMAGE_PATH)

if not os.path.exists(IMAGE_PATH):
    print(f"ERROR: Image file not found: {IMAGE_PATH}", file=sys.stderr)
    sys.exit(1)

# 1. 自动读取图片宽高与格式，智能计算 size
try:
    with Image.open(IMAGE_PATH) as img:
        orig_w, orig_h = img.size
        
        # 如果为 auto，按宽高比自动判断最合适的 API 尺寸
        if SIZE == "auto":
            ratio = orig_w / orig_h
            if ratio >= 1.4:
                SIZE = "1792x1024"
            elif ratio <= 0.7:
                SIZE = "1024x1792"
            elif 1.15 <= ratio < 1.4:
                SIZE = "1360x1024"
            elif 0.7 < ratio <= 0.85:
                SIZE = "1024x1360"
            else:
                SIZE = "1024x1024"
        
        # 确保图片格式转为标准 RGBA/RGB PNG 格式发送给 API
        prep_path = os.path.join("/tmp", f"input_prep_{uuid.uuid4().hex[:8]}.png")
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.mode else "RGB")
        img.save(prep_path, format="PNG")
        upload_path = prep_path
except Exception as e:
    print(f"ERROR: Failed to process reference image: {e}", file=sys.stderr)
    sys.exit(1)

with open(upload_path, "rb") as f:
    image_bytes = f.read()

if os.path.exists(prep_path):
    os.remove(prep_path)

# 2. 构建 multipart/form-data 请求 (POST /v1/images/edits)
boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
body_parts = []

def add_field(name, value):
    body_parts.append(f'--{boundary}'.encode('utf-8'))
    body_parts.append(f'Content-Disposition: form-data; name="{name}"'.encode('utf-8'))
    body_parts.append(b'')
    body_parts.append(str(value).encode('utf-8'))

def add_file(name, filename, data, content_type="image/png"):
    body_parts.append(f'--{boundary}'.encode('utf-8'))
    body_parts.append(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode('utf-8'))
    body_parts.append(f'Content-Type: {content_type}'.encode('utf-8'))
    body_parts.append(b'')
    body_parts.append(data)

# 添加参数
add_file("image", os.path.basename(IMAGE_PATH), image_bytes, "image/png")
add_field("prompt", PROMPT)
add_field("model", "gpt-image-2")
add_field("n", N)
add_field("size", SIZE)
if QUALITY != "auto": add_field("quality", QUALITY)
if BACKGROUND != "auto": add_field("background", BACKGROUND)
if OUTPUT_FORMAT != "png": add_field("output_format", OUTPUT_FORMAT)
if MODERATION != "auto": add_field("moderation", MODERATION)

body_parts.append(f'--{boundary}--'.encode('utf-8'))
body = b'\r\n'.join(body_parts)

req = urllib.request.Request(
    "https://api.zetatechs.com/v1/images/edits",
    data=body,
    headers={
        "Authorization": "Bearer sk-nToLs7Dqydl2YYHAbjjaYJZahgkN8C9dQ7YlfLL6TdmFJfPS",
        "Content-Type": f"multipart/form-data; boundary={boundary}"
    }
)

print(f"正在基于参考图编辑图片 (匹配尺寸: {SIZE})...", file=sys.stderr)

try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode())
except urllib.error.HTTPError as e:
    error_body = e.read().decode()
    print(f"HTTP_ERROR:{e.code}:{error_body}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"NETWORK_ERROR:{str(e)}", file=sys.stderr)
    sys.exit(2)

if "data" not in data or len(data["data"]) == 0:
    print("ERROR: No images in API response.", file=sys.stderr)
    sys.exit(3)

safe_prompt = re.sub(r'[^a-zA-Z0-9_-]', '_', PROMPT[:40]).strip('_') or "edited_image"
safe_prompt = re.sub(r'_+', '_', safe_prompt)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

saved_files = []
for i, img in enumerate(data["data"]):
    fname = f"{safe_prompt}_{timestamp}_{i+1:02d}.{OUTPUT_FORMAT}" if N > 1 else f"{safe_prompt}_{timestamp}.{OUTPUT_FORMAT}"
    fpath = os.path.join(OUTPUT_DIR, fname)
    raw = img.get("b64_json") or img.get("url")
    if not raw: continue
    if img.get("b64_json"):
        with open(fpath, "wb") as f:
            f.write(base64.b64decode(raw))
    else:
        urllib.request.urlretrieve(raw, fpath)
    saved_files.append(os.path.abspath(fpath))

result = {"files": saved_files}
if "usage" in data: result["usage"] = data["usage"]
print(json.dumps(result, indent=2, ensure_ascii=False))
```

#### Step B4 — 展示结果
展示参考图路径、输出图片路径、三段式 Prompt、实际尺寸与 Token 用量，并用 Read 工具显示内联图片。

---

## 输出格式

生成/编辑图片后展示：

```markdown
✅ 已生成/编辑图片：

**参考图：** `/path/to/reference.png`（模式 B 展示）
**文件路径：** `/absolute/path/to/generated_image_20260725_153000.png`
**英文 Prompt：** English prompt sent to API
**生成参数：** 1792x1024 (自动贴合原图比例), quality=auto, format=png
**Token 用量：** 500 total (50 input + 450 output)
```

---

## 错误处理速查

| 现象 | Python 输出 | 处理方式 |
|------|-----------|----------|
| API Key 无效 | `HTTP_ERROR:401:...` | 提示鉴权失败，检查 API Key |
| 频率限制 | `HTTP_ERROR:429:...` | 提示请求过于频繁，请等待 30 秒 |
| 服务器错误 | `HTTP_ERROR:5xx:...` | 提示服务不可用，稍后重试 |
| 网络超时 | `NETWORK_ERROR:...` | 提示网络超时或时间过长，重试或精简 prompt |
| 参考图不存在 | `ERROR: Image file not found` | 提示检查参考图文件路径 |
| 图片处理失败 | `ERROR: Failed to process...` | 提示原图损坏或格式不支持 |

---

## 不要做

- **模式 B 不要调用 `/v1/images/generations` 端点**（防止后端静默退化为纯文生图）
- **不要把原图强行改成 1:1 变形尺寸**（使用脚本自动计算比例）
- **不要跳过原图特征描述**（在模式 B 编写 prompt 时必须显式描述原图要素）
- **不要把 API Key 或 Base64 字符串输出给用户**
- **不要并发发送多个 API 请求**
