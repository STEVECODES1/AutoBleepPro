"""A stream that went out unrecorded because restarting could not help.

The recorder found the stream, started, and then:

    [download] Got error: HTTP Error 403: Forbidden. Retrying fragment 362
    ...hundreds of them...
    The stream's segments are being refused (40 in a row) - the manifest
    has gone stale. Restarting with a fresh one.
    Channel is not live (or the recording never started).

A fresh manifest fixes a STALE one. It cannot fix a yt-dlp YouTube no
longer speaks to, and from inside the loop the two are identical: every
fragment 403s, the attempt is abandoned, the next starts clean and every
fragment 403s again.

The difference is whether anything was EVER downloaded. Refusals after
real progress are a stale manifest doing what stale manifests do.
Refusals with nothing downloaded, twice running, are something else.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "tools")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import record_stream as rec  # noqa: E402
from record_stream import (REFUSAL_RESTARTS_BEFORE_UPDATE,  # noqa: E402
                           Recorder, is_fragment_refusal, update_yt_dlp)


def _recorder(**kw):
    who = Recorder.__new__(Recorder)
    who.refusal_restarts = kw.get("restarts", 0)
    who._tried_update = kw.get("tried", False)
    who._said = {}
    who.said = []
    who.say = who.said.append

    def say_once(key, message):
        if key in who._said:
            return False
        who._said[key] = True
        who.said.append(message)
        return True

    who.say_once = say_once
    return who


# ── the real log line ────────────────────────────────────────────────

def test_the_403_that_lost_the_stream_is_recognised():
    line = ("[download] Got error: HTTP Error 403: Forbidden. "
            "Retrying fragment 362 (1/inf)...")

    assert is_fragment_refusal(line)


# ── one restart is fine, two is a diagnosis ──────────────────────────

def test_one_bad_attempt_does_not_reach_for_the_updater(monkeypatch):
    """A single all-refusal attempt can still be a stale manifest."""
    called = []
    monkeypatch.setattr(rec, "update_yt_dlp",
                        lambda *a, **k: called.append(1) or (True, ""))
    who = _recorder(restarts=1)

    who._fix_refusals()

    assert not called


def test_two_in_a_row_updates_yt_dlp(monkeypatch):
    monkeypatch.setattr(rec, "update_yt_dlp",
                        lambda *a, **k: (True, "updated to 2026.8.19"))
    who = _recorder(restarts=REFUSAL_RESTARTS_BEFORE_UPDATE)

    who._fix_refusals()

    said = " ".join(who.said)
    assert "out of date" in said
    assert "2026.8.19" in said


def test_it_updates_once_not_every_attempt(monkeypatch):
    """A recorder left open for days must not reinstall yt-dlp all night."""
    calls = []
    monkeypatch.setattr(rec, "update_yt_dlp",
                        lambda *a, **k: calls.append(1) or (True, "updated"))
    who = _recorder(restarts=5)

    who._fix_refusals()
    who._fix_refusals()
    who._fix_refusals()

    assert len(calls) == 1


def test_still_refused_after_updating_says_what_is_left(monkeypatch):
    """Not a version problem and not a stale manifest - so say the two
    things it can actually be."""
    who = _recorder(restarts=5, tried=True)

    who._fix_refusals()

    said = " ".join(who.said)
    assert "cookies" in said.lower()
    assert "not a stale manifest" in said


def test_a_failed_update_hands_over_the_command(monkeypatch):
    monkeypatch.setattr(rec, "update_yt_dlp",
                        lambda *a, **k: (False, "no network"))
    who = _recorder(restarts=2)

    who._fix_refusals()

    said = " ".join(who.said)
    assert "pip install -U yt-dlp" in said
    assert "no network" in said


# ── the updater itself ───────────────────────────────────────────────

class _Ran:
    def __init__(self, code=0, out="", err=""):
        self.returncode = code
        self.stdout = out
        self.stderr = err


def test_a_successful_update_reports_the_version():
    ok, detail = update_yt_dlp(
        runner=lambda *a, **k: _Ran(out="Successfully installed yt-dlp-2026.8.19"))

    assert ok and "2026.8.19" in detail


def test_already_current_is_not_a_failure():
    ok, detail = update_yt_dlp(
        runner=lambda *a, **k: _Ran(out="Requirement already satisfied"))

    assert ok and "latest" in detail


def test_a_broken_pip_is_reported_not_raised():
    ok, detail = update_yt_dlp(
        runner=lambda *a, **k: _Ran(code=1, err="ERROR: no such option"))

    assert not ok and "no such option" in detail


def test_the_updater_never_raises():
    def explode(*_a, **_k):
        raise OSError("pip is gone")

    ok, detail = update_yt_dlp(runner=explode)

    assert not ok and "pip is gone" in detail


def test_it_updates_this_interpreter_not_whichever_pip_is_on_path():
    """The recorder prefers `python -m yt_dlp` for the same reason - the
    yt-dlp on PATH is often a standalone exe that a pip upgrade here would
    not touch."""
    seen = []
    update_yt_dlp(runner=lambda cmd, **k: seen.append(cmd) or _Ran())

    assert seen[0][0] == sys.executable
    assert seen[0][1:4] == ["-m", "pip", "install"]
