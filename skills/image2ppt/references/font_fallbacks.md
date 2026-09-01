# Font Identification & Fallback Guide

## Font Category Detection

When the vision model analyzes a slide image, ask it to report for each text element:

1. **Category**: serif, sans-serif, monospace, display/decorative, handwriting
2. **Weight**: thin (100-200), light (300), regular (400), medium (500), semibold (600), bold (700), black (800-900)
3. **Width**: condensed, normal, extended
4. **Distinctive features**: circular O, high x-height, stroke contrast, terminal shape, geometric vs humanist

## Chinese Font Fallbacks (macOS)

| Visual Characteristics | Likely Font | macOS Fallback |
|------------------------|------------|----------------|
| Modern, clean, neutral | PingFang SC (苹方) | PingFang SC |
| Traditional bold headline | Heiti SC (黑体) | Heiti SC |
| Song/Ming style, serif | Songti SC (宋体) | Songti SC |
| Kai style, calligraphic | Kaiti SC (楷体) | Kaiti SC |
| Rounded, friendly | Yuanti SC (圆体) | Yuanti SC |
| Microsoft-style sans | Microsoft YaHei (微软雅黑) | PingFang SC |
| Google Noto style | Noto Sans CJK SC / Source Han Sans | PingFang SC |

## Chinese Font Fallbacks (Cross-Platform)

If the PPTX will be viewed on Windows or other platforms, use these fallbacks:

| macOS Font | Cross-Platform Fallback | Notes |
|-----------|------------------------|-------|
| PingFang SC | Microsoft YaHei | Similar x-height, wider metrics |
| Heiti SC | SimHei | Both are black/sans-serif |
| Songti SC | SimSun | Both are serif/Song |
| Kaiti SC | KaiTi | Both are Kai/calligraphic |

## English Font Fallbacks

| Visual Characteristics | Likely Font | macOS Fallback | Cross-Platform Fallback |
|------------------------|------------|----------------|------------------------|
| Geometric, low contrast, circular O | Futura, Century Gothic | Century Gothic | Arial |
| Neo-grotesque, high x-height | Helvetica, Inter | Helvetica Neue | Arial |
| Humanist sans, angled terminals | Gill Sans, Optima | Gill Sans | Calibri |
| Transitional serif, medium contrast | Times New Roman, Georgia | Times New Roman | Times New Roman |
| Modern/Didone, extreme contrast | Didot, Bodoni | Didot | Georgia |
| Slab serif, rectangular serifs | Rockwell, Museo | Rockwell | Georgia |
| Monospaced | Consolas, Monaco, Fira Code | Menlo | Consolas |
| Rounded sans | Proxima Nova, Nunito | Arial Rounded | Calibri |

## Font Size Mapping

The vision model estimates relative font sizes. Use this hierarchy:

| Relative Size | Typical pt Size (16:9 slide) | Usage |
|--------------|------------------------------|-------|
| XXL (title) | 36-48 pt | Slide title |
| XL (subtitle) | 24-30 pt | Section subtitle |
| L (heading) | 18-22 pt | Section headings |
| M (body) | 12-16 pt | Body text |
| S (caption) | 9-11 pt | Captions, footnotes, labels |
| XS (micro) | 7-8 pt | Source lines, legal text |

If the vision model reports a specific pt size estimate, prefer that over the generic mapping.

## Color Sampling for Text

When extracting text color from an image:
1. Sample from the center of large glyphs (≥24pt) to avoid anti-aliasing contamination
2. For small text, sample the darkest (for dark-on-light) or lightest (for light-on-dark) 25% of pixels in the text region
3. If text sits on a gradient background, sample from 3-5 points along the text and use the average
4. Verify extracted color against the overall palette from `extract_colors.py`
