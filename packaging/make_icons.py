"""Draw the app icon and write every format the builds need.

Run `python packaging/make_icons.py` after changing the design. The outputs are committed, so
builds don't depend on this script.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1024
NAVY = (22, 41, 66, 255)
WHITE = (245, 247, 250, 255)
TEAL = (45, 170, 160, 255)


def draw() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)
    pen.rounded_rectangle((40, 40, SIZE - 40, SIZE - 40), radius=210, fill=NAVY)
    # A shield: flat top, straight sides, curving to a point.
    left, right, top, mid, tip = 270, SIZE - 270, 220, 560, 850
    shield = [(left, top), (right, top), (right, mid)]
    steps = 24
    for i in range(1, steps + 1):  # quadratic curve from (right, mid) to the tip
        t = i / steps
        x = (1 - t) ** 2 * right + 2 * (1 - t) * t * right + t**2 * (SIZE / 2)
        y = (1 - t) ** 2 * mid + 2 * (1 - t) * t * (tip - 60) + t**2 * tip
        shield.append((x, y))
    shield += [(SIZE - x, y) for x, y in reversed(shield[3:-1])]
    shield.append((left, mid))
    pen.polygon(shield, fill=WHITE)
    # A strike through it: data crossed out.
    pen.line((360, 640, SIZE - 360, 380), fill=TEAL, width=70)
    return image


def main() -> None:
    master = draw()
    assets = ROOT / "src" / "stealth_app" / "assets"
    out = ROOT / "packaging"
    master.resize((256, 256), Image.Resampling.LANCZOS).save(assets / "icon.png")
    master.save(
        out / "icon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    master.save(out / "icon.icns")
    print("wrote icon.png, icon.ico, icon.icns")


if __name__ == "__main__":
    main()
