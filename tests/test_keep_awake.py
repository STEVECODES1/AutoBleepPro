"""The PC may sleep between streams: work holds it awake, waiting does not."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "auto_uploader"))
sys.path.insert(0, os.path.join(_ROOT, "tools"))

from utils import keep_awake  # noqa: E402


def _record(monkeypatch):
    calls = []
    monkeypatch.setattr(keep_awake, "_set",
                        lambda flags: calls.append(flags) or True)
    return calls


def test_the_block_holds_the_machine_awake_and_lets_go_after(monkeypatch):
    calls = _record(monkeypatch)
    with keep_awake.KeepAwake("test") as awake:
        assert awake.active
    assert calls == [keep_awake.ES_CONTINUOUS | keep_awake.ES_SYSTEM_REQUIRED,
                     keep_awake.ES_CONTINUOUS]


def test_letting_go_happens_even_when_the_work_fails(monkeypatch):
    calls = _record(monkeypatch)
    try:
        with keep_awake.KeepAwake("test"):
            raise RuntimeError("upload failed")
    except RuntimeError:
        pass
    assert calls[-1] == keep_awake.ES_CONTINUOUS


def test_the_recorder_does_not_hold_the_machine_while_waiting_for_live():
    """2026-10-06: the first attempt waits for the stream INSIDE yt-dlp,
    and the hold around it meant the PC never slept between streams."""
    from record_stream import KeepAwake

    waiting = KeepAwake(start_held=False)
    with waiting:
        assert not waiting.active


def test_the_recorder_holds_once_the_stream_starts(monkeypatch):
    from record_stream import KeepAwake

    awake = KeepAwake(start_held=False)
    monkeypatch.setattr(sys, "platform", "linux")
    assert awake.hold() is False      # nothing to hold off Windows
    awake.release()
