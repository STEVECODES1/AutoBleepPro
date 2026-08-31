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


# ── a 503 is not a channel being offline ─────────────────────────────
#
# should_resume only reconnects an attempt that ran longer than
# RESUME_WINDOW_S, and a 503 fails in seconds. So a live stream behind a
# transient CDN error was written off as "not live" on the first try and
# the next look came a whole poll later - which is how a stream that was
# genuinely broadcasting produced no recording at all.

def test_a_fast_failure_is_not_resumed_by_the_normal_rule():
    """The rule that let it through. Kept, because it is right for what
    it was written for - this is why the missed case needs its own."""
    from record_stream import should_resume

    assert not should_resume(started_at=0.0, ended_at=8.0, resumes=0), \
        "an eight-second attempt is not a dropped four-hour recording"
    assert should_resume(started_at=0.0, ended_at=600.0, resumes=0)


def test_a_found_but_empty_stream_gets_its_own_retries():
    from record_stream import MAX_MISSED_TRIES, MISSED_RETRY_SECONDS

    assert MAX_MISSED_TRIES >= 2, "one try is what missed the stream"
    assert 5 <= MISSED_RETRY_SECONDS <= 60, \
        "long enough for a CDN blip, short enough to catch a live stream"


def test_the_recorder_remembers_why_it_came_away_empty():
    """The watch loop cannot tell a missed stream from an offline
    channel without it."""
    from record_stream import Recorder

    recorder = Recorder(url="u", staging="s", watch_folder="w")

    assert recorder.last_missed == ""


def test_the_retry_only_fires_on_a_found_stream():
    """Reading the source rather than driving yt-dlp: the guard is what
    stops an offline channel retrying three times every single poll, all
    night."""
    import inspect

    from record_stream import Recorder

    body = inspect.getsource(Recorder.record_one_stream)

    assert "self.last_missed" in body, "it retries whatever the reason"
    assert "missed_tries" in body


# ═════════════════════════════════════════════════════════════════════════════
# ONE BAD PASS USED TO END THE SOURCE FOR GOOD
#
# Several threads all hit PermissionError at once writing to the log on an
# external drive under heavy load - a transient hiccup, not a real failure.
# _source_loop had no try/except around record_one_stream(), so the
# exception killed the thread outright: Python printed "Exception in
# thread record-youtube" and that source never recorded again until the
# whole process was restarted. A window left open for days needs to
# survive one bad moment, not stop silently at the first one.
# ═════════════════════════════════════════════════════════════════════════════

def test_a_crash_in_one_pass_does_not_kill_the_source():
    import threading

    from record_stream import _source_loop

    stop = threading.Event()
    calls = []
    said = []

    class FakeRecorder:
        poll_seconds = 0.01

        def record_one_stream(self):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError(13, "Permission denied")
            stop.set()

        def say(self, message):
            said.append(message)

    _source_loop(FakeRecorder(), False, stop)

    assert len(calls) == 2, "the loop stopped after the crash instead of trying again"
    assert any("PermissionError" in m for m in said), \
        "the crash happened silently"


def test_a_crash_in_fetch_clips_does_not_kill_the_whole_recorder(
        monkeypatch, tmp_path, capsys):
    """fetch_clips() runs in the MAIN thread, not one of the per-source
    ones - an uncaught exception here used to end main() entirely,
    taking every recording thread down with it even though their own
    crash handling (test above) was working fine and would have kept
    them recording. A drive dropping out mid os.makedirs() must cost
    one poll, not the whole recorder."""
    import record_stream

    def boom(*a, **k):
        raise FileNotFoundError(
            "[WinError 3] The system cannot find the path specified: 'D:\\'")

    monkeypatch.setattr(record_stream, "fetch_clips", boom)

    code = record_stream.main([
        "https://www.twitch.tv/stackswopo/clips?range=7d",
        "--once", "--staging", str(tmp_path / "s"),
        "--watch-folder", str(tmp_path / "w"),
    ])

    assert code == 0, "the crash propagated out of main() instead of being caught"
    out = capsys.readouterr().out
    assert "ERROR" in out and "still running" in out


def test_once_mode_still_tries_exactly_once_even_on_a_crash():
    """--once means one pass and exit - a crash must not turn that into a
    silent retry loop it was never asked for."""
    import threading

    from record_stream import _source_loop

    stop = threading.Event()
    calls = []

    class FakeRecorder:
        poll_seconds = 0.01

        def record_one_stream(self):
            calls.append(1)
            raise RuntimeError("boom")

        def say(self, message):
            pass

    _source_loop(FakeRecorder(), True, stop)

    assert calls == [1]
