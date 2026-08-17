"""
The recorder's own reporting.

A missed stream and an offline channel printed the same thing. yt-dlp
reached a live video, was told it was no longer live, exited with "Did
not get any data blocks" - and the recorder folded that back into
ordinary polling and said "Still waiting". A stream that was genuinely
missed scrolled past as normal chatter.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "tools")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# ═════════════════════════════════════════════════════════════════════════════
# A MISSED STREAM AND AN OFFLINE CHANNEL LOOKED THE SAME
#
# yt-dlp reached a live video, was told it was no longer live, and exited
# with "Did not get any data blocks". The recorder folded that back into
# its ordinary polling and printed "Still waiting" - so a stream that was
# genuinely missed scrolled past as normal chatter, and the only way to
# find out was to ask.
# ═════════════════════════════════════════════════════════════════════════════

def test_a_found_stream_that_produced_nothing_is_reported():
    from record_stream import _missed_stream

    tail = ["[youtube] 1BHlv_d4nj4: Video is no longer live. Retrying (1/3)...",
            "[download] Got error: HTTP Error 503: Service Unavailable.",
            "ERROR: Did not get any data blocks"]

    assert _missed_stream(tail), "a missed stream said nothing"


def test_an_offline_channel_is_not_a_missed_stream():
    """This is the normal state between streams and happens on every
    poll. Reporting it would make the real one invisible again."""
    from record_stream import _missed_stream

    assert _missed_stream(["ERROR: The channel is not currently live"]) == ""
    assert _missed_stream(["[youtube] Waiting for video to become available"]) == ""
    assert _missed_stream([]) == ""


def test_an_ended_stream_says_that_rather_than_no_data():
    from record_stream import _missed_stream

    assert "ended" in _missed_stream(["ERROR: This live event has ended."])
