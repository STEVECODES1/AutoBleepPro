"""An idle recorder should look idle.

Five channels are watched, each on its own thread, and each was printing
the same sentences at the same second:

    [13:40:50] Holding the machine awake for the recording.
    [13:40:50] Holding the machine awake for the recording.
    [13:40:50] Holding the machine awake for the recording.
    [13:40:50] Holding the machine awake for the recording.
    [13:40:51] Not live yet - checking every 60s. This window can stay
               open for days; each check is one page fetch, so it costs
               practically no data.
    [13:40:51] Not live yet - checking every 60s. ...            x4

The same paragraph four times is not four facts. It made a recorder doing
absolutely nothing look like something going wrong, and every half hour
all night it did it again.

These lines are true of the RUN, not of one channel, so they are said
once for the whole run.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "tools")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import record_stream  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_run():
    """Each test is its own run of the program."""
    record_stream._SAID_FOR_EVERYONE.clear()
    yield
    record_stream._SAID_FOR_EVERYONE.clear()


class Watcher:
    """Just the saying half of a recorder, with the printing captured."""

    def __init__(self, said):
        self._said = {}
        self._out = said

    def say(self, message):
        self._out.append(message)

    say_once = record_stream.Recorder.say_once
    say_once_for_everyone = record_stream.Recorder.say_once_for_everyone


def test_one_channel_says_it_once():
    said = []
    Watcher(said).say_once_for_everyone("not-live-yet", "nothing live")

    assert said == ["nothing live"]


def test_five_channels_still_say_it_once():
    """This is the whole point - five threads, one line."""
    said = []
    for _ in range(5):
        Watcher(said).say_once_for_everyone("not-live-yet", "nothing live")

    assert said == ["nothing live"]


def test_it_reports_whether_it_actually_said_anything():
    said = []
    first = Watcher(said).say_once_for_everyone("k", "m")
    second = Watcher(said).say_once_for_everyone("k", "m")

    assert first is True
    assert second is False


def test_different_lines_are_not_swallowed_by_each_other():
    said = []
    watcher = Watcher(said)
    watcher.say_once_for_everyone("keep-awake", "holding the machine awake")
    watcher.say_once_for_everyone("not-live-yet", "nothing live")

    assert len(said) == 2


def test_it_is_safe_across_threads():
    """One watcher per channel, all starting at the same moment."""
    import threading

    said = []
    lock = threading.Lock()

    def announce():
        watcher = Watcher([])
        if watcher.say_once_for_everyone("not-live-yet", "nothing live"):
            with lock:
                said.append("nothing live")

    threads = [threading.Thread(target=announce) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert said == ["nothing live"]


# ── what the source actually does with it ────────────────────────────────

def _source() -> str:
    with open(os.path.join(_REPO, "tools", "record_stream.py"),
              encoding="utf-8") as fh:
        return fh.read()


def test_the_waiting_notice_uses_it():
    body = _source()
    spot = body.index("Checking every ")

    assert "say_once_for_everyone" in body[spot - 400:spot]


def test_keeping_awake_uses_it():
    body = _source()
    # rindex, not index: the docstring that explains this quotes the very
    # same line, and finds itself first.
    spot = body.rindex("Holding the machine awake for the recording.")

    assert "say_once_for_everyone" in body[spot - 200:spot]


def test_the_half_hourly_reminder_is_shared_too():
    """Otherwise it is five identical lines every thirty minutes, all
    night, which is the same problem on a slower clock."""
    body = _source()
    spot = body.index("Still watching ")

    assert "still-waiting" in body[spot - 300:spot]
    assert "WAIT_HEARTBEAT_S" in body[spot:spot + 400]


def test_the_notice_says_the_interval_costs_no_footage():
    """The real answer to 'why is it checking every minute': on YouTube
    --live-from-start walks back to the stream's first second, so
    detecting it a minute late loses nothing."""
    body = _source()

    assert "costs no footage" in body
    assert "--live-from-start" in body
