"""Make a picked thumbnail look like the ones that win (11-04 research):
Stackswopo's own top thumbnails (1-2.4M views - "Johnny Cox Visits Chicago",
"Hood Adventures", "Court Case") are ONE character, big, razor sharp, lifted
off a bright GTA city by a clean white edge; GTA V logo top-right; no text.

polish(path): in place, 1280x720 out.
  1. HD: Lanczos to 1920x1080 work size, light denoise, detail sharpening.
  2. Grade: gentle contrast/vibrance, warm highlights, cool shadows.
  3. The MAIN character only (largest cut-out, isnet - keeps hats and hair):
     a THIN crisp white stroke + a faint soft glow, a touch more clarity and
     light on him; the background softened and vignetted so he reads first.
     (v1 outlined every NPC with a fat blurry band - owner: "too bold".)
Never raises; without rembg it still does 1, 2 and the vignette.
"""

import os

WORK = (1920, 1080)
OUT = (1280, 720)
STROKE_PX = 7         # crisp white edge at work size (~4.5 px at 1280)
GLOW_PX = 16          # soft halo outside it
GLOW_ALPHA = 0.28
# People only: isnet-general-use cut a car out together with the person.
MODEL = os.environ.get("AUTOBLEEP_CUTOUT_MODEL", "u2net_human_seg")
_SESSION = None


def _session():
    global _SESSION
    if _SESSION is None:
        try:
            from rembg import new_session

            _SESSION = new_session(MODEL)
        except Exception:
            try:
                from rembg import new_session

                _SESSION = new_session("u2net_human_seg")
            except Exception:
                _SESSION = False
    return _SESSION


def _main_subject(alpha):
    """Keep only the largest blob (the main character), cleaned."""
    try:
        import cv2
        import numpy as np
        from PIL import Image

        a = np.asarray(alpha)
        solid = (a > 140).astype("uint8")
        n, labels, stats, _ = cv2.connectedComponentsWithStats(solid, 8)
        if n <= 1:
            return None
        biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        keep = (labels == biggest).astype("uint8") * 255
        # fill small holes, smooth the edge
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, kernel)
        keep = cv2.GaussianBlur(keep, (5, 5), 0)
        share = (keep > 128).mean()
        if not 0.04 <= share <= 0.70:
            return None
        return Image.fromarray(keep)
    except Exception:
        return None


def _mask(img):
    session = _session()
    if not session:
        return None
    try:
        from rembg import remove

        cut = remove(img.convert("RGB"), session=session)
        return _main_subject(cut.split()[-1])
    except Exception:
        return None


def _grade(img):
    from PIL import Image, ImageEnhance

    img = ImageEnhance.Contrast(img).enhance(1.06)
    img = ImageEnhance.Color(img).enhance(1.18)
    try:
        import numpy as np

        a = np.asarray(img).astype("float32") / 255.0
        lum = a.mean(axis=2, keepdims=True)
        tone = lum * np.array([1.035, 1.0, 0.96], "float32") + \
            (1 - lum) * np.array([0.96, 1.0, 1.04], "float32")
        img = Image.fromarray((np.clip(a * tone, 0, 1) * 255).astype("uint8"))
    except Exception:
        pass
    return img


def _vignette(img, strength: float):
    from PIL import Image, ImageDraw, ImageFilter

    w, h = img.size
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).ellipse([-w * 0.18, -h * 0.30, w * 1.18, h * 1.30], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(min(w, h) // 5))
    return Image.composite(img, Image.blend(img, Image.new("RGB", (w, h)), strength), m)


def _grow(mask, px: int):
    try:
        import cv2
        import numpy as np
        from PIL import Image

        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (px * 2 + 1, px * 2 + 1))
        return Image.fromarray(cv2.dilate(np.asarray(mask), k))
    except Exception:
        from PIL import ImageFilter
        return mask.filter(ImageFilter.MaxFilter(px * 2 + 1))


def _smooth(mask):
    """Outer contour without stray hair strands: open away thin spikes,
    close gaps, blur + re-threshold for a smooth anti-aliased silhouette."""
    try:
        import cv2
        import numpy as np
        from PIL import Image

        a = np.asarray(mask)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        a = cv2.morphologyEx(a, cv2.MORPH_OPEN, k)
        a = cv2.morphologyEx(a, cv2.MORPH_CLOSE, cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (21, 21)))
        a = cv2.GaussianBlur(a, (0, 0), 4)
        a = np.clip((a.astype("float32") - 96) * 4, 0, 255).astype("uint8")
        return Image.fromarray(a)
    except Exception:
        return mask


def polish(path: str) -> bool:
    try:
        from PIL import Image, ImageChops, ImageEnhance, ImageFilter
    except Exception:
        return False
    try:
        img = Image.open(path).convert("RGB").resize(WORK, Image.LANCZOS)
        img = img.filter(ImageFilter.MedianFilter(3))
        img = img.filter(ImageFilter.UnsharpMask(radius=2.0, percent=110, threshold=3))
        img = _grade(img)
        mask = _mask(img)
        if mask is not None:
            back = img.filter(ImageFilter.GaussianBlur(1.6))
            back = ImageEnhance.Brightness(back).enhance(0.94)
            back = _vignette(back, 0.38)
            white = Image.new("RGB", img.size, (255, 255, 255))
            black = Image.new("RGB", img.size, (0, 0, 0))
            shape = _smooth(mask)
            # 1. soft drop shadow down-right: gives depth, the stroke reads
            #    even against white clothes / bright sky
            shadow = _grow(shape, STROKE_PX + 4).filter(ImageFilter.GaussianBlur(14))
            moved = Image.new("L", img.size, 0)
            moved.paste(shadow, (10, 12))
            shadow = moved
            back = Image.composite(black, back, shadow.point(lambda v: int(v * 0.55)))
            # 2. faint halo, 3. clean anti-aliased stroke on a smoothed
            #    contour (no hair spikes / jaggies)
            halo = _grow(shape, GLOW_PX).filter(ImageFilter.GaussianBlur(GLOW_PX * 0.7))
            back = Image.composite(white, back, halo.point(lambda v: int(v * GLOW_ALPHA)))
            stroke = _grow(shape, STROKE_PX).filter(ImageFilter.GaussianBlur(1.0))
            back = Image.composite(white, back, stroke)
            # 4. thin dark contact line between stroke and hero = crisp edge
            line = _grow(mask, 2).filter(ImageFilter.GaussianBlur(0.8))
            back = Image.composite(Image.new("RGB", img.size, (20, 20, 24)), back,
                                   line.point(lambda v: int(v * 0.6)))
            mask = mask.filter(ImageFilter.GaussianBlur(0.8))
            hero = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=80, threshold=2))
            hero = ImageEnhance.Brightness(hero).enhance(1.04)
            img = Image.composite(hero, back, mask)
        else:
            img = _vignette(img, 0.30)
        img.resize(OUT, Image.LANCZOS).save(path, quality=95)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import shutil
    import sys

    src, dst = sys.argv[1], sys.argv[2]
    shutil.copyfile(src, dst)
    print("polished:", polish(dst))
