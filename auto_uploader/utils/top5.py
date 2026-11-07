"""Weekly "Top 5" Short - the format that tops the Stackswopo clip charts
(research 11-04: Top5Daily "Ranking funniest StacksWopo moments 💀 // last one
is hilarious" 2.8M views; Rankviperr "Top 5 StacksWopo ... Clips" 31K at 1.3K
subs; Caxeronn "Stackswopo FUNNIEST Moments" 207K).

Built ONLY from clips that already went out on a strict platform this week
(logs/post_refs.jsonl: YouTube Shorts / Instagram / TikTok), so every kid,
slur and gambling check has already passed. Five ~11 s pieces, best score
last ("#1"), a big rank number on each, <= 59 s, 9:16. Dropped into the
watch folder like any clip, so it is censored and posted per platform by
the normal queue. Once a week (ledger logs/top5.json).
"""

import json
import os
import subprocess
import tempfile
import time

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(_APP, "logs", "top5.json")
EVERY_S = 7 * 86400
PIECE_S = 11.0
STRICT = {"youtube_shorts", "instagram", "tiktok", "buffer_tiktok", "facebook"}
FONT = "C\\:/Windows/Fonts/impact.ttf"


def _length(path: str) -> float:
    try:
        return float(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             path], capture_output=True, text=True, timeout=60).stdout.strip() or 0)
    except Exception:
        return 0.0


def _find(clip_key: str, folders) -> str:
    for folder in folders:
        try:
            for name in os.listdir(folder):
                if name.lower() == clip_key and os.path.isfile(os.path.join(folder, name)):
                    return os.path.join(folder, name)
        except OSError:
            continue
    return ""


def picks(folders, days: float = 7, n: int = 5) -> list:
    """[(path, title, score)] - best n clips that went out on a strict platform."""
    from utils.post_refs import recent
    from utils.clip_queue import _clip_score

    seen, out = set(), []
    for item in recent(days * 24):
        if item.get("platform") not in STRICT or item["clip"] in seen:
            continue
        seen.add(item["clip"])
        path = _find(item["clip"], folders)
        if path and _length(path) >= 8:
            out.append((path, item.get("title", ""), _clip_score(path)))
    out.sort(key=lambda t: t[2], reverse=True)
    best = out[:n]
    return list(reversed(best))          # #5 first ... #1 last


def build(chosen, out_path: str) -> bool:
    """Five pieces with a rank badge, joined, 1080x1920, <= 59 s."""
    work = tempfile.mkdtemp(prefix="top5_")
    parts = []
    try:
        for i, (path, _title, _score) in enumerate(chosen):
            rank = len(chosen) - i
            length = _length(path)
            start = max(0.0, length - PIECE_S - 1.0)
            part = os.path.join(work, f"p{i}.mp4")
            vf = ("scale=1080:1920:force_original_aspect_ratio=decrease,"
                  "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,"
                  # Top-right of the picture: the clip's own hook text sits
                  # at the top of the frame and its captions at the bottom.
                  f"drawtext=fontfile='{FONT}':text='#{rank}':fontsize=150:fontcolor=yellow:"
                  "borderw=10:bordercolor=black:x=w-tw-40:y=690")
            r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t",
                                f"{PIECE_S:.2f}", "-i", path, "-vf", vf, "-af",
                                "loudnorm=I=-14:LRA=11:TP=-1.5,aresample=44100,aformat=channel_layouts=stereo",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                                "-c:a", "aac", "-b:a", "160k", part],
                               capture_output=True, text=True, timeout=600)
            if r.returncode != 0:
                return False
            parts.append(part)
        listing = os.path.join(work, "list.txt")
        with open(listing, "w", encoding="utf-8", newline="") as f:
            f.write("".join(f"file '{p.replace(os.sep, '/')}'\n" for p in parts))
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i",
                            listing, "-t", "58.5", "-c", "copy", "-movflags", "+faststart",
                            out_path], capture_output=True, text=True, timeout=600)
        return r.returncode == 0 and os.path.isfile(out_path)
    finally:
        for p in parts + [os.path.join(work, "list.txt")]:
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(work)
        except OSError:
            pass


def maybe_make(watch_folder: str, folders, say=print, now: float = 0.0) -> str:
    """Make this week's Top 5 if one is due and 5 clips qualify. Path or ""."""
    now = now or time.time()
    try:
        last = json.load(open(LEDGER, encoding="utf-8")).get("last", 0)
    except (OSError, ValueError):
        last = 0
    if now - last < EVERY_S:
        return ""
    chosen = picks(folders)
    if len(chosen) < 5:
        return ""
    n = 1
    try:
        n = int(json.load(open(LEDGER, encoding="utf-8")).get("n", 0)) + 1
    except (OSError, ValueError):
        pass
    name = f"Top 5 Stackswopo Moments #{n} - Clip 01"
    out = os.path.join(watch_folder, name + ".mp4")
    if not build(chosen, out):
        say("[Top5] could not build this week's Top 5.")
        return ""
    gta = sum(1 for p, t, s in chosen if "johnny cox" in (t or "").lower()) >= 3
    title = (f"Ranking Johnny Cox's funniest moments this week 💀 #1 is crazy | Stackswopo GTA RP"
             if gta else "Ranking Stackswopo's funniest moments this week 💀 #1 is crazy")
    with open(os.path.splitext(out)[0] + ".txt", "w", encoding="utf-8") as f:
        f.write(title + "\n")
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    json.dump({"last": now, "n": n, "clips": [p for p, _t, _s in chosen]},
              open(LEDGER, "w", encoding="utf-8"), indent=1)
    say(f"[Top5] made {os.path.basename(out)} - it goes out like any clip.")
    return out
