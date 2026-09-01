# AI Image Generation Guide for Visual Assets

Use this guide when generating decorative/atmospheric visual assets via `imagen-skill` (GPT Image-2).
For information-critical visuals (logos, data charts, photos, architecture diagrams), use `crop_region.py` instead.

## When to Use AI Generation

| Element Type | Examples | Generate or Crop? |
|---|---|---|
| Decorative textures | Grid patterns, dot matrices, circuit traces, wave patterns | **Generate** |
| Abstract backgrounds | Gradient glows, tech lines, geometric decorations, light effects | **Generate** |
| Simple decorative icons | Folder, antenna, signal, chip, gear, book, person, building icons | **Generate** |
| Atmospheric scenes | Abstract sci-fi background, stylized landscape, nebula effects | **Generate** |
| Decorative flourishes | Corner ornaments, divider decorations, border patterns | **Generate** |
| Brand logos | Company logo, product mark, university seal | **CROP** |
| Data charts | Bar charts, line graphs, pie charts with specific data | **CROP** |
| Photos | Product photos, team photos, location photos | **CROP** |
| Architecture diagrams | System topology, network diagram, specific flowchart | **CROP** |
| Screenshots | UI mockups, software interfaces | **CROP** |

## Prompt Template

For each AI-generated visual asset, construct a prompt with these sections:

```
[SUBJECT]: Precise description of the visual element
[STYLE]: Artistic style, mood, reference aesthetics
[COLORS]: Exact hex values from extract_colors.py output
[FORMAT]: Aspect ratio, transparency requirement
[RESTRICTION]: "No text, no labels, no numbers, no letters anywhere in the image."
```

### Example Prompts

**Decorative texture (grid pattern):**
```
A subtle dark grid pattern on transparent background. Fine cyan (#31D7FF) lines 
forming a coordinate grid with major grid lines every 5 units and minor grid lines 
every 1 unit, on a dark navy (#0A1628) background. Technical blueprint style. 
Aspect ratio approximately 16:9. No text, no labels, no numbers, no letters.
```

**Abstract tech background:**
```
Abstract technology background with flowing curved lines in cyan (#31D7FF) and 
gold (#C59A4A), subtle glow effects, dark navy (#061426) background. Clean, 
modern, sci-fi aesthetic. Transparent background where there is no glow. 
Aspect ratio approximately 16:9. No text, no labels, no numbers, no letters.
```

**Decorative icon (folder icon):**
```
A flat icon of a file folder in gold (#C59A4A) with subtle shadow, clean geometric 
style, matching a presentation slide aesthetic. Transparent background. 
Square aspect ratio 1:1. No text, no labels, no numbers, no letters.
```

**Decorative wave pattern:**
```
A smooth wave pattern decoration at the bottom of a slide, cyan (#31D7FF) with 
40% opacity, flowing sine-wave style, modern and clean. Transparent background 
above the wave. Aspect ratio approximately 16:3 (very wide and short). 
No text, no labels, no numbers, no letters.
```

**Small decorative illustration:**
```
A minimalist stylized illustration of a radar antenna dish on a dark background, 
cyan (#31D7FF) and gold (#C59A4A) colors, technical blueprint style with fine 
line work. Transparent background. Aspect ratio approximately 1:1. 
No text, no labels, no numbers, no letters.
```

## Critical Rules

### 1. NEVER include text in generated images
Every prompt MUST end with: "No text, no labels, no numbers, no letters anywhere in the image."
Text belongs in PPT text boxes (Layer C), never baked into images.

### 2. Match the source color palette
Use exact hex values from `extract_colors.py` output. Do NOT use generic color names.
Reference the palette in every prompt.

### 3. Match the source style
Observe the original image's visual language:
- **Technical/Scientific**: clean lines, blueprint aesthetic, grid patterns
- **Corporate/Professional**: flat design, subtle shadows, geometric
- **Creative/Artistic**: flowing curves, gradients, organic shapes
- **Academic**: formal, restrained, traditional layout elements

Describe the style explicitly in the prompt.

### 4. Match the aspect ratio
Calculate from the element's bounding box: `width_px / height_px`.
Common ratios: 16:9 (widescreen banner), 1:1 (icon), 16:3 (thin strip).

### 5. One element per image
Do not combine unrelated visual elements into one generated image.
Each distinct visual region gets its own asset.

### 6. Specify transparency
- Elements that overlay other content → "Transparent background"
- Full-width background scenes → "Solid [color] background"

## Asset Naming Convention

```
s{slide_number}_{semantic_role}.png
```

Examples:
- `s01_grid_background.png` — slide 1's coordinate grid background
- `s01_bottom_wave.png` — slide 1's decorative wave at bottom
- `s03_folder_icon.png` — slide 3's folder icon
- `s05_tech_lines_bg.png` — slide 5's abstract tech line background

## Quality Checklist

Before accepting a generated image, verify:
- [ ] No text, numbers, or letters visible in the image
- [ ] Colors match the extracted palette (within reasonable tolerance)
- [ ] Aspect ratio matches the target bounding box
- [ ] Visual style is consistent with the source image
- [ ] Image is clean (no artifacts, no edge glitches)
- [ ] Transparency works correctly (if applicable)
