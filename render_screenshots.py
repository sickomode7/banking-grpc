"""Render captured terminal output into PNG "screenshots" for the report.

Nothing here invents output: it only draws whatever text is in the given file.

Usage:  python render_screenshots.py logs/demo.txt outputs/demo.png "python client.py demo"
"""

from __future__ import annotations

import sys

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "/usr/share/fonts/noto/NotoSansMono-Regular.ttf"
if not __import__("os").path.exists(FONT_PATH):
    FONT_PATH = "/usr/share/fonts/liberation/LiberationMono-Regular.ttf"

FONT_SIZE = 15
LINE_HEIGHT = 22
PAD_X, PAD_Y = 22, 54
BG = (13, 17, 23)
CHROME = (22, 27, 34)
FG = (201, 209, 217)
DIM = (110, 118, 129)
ACCENT = (88, 166, 255)
RED = (248, 81, 73)
GREEN = (63, 185, 80)


def colour_for(line: str) -> tuple[int, int, int]:
    if "RPC failed" in line or "Traceback" in line:
        return RED
    if "COMPLETED" in line:
        return GREEN
    if line.startswith("─") or line.startswith("==="):
        return DIM
    if line.strip().startswith(("1.", "2.", "3.", "4.", "5.", "6.")):
        return ACCENT
    return FG


def render(text_path: str, png_path: str, title: str) -> None:
    with open(text_path, encoding="utf-8") as fh:
        lines = fh.read().rstrip("\n").split("\n")

    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    width = int(max(font.getlength(line) for line in lines)) + PAD_X * 2
    height = LINE_HEIGHT * len(lines) + PAD_Y + PAD_Y

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    # window chrome
    draw.rectangle([0, 0, width, 34], fill=CHROME)
    for i, colour in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        cx = 18 + i * 20
        draw.ellipse([cx - 6, 11, cx + 6, 23], fill=colour)
    draw.text((88, 9), title, font=font, fill=DIM)

    y = PAD_Y
    for line in lines:
        draw.text((PAD_X, y), line, font=font, fill=colour_for(line))
        y += LINE_HEIGHT

    img.save(png_path)
    print(f"wrote {png_path}  ({width}x{height})")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    render(sys.argv[1], sys.argv[2], sys.argv[3])
