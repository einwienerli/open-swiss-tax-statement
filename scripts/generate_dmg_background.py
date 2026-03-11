#!/usr/bin/env python3
"""Generate DMG background image with drag-to-install arrow.

Produces assets/dmg_background.png and assets/dmg_background@2x.png
for use in the DMG installer window.

Window size: 540x360, so background is 540x360 (1x) and 1080x720 (2x).
Icon positions: app at (135, 170), Applications at (405, 170).
Arrow centered between icons, below the icon center line.
"""
import os

from PIL import Image, ImageDraw, ImageFont

WINDOW_W, WINDOW_H = 540, 290
BG_COLOR = "#f0f0f2"
ARROW_COLOR = "#c5c5ca"
TEXT_COLOR = "#999"

# Icon centers (must match AppleScript positions in build_dmg.sh)
APP_X, APPS_X = 135, 405
ICON_Y = 130


def _render(scale: int) -> Image.Image:
    w, h = WINDOW_W * scale, WINDOW_H * scale
    img = Image.new("RGB", (w, h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    s = scale  # shorthand

    # Arrow between icons, vertically centered on icon row
    arrow_y = ICON_Y * s
    x_start = (APP_X + 50) * s  # right edge of app icon area
    x_end = (APPS_X - 50) * s   # left edge of Applications icon area

    # Shaft
    shaft_h = 3 * s
    draw.rounded_rectangle(
        [x_start, arrow_y - shaft_h, x_end - 10 * s, arrow_y + shaft_h],
        radius=shaft_h,
        fill=ARROW_COLOR,
    )

    # Arrowhead
    head_w = 18 * s
    head_h = 14 * s
    draw.polygon(
        [
            (x_end, arrow_y),
            (x_end - head_w, arrow_y - head_h),
            (x_end - head_w, arrow_y + head_h),
        ],
        fill=ARROW_COLOR,
    )

    return img


def generate():
    output_dir = os.path.join(os.path.dirname(__file__), "..", "assets")
    os.makedirs(output_dir, exist_ok=True)

    for scale, suffix in [(1, ""), (2, "@2x")]:
        img = _render(scale)
        path = os.path.join(output_dir, f"dmg_background{suffix}.png")
        img.save(path)
        print(f"Generated: {path} ({img.width}x{img.height})")


if __name__ == "__main__":
    generate()
