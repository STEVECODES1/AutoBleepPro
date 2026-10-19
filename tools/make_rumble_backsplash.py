"""Rumble backsplash for BinScripts, in the same look as the wopovod banner
(tools/make_banner.py): a dark, blurred collage of the newest stream thumbnails
with a black band, red rules and a yellow title.

2960x600 fills Rumble's user-page header (about 4.93:1) with no white bars.
The words stay inside the middle 1500px, which phones keep when they crop.

  python -X utf8 tools/make_rumble_backsplash.py
"""
import io
import json
import os
import urllib.request

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

W, H = 2960, 600
SAFE_W = 1500
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "auto_uploader", "thumbnails", "rumble_backsplash.png")
YELLOW, RED, WHITE = (255, 214, 0), (226, 35, 35), (255, 255, 255)
TITLE = "STACKSWOPO FULL STREAMS"
LINE2 = "UNCUT & UNCENSORED  \u2022  GTA RP  \u2022  REACTIONS  \u2022  MONKEY APP"
LINE3 = "A NEW FULL STREAM AFTER EVERY STREAM"


def font(name, size):
    for n in (name, "impact.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(os.path.join(r"C:\Windows\Fonts", n), size)
        except OSError:
            continue
    return ImageFont.load_default()


def thumbs():
    try:
        with open(os.path.join(ROOT, "auto_uploader", "logs", "banner_ids.json"), encoding="utf-8") as f:
            ids = json.load(f)
    except (OSError, ValueError):
        ids = []
    out = []
    for vid in ids:
        for kind in ("maxresdefault", "hqdefault"):
            try:
                with urllib.request.urlopen(f"https://i.ytimg.com/vi/{vid}/{kind}.jpg", timeout=15) as r:
                    img = Image.open(io.BytesIO(r.read())).convert("RGB")
                if img.width >= 480:
                    out.append(img)
                    break
            except Exception:
                continue
    return out


def collage(images):
    bg = Image.new("RGB", (W, H), (8, 8, 10))
    if not images:
        return bg
    th, tw = H // 2, (H // 2) * 16 // 9  # 300 x 533 tiles, 2 rows
    cols = W // tw + 1
    for i in range(cols * 2):
        img = images[(i * 5) % len(images)].resize((tw, th))
        bg.paste(img, ((i % cols) * tw, (i // cols) * th))
    return ImageEnhance.Brightness(bg.filter(ImageFilter.GaussianBlur(6))).enhance(0.38)


def fit(draw, text, name, size, width=SAFE_W):
    while size > 20 and draw.textlength(text, font=font(name, size)) > width:
        size -= 2
    return font(name, size)


def centered(draw, y, text, f, fill, stroke=0):
    w = draw.textlength(text, font=f)
    draw.text(((W - w) / 2, y), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))


def main():
    img = collage(thumbs())
    d = ImageDraw.Draw(img, "RGBA")
    top, bot = 95, 505
    d.rectangle([0, top, W, bot], fill=(0, 0, 0, 170))
    d.rectangle([0, top - 6, W, top], fill=RED)
    d.rectangle([0, bot, W, bot + 6], fill=RED)
    centered(d, top + 22, TITLE, fit(d, TITLE, "impact.ttf", 175), YELLOW, 8)
    centered(d, top + 232, LINE2, fit(d, LINE2, "impact.ttf", 62), WHITE, 4)
    centered(d, top + 322, LINE3, fit(d, LINE3, "arialbd.ttf", 44), (235, 235, 235), 3)
    # Rumble stores the header as 1430x291; anything not exactly that shape
    # gets a white strip. 2x that size, so nothing is padded.
    img = img.resize((2860, 582), Image.LANCZOS)
    img.save(OUT, "PNG", optimize=True)
    print(OUT, os.path.getsize(OUT) // 1024, "KB")


if __name__ == "__main__":
    main()
