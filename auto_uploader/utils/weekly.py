"""A weekly "best of the week" for STACKSWOPOVODS (@STACKSWOPO10K).

That channel has a strike and cannot earn, but staying active keeps its
viewers, and every upload is another signpost to the full streams on
@wopovod. Fresh streams arrive every week, so this never runs out of
footage the way reposting old videos does.

Each stream's recap (utils/recap.py) is kept - already censored and
music-checked - with a sidecar of where each moment sits in it and how it
scored. About a week after the oldest recap not yet used, the best-scoring
moments from that week's recaps are cut, put in stream order and joined
into one ~18 minute video with chapters, and uploaded. Recaps older than
KEEP_DAYS are then deleted (they are copies; the uploads stay up).

Settings (config.json "weekly"): enabled, token_path, max_minutes, privacy.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from typing import List, Optional

WEEKLY_MAX_S = 18 * 60
WEEKLY_MIN_S = 8 * 60
MIN_STREAMS = 2
WAIT_DAYS = 6.0        # oldest unused recap must be this old before a weekly
WINDOW_DAYS = 8.0      # only recaps this recent go in
KEEP_DAYS = 14.0
DEFAULT_TOKEN = "youtube_compilation_token.json"
WOPOVOD = "https://www.youtube.com/@wopovod"

# Titles that mean strangers on camera (random video chat). Those never go
# public on their own: past channels were removed over that content, so a
# person checks it first.
RISKY_WORDS = ("monkey", "omegle", "ome.tv", "ometv", "chatroulette", "azar",
               "random chat", "video chat")


def risky(stream_title: str) -> bool:
    t = (stream_title or "").lower()
    return any(w in t for w in RISKY_WORDS)


def settings(cfg) -> dict:
    raw = {}
    try:
        with open(os.path.join(cfg.project_root, "config.json"), encoding="utf-8") as f:
            raw = json.load(f).get("weekly") or {}
    except (OSError, ValueError, AttributeError):
        raw = {}
    token = raw.get("token_path") or DEFAULT_TOKEN
    if not os.path.isabs(token):
        token = os.path.join(getattr(cfg, "project_root", "."), token)
    return {"enabled": bool(raw.get("enabled", True)),
            "token_path": token,
            "max_s": float(raw.get("max_minutes", WEEKLY_MAX_S / 60)) * 60,
            "privacy": str(raw.get("privacy", "public"))}


def folder(cfg) -> str:
    return os.path.join(cfg.general.censored_folder, "recaps")


def _ledger_path(cfg) -> str:
    return os.path.join(cfg.general.logs_folder, "weekly.json")


def _ledger(cfg) -> dict:
    try:
        with open(_ledger_path(cfg), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_ledger(cfg, data: dict) -> None:
    with open(_ledger_path(cfg), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def _now(now: Optional[dt.datetime]) -> dt.datetime:
    return now or dt.datetime.now()


def _age_days(made: str, now: dt.datetime) -> float:
    try:
        return (now - dt.datetime.fromisoformat(made)).total_seconds() / 86400
    except (TypeError, ValueError):
        return 0.0


def keep(cfg, path: str, stream_title: str, stream_date: str, recap_url: str,
         full_url: str, moments, now: Optional[dt.datetime] = None) -> str:
    """Move a finished recap into the recaps folder with its sidecar."""
    os.makedirs(folder(cfg), exist_ok=True)
    dest = os.path.join(folder(cfg), os.path.basename(path))
    if os.path.abspath(dest) != os.path.abspath(path):
        shutil.move(path, dest)
    with open(dest + ".json", "w", encoding="utf-8") as f:
        json.dump({"video": os.path.basename(dest), "stream_title": stream_title,
                   "stream_date": stream_date, "recap": recap_url,
                   "full_stream": full_url, "made": _now(now).isoformat(timespec="seconds"),
                   "moments": [[round(float(a), 3), round(float(b), 3), float(sc or 0)]
                               for a, b, sc in moments]}, f, indent=1)
    return dest


def recaps(cfg) -> List[dict]:
    """Every kept recap whose video is still there, oldest first."""
    found = []
    if not os.path.isdir(folder(cfg)):
        return found
    for name in os.listdir(folder(cfg)):
        if not name.endswith(".mp4.json"):
            continue
        try:
            with open(os.path.join(folder(cfg), name), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        info["path"] = os.path.join(folder(cfg), info.get("video") or name[:-5])
        if os.path.exists(info["path"]):
            found.append(info)
    return sorted(found, key=lambda r: r.get("made", ""))


def pick(window: List[dict], max_s: float = WEEKLY_MAX_S) -> list:
    """Best-scoring moments across the week's recaps, up to max_s, in the
    order they happened: [(recap, start, end, score)]."""
    pool = []
    for order, r in enumerate(window):
        for a, b, sc in r.get("moments") or []:
            if b - a >= 4:
                pool.append((float(sc or 0), order, float(a), float(b), r))
    chosen, total = [], 0.0
    for sc, order, a, b, r in sorted(pool, key=lambda p: (-p[0], p[1], p[2])):
        if total + (b - a) > max_s:
            continue
        chosen.append((order, a, b, sc, r))
        total += b - a
    chosen.sort(key=lambda c: (c[0], c[1]))
    return [(r, a, b, sc) for _, a, b, sc, r in chosen]


def _stamp(seconds: float) -> str:
    s = int(seconds)
    h, m = s // 3600, (s % 3600) // 60
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def chapters(groups) -> str:
    """[(start in the weekly, label)] -> YouTube chapter lines, or "" when
    YouTube would not accept them (needs 0:00, 3+, each 10 s or longer)."""
    merged = []
    for start, label in groups:
        if not merged or merged[-1][1] != label:
            merged.append((start, label))
    if len(merged) < 3 or merged[0][0] > 0.5:
        return ""
    if any(b[0] - a[0] < 10 for a, b in zip(merged, merged[1:])):
        return ""
    return "\n".join(f"{_stamp(0 if i == 0 else s)} {label}"
                     for i, (s, label) in enumerate(merged))


def prune(cfg, now: Optional[dt.datetime] = None) -> int:
    """Delete kept recaps older than KEEP_DAYS (copies; the uploads stay)."""
    now, gone = _now(now), 0
    for r in recaps(cfg):
        if _age_days(r.get("made", ""), now) > KEEP_DAYS:
            for p in (r["path"], r["path"] + ".json"):
                try:
                    os.remove(p)
                except OSError:
                    pass
            gone += 1
    return gone


def due(cfg, now: Optional[dt.datetime] = None):
    """(this week's recaps, too-old unused ones) - ([], stale) if not due."""
    now = _now(now)
    used = set(_ledger(cfg).get("used") or [])
    unused = [r for r in recaps(cfg) if r["video"] not in used]
    stale = [r for r in unused if _age_days(r.get("made", ""), now) > WINDOW_DAYS]
    window = [r for r in unused if r not in stale]
    if not window or _age_days(window[0].get("made", ""), now) < WAIT_DAYS:
        return [], stale
    if len(window) < MIN_STREAMS:
        return [], stale
    return window, stale


def _mark_used(cfg, recs, upload: Optional[dict] = None,
               now: Optional[dt.datetime] = None) -> None:
    data = _ledger(cfg)
    data["used"] = sorted(set(data.get("used") or []) | {r["video"] for r in recs})
    if upload:
        data.setdefault("uploads", []).append(upload)
        data["last"] = _now(now).isoformat(timespec="seconds")
    _save_ledger(cfg, data)


def maybe_make(cfg, now: Optional[dt.datetime] = None) -> str:
    """Build and upload the weekly if one is due. The URL, or ""."""
    s = settings(cfg)
    if not s["enabled"]:
        return ""
    window, stale = due(cfg, now)
    if stale:
        _mark_used(cfg, stale)
    try:
        if not window:
            return ""
        if not os.path.exists(s["token_path"]):
            print("[Weekly] Not signed in to STACKSWOPOVODS - skipping the weekly.")
            return ""
        picks = pick(window, s["max_s"])
        total = sum(b - a for _, a, b, _ in picks)
        if total < WEEKLY_MIN_S:
            print(f"[Weekly] Only {total / 60:.1f} min of moments this week - waiting for more.")
            return ""
        from utils.recap import _links_block, build_from, upload

        out = os.path.join(folder(cfg), "weekly_build.mp4")
        print(f"[Weekly] Cutting {len(picks)} moments ({total / 60:.1f} min) "
              f"from {len(window)} streams...")
        try:
            placed = build_from([(r["path"], a, b) for r, a, b, _ in picks], out)
            if not placed:
                print("[Weekly] Could not build the weekly.")
                return ""
            streams = [r for r in window if any(p[0] is r for p in picks)]
            groups = [(start, picks[i][0]["stream_title"].upper()) for start, _, i in placed]
            dates = [r.get("stream_date", "") for r in streams]
            span = dates[0] if dates[0] == dates[-1] else f"{dates[0]} to {dates[-1]}"
            title = f"Stackswopo - BEST OF THE WEEK ({span})"
            lines = [f"▶ Every full stream: {WOPOVOD}", "",
                     "This week's streams:"]
            for r in streams:
                link = r.get("full_stream") or WOPOVOD
                lines.append(f"• {r['stream_title']} ({r.get('stream_date', '')}): {link}")
            marks = chapters(groups)
            if marks:
                lines += ["", marks]
            links = _links_block(cfg)
            if links:
                lines += ["", links]
            lines += ["", "#Stackswopo #GTARP #FunnyMoments"]
            privacy = s["privacy"]
            if any(risky(r.get("stream_title", "")) for r in streams):
                privacy = "private"
                print("[Weekly] Strangers-on-camera stream in this week - uploading "
                      "PRIVATE for you to check before it goes public.")
            url = upload(s["token_path"], out, title, "\n".join(lines),
                         ["Stackswopo", "GTA RP", "best of the week", "funny moments",
                          "Stackswopo stream", "GTA 5"], privacy, WOPOVOD,
                         comment="Every full stream, uncut: " + WOPOVOD)
            print(f"[Weekly] Uploaded to STACKSWOPOVODS ({privacy}): {url}")
            _mark_used(cfg, window, {"url": url, "privacy": privacy, "title": title,
                                     "streams": [r["video"] for r in streams]}, now)
            return url
        finally:
            try:
                os.remove(out)
            except OSError:
                pass
    finally:
        prune(cfg, now)
