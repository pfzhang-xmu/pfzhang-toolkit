---
name: image2ppt
description: "Convert images (PNG, JPG, screenshots, photos of slides) into editable PPTX slides with near 1:1 visual fidelity. Use whenever the user provides an image and asks to turn it into a PowerPoint slide, convert it to PPT, make it editable in PowerPoint, replicate a slide from a screenshot, or extract an editable version from a slide image. Trigger on any request involving an image file + PowerPoint/PPT/PPTX output, even if the user doesn't explicitly say 'convert.' Also trigger when the user says they want to edit an image-based slide, extract text from a slide image into PowerPoint, recreate a slide design from an image, or 'make this look like that in PPT.' If the user mentions both an image and PPT/PPTX/幻灯片 in the same request, this skill should fire. 中文触发词：图片转PPT、截图转PPT、图片转幻灯片、截图转可编辑PPT、把这张图做成PPT、图片生成PPT、还原幻灯片。"
---

# Image2PPT Skill

Convert a raster image into an editable single-slide PPTX with maximum visual fidelity.
Read [references/strategy.md](references/strategy.md) for the full restoration philosophy.

## Quick Reference

| Phase | What Happens | Key Tools |
|-------|-------------|-----------|
| 1. ANALYZE | Understand image structure, extract colors | `vision_analyze`, `extract_colors.py`, `analyze_structure.py` |
| 2. DECOMPOSE | Classify each element: rebuild vs keep-as-image (crop vs AI-gen) | Vision model + [classification_guide.md](references/classification_guide.md) |
| 3. PRESERVE | Crop info-critical elements + AI-generate decorative assets | `crop_region.py`, `imagen-skill` + [image_gen_guide.md](references/image_gen_guide.md) |
| 4. CONSTRUCT | Build the PPTX from a JSON spec | `build_slide.py` |
| 5. VERIFY | Visual comparison, structural validation, score fidelity | `render_slide.sh`, `compare_renders.py`, `vision_analyze` |
| 6. REFINE | Fix gaps, iterate (max 3 attempts) | Re-run phases 4-5 |

## Non-Negotiable Rules

These rules are hard constraints. Violating any of them produces broken output.

01. **NEVER** use a full-slide screenshot as the background — that gives zero editability
02. **NEVER** bake readable text into generated images — text belongs in PPT text boxes
03. **NEVER** use crude PPT shapes to approximate complex illustrations — use AI generation or cropping instead
04. **NEVER** merge unrelated elements (text + visual + shapes) into one image
05. **NEVER** skip the analysis phase — inspect the source image thoroughly before writing any code
06. **NEVER** use a single generic visual motif across multiple slides when the source slides have distinct visuals
07. **NEVER** AI-generate information-critical elements (logos, data charts, photos, architecture diagrams) — crop them from source
08. **ALWAYS** end AI generation prompts with "No text, no labels, no numbers, no letters"
09. **ALWAYS** match the source image's color palette using `extract_colors.py` output, not vision model approximations
10. **ALWAYS** place text as PPT-native text boxes, even if it overlaps a visual asset

## Phase 1: ANALYZE — Understand the Image

Goal: build a complete, structured understanding of every visual element in the source image.

### 1.1 Extract Precise Colors

Run color extraction first — the vision model cannot give exact hex values:

```bash
python3 ~/.claude/skills/image2ppt/scripts/extract_colors.py \
  --image <input_image> --max-colors 16 --format json
```

Save the JSON output. Use these hex values for all element colors — they are more accurate than vision model approximations.

### 1.2 Detect Layout Structure

Run structure analysis for approximate region hints:

```bash
python3 ~/.claude/skills/image2ppt/scripts/analyze_structure.py \
  --image <input_image>
```

The output provides approximate bounding boxes for text regions and potential grid structures. These hints help you write a more targeted vision prompt.

### 1.3 Full Visual Analysis

Call the `vision_analyze` MCP tool with this structured prompt template:

```
Analyze this image as a PowerPoint slide to be recreated with native PPT elements.
Image dimensions: <W>x<H> pixels.
Color palette: <paste key colors from extract_colors.py>

For every visual element, report in this format:

ELEMENT <N>:
- Type: text_block | shape | icon | photo | table | chart | line_divider | tag | button | logo | background
- Bounding box: approximate as "x%,y% → x%,y%" relative to image dimensions
- Content: exact verbatim text (if text element), or visual description
- Style details:
  - Colors: hex values for fill, border, text (use the color palette above when possible)
  - Font: category (serif/sans/mono), weight (light/regular/bold/black), size estimate (XXL/XL/L/M/S/XS)
  - Decoration: border width/color, corner radius (sharp/slight/round/pill), shadow (none/soft/hard)
- Classification: "editable" (can be rebuilt with PPT primitives) or "image" (must keep as cropped image)
- Reasoning: 1 sentence on why this classification

Structural hints from edge detection:
<paste analyze_structure.py output>

Be thorough — capture every visible element. For text, include the exact wording character-for-character.
```

