#!/usr/bin/env python3
"""Extract precise color palette and gradient info from an image or region.

Usage:
    python extract_colors.py --image PATH [--region x,y,w,h] [--max-colors 16] [--format json|text]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required. Install with: pip install Pillow", file=sys.stderr)
    sys.exit(1)


def rgb_to_hex(rgb):
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def classify_color_role(rgb, pct, all_colors):
    """Heuristically classify a color's role based on luminance and frequency."""
    r, g, b = rgb
    luminance = 0.299 * r + 0.587 * g + 0.256 * b

    if pct > 0.25 and (luminance > 240 or luminance < 20):
        return "background"
    elif luminance < 40:
        return "text"
    elif luminance > 200:
        return "text"
    elif pct > 0.15:
        return "accent"
    elif 40 <= luminance <= 200:
        if pct > 0.03:
            return "accent"
        else:
            return "detail"
    return "accent"


def extract_colors(image_path, region=None, max_colors=16):
    """Extract dominant colors from an image."""
    img = Image.open(image_path).convert("RGBA")

    if region:
        x, y, w, h = map(int, region.split(","))
        img = img.crop((x, y, x + w, y + h))

    # Convert to RGB for quantization
    rgb_img = img.convert("RGB")

    # Quantize to find dominant colors
    quantized = rgb_img.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT)
    quantized = quantized.convert("RGB")

    # Count pixel frequencies
    pixels = list(quantized.getdata())
    total = len(pixels)
    counter = Counter(pixels)

    palette = []
    for (r, g, b), count in counter.most_common(max_colors):
        pct = round(count / total, 4)
        palette.append({
            "hex": rgb_to_hex((r, g, b)),
            "rgb": [r, g, b],
            "pct": pct,
            "role": classify_color_role((r, g, b), pct, counter)
        })

    # Re-classify roles with more context
    if palette:
        brightest = max(palette, key=lambda c: c["rgb"][0] + c["rgb"][1] + c["rgb"][2])
        darkest = min(palette, key=lambda c: c["rgb"][0] + c["rgb"][1] + c["rgb"][2])
        if brightest["pct"] > 0.2:
            brightest["role"] = "background"
        if darkest["pct"] < 0.3:
            darkest["role"] = "text"

    return palette


def detect_gradients(image_path, region=None):
    """Detect linear gradients by sampling edges."""
    img = Image.open(image_path).convert("RGBA")
    if region:
        x, y, w, h = map(int, region.split(","))
        img = img.crop((x, y, x + w, y + h))

    w, h = img.size

    # Sample left edge, center, right edge for horizontal gradient
    # Sample top edge, center, bottom edge for vertical gradient
    samples = {}

    # Horizontal sampling
    for name, x_pct in [("left", 0.05), ("center", 0.5), ("right", 0.95)]:
        x = int(w * x_pct)
        col_pixels = [img.getpixel((x, py)) for py in range(0, h, max(1, h // 20))]
        avg = tuple(sum(c[i] for c in col_pixels) // len(col_pixels) for i in range(3))
        samples[name] = {"hex": rgb_to_hex(avg[:3]), "rgb": list(avg[:3])}

    # Vertical sampling
    for name, y_pct in [("top", 0.05), ("middle", 0.5), ("bottom", 0.95)]:
        y = int(h * y_pct)
        row_pixels = [img.getpixel((px, y)) for px in range(0, w, max(1, w // 20))]
        avg = tuple(sum(c[i] for c in row_pixels) // len(row_pixels) for i in range(3))
        samples[name] = {"hex": rgb_to_hex(avg[:3]), "rgb": list(avg[:3])}

    # Detect gradient direction
    gradients = []
    h_diff = sum(abs(samples["left"]["rgb"][i] - samples["right"]["rgb"][i]) for i in range(3))
    v_diff = sum(abs(samples["top"]["rgb"][i] - samples["bottom"]["rgb"][i]) for i in range(3))

    if h_diff > 15:
        gradients.append({
            "type": "linear",
            "direction": "horizontal",
            "stops": [
                {"position": "0%", "color": samples["left"]["hex"]},
                {"position": "100%", "color": samples["right"]["hex"]}
            ]
        })
    if v_diff > 15:
        gradients.append({
            "type": "linear",
            "direction": "vertical",
            "stops": [
                {"position": "0%", "color": samples["top"]["hex"]},
                {"position": "100%", "color": samples["bottom"]["hex"]}
            ]
        })

    return gradients


def detect_transparency(image_path):
    """Check if image has meaningful transparency."""
    img = Image.open(image_path)
    if img.mode != "RGBA":
        return False

    alpha = img.getchannel("A")
    # If any pixel has alpha < 250, consider it has transparency
    alpha_values = list(alpha.getdata())
    transparent = sum(1 for a in alpha_values if a < 250)
    return transparent / len(alpha_values) > 0.01


def main():
    parser = argparse.ArgumentParser(description="Extract colors and gradients from an image")
    parser.add_argument("--image", required=True, help="Path to input image")
    parser.add_argument("--region", help="Crop region: x,y,w,h")
    parser.add_argument("--max-colors", type=int, default=16, help="Max palette colors (default: 16)")
    parser.add_argument("--format", choices=["json", "text"], default="json")
    args = parser.parse_args()

    if not Path(args.image).exists():
        print(f"ERROR: File not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    try:
        img = Image.open(args.image)
    except Exception as e:
        print(f"ERROR: Cannot open image: {e}", file=sys.stderr)
        sys.exit(1)

    result = {
        "image": str(Path(args.image).resolve()),
        "dimensions": {"width": img.width, "height": img.height},
        "dominant_colors": extract_colors(args.image, args.region, args.max_colors),
        "gradients": detect_gradients(args.image, args.region),
        "has_transparency": detect_transparency(args.image),
    }

    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Image: {result['image']}")
        print(f"Dimensions: {result['dimensions']['width']}x{result['dimensions']['height']}")
        print(f"Transparency: {result['has_transparency']}")
        print(f"\nDominant Colors ({len(result['dominant_colors'])}):")
        for c in result["dominant_colors"]:
            bar = "█" * max(1, int(c["pct"] * 40))
            print(f"  {c['hex']}  {c['pct']*100:5.1f}%  [{c['role']:12s}] {bar}")
        if result["gradients"]:
            print(f"\nGradients ({len(result['gradients'])}):")
            for g in result["gradients"]:
                stops = " → ".join(s["color"] for s in g["stops"])
                print(f"  {g['type']} {g['direction']}: {stops}")


if __name__ == "__main__":
    main()
