"""Laughter as a clip signal (autoreel/laughter)."""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from autoreel import laughter as L  # noqa: E402
from autoreel.highlights import Highlight, HighlightScorer  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="needs ffmpeg")


def test_no_laugh_no_lift_and_the_lift_is_capped():
    quiet = [0.0] * 60
    assert L.laugh_bonus(quiet, 10, 30) == 1.0
    assert L.laugh_bonus([], 10, 30) == 1.0
    hedge = list(quiet)
    hedge[20] = L.FLOOR                 # the tagger hedging, not a laugh
    assert L.laugh_bonus(hedge, 10, 30) == 1.0
    loud = list(quiet)
    loud[20] = 0.99
    assert L.laugh_bonus(loud, 10, 30) == pytest.approx(1.4)


def test_the_peak_is_found_inside_the_window_and_where():
    curve = [0.0] * 100
    curve[42] = 0.6
    assert L.peak(curve, 30, 60) == (0.6, 12.0)
    # A reading covers two seconds: one starting just before still counts.
    assert L.peak(curve, 43, 60)[0] == 0.6
    assert L.peak(curve, 50, 60)[0] == 0.0


def _segments(lines):
    return [{"start": float(s), "end": float(e), "text": t}
            for s, e, t in lines]


def test_a_window_somebody_laughed_in_wins_over_its_twin():
    lines = [(0, 5, "yo what are you doing bro"),
             (5, 10, "nah you crazy for that"),
             (10, 15, "get out the car right now"),
             (15, 20, "no way you just did that"),
             (40, 45, "yo what are you doing bro"),
             (45, 50, "nah you crazy for that"),
             (50, 55, "get out the car right now"),
             (55, 60, "no way you just did that")]
    laughs = [0.0] * 70
    laughs[52] = 0.7
    plain = HighlightScorer(min_duration=15, max_duration=20)
    heard = HighlightScorer(min_duration=15, max_duration=20, laughs=laughs)
    first = max(plain.candidate_windows(_segments(lines)),
                key=lambda h: h.score)
    best = max(heard.candidate_windows(_segments(lines)),
               key=lambda h: h.score)
    assert best.start >= 40 and best.laugh == pytest.approx(0.7)
    assert first.laugh == 0.0


def test_the_model_is_told_when_laughter_was_heard():
    from autoreel.llm_highlights import build_prompt

    picks = [Highlight(0, 20, 1.0, text="first one"),
             Highlight(30, 50, 1.0, text="second one", laugh=0.5)]
    prompt = build_prompt(picks, 1, lessons=[], hits="", bible="")
    first, second = prompt.split("[2]")
    assert "laughter heard" not in first
    assert "(laughter heard in it)" in second.splitlines()[0]


def test_no_detector_anywhere_is_no_opinion_not_an_error(monkeypatch):
    monkeypatch.setattr(L, "_ready_here", lambda: False)
    monkeypatch.setattr(L, "_helper_python", lambda: "")
    said = []
    assert L.listen("stream.mp4", say=said.append) == []
    assert said and "No laughter detector" in said[0]


def test_a_helper_that_fails_is_no_opinion_too(monkeypatch):
    monkeypatch.setattr(L, "_ready_here", lambda: False)
    monkeypatch.setattr(L, "_helper_python", lambda: "python-that-breaks")

    def broken(python, source):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(L, "_scan_in", broken)
    said = []
    assert L.listen("stream.mp4", say=said.append) == []
    assert "CUDA out of memory" in said[0]


def _video(tmp_path, name="piece.mp4"):
    path = tmp_path / name
    path.write_bytes(b"pretend video")
    return str(path)


def test_a_cached_curve_is_reused_without_listening(tmp_path, monkeypatch):
    video = _video(tmp_path)
    cache = str(tmp_path / "piece_laugh.json")
    calls = []
    monkeypatch.setattr(L, "listen",
                        lambda *a, **k: calls.append(1) or [0.1, 0.2])
    first = L.listen_cached(video, cache, say=lambda *a: None)
    second = L.listen_cached(video, cache, say=lambda *a: None)
    assert first == second == [0.1, 0.2]
    assert calls == [1], "the second call must come from the cache"


