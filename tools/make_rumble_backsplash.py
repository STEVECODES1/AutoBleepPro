"""Rumble user-page backsplash for BinScripts: 3600x600 (Rumble's spec), text in the
centre safe zone, says plainly it is a fan channel (10-18, after the impersonation
removal on YouTube)."""
import os
from PIL import Image, ImageDraw, ImageFont

W, H = 3600, 600
GREEN = (133, 199, 66)
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "auto_uploader", "thumbnails", "rumble_backsplash.png")


def font(name, size):
    for path in (rf"C:\Windows\Fonts\{name}", r"C:\Windows\Fonts\arialbd.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


img = Image.new("RGB", (W, H), (8, 10, 8))
d = ImageDraw.Draw(img)
for x in range(-H, W, 90):  # faint diagonal stripes
    d.line([(x, H), (x + H, 0)], fill=(16, 24, 14), width=34)
for y in range(H):  # green glow at the bottom
    a = max(0, (y - H * 0.55) / (H * 0.45))
    d.line([(0, y), (W, y)], fill=(int(8 + 20 * a), int(10 + 45 * a), int(8 + 12 * a)))
d.rectangle([0, H - 14, W, H], fill=GREEN)


def centred(text, y, f, fill):
    w = d.textlength(text, font=f)
    d.text(((W - w) / 2, y), text, font=f, fill=fill)


centred("FULL STREAMS - UNCUT & UNCENSORED", 175, font("impact.ttf", 132), (255, 255, 255))
centred("EVERY STACKSWOPO STREAM  |  GTA RP  |  LIVE REACTIONS", 370, font("arialbd.ttf", 60), GREEN)
img.save(OUT, optimize=True)
print(OUT, os.path.getsize(OUT) // 1024, "KB")
