#!/usr/bin/env python3
"""Crop a region from an image, optionally with background removal.

Usage:
    python crop_region.py --image PATH --output PATH --box x,y,w,h
                         [--transparent] [--padding N] [--circular]
"""

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("ERROR: Pillow is required. Install with: pip install Pillow", file=sys.stderr)
    sys.exit(1)


def remove_background(img, tolerance=30):
    """Remove solid-color background from a crop.

    Samples the four corner regions to determine the background color,
    then creates an alpha mask where pixels within tolerance become transparent.
    """
    rgba = img.convert("RGBA")
    w, h = rgba.size
    pixels = rgba.load()

    # Sample edge pixels to determine background color
    edge_pixels = []
    sample_width = max(1, min(w // 10, 20))
    sample_height = max(1, min(h // 10, 20))

    # Top edge
    for x in range(w):
        for y in range(sample_height):
            edge_pixels.append(pixels[x, y])
    # Bottom edge
    for x in range(w):
        for y in range(h - sample_height, h):
            edge_pixels.append(pixels[x, y])
    # Left edge (excluding already sampled corners)
    for y in range(sample_height, h - sample_height):
        for x in range(sample_width):
            edge_pixels.append(pixels[x, y])
    # Right edge
    for y in range(sample_height, h - sample_height):
        for x in range(w - sample_width, w):
            edge_pixels.append(pixels[x, y])

    if not edge_pixels:
        return rgba

    # Find the most common color among edge pixels
    from collections import Counter
    bg_counter = Counter(edge_pixels)
    bg_color = bg_counter.most_common(1)[0][0][:3]

    # Create transparency mask
    new_pixels = rgba.load()
    for x in range(w):
        for y in range(h):
            r, g, b, a = pixels[x, y]
            color_dist = abs(r - bg_color[0]) + abs(g - bg_color[1]) + abs(b - bg_color[2])
            if color_dist <= tolerance:
                new_pixels[x, y] = (r, g, b, 0)

    return rgba


def make_circular(img):
    """Apply a circular mask to the image."""
    rgba = img.convert("RGBA")
    w, h = rgba.size
    size = min(w, h)
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)

    # Center the circle in the crop
    left = (w - size) // 2
    top = (h - size) // 2
    draw.ellipse([left, top, left + size, top + size], fill=255)

    result = rgba.copy()
    result.putalpha(mask)
    return result


def main():
    parser = argparse.ArgumentParser(description="Crop a region from an image")
    parser.add_argument("--image", required=True, help="Path to source image")
    parser.add_argument("--output", required=True, help="Output file path (PNG recommended)")
    parser.add_argument("--box", required=True, help="Crop box: x,y,w,h (pixel coordinates)")
    parser.add_argument("--transparent", action="store_true", help="Remove solid background color")
    parser.add_argument("--circular", action="store_true", help="Apply circular mask")
    parser.add_argument("--padding", type=int, default=0, help="Padding in pixels around crop")
    args = parser.parse_args()

    if not Path(args.image).exists():
        print(f"ERROR: File not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    try:
        img = Image.open(args.image).convert("RGBA")
    except Exception as e:
        print(f"ERROR: Cannot open image: {e}", file=sys.stderr)
        sys.exit(1)

    # Parse bounding box
    try:
        x, y, w, h = map(int, args.box.split(","))
    except ValueError:
        print("ERROR: --box must be x,y,w,h (integers)", file=sys.stderr)
        sys.exit(1)

    # Apply padding
    x = max(0, x - args.padding)
    y = max(0, y - args.padding)
    w = min(img.width - x, w + 2 * args.padding)
    h = min(img.height - y, h + 2 * args.padding)

    if w <= 0 or h <= 0:
        print(f"ERROR: Invalid crop box: {x},{y},{w},{h} (image: {img.width}x{img.height})", file=sys.stderr)
        sys.exit(1)

    # Crop
    cropped = img.crop((x, y, x + w, y + h))

    # Apply transformations
    if args.transparent:
        cropped = remove_background(cropped)

    if args.circular:
        cropped = make_circular(cropped)

    # Save
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(str(out_path), format="PNG")

    print(json.dumps({
        "output": str(out_path.resolve()),
        "dimensions": {"width": cropped.width, "height": cropped.height},
        "source_box": [x, y, w, h]
    }, indent=2))

if __name__ == "__main__":
    import json
    main()
