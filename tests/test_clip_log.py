"""
The clip journal: one line each, and a reason on every failure.

The story of a clip used to be spread across console scrollback (gone
when the window closes), publishers.log (every HTTP detail),
posting_state.json (counters, no names) and clip_jobs.json (machine
state). "Did clip 7 reach Instagram, and if not why" took four files.
"""

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.clip_log import MAX_LINES, counts, record, report, tail


def test_a_line_says_what_happened_and_why(tmp_path):
    line = record(str(tmp_path), "ig", "FAIL", "Clip 01 He walked in",
                  "token expired - run --setup-meta")

    assert "ig" in line and "FAIL" in line
    assert "Clip 01" in line
    assert "token expired" in line, "a failure with no reason is not a log"


def test_every_line_fits_one_screen_width(tmp_path):
    record(str(tmp_path), "instagram", "FAIL",
           "Clip 12 " + "a very long clip title " * 10,
           "an extremely long explanation " * 10)

    for line in tail(str(tmp_path)):
        assert len(line) <= 130, "a line that wraps is a line nobody reads"


def test_the_log_is_trimmed_so_it_stays_readable(tmp_path):
    for n in range(MAX_LINES + 250):
        record(str(tmp_path), "ig", "ok", f"Clip {n:02d}", "posted")

    assert len(tail(str(tmp_path), 0)) <= MAX_LINES + 100


def test_todays_tally_counts_each_outcome(tmp_path):
    record(str(tmp_path), "ig", "ok", "Clip 01")
    record(str(tmp_path), "ig", "ok", "Clip 02")
    record(str(tmp_path), "fb", "FAIL", "Clip 01", "token expired")

    tally = counts(str(tmp_path))

    assert tally["ok"] == 2
    assert tally["FAIL"] == 1


def test_an_empty_log_says_so_rather_than_looking_broken(tmp_path):
    assert "No clips logged yet" in report(str(tmp_path))


def test_logging_never_breaks_the_run_it_describes(tmp_path):
    """A journal that can take down a posting run is worse than none."""
    blocked = tmp_path / "not-a-folder"
    blocked.write_text("I am a file, not a directory")

    # Must not raise even though the folder cannot be created.
    assert record(str(blocked), "ig", "ok", "Clip 01")


def test_the_report_shows_recent_lines_and_the_tally(tmp_path):
    record(str(tmp_path), "cut", "ok", "shadows 8/4/26", "20 clips")
    record(str(tmp_path), "ig", "wait", "Clip 01", "spacing - back in 25 min")

    text = report(str(tmp_path))

    assert "shadows" in text and "spacing" in text
    assert "Today:" in text
