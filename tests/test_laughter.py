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
