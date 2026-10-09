"""Telling Stackswopo's voice from the people he talks to."""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from autoreel import speaker_id as sp  # noqa: E402


def _voices(n_him=60, strangers=6, per=8, seed=1):
    """Him: one voice, a bit of mic noise. Strangers: each their own."""
    rng = np.random.default_rng(seed)
    him = rng.normal(size=256)
    rows = [him + rng.normal(scale=0.6, size=256) for _ in range(n_him)]
    for _ in range(strangers):
        v = rng.normal(size=256)
        rows += [v + rng.normal(scale=0.6, size=256) for _ in range(per)]
    X = np.asarray(rows)
    return X / np.linalg.norm(X, axis=1, keepdims=True), him / np.linalg.norm(him)


def test_the_voice_there_all_stream_is_found_without_a_saved_print():
    X, him = _voices()
    centre, thr = sp.find_streamer(X)
    assert centre @ him > 0.9
    sims = X @ centre
    assert (sims[:60] > thr).mean() > 0.95 and (sims[60:] > thr).mean() < 0.05


def test_with_a_saved_print_he_is_found_even_when_strangers_talk_more():
    X, him = _voices(n_him=25, strangers=3, per=30)    # 25 of his vs 90 theirs
    centre, thr = sp.find_streamer(X, prior=him)
    sims = X @ centre
    assert (sims[:25] > thr).mean() > 0.9 and (sims[25:] > thr).mean() < 0.1


def test_quiet_or_borderline_windows_are_not_guessed():
    got = sp.label_windows(np.array([0.9, 0.42, 0.1, 0.9]), 0.4,
                           np.array([900, 900, 900, 30]))
    assert got == [sp.STREAMER, sp.UNSURE, sp.OTHER, sp.UNSURE]


def test_a_sentence_takes_the_voice_with_most_of_its_time():
    spans = [(0, 1.5, sp.STREAMER), (1.5, 3, sp.STREAMER), (3, 4.5, sp.OTHER)]
    assert sp.who_said(spans, 0, 3.2) == sp.STREAMER
    assert sp.who_said(spans, 2.0, 4.5) == "both"
    assert sp.who_said(spans, 10, 12) == sp.UNSURE


def test_sentences_are_cut_into_short_windows():
    wins = sp.windows([{"start": 0, "end": 4.0}, {"start": 5, "end": 5.5},
                       {"start": 6, "end": 8.0}])
    assert wins == [(0, 1.5), (1.5, 3.0), (3.0, 4.0),     # the 0.5 s one is too short,
                    (6, 8.0)]                             # a short tail joins the last


def test_a_different_voice_never_overwrites_his_print(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PRINT_PATH", str(tmp_path / "v.npy"))
    monkeypatch.setattr(sp, "MODEL_DIR", str(tmp_path))
    a = np.eye(256)[0]
    assert sp.save_print(a, None)
    assert not sp.save_print(np.eye(256)[1], sp.load_print(), say=lambda m: None)
    assert sp.load_print() @ a > 0.99
