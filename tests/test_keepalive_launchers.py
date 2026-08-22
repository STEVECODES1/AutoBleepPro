"""A recorder that is not running loses the one thing that cannot be redone.

START.bat ran each process ONCE. When it exited - a crash, a bad update,
anything - the window sat at a prompt doing nothing, silently, and the
next stream went by with nobody watching. Two streams were lost that way
in three days, and both times the first anyone knew was the video not
being there afterwards.

A clip can be re-cut and a post can be redone. A stream that was never
captured is gone.
"""

from __future__ import annotations

import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(_REPO, name), encoding="utf-8",
              errors="replace") as handle:
        return handle.read()


def test_the_recorder_restarts_itself():
    body = _read("_RUN_RECORDER.bat")

    assert ":loop" in body
    assert "goto loop" in body
    assert "record_stream.py" in body


def test_the_uploader_restarts_itself():
    body = _read("_RUN_UPLOADER.bat")

    assert ":loop" in body and "goto loop" in body
    assert "--watch" in body


def test_start_launches_the_keepalives_not_python_directly():
    """The whole point: nothing may run the recorder once."""
    body = _read("START.bat")

    assert "_RUN_RECORDER.bat" in body
    assert "_RUN_UPLOADER.bat" in body
    assert "python record_stream.py" not in body


def test_a_restart_is_announced_loudly():
    """A silent restart loop hides a crash that repeats forever, which is
    the same failure wearing a different hat.

    This used to assert the banner contained "%errorlevel%" - which it
    did, and which was the bug. ERRORLEVEL is whatever the LAST command
    set, the counter increment happens first, and `set /a` sets it too,
    so the banner printed the counter's success and every crash read
    "exit 0". The code is captured into CODE the instant the process
    exits now; test_keepalive_exit_codes.py holds that down properly.
    """
    body = _read("_RUN_RECORDER.bat")

    assert "STOPPED" in body
    assert "%CODE%" in body, "the banner stopped reporting an exit code"
    assert "RESTARTS" in body


def test_there_is_a_way_out():
    """A loop nobody can stop is its own problem."""
    for name in ("_RUN_RECORDER.bat", "_RUN_UPLOADER.bat"):
        assert "Ctrl+C" in _read(name), name


def test_it_waits_before_restarting():
    """A crash-on-startup would otherwise spin as fast as the disk
    allows and fill the log folder in minutes."""
    for name in ("_RUN_RECORDER.bat", "_RUN_UPLOADER.bat"):
        assert "timeout /t" in _read(name), name


def test_every_watched_channel_survived_the_move():
    """The URLs moved from START.bat into the wrapper - losing one would
    mean a platform silently stops being recorded."""
    body = _read("_RUN_RECORDER.bat")

    for url in ("youtube.com/@stackswopo_/live",
                "twitch.tv/stackswopo",
                "twitch.tv/stackswopo/clips",
                "kick.com/stackswopo1k",
                "youtube.com/@OnlyThaGuys26/live"):
        assert url in body, url
    assert '--name "Stackswopo"' in body
