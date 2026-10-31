"""Numbered series titles, the way the Stackswopo videos that get views
are named (searched 10-31):

  "Stackswopo Funny Moments #80"                     97k
  "1 hour of stackswopo funniest moments - Johnny Cox #72"   416k
  "Johnny Cox Visits Chicago In GTA RP"              2.4M
  "Stackswopo Trolling Monkey App #09"               208k

against ours: "Stackswopo - TOPIC BOUL - 10-30-26 (BEST MOMENTS)" - a
stream name nobody searches. A series number tells a viewer there is a
next one to binge, and "Johnny Cox" (his GTA RP character, a name people
search on its own) goes in when the stream is mostly GTA.

Counters live in logs/series.json, one per series, so numbers never repeat.
"""
from __future__ import annotations

import json
import os
import threading

_LOCK = threading.Lock()
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(_HERE, "logs", "series.json")
GTA_SHARE = 0.5      # this share of the stream looked at is GTA -> "Johnny Cox"


def next_number(series: str, start: int = 1, path: str = LEDGER) -> int:
    """The next number in a series (and remember it)."""
    with _LOCK:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        n = max(int(data.get(series, start - 1)) + 1, start)
        data[series] = n
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, path)
        except OSError:
            pass
        return n


def is_gta(kinds) -> bool:
    """kinds: what each sampled minute of the stream looked like."""
    seen = [k for k in (kinds or []) if k]
    return bool(seen) and sum(1 for k in seen if k == "gta") / len(seen) >= GTA_SHARE


def recap_title(n: int, gta: bool, hook: str = "") -> str:
    base = (f"Johnny Cox GTA RP Funny Moments #{n}" if gta
            else f"Stackswopo Funny Moments #{n}")
    hook = " ".join(str(hook or "").split())
    return f"{base} - {hook}" if hook and len(base) + len(hook) + 3 <= 95 else base


def weekly_title(n: int, gta: bool) -> str:
    return (f"Stackswopo Best of the Week #{n} | Johnny Cox GTA RP Funny Moments" if gta
            else f"Stackswopo Best of the Week #{n} | Funny Moments")


HOUR_S = 50 * 60      # this long or more -> "1 Hour of ..." (the format that gets views)


def hour_title(n: int, gta: bool) -> str:
    return (f"1 Hour of Stackswopo Funny Moments #{n} | Johnny Cox GTA RP" if gta
            else f"1 Hour of Stackswopo Funny Moments #{n}")


def tags(gta: bool, base=()) -> list:
    out = list(base)
    for t in (["Johnny Cox", "Johnny Cox GTA RP", "Stackswopo Johnny Cox"] if gta else []) + \
             ["Stackswopo funny moments", "Stackswopo"]:
        if t not in out:
            out.append(t)
    return out[:15]
