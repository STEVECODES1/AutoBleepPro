"""The TikTok copy of a clip: no "FULL STREAMS UNCUT ON RUMBLE /
rumble.com/user/BinScripts" banner (11-07).

@wopocl1ps had 0 views on every post. TikTok keeps content that sends
people to another platform (a URL burned into the picture) out of the For
You feed - so every TikTok post carried the one thing that guarantees
nobody sees it. Shorts / Reels / Facebook keep the banner (that is where it
was asked for); TikTok gets the clean twin, its link lives in the bio.

How: the clip is rendered once WITHOUT the banner (that file is kept here
as the clean twin), then the banner is drawn on in a quick second pass to
make the normal clip. find() maps any name the clip has to its twin.
"""

import os
import re
import shutil
import subprocess
import time

CLEAN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "auto_uploader", "clip_clean")
KEEP_DAYS = 14
NO_BANNER_PLATFORMS = frozenset({"buffer_tiktok", "tiktok", "zernio_tiktok",
                                 "postproxy_tiktok"})


def _stem(path: str) -> str:
    stem = os.path.splitext(os.path.basename(str(path or "")))[0]
    stem = re.sub(r"^_?vertical[_\s]+", "", stem, flags=re.I)
    stem = re.sub(r"_CENSORED.*$", "", stem)
    stem = re.sub(r"\.(nomusic|tight|partial).*$", "", stem)
    return stem.strip()


def path_for(clip_path: str) -> str:
    return os.path.join(CLEAN_DIR, _stem(clip_path) + " CLEAN.mp4")


def find(clip_path: str) -> str:
    try:
        p = path_for(clip_path)
        return p if os.path.isfile(p) and os.path.getsize(p) > 0 else ""
    except Exception:
        return ""


def _prune() -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for name in os.listdir(CLEAN_DIR):
            full = os.path.join(CLEAN_DIR, name)
            if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                os.remove(full)
    except OSError:
        pass


def keep_and_brand(clip_path: str, banner_filter: str) -> bool:
    """`clip_path` was rendered without the banner: keep a copy as the
    clean twin, then burn the banner onto clip_path itself. On any failure
    clip_path is left as it is (clean beats missing)."""
    try:
        os.makedirs(CLEAN_DIR, exist_ok=True)
        _prune()
        twin = path_for(clip_path)
        shutil.copyfile(clip_path, twin)
        if not banner_filter:
            return False
        tmp = clip_path + ".brand.mp4"
        done = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", clip_path,
             "-vf", banner_filter, "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy",
             "-movflags", "+faststart", tmp],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
        if done.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False
        os.replace(tmp, clip_path)
        return True
    except Exception as exc:
        print(f"[Clips] no clean TikTok copy ({exc})")
        return False
