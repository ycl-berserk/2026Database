from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "app" / "static" / "icons"
INK = "#232b25"
PAPER = "#f6f2e9"
SEAL = "#a8432c"
RULE = "#b9ad92"

SUPERSAMPLE = 4


def draw_icon(size: int, maskable: bool) -> Image.Image:
    scale = SUPERSAMPLE
    canvas = size * scale
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    radius = 0 if maskable else int(canvas * 0.22)
    draw.rounded_rectangle(
        [0, 0, canvas - 1, canvas - 1], radius=radius, fill=INK
    )

    left_page = [
        (canvas * 0.25, canvas * 0.35),
        (canvas * 0.49, canvas * 0.29),
        (canvas * 0.49, canvas * 0.70),
        (canvas * 0.25, canvas * 0.66),
    ]
    right_page = [
        (canvas * 0.51, canvas * 0.29),
        (canvas * 0.75, canvas * 0.35),
        (canvas * 0.75, canvas * 0.66),
        (canvas * 0.51, canvas * 0.70),
    ]
    for page in (left_page, right_page):
        draw.polygon(page, fill=PAPER)

    line_width = max(1, int(canvas * 0.008))
    for y in (0.41, 0.47, 0.53):
        draw.line(
            [(canvas * 0.30, canvas * (y + 0.015)), (canvas * 0.44, canvas * (y - 0.002))],
            fill=RULE,
            width=line_width,
        )
        draw.line(
            [(canvas * 0.56, canvas * (y - 0.002)), (canvas * 0.70, canvas * (y + 0.015))],
            fill=RULE,
            width=line_width,
        )

    draw.line(
        [(canvas * 0.50, canvas * 0.30), (canvas * 0.50, canvas * 0.70)],
        fill=SEAL,
        width=max(2, int(canvas * 0.014)),
    )

    bookmark = [
        (canvas * 0.655, canvas * 0.235),
        (canvas * 0.715, canvas * 0.235),
        (canvas * 0.715, canvas * 0.365),
        (canvas * 0.685, canvas * 0.335),
        (canvas * 0.655, canvas * 0.365),
    ]
    draw.polygon(bookmark, fill=SEAL)

    return image.resize((size, size), Image.LANCZOS)


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    draw_icon(192, maskable=False).save(OUTPUT / "icon-192.png")
    draw_icon(512, maskable=False).save(OUTPUT / "icon-512.png")
    draw_icon(512, maskable=True).save(OUTPUT / "maskable-512.png")
    print(f"[OK] icons written to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
