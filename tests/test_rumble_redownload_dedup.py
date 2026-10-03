"""
A stream downloaded again from the channel must not go up twice.

A real run: "*I DONT BELONG HERE* Stackswopo Stream 10/2/26" was already
on Rumble. Downloaded again and dropped in the watch folder as "I DONT
BELONG HERE Stackswopo Stream.mp4", it hashed differently, got the date it
was downloaded and the title '"I DONT BELONG HERE Stackswopo Stream"
10/3/26 Stackswopo Stream' - and the exact-title check let a second
5.3 GB copy go up.
"""

import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.duplicate_checker import DuplicateChecker, title_core  # noqa
from utils.templating import title_from_plain_filename  # noqa

PREFIXES = ("Stackswopo", "StacksWopo", "StackswopoGames", "BinScripts")


def test_the_two_titles_are_the_same_stream():
    assert title_core('"*I DONT BELONG HERE*" 10/2/26 Stackswopo Stream') \
        == title_core('"I DONT BELONG HERE Stackswopo Stream" 10/3/26 '
                      'Stackswopo Stream') == "i dont belong here"


def test_a_redownloaded_stream_is_found_in_the_history(tmp_path):
    checker = DuplicateChecker(str(tmp_path / "h.json"))
    checker.record_platform_result(
        "old-hash", "I DONT BELONG HERE.ts", "rumble",
        "https://rumble.com/v1-i-dont-belong-here.html",
        title='"*I DONT BELONG HERE*" 10/2/26 Stackswopo Stream')

    found = checker.find_platform_title_like(
        "rumble", '"I DONT BELONG HERE Stackswopo Stream" 10/3/26 '
        'Stackswopo Stream', PREFIXES)

    assert found == "https://rumble.com/v1-i-dont-belong-here.html"


def test_a_short_title_is_not_matched_loosely(tmp_path):
    """"GTA RP" is half the streams on the channel."""
    checker = DuplicateChecker(str(tmp_path / "h.json"))
    checker.record_platform_result("a", "a.ts", "rumble", "https://r/1",
                                   title='"GTA RP" 9/1/26 Stackswopo Stream')
    assert checker.find_platform_title_like(
        "rumble", '"GTA RP" 9/2/26 Stackswopo Stream', PREFIXES) is None


def test_the_same_title_weeks_later_is_a_new_stream(tmp_path):
    checker = DuplicateChecker(str(tmp_path / "h.json"))
    checker.record_platform_result(
        "a", "a.ts", "rumble", "https://r/1",
        title='"Back In The City Again" 9/1/26 Stackswopo Stream')
    later = time.time() + 30 * 86400
    assert checker.find_platform_title_like(
        "rumble", '"Back In The City Again" 10/1/26 Stackswopo Stream',
        PREFIXES, now=later) is None


def test_a_failed_upload_is_not_a_match(tmp_path):
    checker = DuplicateChecker(str(tmp_path / "h.json"))
    checker.record_platform_result(
        "a", "a.ts", "rumble", "FAILED: timeout",
        title='"I DONT BELONG HERE" 10/2/26 Stackswopo Stream')
    assert checker.find_platform_title_like(
        "rumble", '"I DONT BELONG HERE" 10/3/26 Stackswopo Stream',
        PREFIXES) is None


def test_the_channel_words_come_off_a_saved_filename():
    assert title_from_plain_filename(
        "I DONT BELONG HERE Stackswopo Stream.mp4", PREFIXES) \
        == "I DONT BELONG HERE"
    # A title that merely ends in "Stream" keeps it.
    assert title_from_plain_filename(
        "Late Night Stream.mp4", PREFIXES) == "Late Night Stream"
