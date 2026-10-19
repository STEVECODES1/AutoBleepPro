"""The censor's second listen asks for a batched decode (10-18: 84 min on a
5 h stream one window at a time), and still works with a transcriber that
takes no batch_size."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils import censor_verify as cv  # noqa: E402


class Engine:
    def scan_segments(self, segments):
        return []


def _quiet(monkeypatch):
    monkeypatch.setattr(cv, "_to_16k", lambda a, b: None)
    monkeypatch.setattr(cv, "_join_windows", lambda wav, wins, out: [(0.0, 10.0, 0.0)])


def test_batch_size_reaches_the_transcriber(monkeypatch):
    _quiet(monkeypatch)
    seen = {}

    class T:
        def transcribe(self, path, batch_size=None):
            seen["bs"] = batch_size
            return {"segments": []}

    assert cv.second_listen("x.wav", [(0, 1000)], Engine(), T(), 10.0, batch_size=8) == ([], [])
    assert seen["bs"] == 8


def test_old_transcriber_without_batch_size_still_works(monkeypatch):
    _quiet(monkeypatch)

    class Old:
        def transcribe(self, path):
            return {"segments": []}

    assert cv.second_listen("x.wav", [(0, 1000)], Engine(), Old(), 10.0, batch_size=8) == ([], [])
