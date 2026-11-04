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
        # the player character: big AND near the middle-bottom where the
        # GTA camera keeps him (not just the biggest NPC at the edge)
        h, w = a.shape[:2]
        best, biggest = -1.0, 1
        for i in range(1, n):
            area = stats[i, cv2.CC_STAT_AREA]
            cx = (stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] / 2) / w
            centre = 1.0 - min(1.0, abs(cx - 0.5) * 1.4)
            score = area * (0.45 + 0.55 * centre)
            if score > best:
                best, biggest = score, i
        keep = (labels == biggest).astype("uint8") * 255
        # People standing together come out as ONE blob (11-04: three
        # outlined as one shape). Erode them apart, keep the player's
        # piece, grow it back inside the original cut-out.
        big = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))
        core = cv2.erode(keep, big)
        m, lab2, st2, _ = cv2.connectedComponentsWithStats((core > 0).astype("uint8"), 8)
        if m > 2:
            best2, pick = -1.0, 1
            for i in range(1, m):
                cx = (st2[i, cv2.CC_STAT_LEFT] + st2[i, cv2.CC_STAT_WIDTH] / 2) / w
                centre = 1.0 - min(1.0, abs(cx - 0.5) * 1.4)
                score = st2[i, cv2.CC_STAT_AREA] * (0.45 + 0.55 * centre)
                if score > best2:
                    best2, pick = score, i
            piece = (lab2 == pick).astype("uint8") * 255
            keep = cv2.bitwise_and(cv2.dilate(piece, big, iterations=2), keep)
        # Still a crowd-wide shape: no outline beats a wrong one.
        xs = np.where(keep.max(axis=0) > 0)[0]
        if xs.size and (xs[-1] - xs[0]) / w > 0.55:
            return None
        # fill every hole (11-04: a red tie left out got its own outline)
        # - also gaps open to the bottom edge, where he is cut by the frame
        low = np.where(keep[-12:].max(axis=0) > 0)[0]
        if low.size:
            keep[-2:, low[0]:low[-1] + 1] = 255
        cnts, _ = cv2.findContours(keep, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        keep = np.zeros_like(keep)
        cv2.drawContours(keep, cnts, -1, 255, thickness=-1)
        # knock off small things stuck to him (a pumpkin on his head)
        opened = cv2.morphologyEx(keep, cv2.MORPH_OPEN, cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (19, 19)))
        k2, lab3, st3, _ = cv2.connectedComponentsWithStats((opened > 0).astype("uint8"), 8)
        if k2 > 1:
            main = 1 + int(np.argmax(st3[1:, cv2.CC_STAT_AREA]))
            body = (lab3 == main).astype("uint8") * 255
            # put back fine detail (hair) touching the body, not the blobs
            near = cv2.dilate(body, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
            keep = cv2.bitwise_and(keep, near)
        # smooth the edge
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, kernel)
        keep = cv2.GaussianBlur(keep, (5, 5), 0)
        share = (keep > 128).mean()
        if not 0.04 <= share <= 0.70:
            return None
        return Image.fromarray(keep)
    except Exception:
        return None


_YOLO = None
YOLO_WEIGHTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "models", "yolo11m-seg.pt")


def _yolo():
    global _YOLO
    if _YOLO is None:
        try:
            from ultralytics import YOLO

            os.makedirs(os.path.dirname(YOLO_WEIGHTS), exist_ok=True)
            _YOLO = YOLO(YOLO_WEIGHTS)   # downloads once if missing
        except Exception:
            _YOLO = False
    return _YOLO


def _person(img, focus):
    """ONE person's mask (people apart even when they overlap): the one
    at `focus` (the picker's face, as 0-1 x/y of this picture), else the
    biggest near the middle. None when there is nobody / no model."""
    model = _yolo()
    if not model:
        return None
    try:
        import cv2
        import numpy as np

        r = model(np.asarray(img)[:, :, ::-1], classes=[0], conf=0.35,
                  retina_masks=True, verbose=False)[0]
        if r.masks is None or not len(r.boxes):
            return None
        w, h = img.size
        boxes = r.boxes.xyxy.cpu().numpy()
        masks = r.masks.data.cpu().numpy()
        mh, mw = masks.shape[1:]
        best, pick = -1e18, 0
        for i, (x0, y0, x1, y1) in enumerate(boxes):
            area = (x1 - x0) * (y1 - y0)
            if focus:
                fx, fy = focus[0] * w, focus[1] * h
                # HIS pixels at the face point - boxes overlap, masks don't
                mx, my = int(focus[0] * mw), int(focus[1] * mh)
                patch = masks[i, max(0, my - 6):my + 7, max(0, mx - 6):mx + 7]
                on = float(patch.mean()) if patch.size else 0.0
                inside = x0 <= fx <= x1 and y0 <= fy <= y1
                dist = np.hypot((x0 + x1) / 2 - fx, y0 - fy)
                score = on * 1e13 + (1e12 if inside else 0) - dist * dist
            else:
                cx = (x0 + x1) / 2 / w
                score = area * (0.45 + 0.55 * (1 - min(1, abs(cx - 0.5) * 1.4)))
            if score > best:
                best, pick = score, i
        m = cv2.resize(masks[pick].astype("float32"), (w, h)) > 0.5
        rest = np.zeros((h, w), bool)
        for i in range(len(masks)):
            if i != pick:
                rest |= cv2.resize(masks[i].astype("float32"), (w, h)) > 0.5
        global _OTHERS
        # people IN FRONT of / next to him: his outline never paints them
        _OTHERS = ((rest & ~m) * 255).astype("uint8")
        return (m * 255).astype("uint8")
    except Exception:
        return None


_OTHERS = None


def _mask(img, focus=None):
    global _OTHERS
    _OTHERS = None
    person = _person(img, focus)
    session = _session()
    alpha = None
    if session:
        try:
            from rembg import remove

            alpha = remove(img.convert("RGB"), session=session).split()[-1]
        except Exception:
            alpha = None
    if person is not None:
        try:
            import cv2
            import numpy as np
            from PIL import Image

            # rembg's finer edge (hair) - but only inside this one person
            grown = cv2.dilate(person, cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (11, 11)))
            if alpha is not None:
                a = np.minimum(np.asarray(alpha), grown)
                if (a > 140).sum() < 0.5 * (person > 0).sum():
                    a = person          # rembg missed him: YOLO's own shape
            else:
                a = person
            return _main_subject(Image.fromarray(a))
        except Exception:
            pass
    if alpha is None:
        return None
    return _main_subject(alpha)


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


def polish(path: str, focus=None) -> bool:
    try:
        from PIL import Image, ImageChops, ImageEnhance, ImageFilter
    except Exception:
        return False
    try:
        img = Image.open(path).convert("RGB").resize(WORK, Image.LANCZOS)
        # gentle: a zoomed-in stream frame over-sharpened looks painted
        img = img.filter(ImageFilter.UnsharpMask(radius=1.6, percent=70, threshold=4))
        img = _grade(img)
        mask = _mask(img, focus)
        if mask is not None:
            back = img.filter(ImageFilter.GaussianBlur(1.6))
            back = ImageEnhance.Brightness(back).enhance(0.94)
            back = _vignette(back, 0.38)
            plain = back
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
            others = _OTHERS
            if others is not None and others.shape[::-1] == img.size:
                # someone in front of him: no outline painted over them,
                # and they stay in front
                occl = _grow(Image.fromarray(others), 6).filter(ImageFilter.GaussianBlur(1.5))
                back = Image.composite(plain, back, occl)
                mask = ImageChops.subtract(mask, occl)
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
