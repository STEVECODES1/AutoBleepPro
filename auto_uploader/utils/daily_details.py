"""The daily report's attachment: everything, organised (owner 11-05:
"I was expecting the words that were muted or taken out ... well
organized and better" - the .txt had been a copy of the message).

Sections, last 24 h:
  1. Full streams - title, links
  2. Muted words - every stream and clip: how many, which, when
  3. Posted clips - per clip, every platform it reached, with links
  4. Held back - per clip, why, from where
  5. Failed - per clip and platform, the last error
  6. Waiting for a slot - per platform
Bad words are starred by discord_safe before it is sent.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter, defaultdict

DAY = 86400
NAMES = {"youtube_shorts": "Shorts", "instagram": "IG", "facebook": "FB",
         "buffer_x": "X", "buffer_tiktok": "TikTok", "rumble": "Rumble",
         "x": "X", "tiktok": "TikTok", "zernio_tiktok": "TikTok"}
LINE = "-" * 60


def _jsonl(path: str) -> list:
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return rows


def _json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _clock(seconds: float) -> str:
    s = int(seconds)
    return (f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600
            else f"{s // 60}:{s % 60:02d}")


def _name(file: str) -> str:
    import re

    stem = os.path.splitext(os.path.basename(file))[0]
    stem = re.sub(r"^_?vertical[_\s]+", "", stem, flags=re.I)
    stem = re.sub(r"_CENSORED.*$", "", stem)
    stem = re.sub(r"\.(nomusic|tight).*$", "", stem)
    stem = re.sub(r"\s*XCUT$", " (X cut)", stem)
    return stem


def _plat(p: str) -> str:
    return NAMES.get(p, p)


def muted_section(rows: list, now: float) -> list:
    out = ["2) MUTED WORDS", LINE]
    recent = [r for r in rows if now - float(r.get("at", 0) or 0) < DAY]
    if not recent:
        return out + ["Nothing was muted in the last 24 h.", ""]
    # newest pass per file wins (a clip censored per platform repeats)
    by_file: dict = {}
    for r in recent:
        key = (_name(r.get("file", "")), r.get("scope", ""))
        by_file[key] = r
    streams = [(k, r) for k, r in by_file.items() if " - Clip " not in k[0]
               and "clip" not in k[0].lower()]
    clips = [(k, r) for k, r in by_file.items() if (k, r) not in streams]
    total = Counter()
    for (name, scope), r in streams:
        words = r.get("words") or []
        total.update(w["word"].lower() for w in words)
        out.append(f"FULL STREAM: {name}  [{scope}]  - {len(words)} muted")
        counts = Counter(w["word"].lower() for w in words)
        out.append("  Most: " + ", ".join(f"{w} x{c}" for w, c in counts.most_common(12)))
        for w in words[:60]:
            out.append(f"    {_clock(w['t']):>8}  {w['word']}  ({w.get('cat', '')})")
        if len(words) > 60:
            out.append(f"    ... and {len(words) - 60} more")
        out.append("")
    if clips:
        out.append("CLIPS (word at time in the clip):")
        for (name, scope), r in sorted(clips):
            words = r.get("words") or []
            total.update(w["word"].lower() for w in words)
            listed = ", ".join(f"{w['word']} {_clock(w['t'])}" for w in words[:15])
            more = f" +{len(words) - 15}" if len(words) > 15 else ""
            out.append(f"  - {name}  [{scope}]  {len(words)} muted: {listed}{more}")
        out.append("")
    if total:
        out.append("ALL TOGETHER: " + ", ".join(f"{w} x{c}" for w, c in total.most_common(15)))
    return out + [""]


def build(now: float, logs_folder: str, project_root: str,
          stream_lines: list, disk_line: str, needs_line: str) -> str:
    out = [f"AUTOBLEEP DAILY REPORT - {time.strftime('%a %b %d %Y, %I:%M %p', time.localtime(now))}",
           "=" * 60, "", "1) FULL STREAMS", LINE]
    out += [l.replace("**", "") for l in stream_lines] or ["None in the last 24 h."]
    out.append("")

    try:
        from utils.censor import MUTES_LOG
    except Exception:
        MUTES_LOG = os.path.join(logs_folder, "mutes.jsonl")
    out += muted_section(_jsonl(MUTES_LOG), now)

    data = _json(os.path.join(project_root, "clip_jobs.json"), {})
    jobs = data.get("jobs", {}) if isinstance(data, dict) else {}
    jobs = [j for j in (jobs.values() if isinstance(jobs, dict) else jobs)
            if isinstance(j, dict) and now - float(j.get("updated_at", 0) or 0) < DAY]
    links = _json(os.path.join(logs_folder, "post_links.json"), {})

    posted = defaultdict(list)
    held = defaultdict(lambda: [set(), ""])
    failed = {}
    waiting = Counter()
    for j in jobs:
        name, plat = _name(j.get("clip_path", "")), _plat(j.get("platform", ""))
        why = str(j.get("last_error") or "")
        state = j.get("state")
        if state == "done":
            posted[name].append(plat)
        elif state == "failed" and why.startswith("held"):
            held[name][0].add(plat)
            held[name][1] = why.replace("held: ", "")
        elif state == "failed":
            failed[(name, plat)] = why[:160] or "no reason recorded"
        elif state == "blocked":
            waiting[plat] += 1

    out += ["3) POSTED CLIPS", LINE]
    if not posted:
        out.append("None.")
    for name in sorted(posted):
        entry = links.get(name.lower() + ".mp4") or {}
        out.append(f"- {entry.get('title') or name}")
        out.append(f"    on: {', '.join(sorted(set(posted[name])))}   ({name})")
        for where, url in sorted((entry.get("links") or {}).items()):
            out.append(f"    {_plat(where)}: {url}")
    out += ["", "4) HELD BACK (not posted there - Rumble still gets it)", LINE]
    if not held:
        out.append("None.")
    shown = 0
    for name in sorted(held):
        plats, why = held[name]
        plats = plats - set(posted.get(name, ()))   # an old hold since lifted
        if plats:
            shown += 1
            out.append(f"- {name}: {why}  -> held from {', '.join(sorted(plats))}")
    if held and not shown:
        out.append("None.")
    out += ["", "5) FAILED", LINE]
    if not failed:
        out.append("None.")
    for (name, plat), why in sorted(failed.items()):
        out.append(f"- {name} on {plat}: {why}")
    out += ["", "6) WAITING FOR A SLOT", LINE,
            (", ".join(f"{p} {c}" for p, c in waiting.most_common()) or "None."),
            "", disk_line, needs_line]
    return "\n".join(out)
