"""The top-left game mark on GTA RP full-stream thumbnails (config: general.thumbnail_logo).

    python -X utf8 tools/make_game_badge.py [out.png]

An original badge - the words GTA RP on a red plate in the channel's own
colours - not Rockstar's logo: their artwork in every thumbnail is theirs
to claim. thumbnail.py scales it to a fifth of the width, top-left, and
only on streams that are the game (a Monkey call or a desktop stream gets
no mark at all).
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "auto_uploader", "thumbnails", "gta_rp_badge.png")
W, H = 800, 340
RED, YELLOW, BLACK, WHITE = (220, 20, 30), (255, 214, 0), (0, 0, 0), (255, 255, 255)


def font(size):
    for name in ("impact.ttf", "ariblk.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(os.path.join(r"C:\Windows\Fonts", name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # drop shadow, black rim, red plate, yellow rule along the bottom
    d.rounded_rectangle((14, 18, W - 2, H - 2), radius=46, fill=(0, 0, 0, 150))
    d.rounded_rectangle((2, 2, W - 14, H - 18), radius=46, fill=BLACK)
    d.rounded_rectangle((14, 14, W - 26, H - 30), radius=38, fill=RED)
    d.rounded_rectangle((60, H - 82, W - 72, H - 62), radius=10, fill=YELLOW)
    f = font(230)
    text = "GTA RP"
    box = d.textbbox((0, 0), text, font=f, stroke_width=10)
    tw, th = box[2] - box[0], box[3] - box[1]
    x = (W - 12 - tw) / 2 - box[0]
    y = (H - 100 - th) / 2 - box[1] + 6
    d.text((x, y), text, font=f, fill=WHITE, stroke_width=10, stroke_fill=BLACK)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(OUT, os.path.getsize(OUT) // 1024, "KB")


if __name__ == "__main__":
    main()