Save the complete vision model output as your element inventory.

### 1.4 Completeness Self-Check (Required)

After cataloging all elements for the slide, re-examine the source image ONE MORE TIME with fresh eyes. This second pass specifically targets **small or easy-to-miss elements** that the first pass may have overlooked.

Go through these questions systematically:

1. **Small icons**: Are there any small icons (badges, bullet-point icons, folder icons, chip icons, person icons, book icons, gear icons) that I missed or misclassified? If an icon has >3-4 visual features (gradients, shadows, internal detail), it should be "keep-as-image," not rebuilt as a PPT shape.

2. **Decorative details**: Are there small decorative elements I skipped entirely? Look in corners, edges, between panels, along dividers, and in header/footer areas. Things like: small wave patterns, dot grids, circuit-trace textures, line-art buildings, faint background motifs.

3. **In-card / In-panel visuals**: For each card or panel on the slide, does it contain a small illustration or diagram inside it? These are frequently missed because the analyst focuses on the card frame (Layer B) and card text (Layer C) but forgets the small visual inside.

4. **Chart / figure surroundings**: Around charts or data figures, are there small visual elements like marker icons, signal-path illustrations, or comparison diagrams that might have been grouped with the main chart but are actually separate elements?

5. **Count check**: Count the total "keep-as-image" elements. A visually rich slide typically has 3-8 distinct visual assets. If a complex-looking slide has only 1-2 image elements, something is likely missing — re-examine it.

If this second pass finds any missed elements, add them to the element inventory with a note: `"found_in": "completeness_check"`. Do NOT remove or modify existing elements — only add.

## Phase 2: DECOMPOSE — Classify Elements

For each element from Phase 1, apply the classification rules. Read [references/classification_guide.md](references/classification_guide.md) for the full decision tree and edge case handling.

Produce three lists:

**Rebuild as Editable:** Elements to reconstruct with PPT native primitives (text, shapes, lines, tables).
**Keep as Image — Crop from Source:** Information-critical visuals (logos, data charts, photos, architecture diagrams, UI screenshots). Use `crop_region.py`.
**Keep as Image — AI Generate:** Decorative / atmospheric visuals (textures, abstract backgrounds, simple decorative icons, wave patterns, light effects). Use `imagen-skill`.

When in doubt, prioritize: (1) text editability, (2) visual fidelity, (3) implementation reliability.
For the crop vs AI-generate decision: ask "If this element were replaced with a similar but not identical version, would someone familiar with the original notice?" If yes → crop. If no → AI-generate.

## Phase 3: PRESERVE — Extract & Generate Visual Assets

### 3.1 Crop Information-Critical Elements

For each "keep-as-image — crop" element, extract from the source:

```bash
python3 ~/.claude/skills/image2ppt/scripts/crop_region.py \
  --image <input_image> \
  --output /tmp/image2ppt_assets/<name>.png \
  --box <x>,<y>,<w>,<h>
```

Flags when needed:
- `--transparent` — remove solid background (for logos/icons on uniform backgrounds)
- `--circular` — apply circular mask (for profile photos, circular icons)
- `--padding 2` — add 2px padding to avoid edge clipping

Convert percentage coordinates to pixels: `px = pct / 100 * image_dimension`

### 3.2 AI-Generate Decorative / Atmospheric Elements

For each "keep-as-image — AI-generate" element, use `imagen-skill` (GPT Image-2).

Read [references/image_gen_guide.md](references/image_gen_guide.md) for full prompt templates and rules. Key requirements:

1. **Construct the prompt** with 5 sections: [SUBJECT] [STYLE] [COLORS] [FORMAT] [RESTRICTION]
2. **Use exact hex colors** from `extract_colors.py` output
3. **Always end with**: "No text, no labels, no numbers, no letters anywhere in the image."
4. **Match aspect ratio** to the element's bounding box
5. **Specify transparency**: "Transparent background" for overlay elements

