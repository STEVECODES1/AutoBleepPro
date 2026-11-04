"""Make a picked thumbnail look like the ones that win (11-04 research):
Stackswopo's own top thumbnails (1-2.4M views - "Johnny Cox Visits Chicago",
"Hood Adventures", "Court Case") are ONE character, big, razor sharp, with a
WHITE GLOW OUTLINE that lifts him off a bright, saturated GTA city; GTA V logo
top-right; little or no text. A GTA loading-screen / movie-poster look.

polish(path): in place, 1280x720 out.
  1. HD: upscaled with Lanczos to 1920x1080 work size, denoised, sharpened.
  2. Grade: contrast, vibrance, warm highlights / cool shadows (cinematic).
  3. Subject: cut out (rembg), outlined with a soft white glow, a touch of
     extra sharpness and light on him; the background is softened and
     slightly darkened at the edges (vignette) so he pops.
Never raises; without rembg it still does 1, 2 and the vignette.
"""

import os

WORK = (1920, 1080)
OUT = (1280, 720)
GLOW_PX = 14          # outline thickness at work size
_SESSION = None


def _session():
    global _SESSION
    if _SESSION is None:
        try:
            from rembg import new_session

            _SESSION = new_session("u2net_human_seg")
        except Exception:
            _SESSION = False
    return _SESSION


def _mask(img):
    """L-mode mask of the person/character, or None."""
    session = _session()
    if not session:
        return None
    try:
        from rembg import remove

        cut = remove(img.convert("RGB"), session=session)
        alpha = cut.split()[-1]
        # Too small (nobody found) or nearly everything (a bad cut): no outline.
        hist = alpha.histogram()
        on = sum(hist[128:]) / float(sum(hist) or 1)
        return alpha if 0.04 <= on <= 0.75 else None
    except Exception:
        return None


def _grade(img):
    from PIL import ImageEnhance

    # Gentler than the first try: the stream grade already lifted it, and a
    # white shirt blew out to flat white (preview 11-04).
    img = ImageEnhance.Contrast(img).enhance(1.06)
    img = ImageEnhance.Color(img).enhance(1.18)
    # Cinematic split-tone: a little warmth in the highlights, teal in shadows.
    try:
        import numpy as np

        a = np.asarray(img).astype("float32") / 255.0
        lum = a.mean(axis=2, keepdims=True)
        warm = np.array([1.04, 1.0, 0.95], dtype="float32")
        cool = np.array([0.95, 1.0, 1.05], dtype="float32")
        tone = lum * warm + (1 - lum) * cool
        a = np.clip(a * tone, 0, 1)
        from PIL import Image

        img = Image.fromarray((a * 255).astype("uint8"))
    except Exception:
        pass
    return img


def _vignette(img, strength: float = 0.35):
    from PIL import Image, ImageDraw, ImageFilter

    w, h = img.size
    m = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(m)
    d.ellipse([-w * 0.15, -h * 0.25, w * 1.15, h * 1.25], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(min(w, h) // 6))
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    faded = Image.blend(img, dark, strength)
    return Image.composite(img, faded, m)


def polish(path: str) -> bool:
    try:
        from PIL import Image, ImageFilter
    except Exception:
        return False
    try:
        img = Image.open(path).convert("RGB")
        img = img.resize(WORK, Image.LANCZOS)
        img = img.filter(ImageFilter.MedianFilter(3))
        img = img.filter(ImageFilter.UnsharpMask(radius=2.2, percent=140, threshold=2))
        img = _grade(img)
        mask = _mask(img)
        if mask is not None:
            # Background softened so the character reads first.
            back = img.filter(ImageFilter.GaussianBlur(2.2))
            back = _vignette(back, 0.40)
            # White glow: the mask grown and blurred, laid under the character.
            grown = mask.filter(ImageFilter.MaxFilter(GLOW_PX * 2 + 1))
            glow = grown.filter(ImageFilter.GaussianBlur(GLOW_PX * 0.6))
            white = Image.new("RGB", img.size, (255, 255, 255))
            back = Image.composite(white, back, glow.point(lambda v: int(v * 0.92)))
            hero = img.filter(ImageFilter.UnsharpMask(radius=1.4, percent=90, threshold=1))
            img = Image.composite(hero, back, mask.filter(ImageFilter.GaussianBlur(1)))
        else:
            img = _vignette(img, 0.30)
        img = img.resize(OUT, Image.LANCZOS)
        img.save(path, quality=95)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import shutil
    import sys

    src, dst = sys.argv[1], sys.argv[2]
    shutil.copyfile(src, dst)
    print("polished:", polish(dst))