def test_a_recut_piece_is_heard_again(tmp_path, monkeypatch):
    video = _video(tmp_path)
    cache = str(tmp_path / "piece_laugh.json")
    monkeypatch.setattr(L, "listen", lambda *a, **k: [0.5])
    L.listen_cached(video, cache, say=lambda *a: None)
    with open(video, "wb") as handle:      # same name, new bytes
        handle.write(b"a different cut")
    monkeypatch.setattr(L, "listen", lambda *a, **k: [0.9])
    assert L.listen_cached(video, cache, say=lambda *a: None) == [0.9]


def test_no_detector_is_not_cached_as_no_laughter(tmp_path, monkeypatch):
    """[] means the model was missing, not that nobody laughed -
    caching it would skip the piece forever."""
    video = _video(tmp_path)
    cache = str(tmp_path / "piece_laugh.json")
    monkeypatch.setattr(L, "listen", lambda *a, **k: [])
    assert L.listen_cached(video, cache, say=lambda *a: None) == []
    assert not os.path.exists(cache)


class _FakeProcess:
    """A child that behaves as directed, without spawning anything."""
    instances = []

    def __init__(self, target, args, exitcode=0, dies=False, hangs=False):
        self._target = target
        self._args = args
        self.exitcode = exitcode
        self._dies = dies
        self._alive = hangs
        self.terminated = False
        _FakeProcess.instances.append(self)

    def start(self):
        if self._dies:
            self.exitcode = 1
            return
        if self._alive:
            return
        self._target(*self._args)

    def join(self, timeout=None):
        return

    def is_alive(self):
        return self._alive

    def terminate(self):
        self.terminated = True
        self._alive = False


def _fake_context(monkeypatch, **kwargs):
    import multiprocessing

    _FakeProcess.instances = []
    monkeypatch.setattr(
        multiprocessing, "get_context",
        lambda name=None: type("Ctx", (), {
            "Process": lambda self, target, args: _FakeProcess(
                target, args, **kwargs)})())


def test_isolated_uses_the_cache_without_spawning(tmp_path, monkeypatch):
    video = _video(tmp_path)
    cache = str(tmp_path / "piece_laugh.json")
    monkeypatch.setattr(L, "listen", lambda *a, **k: [0.3])
    L.listen_cached(video, cache, say=lambda *a: None)
    _fake_context(monkeypatch)
    assert L.listen_isolated(video, cache, say=lambda *a: None) == [0.3]
    assert _FakeProcess.instances == []


def test_isolated_child_death_costs_the_bonus_not_the_draft(tmp_path, monkeypatch):
    video = _video(tmp_path)
    cache = str(tmp_path / "piece_laugh.json")
    _fake_context(monkeypatch, dies=True)
    said = []
    assert L.listen_isolated(video, cache, say=said.append) == []
    assert len(_FakeProcess.instances) == 1
    assert said and "died" in said[0]


def test_isolated_timeout_kills_the_child(tmp_path, monkeypatch):
    video = _video(tmp_path)
    cache = str(tmp_path / "piece_laugh.json")
    _fake_context(monkeypatch, hangs=True)
    assert L.listen_isolated(video, cache, say=lambda *a: None, timeout=1) == []
    assert _FakeProcess.instances[0].terminated is True


@needs_ffmpeg
def test_one_reading_per_second_lined_up_with_the_audio(tmp_path, monkeypatch):
    """The framing, without the 330 MB model: a stand-in tagger that
    reports how loud each two-second frame is."""
    audio = tmp_path / "beep.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,2,3),0.5*sin(2*PI*440*t),0)'"
                    ":d=5:s=32000", str(audio)], check=True)
    monkeypatch.setattr(L, "_load", lambda: (None, "cpu", [0]))
    monkeypatch.setattr(L, "_tag", lambda frames, *a: [
        float(abs(frame).max()) for frame in frames])
    curve = L.scan_here(str(audio))
    assert len(curve) == 5
    # Frames start each second and last two: 1-3 s and 2-4 s hear it.
    assert curve[1] > 0.3 and curve[2] > 0.3
    assert curve[4] < 0.01
