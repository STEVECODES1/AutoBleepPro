"""The keepalive banners were reporting every crash as a clean exit.

_RUN_RECORDER.bat and _RUN_UPLOADER.bat wrap the two long-running
processes in a restart loop and print a banner whenever one dies. The
banner is the only record of *why* it died that survives at a glance -
the traceback is somewhere up the scrollback, the banner is what you see.

It read the exit code like this:

    python record_stream.py ...
    set /a RESTARTS+=1
    echo  ... STOPPED at %TIME% (exit %errorlevel%).

ERRORLEVEL is whatever the LAST command set, and `set /a` sets it - 0 for
a successful bit of arithmetic. So by the time the banner is parsed the
recorder's exit code is gone and every crash, of every kind, printed
`exit 0`. A window full of "stopped, exit 0, restarting" reads like a
process being polite about shutting down, not one dying on a bad import.

The second bug in the same six lines: a fixed 10-second wait. A crash
that happens instantly - a missing yt-dlp, a bad import after a pull -
restarts six times a minute forever. The window fills with identical
banners, the real error scrolls out of reach, and from across the room a
crash-looping recorder looks exactly like a working one.

These are text assertions on batch files. There is no way to run cmd.exe
from here, and both bugs are invisible to every other kind of test.
"""

from __future__ import annotations

import os
import re

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

KEEPALIVES = ["_RUN_RECORDER.bat", "_RUN_UPLOADER.bat"]


def _lines(name: str) -> list[str]:
    with open(os.path.join(_REPO, name), encoding="utf-8") as fh:
        return [ln.strip() for ln in fh
                if ln.strip() and not ln.strip().lower().startswith("rem")]


@pytest.fixture(params=KEEPALIVES)
def keepalive(request) -> list[str]:
    return _lines(request.param)


# ── the exit code has to be read before anything else runs ───────────────

def test_the_exit_code_is_captured_before_the_counter_moves(keepalive):
    grab = keepalive.index('set "CODE=%ERRORLEVEL%"')
    bump = keepalive.index("set /a RESTARTS+=1")

    assert grab < bump, "set /a overwrites ERRORLEVEL before it is read"


def test_nothing_runs_between_the_process_and_the_grab(keepalive):
    """Any command in between sets its own ERRORLEVEL. `echo` does too."""
    grab = keepalive.index('set "CODE=%ERRORLEVEL%"')

    assert keepalive[grab - 1].startswith("python "), (
        f"something ran between the crash and the grab: "
        f"{keepalive[grab - 1]!r}")


def test_the_banner_reports_the_saved_code(keepalive):
    banner = [ln for ln in keepalive if "STOPPED at" in ln]

    assert len(banner) == 1
    assert "%CODE%" in banner[0]


def test_no_banner_still_reads_errorlevel_live(keepalive):
    for line in keepalive:
        if line.lower().startswith("echo"):
            assert "%errorlevel%" not in line.lower(), line


# ── a crash loop must not drown the reason it is crashing ────────────────

def _waits(keepalive: list[str]) -> list[int]:
    """Every value WAIT is ever given, in the order the file assigns them.

    The escalation lines are guarded - `if %RESTARTS% GEQ 6 set WAIT=30` -
    so the assignment is not at the start of the line.
    """
    found = []
    for line in keepalive:
        match = re.search(r"set WAIT=(\d+)", line)
        if match:
            found.append(int(match.group(1)))
    return found


def test_the_wait_grows_with_the_restart_count(keepalive):
    values = _waits(keepalive)

    assert len(values) >= 3, "the wait is fixed - a fast crash loop spams"
    assert values == sorted(values), "the backoff goes backwards"


def test_the_wait_is_capped(keepalive):
    assert max(_waits(keepalive)) <= 120, (
        "a longer wait than this means a stream that starts late is missed")


def test_the_loop_never_gives_up(keepalive):
    """A stream that starts an hour after a crash still has to be caught."""
    assert "goto loop" in keepalive
    assert not any(ln.startswith("exit") and "/b" not in ln
                   for ln in keepalive)


def test_the_wait_is_used(keepalive):
    """Computing a backoff and then sleeping a constant is worse than not
    computing one, because it looks handled."""
    sleeps = [ln for ln in keepalive if ln.startswith("timeout /t")]

    assert len(sleeps) == 1
    assert "%WAIT%" in sleeps[0]


def test_the_counter_starts_from_zero_each_run(keepalive):
    """It is announced to the user as "Restart #N". Inheriting a leftover
    N from an earlier window in the same shell makes that a lie."""
    assert "setlocal" in keepalive
    assert keepalive.index("setlocal") < keepalive.index("set RESTARTS=0")


def test_the_backoff_is_reachable(keepalive):
    assert "call :backoff" in keepalive
    assert ":backoff" in keepalive

    called = keepalive.index("call :backoff")
    used = [i for i, ln in enumerate(keepalive) if ln.startswith("timeout /t")][0]
    assert called < used


def test_the_backoff_returns_instead_of_falling_through(keepalive):
    """Without the goto, the main loop runs on into :backoff and restarts
    the process a second time with no wait at all."""
    label = keepalive.index(":backoff")

    assert "goto :eof" in keepalive[label:]
    assert "goto loop" in keepalive[:label], (
        "the loop must jump back before reaching the subroutine")
