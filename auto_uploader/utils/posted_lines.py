"""What is already out there, by what is SAID in it - so a new Monkey
draft never reuses a moment BinScripts has posted or an earlier draft took.

Why the words and not the picture: a BinScripts video is heavily edited
(the avatar on one side, the call on the other, stickers, text), so its
frames look nothing like the raw stream. What is said survives every edit.
YouTube's own captions give the posted side for free (no login, no
download of the video); the drafts' own transcripts give the other.

A moment counts as already used when enough of its 4-word runs appear in
what was posted: one shared phrase is chance, a quarter of the moment is
the same conversation.
"""
import json
import os
import re
import subprocess
import sys
import time
from typing import Iterable, Optional, Set

CHANNEL = "https://www.youtube.com/@BinScript/videos"
N = 4                      # words per shingle
MIN_HITS = 4               # shared shingles before a moment can count as used
MIN_SHARE = 0.25           # ... and this share of the moment's own shingles
REFRESH_H = 24.0
_WORD = re.compile(r"[a-z0-9']+")


def words(text: str) -> list:
    return _WORD.findall(str(text or "").lower().replace("\u2019", "'"))


def shingles(text: str, n: int = N) -> Set[str]:
    w = words(text)
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def _python_with_ytdlp() -> str:
    for py in (sys.executable, r"C:\Python314\python.exe"):
        try:
            r = subprocess.run([py, "-m", "yt_dlp", "--version"],
                               capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                return py
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


def _caption_text(path: str) -> str:
    """Plain text of a json3 or vtt caption file."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return ""
    if path.endswith(".json3"):
        try:
            events = json.loads(raw).get("events", [])
        except ValueError:
            return ""
        return " ".join(seg.get("utf8", "") for ev in events for seg in ev.get("segs", []) or [])
    lines = [line for line in raw.splitlines()
             if line.strip() and "-->" not in line and not line.startswith(("WEBVTT", "Kind:", "Language:"))]
    return " ".join(re.sub(r"<[^>]+>", "", line) for line in lines)


def refresh(folder: str, channel: str = CHANNEL, say=print, max_age_h: float = REFRESH_H) -> int:
    """Captions of every public video on the channel into `folder`.
    Only the new ones are fetched. Returns how many videos are known."""
    os.makedirs(folder, exist_ok=True)
    stamp = os.path.join(folder, ".refreshed")
    have = {name.split(".")[0] for name in os.listdir(folder) if name.endswith((".json3", ".vtt"))}
    if have and os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < max_age_h * 3600:
        return len(have)
    py = _python_with_ytdlp()
    if not py:
        say("[Posted] yt-dlp not found - cannot check what BinScripts already posted.")
        return len(have)
    try:
        listed = subprocess.run([py, "-m", "yt_dlp", "--flat-playlist", "--print", "id", channel],
                                capture_output=True, text=True, timeout=180).stdout.split()
    except (OSError, subprocess.SubprocessError):
        listed = []
    for vid in listed:
        if vid in have:
            continue
        say(f"[Posted] Reading what is said in BinScripts video {vid}...")
        try:
            subprocess.run([py, "-m", "yt_dlp", "--skip-download", "--write-auto-subs",
                            "--write-subs", "--sub-langs", "en.*,en", "--sub-format", "json3/vtt",
                            "-o", os.path.join(folder, "%(id)s.%(ext)s"),
                            f"https://www.youtube.com/watch?v={vid}"],
                           capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.SubprocessError):
            continue
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(str(time.time()))
    return len({n.split(".")[0] for n in os.listdir(folder) if n.endswith((".json3", ".vtt"))})


def posted_shingles(folder: str) -> Set[str]:
    out: Set[str] = set()
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        if name.endswith((".json3", ".vtt")):
            out |= shingles(_caption_text(os.path.join(folder, name)))
    return out


USED = "used.json"         # in each draft folder: what that draft's moments say


def drafted_shingles(drafts: str, skip: str = "") -> Set[str]:
    """Everything the earlier drafts' moments say (not `skip`, the one
    being made now - re-running a draft must not exclude itself)."""
    out: Set[str] = set()
    try:
        folders = os.listdir(drafts)
    except OSError:
        return out
    for name in folders:
        path = os.path.join(drafts, name, USED)
        if os.path.normcase(os.path.join(drafts, name)) == os.path.normcase(skip) \
                or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for m in json.load(f).get("moments", []):
                    out |= shingles(m.get("said", ""))
        except (OSError, ValueError):
            continue
    return out


def already_used(said: str, known: Iterable[str]) -> bool:
    mine = shingles(said)
    if not mine:
        return False
    hits = len(mine & set(known))
    return hits >= MIN_HITS and hits >= MIN_SHARE * len(mine)


def write_used(out_dir: str, source: str, moments: list) -> None:
    with open(os.path.join(out_dir, USED), "w", encoding="utf-8") as f:
        json.dump({"source": source, "at": time.time(),
                   "moments": [{"where": m.get("where", ""), "said": m.get("said", "")}
                               for m in moments]}, f, indent=1, ensure_ascii=False)


def backfill_used(drafts: str, say=print) -> int:
    """Drafts made before used.json existed: rebuild it from review.html's
    'said' text so they count too. Returns how many were filled in."""
    filled = 0
    try:
        folders = os.listdir(drafts)
    except OSError:
        return 0
    for name in folders:
        folder = os.path.join(drafts, name)
        page = os.path.join(folder, "review.html")
        if not os.path.isfile(page) or os.path.isfile(os.path.join(folder, USED)):
            continue
        try:
            with open(page, encoding="utf-8") as f:
                html = f.read()
        except OSError:
            continue
        # Only the moments IN the cut: the page lists the left-out ones
        # after the "Left out" heading. "What was said" is each row's last cell.
        import html as _html

        kept_part = re.split(r"<h2[^>]*>\s*Left out", html, maxsplit=1)[0]
        kept_part = kept_part.split("In the cut", 1)[-1]
        said = []
        for row in re.findall(r"<tr>(.*?)</tr>", kept_part, flags=re.S):
            cells = re.findall(r"<td>(.*?)</td>", row, flags=re.S)
            if cells:
                said.append(_html.unescape(re.sub(r"<[^>]+>", " ", cells[-1])))
        if said:
            write_used(folder, name, [{"said": s} for s in said])
            filled += 1
    if filled:
        say(f"[Posted] {filled} earlier draft(s) indexed so their moments are not reused.")
    return filled
