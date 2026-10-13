"""Cutting Twitch "Commercial break in progress" slates.

The slate is a still purple-to-blue gradient. Nothing here needs a
video file or ffmpeg: classification runs on synthetic frames, run
detection on synthetic flag lists. Sampling a real file is the only
part that touches ffmpeg, and it is deliberately untested here - a
test that needs a 3-hour stream is a test that never runs.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

np = pytest.importorskip("numpy")

from autoreel.adbreak import (  # noqa: E402
    _outside_median, _quiet, _runs, classify_samples, describe,
    is_slate_frame, MIN_BREAK_S)

H, W = 90, 160


def _slate(count=3, seed=1):
    """The break screen: a smooth purple-to-blue gradient, holding
    still apart from compression shimmer."""
    rng = np.random.default_rng(seed)
    left = np.array([150, 60, 180], dtype=np.float32)     # magenta-purple
    right = np.array([40, 140, 220], dtype=np.float32)    # cyan-blue
    mix = np.linspace(0, 1, W, dtype=np.float32)[None, :, None]
    base = left[None, None, :] * (1 - mix) + right[None, None, :] * mix
    frames = []
    for _ in range(count):
        shimmer = rng.integers(-3, 4, (H, W, 1)).astype(np.float32)
        frames.append(np.clip(base + shimmer, 0, 255).astype(np.uint8))
    return frames


def _gameplay(count=3, seed=7):
    """A camera moving through a world: everything changes at once."""
    rng = np.random.default_rng(seed)
    return [rng.integers(0, 255, (H, W, 3), dtype=np.uint8)
            for _ in range(count)]


# ── the picture test ───────────────────────────────────────────────

def test_the_slate_reads_as_a_slate():
    assert all(is_slate_frame(f) for f in _slate())


def test_gameplay_is_not_a_slate():
    assert not any(is_slate_frame(f) for f in _gameplay())


def test_black_is_not_a_slate():
    black = [np.zeros((H, W, 3), dtype=np.uint8) for _ in range(3)]
    assert not any(is_slate_frame(f) for f in black)


def test_flat_grey_is_not_a_slate():
    grey = [np.full((H, W, 3), 128, dtype=np.uint8) for _ in range(3)]
    assert not any(is_slate_frame(f) for f in grey)


def test_flat_red_is_not_a_slate():
    red = [np.zeros((H, W, 3), dtype=np.uint8) for _ in range(3)]
    for frame in red:
        frame[:, :, 0] = 220
    assert not any(is_slate_frame(f) for f in red)


def test_dark_navy_menu_is_not_a_slate():
    navy = [np.full((H, W, 3), (20, 30, 90), dtype=np.uint8)
            for _ in range(3)]
    assert not any(is_slate_frame(f) for f in navy)


def test_a_moving_slate_is_not_a_break():
    """Colour alone is not enough: the same gradient visibly changing
    is a transition effect, not a held break screen."""
    frames = _slate(count=2) + _gameplay(count=1)
    assert classify_samples(frames)[-1] is False


def test_garbage_is_never_a_slate():
    assert is_slate_frame(None) is False
    assert is_slate_frame("not a frame") is False
    assert classify_samples([]) == []
    assert classify_samples(None) == []


# ── run detection ──────────────────────────────────────────────────

def _flags(seconds, breaks):
    """One (t, positive) per second; `breaks` are (start, end) spans."""
    out = []
    for second in range(seconds):
        at = second + 0.5
        positive = any(s <= at < e for s, e in breaks)
        out.append((at, positive))
    return out


def test_a_short_slate_is_a_menu_not_a_break():
    flags = _flags(60, [(10.0, 10.0 + MIN_BREAK_S - 5)])
    assert _runs(flags) == []


def test_a_full_break_is_found_whole():
    flags = _flags(120, [(30.0, 75.0)])
    runs = _runs(flags)
    assert len(runs) == 1
    start, end = runs[0]
    assert start == pytest.approx(30.0, abs=1.5)
    assert end == pytest.approx(75.0, abs=1.5)


def test_one_noisy_frame_does_not_split_a_break():
    flags = _flags(120, [(30.0, 75.0)])
    flags[50] = (flags[50][0], False)  # shimmer flips one sample
    assert len(_runs(flags)) == 1


def test_two_breaks_stay_two():
    flags = _flags(300, [(30.0, 70.0), (200.0, 250.0)])
    assert len(_runs(flags)) == 2


# ── the audio confirm ────────────────────────────────────────────

def test_quiet_break_against_loud_rest_counts():
    levels = [-20.0] * 10 + [-70.0] * 25 + [-20.0] * 10
    assert _quiet(levels, 10.0, 35.0) is True


def test_loud_break_against_quiet_rest_does_not():
    levels = [-70.0] * 10 + [-20.0] * 25 + [-70.0] * 10
    assert _quiet(levels, 10.0, 35.0) is False


def test_baseline_ignores_the_break_itself():
    """A video that is half break must not measure its normal from
    the break: the median would land in the quiet and nothing would
    ever count as quiet."""
    levels = [-20.0] * 10 + [-70.0] * 25 + [-20.0] * 10
    assert _outside_median(levels, 10.0, 35.0) == pytest.approx(-20.0)
    assert _outside_median(levels, 0.0, 45.0) is None


def test_no_audio_leaves_the_picture_signal_standing():
    assert _quiet([], 10.0, 35.0) is True


# ── the log line ───────────────────────────────────────────────────

def test_describe_says_what_was_cut():
    line = describe([(30.0, 90.0)], 600.0)
    assert "1" in line and "commercial break" in line


def test_describe_empty_is_quiet():
    assert describe([], 600.0) == "No commercial breaks worth cutting."
