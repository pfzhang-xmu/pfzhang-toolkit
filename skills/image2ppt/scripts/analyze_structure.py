#!/usr/bin/env python3
"""Analyze image structure for layout hints (text regions, lines, grid).

Usage:
    python analyze_structure.py --image PATH [--output PATH]
"""

import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image, ImageFilter
except ImportError:
    print("ERROR: Pillow is required. Install with: pip install Pillow", file=sys.stderr)
    sys.exit(1)


def find_edges(img):
    """Detect edges using Sobel-like filter."""
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    return edges


def detect_horizontal_lines(edge_img, min_length_ratio=0.3):
    """Detect horizontal line segments by scanning rows."""
    w, h = edge_img.size
    pixels = edge_img.load()
    lines = []

    min_length = int(w * min_length_ratio)

    for y in range(h):
        run_start = None
        for x in range(w):
            if pixels[x, y] > 40:  # Edge pixel
                if run_start is None:
                    run_start = x
            else:
                if run_start is not None:
                    run_length = x - run_start
                    if run_length >= min_length:
                        lines.append({
                            "y": y,
                            "x_range": [run_start, x],
                            "length": run_length
                        })
                    run_start = None
        # End of row run
        if run_start is not None:
            run_length = w - run_start
            if run_length >= min_length:
                lines.append({
                    "y": y,
                    "x_range": [run_start, w],
                    "length": run_length
                })

    # Merge adjacent lines (within 3px)
    if not lines:
        return []

    lines.sort(key=lambda l: l["y"])
    merged = [lines[0]]
    for line in lines[1:]:
        prev = merged[-1]
        if line["y"] - prev["y"] <= 3:
            # Merge: take the union of x_ranges and average y
            prev["x_range"][0] = min(prev["x_range"][0], line["x_range"][0])
            prev["x_range"][1] = max(prev["x_range"][1], line["x_range"][1])
            prev["length"] = prev["x_range"][1] - prev["x_range"][0]
            prev["y"] = (prev["y"] + line["y"]) // 2
        else:
            merged.append(line)

    return merged


def detect_text_regions(img):
    """Detect likely text regions using horizontal projection."""
    gray = img.convert("L")
    w, h = gray.size
    pixels = gray.load()

    # Compute horizontal projection (dark pixel density per row)
    # Text rows have frequent dark/light transitions
    row_activity = []
    for y in range(h):
        transitions = 0
        prev = pixels[0, y]
        for x in range(1, w):
            curr = pixels[x, y]
            if abs(curr - prev) > 20:
                transitions += 1
            prev = curr
        row_activity.append(transitions)

    # Find contiguous blocks of high activity
    threshold = max(10, max(row_activity) * 0.2) if row_activity else 10
    regions = []
    in_region = False
    region_start = 0

    for y in range(h):
        if row_activity[y] > threshold:
            if not in_region:
                region_start = y
                in_region = True
        else:
            if in_region:
                region_height = y - region_start
                if region_height >= 8:  # Min text height
                    regions.append({
                        "type": "likely_text",
                        "y_start": region_start,
                        "y_end": y,
                        "height": region_height,
                        "box": [0, region_start, w, region_height],
                        "confidence": min(0.9, row_activity[region_start] / max(row_activity) if max(row_activity) > 0 else 0.5)
                    })
                in_region = False

    # Handle region at bottom of image
    if in_region:
        region_height = h - region_start
        if region_height >= 8:
            regions.append({
                "type": "likely_text",
                "y_start": region_start,
                "y_end": h,
                "height": region_height,
                "box": [0, region_start, w, region_height],
                "confidence": min(0.9, row_activity[region_start] / max(row_activity) if max(row_activity) > 0 else 0.5)
            })

    # Merge close regions (within 15px)
    if regions:
        regions.sort(key=lambda r: r["y_start"])
        merged = [regions[0]]
        for r in regions[1:]:
            prev = merged[-1]
            if r["y_start"] - prev["y_end"] <= 15:
                prev["y_end"] = r["y_end"]
                prev["height"] = prev["y_end"] - prev["y_start"]
                prev["box"] = [0, prev["y_start"], w, prev["height"]]
                prev["confidence"] = max(prev["confidence"], r["confidence"])
            else:
                merged.append(r)
        regions = merged

    return regions


