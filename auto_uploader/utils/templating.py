"""
Title/description templating - pure string logic, no external dependencies.

Kept dependency-free on purpose so it's trivial to unit-test and to reuse
from main.py, the file watcher, and --dry-run previews without dragging in
any upload-library imports.
"""

from datetime import datetime


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
