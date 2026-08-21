"""One stream is often two shows.

A four-hour stream that opened on Monkey and then played GTA went up as a
single four-hour video. An account reposting the same stream split it -
the GTA run as its own upload, the Monkey run as another - and the GTA
one took 1.21K views while the buried Monkey hour took none, because
nobody looking for Monkey content scrolls to hour three of a GTA VOD.

What this must not do is chop a stream into confetti, or split one that
was one thing all along.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel import vod_segments as vs  # noqa: E402
from autoreel.vod_segments import (MIN_SEGMENT_SECONDS, Segment,  # noqa: E402
                                   describe, segments_for, worth_splitting)


def _looker(plan):
    """plan: [(kind, minutes)] read along the timeline."""
    timeline = []
    for kind, minutes in plan:
        timeline += [kind] * int(minutes)

    def look(source, start=0.0, duration=6.0):
        index = int(start // 60)
        return timeline[index] if index < len(timeline) else timeline[-1]

    return look, len(timeline) * 60.0


def _segments(plan, **kw):
    look, span = _looker(plan)
    return segments_for("video.ts", span=span, look=look, **kw)


def test_the_real_stream_splits_where_the_show_changed(tmp_path,
                                                       monkeypatch):
    """58 minutes of Monkey then four hours of GTA - which is exactly the
    stream somebody else split and got 1.21K views on."""
    monkeypatch.setattr(os.path, "isfile", lambda p: True)

    parts = _segments([("monkey", 58), ("gta", 244)])

    assert len(parts) == 2
    assert parts[0].kind == "monkey"
    assert parts[1].kind == "gta"
    assert 55 * 60 <= parts[0].duration <= 61 * 60


def test_a_stream_that_was_one_thing_stays_one_video(monkeypatch):
    """The common answer, and not a failure."""
    monkeypatch.setattr(os.path, "isfile", lambda p: True)

    parts = _segments([("gta", 180)])

    assert len(parts) == 1
    assert not worth_splitting(parts)


def test_a_short_stretch_is_not_its_own_upload(monkeypatch):
    """Five minutes of menus is not a show."""
    monkeypatch.setattr(os.path, "isfile", lambda p: True)

    parts = _segments([("gta", 120), ("monkey", 5), ("gta", 120)])

    assert len(parts) == 1
    assert parts[0].kind == "gta"


def test_a_short_stretch_joins_the_LONGER_neighbour(monkeypatch):
    """Ten minutes between four hours of GTA and forty of Monkey belongs
    with the four hours."""
    monkeypatch.setattr(os.path, "isfile", lambda p: True)

    parts = _segments([("gta", 240), ("", 10), ("monkey", 40)])

    assert len(parts) == 2
    assert parts[0].kind == "gta"
    assert parts[0].duration > 240 * 60


def test_one_odd_reading_is_not_a_change_of_show(monkeypatch):
    """A loading screen inside a Monkey call reads as gameplay."""
    monkeypatch.setattr(os.path, "isfile", lambda p: True)

    parts = _segments([("monkey", 40), ("gta", 1), ("monkey", 40)])

    assert len(parts) == 1
    assert parts[0].kind == "monkey"


def test_the_segments_cover_the_whole_video(monkeypatch):
    """A gap would be a stretch of stream that never gets uploaded."""
    monkeypatch.setattr(os.path, "isfile", lambda p: True)
    look, span = _looker([("monkey", 60), ("gta", 120)])

    parts = segments_for("video.ts", span=span, look=look)

    assert parts[0].start == 0.0
    assert parts[-1].end == span
    for earlier, later in zip(parts, parts[1:]):
        assert earlier.end == later.start


def test_splitting_needs_more_than_one_kind():
    """Two segments of the same thing is one video cut in half for no
    reason."""
    assert not worth_splitting([Segment(0, 3600, "gta"),
                                Segment(3600, 7200, "gta")])
    assert worth_splitting([Segment(0, 3600, "monkey"),
                            Segment(3600, 7200, "gta")])


def test_nothing_readable_means_upload_it_whole(monkeypatch):
    """No numpy, no ffmpeg, an unreadable file - the VOD still goes up."""
    monkeypatch.setattr(os.path, "isfile", lambda p: True)

    def blind(*_a, **_k):
        raise OSError("no ffmpeg")

    parts = segments_for("video.ts", span=7200.0, look=blind)

    assert len(parts) <= 1


def test_a_missing_file_is_not_a_crash():
    assert segments_for("/no/such/video.ts") == []


def test_a_zero_length_video_is_not_a_crash(monkeypatch):
    monkeypatch.setattr(os.path, "isfile", lambda p: True)

    assert segments_for("video.ts", span=0.0) == []


def test_the_minimum_is_long_enough_to_deserve_a_title():
    assert MIN_SEGMENT_SECONDS >= 10 * 60


def test_it_can_be_read_in_the_log(monkeypatch):
    monkeypatch.setattr(os.path, "isfile", lambda p: True)

    said = describe(_segments([("monkey", 58), ("gta", 244)]))

    assert "part 1" in said and "part 2" in said
    assert "monkey" in said and "gta" in said
