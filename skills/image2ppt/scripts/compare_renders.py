#!/usr/bin/env python3
"""Compare original image with rendered PPTX slide.

Usage:
    python compare_renders.py --original PATH --rendered PATH --output PATH
                             [--mode side-by-side|overlay|diff]
"""

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageStat
except ImportError:
    print("ERROR: Pillow is required. Install with: pip install Pillow", file=sys.stderr)
    sys.exit(1)


def make_side_by_side(original, rendered):
    """Create side-by-side comparison with labels."""
    ow, oh = original.size
    rw, rh = rendered.size

    # Scale rendered to match original height
    scale = oh / rh
    new_rw = int(rw * scale)
    rendered_scaled = rendered.resize((new_rw, oh), Image.LANCZOS)

    # Canvas
    divider = 4
    canvas_w = ow + divider + new_rw
    canvas_h = oh + 60  # Extra space for labels
    canvas = Image.new("RGB", (canvas_w, canvas_h), (240, 240, 240))

    # Place images
    canvas.paste(original, (0, 60))
    canvas.paste(rendered_scaled, (ow + divider, 60))

    # Divider line
    for y in range(60, canvas_h):
        canvas.putpixel((ow + 1, y), (180, 180, 180))

    # Labels
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 20)
    except (IOError, OSError):
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
        except (IOError, OSError):
            font = ImageFont.load_default()

    label_y = 15
    draw.text((ow // 2 - 40, label_y), "Original", fill=(60, 60, 60), font=font)
    draw.text((ow + divider + new_rw // 2 - 50, label_y), "Recreation", fill=(60, 60, 60), font=font)

    return canvas


def make_overlay(original, rendered):
    """50% opacity blend overlay to reveal positional misalignments."""
    ow, oh = original.size
    rw, rh = rendered.size

    # Scale rendered to match original
    rendered_scaled = rendered.resize((ow, oh), Image.LANCZOS)

    original_rgba = original.convert("RGBA")
    rendered_rgba = rendered_scaled.convert("RGBA")

    # 50% blend
    blended = Image.blend(original_rgba, rendered_rgba, 0.5)
    return blended


def make_diff(original, rendered):
    """Pixel difference heatmap."""
    ow, oh = original.size
    rw, rh = rendered.size

    # Scale rendered to match original
    rendered_scaled = rendered.resize((ow, oh), Image.LANCZOS)

    # Compute absolute difference
    diff = ImageChops.difference(original.convert("RGB"), rendered_scaled.convert("RGB"))

    # Amplify differences for visibility
    diff_data = diff.getdata()
    amplified = []
    threshold = 10  # Ignore anti-aliasing noise
    for r, g, b in diff_data:
        if r + g + b > threshold * 3:
            # Amplify: bright red for high differences
            intensity = min(255, (r + g + b) // 2)
            amplified.append((min(255, intensity + 100), 30, 30))
        else:
            # Dark gray for no difference
            amplified.append((20, 20, 20))

    diff.putdata(amplified)

    # Add diff stats
    stat = ImageStat.Stat(diff)
    total_pixels = ow * oh
    diff_pixels = sum(1 for r, g, b in diff_data if r + g + b > threshold * 3)
    diff_pct = round(diff_pixels / total_pixels * 100, 2)

    # Add stats overlay
    canvas = Image.new("RGB", (ow, oh + 40), (30, 30, 30))
    canvas.paste(diff, (0, 0))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 16)
    except (IOError, OSError):
        font = ImageFont.load_default()

    draw.text((10, oh + 8), f"Difference: {diff_pct}% of pixels (threshold={threshold})",
              fill=(200, 200, 200), font=font)

    return canvas


def main():
    parser = argparse.ArgumentParser(description="Compare original image with rendered PPTX slide")
    parser.add_argument("--original", required=True, help="Path to original image")
    parser.add_argument("--rendered", required=True, help="Path to rendered slide image")
    parser.add_argument("--output", required=True, help="Output comparison image path")
    parser.add_argument("--mode", choices=["side-by-side", "overlay", "diff"],
                        default="overlay", help="Comparison mode (default: overlay)")
    args = parser.parse_args()

    for path_arg in [args.original, args.rendered]:
        if not Path(path_arg).exists():
            print(f"ERROR: File not found: {path_arg}", file=sys.stderr)
            sys.exit(1)

    original = Image.open(args.original).convert("RGB")
    rendered = Image.open(args.rendered).convert("RGB")

    if args.mode == "side-by-side":
        result = make_side_by_side(original, rendered)
    elif args.mode == "overlay":
        result = make_overlay(original, rendered)
    elif args.mode == "diff":
        result = make_diff(original, rendered)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Determine format from extension, convert RGBA→RGB for JPEG
    ext = out_path.suffix.lower()
    if ext in (".jpg", ".jpeg") and result.mode == "RGBA":
        result = result.convert("RGB")
    result.save(str(out_path), quality=90)
    print(f"Comparison saved to: {out_path.resolve()}")


if __name__ == "__main__":
    main()