def detect_rectangular_regions(edge_img, min_area_ratio=0.01):
    """Detect rectangular regions bounded by edges (potential image/chart areas)."""
    w, h = edge_img.size
    pixels = edge_img.load()
    min_area = w * h * min_area_ratio

    # Find horizontal and vertical lines first
    h_lines = detect_horizontal_lines(edge_img, min_length_ratio=0.2)
    v_lines = []

    # Vertical line detection (transpose logic)
    for x in range(w):
        run_start = None
        for y in range(h):
            if pixels[x, y] > 40:
                if run_start is None:
                    run_start = y
            else:
                if run_start is not None:
                    run_length = y - run_start
                    if run_length >= h * 0.2:
                        v_lines.append({
                            "x": x,
                            "y_range": [run_start, y],
                            "length": run_length
                        })
                    run_start = None
        if run_start is not None:
            run_length = h - run_start
            if run_length >= h * 0.2:
                v_lines.append({
                    "x": x,
                    "y_range": [run_start, h],
                    "length": run_length
                })

    regions = []
    # A naive rectangle detection: find where h_lines and v_lines intersect
    # and form bounding boxes
    if len(h_lines) >= 2 and len(v_lines) >= 2:
        h_ys = sorted(set(l["y"] for l in h_lines))
        v_xs = sorted(set(l["x"] for l in v_lines))

        # Group nearby y coordinates and x coordinates into bands
        h_bands = _group_nearby(h_ys, threshold=10)
        v_bands = _group_nearby(v_xs, threshold=10)

        # Form rectangles from band intersections
        for i in range(len(h_bands) - 1):
            for j in range(len(v_bands) - 1):
                y1 = h_bands[i]
                y2 = h_bands[i + 1]
                x1 = v_bands[j]
                x2 = v_bands[j + 1]
                area = (x2 - x1) * (y2 - y1)
                if area >= min_area:
                    regions.append({
                        "type": "likely_image",
                        "box": [x1, y1, x2 - x1, y2 - y1],
                        "confidence": 0.6
                    })

    return regions


def _group_nearby(values, threshold=10):
    """Group nearby values, returning the mean of each group."""
    if not values:
        return []
    values = sorted(values)
    groups = [[values[0]]]
    for v in values[1:]:
        if v - groups[-1][-1] <= threshold:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) // len(g) for g in groups]


def detect_grid_hint(h_lines, v_lines, img_w, img_h):
    """Detect grid structure hints from line positions."""
    if not h_lines or not v_lines:
        return None

    h_ys = sorted(set(l["y"] for l in h_lines))
    v_xs = sorted(set(l["x"] for l in v_lines))

    h_bands = _group_nearby(h_ys, threshold=15)
    v_bands = _group_nearby(v_xs, threshold=15)

    # A grid needs at least 2 horizontal and 2 vertical lines
    if len(h_bands) >= 2 and len(v_bands) >= 2:
        return {
            "rows": max(1, len(h_bands) - 1),
            "columns": max(1, len(v_bands) - 1),
            "confidence": min(0.8, (len(h_bands) + len(v_bands)) / 10)
        }
    return None


def main():
    parser = argparse.ArgumentParser(description="Analyze image structure for layout hints")
    parser.add_argument("--image", required=True, help="Path to input image")
    parser.add_argument("--output", help="Optional path to write JSON result")
    args = parser.parse_args()

    if not Path(args.image).exists():
        print(f"ERROR: File not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    try:
        img = Image.open(args.image).convert("RGB")
    except Exception as e:
        print(f"ERROR: Cannot open image: {e}", file=sys.stderr)
        sys.exit(1)

    w, h = img.size
    edge_img = find_edges(img)

    h_lines = detect_horizontal_lines(edge_img)
    v_lines_raw = []
    # Detect vertical lines by rotating edge image 90 degrees
    rotated = edge_img.transpose(Image.ROTATE_90)
    v_lines_rotated = detect_horizontal_lines(rotated)
    # Convert back: x in original = y in rotated, y in original = w - x in rotated
    for line in v_lines_rotated:
        v_lines_raw.append({
            "x": h - line["y"],
            "y_range": line["x_range"],
            "length": line["length"]
        })

    text_regions = detect_text_regions(img)
    rect_regions = detect_rectangular_regions(edge_img)
    grid_hint = detect_grid_hint(h_lines, v_lines_raw, w, h)

    result = {
        "image": str(Path(args.image).resolve()),
        "dimensions": {"width": w, "height": h},
        "horizontal_lines": len(h_lines),
        "vertical_lines": len(v_lines_raw),
        "regions": text_regions + rect_regions,
        "grid_hint": grid_hint,
    }

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"Written to {out_path}")
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
