#!/usr/bin/env python3
"""Generate placeholder app icon with 'oTax' text.

Produces assets/icon.icns via Pillow + macOS iconutil.
"""
import os
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont


SIZES = [16, 32, 64, 128, 256, 512, 1024]
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
ICONSET_DIR = os.path.join(OUTPUT_DIR, "icon.iconset")


def _render_icon(size: int) -> Image.Image:
    """Render a single icon at the given size."""
    img = Image.new("RGB", (size, size), "#1a1a2e")
    draw = ImageDraw.Draw(img)

    # Draw rounded-rect background
    margin = max(1, size // 16)
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=size // 6,
        fill="#16213e",
        outline="#0f3460",
        width=max(1, size // 64),
    )

    # "oTax" text centered
    font_size = max(8, size // 3)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", font_size)
    except (OSError, IOError):
        font = ImageFont.load_default(font_size)

    bbox = draw.textbbox((0, 0), "oTax", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), "oTax", fill="#e94560", font=font)

    return img


def generate():
    os.makedirs(ICONSET_DIR, exist_ok=True)

    for size in SIZES:
        img = _render_icon(size)
        # Standard resolution
        if size <= 512:
            img.save(os.path.join(ICONSET_DIR, f"icon_{size}x{size}.png"))
        # @2x variants (retina)
        half = size // 2
        if half in [16, 32, 64, 128, 256, 512]:
            img.save(os.path.join(ICONSET_DIR, f"icon_{half}x{half}@2x.png"))

    # Convert to .icns using macOS iconutil
    icns_path = os.path.join(OUTPUT_DIR, "icon.icns")
    subprocess.run(
        ["iconutil", "-c", "icns", ICONSET_DIR, "-o", icns_path],
        check=True,
    )
    # Clean up iconset
    shutil.rmtree(ICONSET_DIR)
    print(f"Generated: {icns_path}")


if __name__ == "__main__":
    generate()
