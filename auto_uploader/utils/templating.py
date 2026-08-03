"""
Title/description templating - pure string logic, no external dependencies.

Kept dependency-free on purpose so it's trivial to unit-test and to reuse
from main.py, the file watcher, and --dry-run previews without dragging in
any upload-library imports.
"""

import os
import re
from datetime import datetime
from typing import Optional

# Tried in order, most-specific/least-ambiguous first. All assume
# month-first (US) ordering, matching every real filename/title seen on
# this channel so far (e.g. "7/31/26", "09-14-2025").
#
# The separator is `\D` (any single non-digit character), not a literal
# [-/] class: "/" is illegal in Windows filenames, so any tool that
# generates a "M/D/YY"-style filename has to substitute *some* look-alike
# Unicode character for the slash instead (fullwidth solidus, fraction
# slash, en dash, etc. have all been seen in the wild) - trying to
# enumerate every possible look-alike is a losing game, so this just
# accepts any single non-digit character in that position instead.
_DATE_PATTERNS = (
    (re.compile(r"(?<!\d)(\d{4})\D(\d{1,2})\D(\d{1,2})(?!\d)"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),  # YYYY-MM-DD
    (re.compile(r"(?<!\d)(\d{1,2})\D(\d{1,2})\D(\d{4})(?!\d)"), lambda m: (int(m[3]), int(m[1]), int(m[2]))),  # M-D-YYYY
    (re.compile(r"(?<!\d)(\d{1,2})\D(\d{1,2})\D(\d{2})(?!\d)"), lambda m: (2000 + int(m[3]), int(m[1]), int(m[2]))),  # M-D-YY
)


def extract_date_from_filename(filename: str) -> Optional[datetime]:
    """Best-effort date extraction for backlog files (e.g.
    "'!howl' 3-20-26 Stackswopo Kick Stream.mp4" -> 2026-03-20,
    "!howl 2026-03-18_19_32.mp4" -> 2026-03-18). Returns None if nothing
    matches, so callers can fall back to today's date - appropriate for a
    freshly-finished stream being uploaded live, but not for backfilling
    an old VOD where the filename is the only clue to when it aired."""
    for pattern, to_ymd in _DATE_PATTERNS:
        match = pattern.search(filename)
        if match:
            try:
                year, month, day = to_ymd(match)
                return datetime(year, month, day)
            except ValueError:
                continue  # e.g. "13-45-26" matched the shape but isn't a real date
    return None


# Straight and "smart"/curly quote characters, in matched pairs, tried in
# order. Covers filenames like "'!howl' 3-20-26 ..." (single) and
# "\"Back from the dead\" 05/08/26 ..." (double).
_QUOTE_PAIRS = (("'", "'"), ('"', '"'), ("‘", "’"), ("“", "”"))

# yt-dlp's default output templates end in the video ID, e.g.
# "Stackswopo - LOL  NO -YdH8jO6Vjs.mp4" or "Some Title [dQw4w9WgXcQ].mp4".
# YouTube IDs are 11 chars from [A-Za-z0-9_-]; the range is widened
# slightly because other extractors use similar-but-not-identical lengths.
_YTDLP_ID_SUFFIX = re.compile(r"[\s._-]+[\[(]?([A-Za-z0-9_-]{10,12})[\])]?$")


def _looks_like_video_id(token: str) -> bool:
    """Guard against eating a real trailing word like 'Stream' or 'Gameplay'.

    Real IDs are effectively random, so they mix character classes; English
    words of this length almost never do.
    """
    return any(c.isdigit() for c in token) or (
        any(c.isupper() for c in token) and any(c.islower() for c in token)
        and sum(c.isupper() for c in token) > 1
    )


def strip_ytdlp_suffix(stem: str) -> str:
    """Remove a trailing yt-dlp video ID, if there is one."""
    match = _YTDLP_ID_SUFFIX.search(stem)
    if not match:
        return stem
    token = match.group(1)
    if not _looks_like_video_id(token):
        return stem
    remainder = stem[: match.start()].strip()
    # Never strip away everything - a bare ID is better than nothing.
    return remainder if len(remainder) >= 3 else stem


def strip_channel_prefix(stem: str, prefixes) -> str:
    """Drop a leading "<channel> - " that yt-dlp's %(uploader)s adds.

    Only the exact configured channel names are stripped, and only the
    first segment: a title that genuinely contains " - " ("Part 1 - the
    finale") keeps everything after the channel name.
    """
    for prefix in prefixes or ():
        prefix = (prefix or "").strip()
        if not prefix:
            continue
        for separator in (" - ", " – ", " — ", "-"):
            candidate = prefix + separator
            if stem.lower().startswith(candidate.lower()):
                remainder = stem[len(candidate):].strip()
                if remainder:
                    return remainder
    return stem


def extract_title_from_filename(filename: str, channel_prefixes=()) -> Optional[str]:
    """Best-effort stream-title extraction, so unattended runs don't have to
    stop and ask for every file:

    - Quoted filenames use the quoted text: "'!howl' 3-20-26 Stackswopo
      Kick Stream.mp4" -> "!howl".
    - Unquoted filenames use whatever's before the date:
      "!howl 2026-03-19_21_23.mp4" -> "!howl".
    - yt-dlp downloads drop the trailing video ID and the leading channel
      name: "Stackswopo - LOL  NO -YdH8jO6Vjs.mp4" -> "LOL NO".

    Returns None (caller falls back to asking, or to the default title) if
    nothing usable is found - better to ask than to silently title a video
    with something wrong.
    """
    name = os.path.splitext(filename)[0]

    for open_q, close_q in _QUOTE_PAIRS:
        match = re.search(re.escape(open_q) + r"([^" + re.escape(open_q + close_q) + r"]+)" + re.escape(close_q), name)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate

    for pattern, _ in _DATE_PATTERNS:
        match = pattern.search(name)
        if match:
            candidate = name[: match.start()].strip(" _-'\"")
            if candidate:
                return _collapse_spaces(candidate)

    # yt-dlp: only treated as such when a trailing video ID is actually
    # present, so ordinary filenames fall through untouched.
    without_id = strip_ytdlp_suffix(name)
    if without_id != name:
        candidate = strip_channel_prefix(without_id, channel_prefixes)
        candidate = _collapse_spaces(candidate.strip(" _-'\""))
        if candidate:
            return candidate

    return None


def _collapse_spaces(text: str) -> str:
    """yt-dlp preserves double spaces from YouTube titles ("LOL  NO")."""
    return re.sub(r"\s{2,}", " ", text).strip()


def format_date(dt: datetime, date_style: str) -> str:
    """Render `dt` per `date_style`.

    'M/D/YY' matches how the existing Stackswopo channel titles their
    uploads (e.g. "7/31/26", no zero-padding) - built manually because
    strftime's zero-strip flag ('%-m' on Linux/Mac, '%#m' on Windows)
    isn't portable and this tool needs to run on Windows.
    Anything else is treated as a literal strftime format string, so you
    can set e.g. "%Y-%m-%d" in config.json if you'd rather have ISO dates.
    """
    if date_style == "M/D/YY":
        return f"{dt.month}/{dt.day}/{dt.strftime('%y')}"
    return dt.strftime(date_style)


def build_title(stream_title: str, date_str: str, title_format: str) -> str:
    """Fill `title_format` (e.g. '"{title}" {date} Stackswopo Stream')."""
    title = title_format.format(title=stream_title, date=date_str)
    if len(title) > 100:
        # YouTube hard-caps video titles at 100 characters.
        title = title[:100]
    return title


def build_description(template: str, date_str: str, stream_title: str) -> str:
    """Fill [DATE] / [STREAM TITLE] placeholders in a description template."""
    description = template.replace("[DATE]", date_str).replace("[STREAM TITLE]", stream_title)
    if len(description) > 5000:
        # YouTube hard-caps descriptions at 5000 characters.
        description = description[:5000]
    return description
