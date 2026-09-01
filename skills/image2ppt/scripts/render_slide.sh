#!/bin/bash
# Render a PPTX file to JPEG images for visual comparison.
# Usage: render_slide.sh input.pptx [output_prefix] [dpi]
#
# Dependencies: LibreOffice (soffice), Poppler (pdftoppm)
# Uses the pptx skill's soffice.py for sandbox-aware conversion.

set -e

PPTX="$1"
PREFIX="${2:-slide_render}"
DPI="${3:-150}"
DIR=$(dirname "$PPTX")
PPTX_ABS=$(cd "$DIR" && pwd)/$(basename "$PPTX")

if [ ! -f "$PPTX_ABS" ]; then
    echo "ERROR: File not found: $PPTX_ABS" >&2
    exit 1
fi

# Step 1: PPTX -> PDF via LibreOffice
SOFFICE_SCRIPT="$HOME/.claude/skills/pptx/scripts/office/soffice.py"
if [ -f "$SOFFICE_SCRIPT" ]; then
    python3 "$SOFFICE_SCRIPT" --headless --convert-to pdf --outdir "$DIR" "$PPTX_ABS"
else
    soffice --headless --convert-to pdf --outdir "$DIR" "$PPTX_ABS"
fi

PDF="${PPTX_ABS%.pptx}.pdf"

if [ ! -f "$PDF" ]; then
    echo "ERROR: PDF conversion failed" >&2
    exit 1
fi

# Step 2: PDF -> JPEG via Poppler
OUTPUT_PATH="${DIR}/${PREFIX}"
pdftoppm -jpeg -r "$DPI" "$PDF" "$OUTPUT_PATH"

# Output the first slide image path
FIRST_SLIDE="${OUTPUT_PATH}-01.jpg"
if [ -f "$FIRST_SLIDE" ]; then
    echo "$FIRST_SLIDE"
elif [ -f "${OUTPUT_PATH}-1.jpg" ]; then
    echo "${OUTPUT_PATH}-1.jpg"
else
    # List all generated files
    ls "${OUTPUT_PATH}"*.jpg 2>/dev/null || echo "ERROR: No output images found" >&2
fi
