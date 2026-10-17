"""Build the STACKSWOPO VODS (@WOPOVOD) channel banner.

    python -X utf8 tools/make_banner.py [out.png]

2560x1440, the size YouTube asks for. Everything that must be read sits
in the 1546x423 middle strip - the only part every device shows (phones
crop to it); the rest is a dark collage of the newest stream thumbnails,
so a TV or desktop shows more of the streams around it.
"""
import io
import json
import os
import sys
import urllib.request

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

W, H = 2560, 1440
SAFE_W, SAFE_H = 1546, 423
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "auto_uploader", "thumbnails",
                                                         "banner_wopovod.png")
YELLOW, RED, WHITE = (255, 214, 0), (226, 35, 35), (255, 255, 255)
FONTS = r"C:\Windows\Fonts"
# Both kept inside SAFE_W - phones show nothing outside it.
LINE2 = "EVERY FULL STREAM  \u2022  GTA RP  \u2022  REACTIONS  \u2022  MONKEY APP"
LINE3 = "NEW VODS AFTER EVERY STREAM  \u2022  UNCUT ON RUMBLE: BINSCRIPTS"


def font(name, size):
    for n in (name, "impact.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(os.path.join(FONTS, n), size)
        except OSError:
            continue
    return ImageFont.load_default()


def thumbs(ids):
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
    cols, rows = 4, 4
    cw, ch = W // cols, H // rows
    for i in range(cols * rows):
        # 640x360 tiles are exactly 16:9; a stride of 5 keeps repeats apart
        img = images[(i * 5) % len(images)].resize((cw, ch))
        bg.paste(img, ((i % cols) * cw, (i // cols) * ch))
    bg = bg.filter(ImageFilter.GaussianBlur(6))
    bg = ImageEnhance.Brightness(bg).enhance(0.38)
    return bg


def fit(draw, text, name, size, width=SAFE_W - 60):
    """The biggest font up to `size` that keeps `text` inside `width`."""
    while size > 20 and draw.textlength(text, font=font(name, size)) > width:
        size -= 2
    return font(name, size)


def centered(draw, y, text, f, fill, stroke=0, stroke_fill=(0, 0, 0)):
    w = draw.textlength(text, font=f)
    draw.text(((W - w) / 2, y), text, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def main():
    ids = []
    try:
        with open(os.path.join(ROOT, "auto_uploader", "logs", "banner_ids.json"), encoding="utf-8") as f:
            ids = json.load(f)
    except (OSError, ValueError):
        pass
    img = collage(thumbs(ids))
    d = ImageDraw.Draw(img, "RGBA")
    # the readable strip: darker band, red rules above and below
    top, bot = (H - SAFE_H) // 2, (H + SAFE_H) // 2
    d.rectangle([0, top - 30, W, bot + 30], fill=(0, 0, 0, 170))
    d.rectangle([0, top - 36, W, top - 30], fill=RED)
    d.rectangle([0, bot + 30, W, bot + 36], fill=RED)
    centered(d, top + 8, "STACKSWOPO VODS", font("impact.ttf", 190), YELLOW, 8)
    centered(d, top + 225, LINE2,
             fit(d, LINE2, "impact.ttf", 66), WHITE, 4)
    centered(d, top + 318, LINE3,
             fit(d, LINE3, "arialbd.ttf", 46), (235, 235, 235), 3)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(OUT, os.path.getsize(OUT) // 1024, "KB")


if __name__ == "__main__":
    main()
