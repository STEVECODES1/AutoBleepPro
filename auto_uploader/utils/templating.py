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


# Names that are what a tool called a file, not what anybody called the
# stream. Titling a video "output" is worse than admitting we do not know.
_MACHINE_NAMES = frozenset({
    "video", "output", "out", "final", "temp", "tmp", "untitled", "new",
    "recording", "record", "capture", "clip", "stream", "vod", "download",
    "movie", "render", "export", "test", "sample", "footage",
})

# Machinery inside a filename that is never part of a title.
_NAME_NOISE = (
    re.compile(r"\[[a-z0-9_-]{6,}\]", re.I),      # [v70rbpc]
    re.compile(r"\b\d{8}[\s_-]+\d{6}\b"),         # 20250914 204409
    re.compile(r"\bvid[\s_-]*\d{6,}\b", re.I),    # VID_20240101
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"^\s*_?vertical[_\s]+", re.I),
    re.compile(r"[-\s]+clip\s*\d+\s*$", re.I),
    re.compile(r"\b(1080p|720p|4k|60fps|30fps|mp4|hevc|h264)\b", re.I),
)


def title_from_plain_filename(filename: str) -> Optional[str]:
    """The filename itself, when a PERSON clearly typed it.

    extract_title_from_filename only answers when it finds a pattern - a
    quoted section, a date, a yt-dlp id. That was the right caution when
    filenames looked like "video_2026_08_15.mp4". It is the wrong answer
    for "WIFI COOKED.mp4", which was named by hand and IS the title: the
    tool threw it away and uploaded a stream called "Gaming Stream".

    So this is the last look before giving up. It accepts a name only
    when what is left after the machinery comes out reads like words
    somebody wrote, and returns None otherwise - because a wrong title
    is published and a missing one is only a default.
    """
    name = os.path.splitext(os.path.basename(filename or ""))[0]
    name = name.replace("_", " ")
    for pattern in _NAME_NOISE:
        name = pattern.sub(" ", name)
    name = _collapse_spaces(name).strip(" -.")
    if not name:
        return None

    letters = sum(1 for ch in name if ch.isalpha())
    # Two letters is the floor - "GG" is a plausible stream title, "3" is
    # not - and letters have to be most of what is there, so a timestamp
    # with a stray character in it cannot pass.
    if letters < 2 or letters < len(name.replace(" ", "")) * 0.5:
        return None
    if all(word.lower() in _MACHINE_NAMES for word in name.split()):
        return None
    return name


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


# YouTube hard-caps video titles here, and Rumble is not far off.
MAX_TITLE_CHARS = 100

# A timestamp the streamer left on the end of their own stream name. It
# is already going to appear as {date}, and carrying it twice is what
# pushed a title past the cap.
_TRAILING_STAMP = re.compile(
    r"[\s\-|]*\(?\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b"      # 2026-08-11
    r"(?:[\s,]+\d{1,2}[:_.]\d{2}(?::\d{2})?)?\)?\s*$"     # 14:00 / 06_16
)


# Punctuation left stranded when a placeholder resolved to nothing.
# A format of "{title} - {date} - Stackswopo Stream" with no date
# published 'Culture' - - Stackswopo Stream.
_STRANDED = re.compile(r"([-|,])(?:\s*[-|,])+")


def _fill(title_format: str, stream_title: str, date_str: str) -> str:
    """Substitute the two placeholders, literally.

    str.format() would read braces in the DATA as placeholders of its
    own: a stream called "drop the {beat}" raises KeyError and takes the
    whole upload with it, and any other token someone puts in the format
    string does the same. Nothing here needs format()'s power.

    Literal on purpose - `_tidy` below is what deals with an empty
    placeholder, and only where a finished title is being returned. The
    length arithmetic in build_title measures this raw form.
    """
    return (title_format
            .replace("{title}", stream_title)
            .replace("{date}", date_str))


def _tidy(title: str, stream_title: str, date_str: str) -> str:
    """Take a placeholder's punctuation with it when it resolved to
    nothing.

    The separators in a format string sit BETWEEN two things. With one
    of them missing they are just noise, and they get published:
    "{title} - {date} - Stackswopo Stream" with no date went up as

        'Culture' - - Stackswopo Stream

    Only when something IS missing, so a stream whose name really does
    contain "--" keeps it.
    """
    if stream_title.strip() and date_str.strip():
        return title
    return _collapse_spaces(_STRANDED.sub(r"\1", title)).strip(" -|,")


def strip_trailing_stamp(stream_title: str) -> str:
    """Drop a date/time the stream name already ends with.

    The recorder names a stream after what the platform called it, and
    that is often "<name> 2026-08-11 14:00". The template adds the date
    itself, so keeping this means saying it twice AND spending twenty of
    the hundred characters a title gets.
    """
    trimmed = _TRAILING_STAMP.sub("", (stream_title or "").strip())
    return trimmed.strip(" -|,") or (stream_title or "").strip()


def build_title(stream_title: str, date_str: str, title_format: str) -> str:
    """Fill `title_format` (e.g. '"{title}" {date} Stackswopo Stream').

    The STREAM NAME is what gets shortened when the result is too long,
    never the format around it. Truncating the finished string instead
    published `"stackswopo + gta D10 johnny cox + Lifestyle RP + Windy
    City + Cuffem + Adin Ross 2026-08-11 14:00"` - the date and the
    "Stackswopo Stream" that identifies the channel both cut clean off
    the end, on a title that was nothing but the raw stream name.
    """
    stream_title = strip_trailing_stamp(stream_title)
    title = _fill(title_format, stream_title, date_str)
    if len(title) <= MAX_TITLE_CHARS:
        return _tidy(title, stream_title, date_str)

    # How much room the name actually has, once the format has taken its
    # share. Measured rather than assumed, so editing title_format in
    # config cannot silently break this.
    overhead = len(_fill(title_format, "", date_str))
    budget = MAX_TITLE_CHARS - overhead
    if budget < 12:
        # A format with no room left for a name at all: nothing sensible
        # to shorten, so cut the whole thing and keep it valid.
        return title[:MAX_TITLE_CHARS]

    shortened = stream_title[:budget].rsplit(" ", 1)[0].rstrip(" ,-+|")
    if not shortened:
        shortened = stream_title[:budget].rstrip(" ,-+|")
    # Tidied against the ORIGINAL name: shortening is not what makes a
    # placeholder empty, and a name cut to nothing still leaves the same
    # stranded separators behind.
    return _tidy(_fill(title_format, shortened, date_str),
                 shortened, date_str)


def build_description(template: str, date_str: str, stream_title: str) -> str:
    """Fill [DATE] / [STREAM TITLE] placeholders in a description template."""
    description = template.replace("[DATE]", date_str).replace("[STREAM TITLE]", stream_title)
    if len(description) > 5000:
        # YouTube hard-caps descriptions at 5000 characters.
        description = description[:5000]
    return description