Example prompt structure:
```
[SUBJECT]: <precise description of the visual element>
[STYLE]: <artistic style matching the source image aesthetic>
[COLORS]: <hex values from extract_colors.py>
[FORMAT]: <aspect ratio>, transparent background
[RESTRICTION]: No text, no labels, no numbers, no letters anywhere in the image.
```

Save generated assets to `/tmp/image2ppt_assets/` using semantic naming:
`s{slide_number}_{semantic_role}.png` (e.g., `s01_grid_background.png`, `s03_folder_icon.png`)

**Do NOT AI-generate**: logos, data charts, photos, architecture diagrams, UI screenshots, or any element whose exact content is load-bearing. Those must be cropped from source.

## Phase 4: CONSTRUCT — Build the PPTX

### 4.1 Determine Slide Dimensions

Map the input image's aspect ratio to standard slide sizes:
- Aspect ratio ~1.78 (16:9) → 13.333" × 7.5" (widescreen)
- Aspect ratio ~1.33 (4:3) → 10" × 7.5" (standard)
- Custom aspect ratio → compute: height = 7.5", width = image_w / image_h * 7.5"

### 4.2 Write Build Specification

Create a JSON specification file. Convert all coordinates from pixel to inches:

```
x_inches = x_px / image_width_px * slide_width_inches
y_inches = y_px / image_height_px * slide_height_inches
w_inches = w_px / image_width_px * slide_width_inches
h_inches = h_px / image_height_px * slide_height_inches
```

Spec schema — see `build_slide.py` docstring for full details. Element types:

- **text_box**: Text with per-run formatting. Use `runs` array for mixed formatting within one box.
- **image**: Embedded cropped PNG from Phase 3.
- **shape**: rectangle, rounded_rectangle, oval, chevron, arrows, etc. Supports fill, border, corner radius, shadow, rotation.
- **line**: Straight connector between two points. Supports color, width, dash style.
- **simple_table**: Grid table with cell-level formatting (text, font, fill, color, borders).

**Layer ordering:** Assign layer values from lowest (backgrounds = -1 or 0) to highest (foreground text = 10+). Elements are rendered in layer order.

**Font selection:** Use the vision model's font category report and map via [references/font_fallbacks.md](references/font_fallbacks.md). On macOS, prefer PingFang SC for Chinese and Helvetica Neue for English. Always set the East Asian font attribute on runs containing CJK characters.

**Color accuracy:** Use the hex values from `extract_colors.py` output, not the vision model's approximate color names.

### 4.3 Run Construction

```bash
python3 ~/.claude/skills/image2ppt/scripts/build_slide.py \
  --spec /tmp/image2ppt_spec.json \
  --output <output_path>.pptx
```

## Phase 5: VERIFY — Visual QA

### 5.0 Structural Validation (Fast Sanity Check)

Before rendering, run these quick structural checks on the built PPTX:

1. **Object count**: Each slide should have multiple text frames (≥2) and multiple shapes. If the slide has only 1-2 objects, something is wrong.
2. **No full-slide screenshot**: No single image should cover >85% of the slide area. If it does, the reconstruction failed — the slide is just a screenshot pasted as background.
3. **Text boxes exist**: Verify that text elements are PPT text boxes, not baked into images. Open the PPTX and check that clicking on text allows editing.
4. **Source image is not embedded as-is**: If the original source image file path appears in the PPTX, it was pasted wholesale instead of being decomposed.

If any of these checks fail, return to Phase 2 and re-classify before rebuilding.

### 5.1 Render PPTX to Image

```bash
bash ~/.claude/skills/image2ppt/scripts/render_slide.sh \
  <output_path>.pptx image2ppt_render 150
```

### 5.2 Generate Comparison

```bash
python3 ~/.claude/skills/image2ppt/scripts/compare_renders.py \
  --original <input_image> \
  --rendered <rendered_jpg> \
  --output /tmp/image2ppt_comparison.jpg \
  --mode overlay
```

### 5.3 Evaluate Fidelity

Call `vision_analyze` on the comparison image:

```
This is an overlay comparison (50% blend) of an original slide image and its PPTX recreation.
Analyze the differences. Score each category:

1. Position accuracy: Are elements in the right places? Any shifted or mis-sized?
2. Color accuracy: Are colors matching? Any hue/saturation/brightness differences?
3. Text accuracy: Is all text present? Correct font sizes? Any overflow or missing text?
4. Completeness: Any elements missing from the recreation?
5. Visual quality: Do shadows, borders, spacing, and details match?

Overall fidelity rating (pick one):
- EXCELLENT (>95% match): Ready to deliver
- GOOD (85-95%): Minor fixes needed
- NEEDS_WORK (<85%): Significant rework needed

List specific elements needing adjustment with concrete fix instructions.
```

