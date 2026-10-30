"""The shared YouTube quota day, counted here so wopovod is never starved.

One Google project, 10,000 units a day, shared by all four channels; one
upload (videos.insert) costs 1,600. On 10-29 two full streams + three
Shorts + a recap + a story came to 11,200 - more than the day holds - and a
full stream had to wait for the reset. Every insert is written down here,
and the extras (Shorts, recaps, story episodes, weekly) ask allow_extra()
first: they go only while RESERVE units are still left for a wopovod upload.

The day resets at midnight Pacific time, like YouTube's own counter.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading

DAY_LIMIT = 10_000
INSERT = 1_600
RESERVE = 1_600          # always kept for one full stream on wopovod
_LOCK = threading.Lock()
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(_HERE, "logs", "quota.json")


def _pacific_now() -> dt.datetime:
    utc = dt.datetime.now(dt.timezone.utc)
    # US Pacific: UTC-7 from the second Sunday of March to the first Sunday
    # of November, else UTC-8 (no tz database needed on a fresh Windows).
    year = utc.year
    march = dt.datetime(year, 3, 8, 10, tzinfo=dt.timezone.utc)
    march += dt.timedelta(days=(6 - march.weekday()) % 7)
    nov = dt.datetime(year, 11, 1, 9, tzinfo=dt.timezone.utc)
    nov += dt.timedelta(days=(6 - nov.weekday()) % 7)
    offset = -7 if march <= utc < nov else -8
    return utc + dt.timedelta(hours=offset)


def today() -> str:
    return _pacific_now().strftime("%Y-%m-%d")


def seconds_to_reset() -> int:
    now = _pacific_now()
    tomorrow = (now + dt.timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def spent(path: str = LEDGER) -> int:
    return int((_load(path).get(today()) or {}).get("units", 0))


def record(units: int, what: str = "", path: str = LEDGER) -> None:
    """Write down units spent today. Never raises."""
    try:
        with _LOCK:
            data = _load(path)
            day = data.setdefault(today(), {"units": 0, "uses": []})
            day["units"] = int(day.get("units", 0)) + int(units)
            day.setdefault("uses", []).append(f"{_pacific_now():%H:%M} {units} {what}"[:120])
            for old in sorted(data)[:-7]:          # a week is plenty
                data.pop(old, None)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, path)
    except Exception:
        pass


def allow_extra(cost: int = INSERT, path: str = LEDGER) -> bool:
    """May a non-wopovod upload spend `cost` now and still leave RESERVE?"""
    return spent(path) + cost + RESERVE <= DAY_LIMIT
