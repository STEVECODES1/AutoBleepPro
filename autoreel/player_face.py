"""Make sure the thumbnail shows the PLAYER'S FACE (owner 11-04: Johnny
Cox is the character the gameplay camera follows).

In third person the camera sits behind him, so most frames show the back
of his head - and the picker happily chose one (11-04: red jacket, white
cap, no face). A pose model tells front from back (nose + eyes visible).

better_pick(looks, picked, box) -> (picked, box):
  keeps the pick when the picked person's face shows; otherwise moves to
  the look where the person in the middle of the screen (the player)
  faces the camera and is biggest, with a face box from the keypoints.
Never raises; without the model it changes nothing.
"""

import io
import os

WEIGHTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "models", "yolo11m-pose.pt")
_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        try:
            from ultralytics import YOLO

            os.makedirs(os.path.dirname(WEIGHTS), exist_ok=True)
            _MODEL = YOLO(WEIGHTS)
        except Exception:
            _MODEL = False
    return _MODEL


def people(data: bytes) -> list:
    """Each person: box/face as 0-1 (x0, y0, x1, y1), facing, height."""
    model = _model()
    if not model:
        return []
    try:
        import numpy as np
        from PIL import Image

        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        r = model(np.asarray(img)[:, :, ::-1], conf=0.35, verbose=False)[0]
        if r.keypoints is None or not len(r.boxes):
            return []
        out = []
        kps = r.keypoints.data.cpu().numpy()        # n x 17 x (x, y, conf)
        for (x0, y0, x1, y1), k in zip(r.boxes.xyxy.cpu().numpy(), kps):
            nose, le, re_ = k[0][2], k[1][2], k[2][2]
            # strict: a head turned away still gives a weak nose/eye guess
            facing = bool(nose > 0.7 and le > 0.6 and re_ > 0.6)
            head = [p for p in k[:5] if p[2] > 0.3]
            face = None
            if head:
                xs = [p[0] for p in head]
                ys = [p[1] for p in head]
                cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
                shoulders = abs(k[5][0] - k[6][0]) if min(k[5][2], k[6][2]) > 0.3 else 0
                s = max((max(xs) - min(xs)) * 1.7, shoulders * 0.55,
                        (y1 - y0) * 0.11)
                face = ((cx - s * 0.5) / w, (cy - s * 0.6) / h,
                        (cx + s * 0.5) / w, (cy + s * 0.6) / h)
            back = bool(nose < 0.3 and le < 0.3 and re_ < 0.3)
            out.append({"box": (x0 / w, y0 / h, x1 / w, y1 / h),
                        "face": face, "facing": facing,
                        # small/far faces score lower: front-ish is enough
                        "front": facing or bool(not back and nose > 0.5
                                                and max(le, re_) > 0.5),
                        "back": back,
                        "height": (y1 - y0) / h,
                        "look": _outfit(img, (x0, y0, x1, y1))})
        return out
    except Exception:
        return []


def _outfit(img, box):
    """Colour fingerprint of the clothes (torso + hair/hat band)."""
    try:
        import cv2
        import numpy as np

        x0, y0, x1, y1 = (int(v) for v in box)
        bh = y1 - y0
        crop = np.asarray(img)[y0 + int(bh * 0.18):y0 + int(bh * 0.55),
                               x0 + int((x1 - x0) * 0.2):x1 - int((x1 - x0) * 0.2)]
        if crop.size == 0:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [18, 8], [0, 180, 0, 256])
        return cv2.normalize(hist, hist).flatten()
    except Exception:
        return None


def _same(a, b) -> float:
    try:
        import cv2

        if a is None or b is None:
            return 0.0
        return float(cv2.compareHist(a, b, cv2.HISTCMP_CORREL))
    except Exception:
        return 0.0


def player_outfit(all_people: list):
    """The player = the person the camera sits behind: back to camera,
    low in the middle of the screen, in most frames. Their outfit is the
    one most like the other such shots."""
    behind = [p for found in all_people for p in found
              if p["back"] and p["height"] >= 0.3 and p["box"][3] >= 0.8
              and 0.25 <= (p["box"][0] + p["box"][2]) / 2 <= 0.75
              and p["look"] is not None]
    if not behind:
        return None
    return max(behind, key=lambda p: sum(_same(p["look"], q["look"])
                                         for q in behind))["look"]


MATCH = 0.55


def _centre_person(found: list):
    """The player: big enough, nearest the middle of the screen."""
    big = [p for p in found if p["height"] >= 0.25]
    if not big:
        return None
    return min(big, key=lambda p: abs((p["box"][0] + p["box"][2]) / 2 - 0.5))


def _near(found: list, point):
    if not found or point is None:
        return None
    fx, fy = point
    for p in found:
        x0, y0, x1, y1 = p["box"]
        if x0 <= fx <= x1 and y0 <= fy <= y1:
            return p
    return None


def better_pick(looks: list, picked: int, box):
    """`looks`: jpeg bytes per candidate frame. `box`: the picker's
    ("face", (x0,y0,x1,y1)) or (x0,y0,x1,y1) or None."""
    try:
        if not _model() or not looks or picked is None:
            return picked, box
        hint = box[1] if (isinstance(box, tuple) and len(box) == 2
                          and box[0] == "face") else box
        point = None
        if hint:
            point = ((hint[0] + hint[2]) / 2, (hint[1] + hint[3]) / 2)
        found = [people(data) for data in looks]
        outfit = player_outfit(found)
        here = found[picked]
        who = _near(here, point) or _centre_person(here)
        is_player = outfit is None or (who is not None
                                       and _same(who["look"], outfit) >= MATCH)
        if os.environ.get("AUTOBLEEP_THUMB_DEBUG"):
            print("[player_face] picked", picked, "outfit", outfit is not None,
                  "who", who and (who["box"], who["facing"],
                                  round(_same(who["look"], outfit), 2)))
        if who and who["facing"] and is_player:
            return picked, box
        # The player, facing the camera, biggest - matched by his outfit.
        best, best_i = None, None
        for i, group in enumerate(found):
            for p in group:
                if not (p["front"] and p["face"] and p["height"] >= 0.25):
                    continue
                if outfit is not None and _same(p["look"], outfit) < MATCH:
                    continue
                # a clear face counts double, then size
                rank = (p["facing"], p["height"])
                if best is None or rank > (best["facing"], best["height"]):
                    best, best_i = p, i
        if best is None:
            return picked, box
        print(f"[Thumbnail] frame {best_i + 1} has the player (outfit "
              f"match) facing the camera - using it.")
        return best_i, ("face", best["face"])
    except Exception:
        return picked, box