## Phase 6: REFINE — Iterate

Based on the fidelity rating:

- **EXCELLENT**: Done. Present the PPTX file with an element classification summary (see below).
- **GOOD**: Fix listed issues by adjusting the JSON spec and re-running Phase 4-5. One iteration.
- **NEEDS_WORK**: Re-analyze problem elements. May need to re-crop images, change classification decisions, or adjust font/color choices. Re-run Phase 4-5.

**Hard stop: Maximum 3 construction attempts.** After 3 builds, present the best result with an annotation of remaining differences.

## Common Failure Modes & How to Prevent Them

| Failure Mode | Symptom | Root Cause | Prevention |
|---|---|---|---|
| **"新模板"而非还原** | 输出看起来是同主题的另一套PPT，布局与源图不同 | 跳过了详细分析，使用了通用布局 | Phase 1 强制逐元素精确测量位置和尺寸 |
| **遗漏小元素** | 小图标、装饰细节在输出中缺失 | 首次分析时忽略了小型/边缘元素 | Step 1.4 完整性自检专门捕捉遗漏 |
| **形状硬画** | 复杂插图被丑陋的PPT形状近似替代 | 将复杂视觉错误分类为"可重建" | 分类规则将复杂视觉导向 AI 生图或原图裁切 |
| **文字烤进图片** | 文字可见但不可编辑（在图片内部） | AI 生图 prompt 包含了文字描述 | 所有 AI 生图 prompt 必须以 "No text, no labels, no numbers, no letters" 结尾 |
| **通用素材泛滥** | 多页使用相同的背景图 | 为所有页面生成了相同或相似的视觉素材 | 每页生成匹配其源图的独特素材 |
| **整页截图贴背景** | PPTX 只有一张覆盖全页的图片 | 完全跳过了分解流程 | 结构验证检查没有图片 >85% 面积 |
| **AI生图跑偏** | 生成图与原图风格/内容不一致 | 对信息关键型元素用了AI生图，或 prompt 配色不匹配 | 信息关键型→裁切；prompt 使用 extract_colors.py 的精确 hex 值 |
| **裁切图分辨率低** | 嵌入图片模糊、有锯齿 | 原图分辨率不足，裁切区域太小 | 小尺寸装饰元素优先用 AI 生图；裁切时确保区域 ≥150px |

### Final Output Format

When the result is ready, present:

1. The PPTX file path
2. An element classification summary:
   - **可编辑文本/形状/图表**: (list of editable elements)
   - **原图裁切/提取的图片素材**: (list of image elements with source regions)
   - **因嵌入复杂视觉区域而保留为图片的文字**: (if any)
   - **为保留质感而牺牲可编辑性的地方**: (if any)
3. The comparison image for the user's visual reference

## Integration with the pptx Skill

This skill produces a single-slide PPTX. If the user needs to add it to an existing deck:
1. Image2ppt produces `new_slide.pptx`
2. Use the pptx skill's tools to merge into the target presentation (unpack both, copy slide XML, reassign IDs, repack)

For multi-slide conversion (user provides multiple images): process each image independently through phases 1-6, then combine the resulting slides into one PPTX using the pptx skill.

## Script Reference

| Script | Purpose |
|--------|---------|
| `scripts/extract_colors.py` | Extract precise hex color palette + detect gradients |
| `scripts/analyze_structure.py` | Edge detection, text region hints, grid detection |
| `scripts/crop_region.py` | Crop image regions with optional transparency/circular mask |
| `scripts/build_slide.py` | Core engine: JSON spec → single-slide PPTX |
| `scripts/render_slide.sh` | PPTX → PDF → JPEG via LibreOffice + Poppler |
| `scripts/compare_renders.py` | Side-by-side / overlay / diff comparison images |

## Document Reference

| Document | When to Read |
|----------|-------------|
| `references/strategy.md` | When starting — understand the restoration philosophy and three-layer decomposition |
| `references/classification_guide.md` | Phase 2 — classify elements as rebuild vs crop vs AI-generate |
| `references/image_gen_guide.md` | Phase 3 — prompt templates and rules for AI-generating decorative visual assets |
| `references/font_fallbacks.md` | Phase 4 — select fonts for text elements |
