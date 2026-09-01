# Element Classification Guide

Decision tree for classifying each visual element as "keep-as-image" or "rebuild-editable."
For elements classified as "keep-as-image," further classify as "crop-from-source" or "AI-generate."

## Primary Decision Tree

For each visual element identified in the analysis phase:

```
├── Is it purely decorative, photographic, or textural?
│   └── KEEP AS IMAGE → Secondary classification below
│
├── Is it a logo, brand mark, or complex icon with gradients/shadows?
│   └── KEEP AS IMAGE → Secondary classification: "信息关键型"
│
├── Is it a data visualization (chart, heatmap, treemap, radar)?
│   └── KEEP AS IMAGE → Secondary classification: "信息关键型"
│
├── Is it a complex flowchart, cycle diagram, or org chart?
│   └── KEEP AS IMAGE → Secondary classification: "信息关键型"
│
├── Is it a product photo, person portrait, or illustration?
│   └── KEEP AS IMAGE → Secondary classification: "信息关键型"
│
├── Is it a simple icon (single color, clear geometric silhouette)?
│   └── REBUILD (PPT shape or unicode symbol)
│
├── Is it a solid-color shape (rectangle, circle, tag, button, color block)?
│   └── REBUILD (PPT shape with fill + optional border + text overlay)
│
├── Is it a horizontal or vertical divider line?
│   └── REBUILD (PPT line shape)
│
├── Is it a table with visible grid lines?
│   └── REBUILD (PPT table, but cross-validate structure with vision model)
│
├── Is it a simple flowchart with ≤10 nodes?
│   └── REBUILD (PPT shapes + connectors)
│
├── Is it text in any form (heading, body, label, caption, annotation)?
│   └── REBUILD (PPT text box with matching formatting)
│
└── Is it a gradient background (2-4 color stops, linear/radial)?
    └── REBUILD (PPT gradient fill on slide background or shape)
```

## Secondary Classification: Crop vs AI-Generate

For elements classified as "keep-as-image" above, apply this second decision tree:

```
├── Does the element carry specific, non-negotiable information?
│   ├── Brand Logo → CROP FROM SOURCE (AI cannot reproduce the correct logo)
│   ├── Data chart with specific numbers → CROP FROM SOURCE (values must be exact)
│   ├── Product photo / person portrait → CROP FROM SOURCE (must be the real thing)
│   ├── Architecture diagram / specific flowchart → CROP FROM SOURCE (topology matters)
│   ├── Screenshot of UI / software interface → CROP FROM SOURCE (pixel-accurate)
│   ├── Map with specific locations → CROP FROM SOURCE (geography matters)
│   ├── Scientific figure with specific data → CROP FROM SOURCE (data integrity)
│   └── Specific illustration with precise meaning → CROP FROM SOURCE
│
│   Decision rule: If a viewer familiar with the original would notice the difference,
│   CROP FROM SOURCE.
│
└── Is the element decorative / atmospheric (style matters, not exact content)?
    ├── Decorative texture / pattern (grid, dots, circuit traces, waves)
    ├── Abstract background (gradient glow, tech lines, geometric decoration)
    ├── Simple decorative icon (folder, antenna, signal, chip, gear, book)
    ├── Atmospheric scene (abstract sci-fi, stylized landscape, light effects)
    ├── Decorative divider / flourish / corner ornament
    └── Background illustration without specific meaning
    │
    └── AI-GENERATE via imagen-skill (cleaner, higher resolution, transparent bg)
        Read [image_gen_guide.md](image_gen_guide.md) for prompt templates.

    Decision rule: If the element's role is to set mood / style, and a similar-but-not-
    identical version would serve the same purpose, AI-GENERATE.
```

### Quick Test for Crop vs AI-Generate

Ask: **"If this element were replaced with a similar but not identical version, would a viewer familiar with the original notice?"**

- **YES, they would notice → CROP FROM SOURCE**
- **No, they wouldn't → AI-GENERATE**

### Priority Within Image Elements

When both approaches could work, prefer:
1. **AI-GENERATE** for cleaner output (higher resolution, transparent backgrounds, no edge artifacts)
2. **CROP FROM SOURCE** when exact fidelity to the original visual is required

## Edge Cases

### Partial Image / Partial Editable
Some elements span both categories. Example: a photo with a text caption overlaid. In this case:
- Keep the photo portion as a cropped image (or AI-generate if decorative)
- Overlay a text box for the caption in PPT

### Embedded Text in Complex Visuals
If text is deeply embedded in a complex visual (e.g., labels inside a 3D diagram, text on a curved path, text with complex masking):
- Keep as image (crop from source), document in the output notes
- DO NOT attempt to extract and overlay text — the result will look worse than the image

### Gradient Text
If text has a gradient fill that python-pptx cannot easily replicate:
- Try solid color approximation first
- If the gradient is critical to the design, keep as image and document

### Semi-Transparent Overlays
If an element has partial transparency over a complex background:
- If the overlay is a simple shape with uniform alpha → REBUILD (PPT shape with transparency)
- If the overlay has varying opacity or complex blend modes → KEEP AS IMAGE

### Decorative Icons with Ambiguous Classification
Some small icons (folder, gear, person, chip) sit on the boundary between "simple shape" and "complex visual":
- If the icon has **>3-4 visual features** (gradients, internal detail, shadows, curves): KEEP AS IMAGE → AI-GENERATE
- If the icon is **flat, single-color, simple geometry**: REBUILD as PPT shape

## Triage Priority

When in doubt between "keep as image" and "rebuild," prioritize:
1. Text editability (user's primary goal)
2. Visual fidelity (close to original appearance)
3. Implementation reliability (avoid fragile reconstructions)

When in doubt between "crop" and "AI-generate" for an image element, prioritize:
1. Information integrity (is the exact content load-bearing?)
2. Output quality (cleanliness, resolution, transparency)
3. Consistency with surrounding elements

If rebuilding would compromise fidelity beyond an acceptable threshold, keep as image.
The acceptable threshold is: the reconstructed element looks clearly worse than the
original when viewed at presentation size (not pixel-peeping).
