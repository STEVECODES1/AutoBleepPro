"""music_guard: span finding and score combining (no models needed)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from autoreel import music_guard as M  # noqa: E402


def _scores(music, under=None, under_db=None):
    n = len(music)
    s = {"music": np.asarray(music, np.float32),
         "speech": np.zeros(n, np.float32),
         "singing": np.zeros(n, np.float32),
         "level": np.full(n, -20.0, np.float32)}
    if under is not None:
        s["under"] = np.asarray(under, np.float32)
        s["under_level"] = np.asarray(under_db, np.float32)
    return s


def test_no_music_no_spans():
    assert M.find_spans(_scores([0.05] * 100), 200.0) == []


def test_a_song_becomes_one_padded_span():
    music = [0.05] * 20 + [0.9] * 30 + [0.05] * 50
    spans = M.find_spans(_scores(music), 200.0)
    assert spans == [(40.0 - M.PAD_S, 100.0 + M.PAD_S)]


def test_a_blip_is_ignored():
    music = [0.05] * 20 + [0.9] + [0.05] * 20
    assert M.find_spans(_scores(music), 82.0) == []


def test_close_stretches_merge():
    music = [0.05] * 10 + [0.9] * 5 + [0.05] * 2 + [0.9] * 5 + [0.05] * 10
    assert len(M.find_spans(_scores(music), 64.0)) == 1


def test_song_under_talking_counts_only_when_audible():
    music = [0.05] * 40
    under = [0.05] * 10 + [0.9] * 20 + [0.05] * 10
    loud = M.find_spans(_scores(music, under, [-35.0] * 40), 80.0)
    faint = M.find_spans(_scores(music, under, [-70.0] * 40), 80.0)
    assert len(loud) == 1 and faint == []


def test_car_engine_under_talking_is_not_a_song():
    music = [0.05] * 40
    under = [0.05] * 10 + [0.65] * 20 + [0.05] * 10
    s = _scores(music, under, [-35.0] * 40)
    s["under_engine"] = np.full(40, 0.45, np.float32)
    assert M.find_spans(s, 80.0) == []


def test_ramp_mean_keeps_length_and_range():
    x = np.zeros(1000, np.float32)
    x[400:600] = 1
    y = M._ramp_mean(x, 50)
    assert len(y) == 1000 and y.min() >= 0 and y.max() <= 1.0001
    assert y[300] == 0 and abs(y[500] - 1) < 1e-5


def test_guard_never_raises(tmp_path):
    missing = str(tmp_path / "nope.mp4")
    assert M.guard(missing, say=lambda *_: None) == missing
