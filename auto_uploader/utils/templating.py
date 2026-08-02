"""
Title/description templating - pure string logic, no external dependencies.

Kept dependency-free on purpose so it's trivial to unit-test and to reuse
from main.py, the file watcher, and --dry-run previews without dragging in
any upload-library imports.
"""

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
