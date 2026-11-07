"""The X cut of a clip: the same moment, 16:9, the whole game in view.

Owner 11-04: X, Reels and YouTube clips should differ. On X the clip
plays in the timeline, sideways is native there, and a GTA moment cropped
to a 9:16 strip loses the half of the screen where the car, the cop or
the other player is. The clippers that win on X post the full picture.
Reels / Shorts / TikTok keep the vertical, captioned clip.

render(): beside every vertical clip, from the SOURCE stream (not an
up-scaled crop), same start/end, same jump cuts. Kept in
auto_uploader/clip_wide/ - NOT the watch folder, where it would be taken
for a new clip and posted everywhere.
find(): the X cut for a clip, under whatever name the clip now has
(watch_folder/X.mp4, clip_queue_files/X.mp4, censored/_vertical_X.mp4...).
Never raises: no X cut just means X gets the vertical clip as before.
"""

import os
import re
import subprocess
import time

WIDE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "auto_uploader", "clip_wide")
KEEP_DAYS = 14


def _stem(path: str) -> str:
    stem = os.path.splitext(os.path.basename(str(path or "")))[0]
    stem = re.sub(r"^_?vertical[_\s]+", "", stem, flags=re.I)
    stem = re.sub(r"_CENSORED.*$", "", stem)
    stem = re.sub(r"\.(nomusic|tight|partial).*$", "", stem)
    return stem.strip()


def path_for(clip_path: str) -> str:
    # Its own name: the censor pass names its copy after the input, and
    # the vertical clip's censored copy must never be mistaken for this
    # one (or the other way round - a 16:9 Reel).
    return os.path.join(WIDE_DIR, _stem(clip_path) + " XCUT.mp4")


def find(clip_path: str) -> str:
    try:
        wide = path_for(clip_path)
        return wide if os.path.isfile(wide) and os.path.getsize(wide) > 0 else ""
    except Exception:
        return ""


def _prune() -> None:
    cutoff = time.time() - KEEP_DAYS * 86400
    try:
        for name in os.listdir(WIDE_DIR):
            full = os.path.join(WIDE_DIR, name)
            if os.path.isfile(full) and os.path.getmtime(full) < cutoff:
                os.remove(full)   # our own render, long posted
    except OSError:
        pass


def render(source_path: str, spec, clip_path: str, segments=None,
           jump_cuts: bool = True, silence_s: float = 0.9,
           min_seconds: float = 15.0) -> str:
    """Write the X cut for `clip_path`. Returns its path, or ""."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return ""          # tests never write into the live folder
    try:
        os.makedirs(WIDE_DIR, exist_ok=True)
        _prune()
        out = path_for(clip_path)
        partial = out + ".partial.mp4"
        start = float(spec.start)
        duration = float(spec.end) - start
        if duration <= 0:
            return ""
        vf = ("scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
              "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,"
              # the same light lift the vertical clips get
              "eq=contrast=1.04:saturation=1.10")
        args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-accurate_seek", "-ss", f"{start:.3f}", "-i", source_path,
                "-t", f"{duration:.3f}", "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                "-pix_fmt", "yuv420p", "-r", "30",
                "-af", "aresample=48000", "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                "-movflags", "+faststart", partial]
        done = subprocess.run(args, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=900)
        if done.returncode != 0 or not os.path.isfile(partial):
            try:
                os.remove(partial)
            except OSError:
                pass
            return ""
        os.replace(partial, out)
        try:
            from autoreel.loudness import level
            level(out)
        except Exception:
            pass
        if jump_cuts and segments:
            from autoreel.clip_maker import jump_cut

            jump_cut(out, segments, spec, min_silence_s=silence_s,
                     min_seconds=min_seconds)
        print(f"[Clips] Clip {getattr(spec, 'index', 0):02d}: X cut (16:9, "
              f"full game view) ready.")
        return out
    except Exception as exc:
        print(f"[Clips] no X cut for {os.path.basename(str(clip_path))}: {exc}")
        return ""
