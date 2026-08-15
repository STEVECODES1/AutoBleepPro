"""Telling Monkey App content from GTA gameplay by looking at it.

The framing comes from the stream title, and titles are often silent:
"yoo_howl" and "culture" are both real recordings here. A silent title
falls back to `whole`, which wastes most of a vertical frame.

What separates the two is motion. GTA is a camera moving through a 3D
world - pan it and nearly every pixel changes. Monkey App content is a
desktop: a call pane, a slots page that is still between spins, and a
taskbar that never moves at all.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

np = pytest.importorskip("numpy")

from autoreel.content_kind import (  # noqa: E402
    kind_for_frames, kind_for_video, moving_fraction, profile_for,
    taskbar_fraction, GAMEPLAY_MOVING, DESKTOP_MOVING)

H, W = 90, 160


def _gameplay(count=6, seed=1):
    """A camera moving through a world: everything changes at once."""
    rng = np.random.default_rng(seed)
    return [rng.integers(0, 255, (H, W), dtype=np.uint8) for _ in range(count)]


def _desktop(count=6, seed=2, pane=0.25):
    """Windows that hold still, one small pane with a person in it, and
    a taskbar that never moves."""
    rng = np.random.default_rng(seed)
    base = rng.integers(0, 255, (H, W), dtype=np.uint8)
    frames = []
    for _ in range(count):
        frame = base.copy()
        edge = int(W * pane)
        frame[:int(H * 0.5), :edge] = rng.integers(
            0, 255, (int(H * 0.5), edge), dtype=np.uint8)
        frames.append(frame)
    return frames


# ── the measurements ─────────────────────────────────────────────────

def test_a_moving_camera_changes_most_of_the_frame():
    assert moving_fraction(_gameplay()) >= GAMEPLAY_MOVING


def test_a_desktop_holds_most_of_the_frame_still():
    assert moving_fraction(_desktop()) <= DESKTOP_MOVING


def test_one_frame_says_nothing_about_motion():
    assert moving_fraction(_gameplay(count=1)) is None
    assert moving_fraction([]) is None
    assert moving_fraction(None) is None


def test_a_taskbar_is_inert():
    assert taskbar_fraction(_desktop()) == pytest.approx(0.0, abs=0.01)


def test_gameplay_has_no_still_strip_at_the_bottom():
    assert taskbar_fraction(_gameplay()) > 0.1


# ── the verdict ──────────────────────────────────────────────────────

def test_gameplay_is_recognised():
    assert kind_for_frames(_gameplay()) == "gta"


def test_monkey_content_is_recognised():
    assert kind_for_frames(_desktop()) == "monkey"


def test_nothing_to_look_at_is_not_a_guess():
    assert kind_for_frames([]) == ""
    assert kind_for_frames(None) == ""


def test_a_still_game_is_not_called_a_desktop():
    """A menu, a loading screen, standing in a lobby - still, but with a
    live HUD under it rather than an inert taskbar."""
    rng = np.random.default_rng(5)
    base = rng.integers(0, 255, (H, W), dtype=np.uint8)
    frames = []
    for _ in range(6):
        frame = base.copy()
        frame[-6:, :] = rng.integers(0, 255, (6, W), dtype=np.uint8)
        frames.append(frame)
    assert kind_for_frames(frames) != "monkey"


def test_a_full_screen_overlay_over_gameplay_is_not_guessed():
    """Everything moving AND a pinned strip is contradictory. Say so."""
    frames = _gameplay()
    still = frames[0][-6:, :].copy()
    for frame in frames:
        frame[-6:, :] = still
    assert kind_for_frames(frames) == "", "a pinned strip over motion is contradictory"


def test_the_middle_ground_refuses_to_answer():
    """A live bottom strip says "not a desktop", but too little else is
    moving to call it gameplay. Neither answer is earned."""
    rng = np.random.default_rng(7)
    base = rng.integers(0, 255, (H, W), dtype=np.uint8)
    frames = []
    for _ in range(6):
        frame = base.copy()
        frame[-6:, :] = rng.integers(0, 255, (6, W), dtype=np.uint8)
        frame[:8, :8] = rng.integers(0, 255, (8, 8), dtype=np.uint8)
        frames.append(frame)
    overall = moving_fraction(frames)
    assert DESKTOP_MOVING < overall < GAMEPLAY_MOVING or overall < GAMEPLAY_MOVING
    assert kind_for_frames(frames) == ""


# ── the title still wins ─────────────────────────────────────────────

def test_the_title_beats_the_pixels(monkeypatch):
    """The streamer naming their own stream is better evidence than six
    seconds of frames."""
    import autoreel.content_kind as module

    monkeypatch.setattr(module, "kind_for_video", lambda *a, **k: "gta")
    assert profile_for("/any.mp4", "monkey app trolling") == "monkey"


def test_a_silent_title_lets_the_picture_decide(monkeypatch):
    import autoreel.content_kind as module

    monkeypatch.setattr(module, "kind_for_video", lambda *a, **k: "monkey")
    assert profile_for("/any.mp4", "culture") == "monkey"


def test_a_silent_title_and_an_unsure_picture_keeps_the_fallback(monkeypatch):
    import autoreel.content_kind as module

    monkeypatch.setattr(module, "kind_for_video", lambda *a, **k: "")
    assert profile_for("/any.mp4", "culture") == "whole"


def test_disagreeing_samples_keep_the_fallback(monkeypatch):
    """A stream that opens on a waiting screen must not be decided by
    whichever second was looked at first."""
    import autoreel.content_kind as module

    answers = iter(["monkey", "gta", ""])
    monkeypatch.setattr(module, "kind_for_video",
                        lambda *a, **k: next(answers, ""))
    assert profile_for("/any.mp4", "culture", samples=3) == "whole"


def test_a_majority_across_samples_wins(monkeypatch):
    import autoreel.content_kind as module

    answers = iter(["monkey", "monkey", "gta"])
    monkeypatch.setattr(module, "kind_for_video",
                        lambda *a, **k: next(answers, ""))
    assert profile_for("/any.mp4", "culture", samples=3) == "monkey"


def test_an_unreadable_file_is_never_a_crash():
    assert kind_for_video("/no/such/file.mp4") == ""


# ── measured against real rendered video, not arrays ─────────────────

def test_the_thresholds_separate_real_video(tmp_path):
    """The first GAMEPLAY_MOVING here was 0.45 and would never once have
    fired: nothing rendered reached it. These are the measurements that
    replaced the guess."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("needs ffmpeg")

    desktop = str(tmp_path / "desktop.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=30:duration=6",
         "-f", "lavfi", "-i", "testsrc=size=160x90:rate=30:duration=6",
         "-filter_complex", "[0:v][1:v]overlay=20:20[v]", "-map", "[v]",
         "-c:v", "libx264", desktop],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    alive = str(tmp_path / "alive.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "life=size=640x360:rate=30:ratio=0.3", "-t", "6",
         "-c:v", "libx264", alive],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    assert kind_for_video(desktop) == "monkey"
    assert kind_for_video(alive) == "gta"
