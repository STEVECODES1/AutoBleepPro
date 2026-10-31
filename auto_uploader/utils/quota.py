"""The shared YouTube quota day, counted here so wopovod is never starved.

One Google project, 10,000 units a day, shared by all four channels; one
upload (videos.insert) costs 1,600. On 10-29 two full streams + three
Shorts + a recap + a story came to 11,200 - more than the day holds - and a
full stream had to wait for the reset. Every insert is written down here,
and the extras (Shorts, recaps, story episodes, weekly) ask allow_extra()
first: they go only while RESERVE units are still left for a wopovod upload.

The day resets at midnight Pacific time, like YouTube's own counter.

A channel signed in through a SECOND Google project (its own
client_secrets file, another client_id) has its own 10,000 a day
(10-31: the CLIPS channel, so Shorts stop competing with wopovod). Those
uploads are counted under that project and never touch the main day.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading

DAY_LIMIT = 10_000
# 10-31: an upload costs ~100 units since 2025-12-04 (was ~1,600), and since
# 2026-06-01 videos.insert has its own quota bucket (developers.google.com/
# youtube/v3/revision_history) - about 100 uploads a day, not 6. The count
# stays as a safety net; the channel's own daily upload limit is the real one.
INSERT = 100
RESERVE = 400            # always kept for wopovod full streams
_LOCK = threading.Lock()
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(_HERE, "logs", "quota.json")
MAIN = "main"
MAIN_SECRETS = os.path.join(_HERE, "client_secrets.json")


def _client_id(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        inner = data.get("installed") or data.get("web") or data
        return str(inner.get("client_id") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def project_of(secrets_path: str, main_secrets: str = "") -> str:
    """MAIN, or the other project's client_id when the file is another
    Google project's."""
    if not secrets_path:
        return MAIN
    mine = _client_id(secrets_path)
    main = _client_id(main_secrets or MAIN_SECRETS)
    if not mine or not main or mine == main:
        return MAIN
    return mine.split("-")[0] or mine


def project_for_route(route: str, config_path: str = "") -> str:
    """The project a clip route uploads through (only youtube_shorts can
    have its own)."""
    if route != "youtube_shorts":
        return MAIN
    try:
        with open(config_path or os.path.join(_HERE, "config.json"), encoding="utf-8") as f:
            sec = (json.load(f).get("youtube_shorts") or {}).get("client_secrets_path") or ""
    except (OSError, ValueError, AttributeError):
        return MAIN
    if sec and not os.path.isabs(sec):
        sec = os.path.normpath(os.path.join(_HERE, sec))
    return project_of(sec) if sec else MAIN


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


def spent(path: str = LEDGER, project: str = MAIN) -> int:
    day = _load(path).get(today()) or {}
    if project and project != MAIN:
        return int((day.get("projects") or {}).get(project, 0))
    return int(day.get("units", 0))


def record(units: int, what: str = "", path: str = LEDGER, project: str = MAIN) -> None:
    """Write down units spent today. Never raises."""
    if path == LEDGER and os.environ.get("PYTEST_CURRENT_TEST"):
        return          # tests never touch the real ledger
    try:
        with _LOCK:
            data = _load(path)
            day = data.setdefault(today(), {"units": 0, "uses": []})
            if project and project != MAIN:
                other = day.setdefault("projects", {})
                other[project] = int(other.get(project, 0)) + int(units)
                what = f"[{project}] {what}"
            else:
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


def allow_extra(cost: int = INSERT, path: str = LEDGER, project: str = MAIN) -> bool:
    """May a non-wopovod upload spend `cost` now and still leave RESERVE?
    On its own project there is no wopovod to keep anything for."""
    if project and project != MAIN:
        return spent(path, project) + cost <= DAY_LIMIT
    return spent(path) + cost + RESERVE <= DAY_LIMIT
