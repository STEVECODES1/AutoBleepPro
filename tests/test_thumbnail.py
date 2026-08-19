"""The frame that makes someone stop scrolling.

The thumbnail used to be whatever the platform grabbed, which in practice
is the first frame - and on a clip cut out of a stream that is the tail
end of whatever came before it. A grey loading screen, a menu, the back
of somebody's head.

Rules this must keep:
  * the picture is used AS IT IS - no text, no zoom, no border. The clips
    already carry their title across the top; saying it twice is worse
    than saying it once.
  * it never blocks a post. A clip with no thumbnail is a clip the
    platform picks a frame for, which is where this started.
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel import thumbnail  # noqa: E402
from autoreel.thumbnail import (FALLBACK_FRACTION, SAMPLE_FRACTIONS,  # noqa
                                _read_number, make, timestamps)


# ── where it looks ───────────────────────────────────────────────────

def test_it_looks_across_the_clip_not_at_the_start():
    """The first frame is the previous shot; the last is the cut."""
    marks = timestamps(100.0)

    assert len(marks) == len(SAMPLE_FRACTIONS)
    assert min(marks) > 0.0
    assert max(marks) < 100.0
    assert marks == sorted(marks)


def test_a_clip_with_no_length_has_nowhere_to_look():
    assert timestamps(0.0) == []
    assert timestamps(-5.0) == []


# ── reading the answer ───────────────────────────────────────────────

def test_the_documented_answer_is_read():
    assert _read_number(json.dumps({"frame": 3}), 8) == 3


def test_a_bare_number_is_still_an_answer():
    assert _read_number("3", 8) == 3


def test_a_fenced_answer_is_read():
    assert _read_number('```json\n{"frame": 5}\n```', 8) == 5


def test_a_frame_that_does_not_exist_is_refused():
    assert _read_number(json.dumps({"frame": 99}), 8) is None
    assert _read_number(json.dumps({"frame": 0}), 8) is None


def test_junk_is_no_answer():
    assert _read_number("", 8) is None
    assert _read_number("I'd rather not", 8) is None
    assert _read_number(json.dumps({"frame": "best one"}), 8) is None


# ── making one ───────────────────────────────────────────────────────

def _fake_ffmpeg(monkeypatch, grabbed):
    def grab(source, at, out_path, width=0):
        grabbed.append(round(at, 2))
        with open(out_path, "wb") as handle:
            handle.write(b"\xff\xd8jpeg")
        return True

    monkeypatch.setattr(thumbnail, "_grab", grab)
    monkeypatch.setattr(thumbnail.shutil, "which", lambda name: "/usr/bin/" + name)


def test_the_chosen_frame_is_the_one_written(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    grabbed = []
    _fake_ffmpeg(monkeypatch, grabbed)

    out = make(str(clip), 100.0, ask=lambda prompt, frames: '{"frame": 3}')

    assert out.endswith("clip_thumb.jpg")
    # Eight small looks, then the real one at the third mark.
    assert grabbed[-1] == round(100.0 * SAMPLE_FRACTIONS[2], 2)


def test_no_model_answer_falls_back_to_a_sane_frame(tmp_path, monkeypatch):
    """Not frame zero, which is the thing being fixed."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    grabbed = []
    _fake_ffmpeg(monkeypatch, grabbed)

    make(str(clip), 100.0, ask=lambda prompt, frames: "no thanks")

    assert grabbed[-1] == round(100.0 * FALLBACK_FRACTION, 2)
    assert grabbed[-1] > 0.0


def test_a_model_that_throws_still_produces_a_thumbnail(tmp_path,
                                                        monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    _fake_ffmpeg(monkeypatch, [])

    def explode(*_a):
        raise OSError("down")

    assert make(str(clip), 60.0, ask=explode)


def test_no_ffmpeg_means_no_thumbnail_not_a_crash(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(thumbnail.shutil, "which", lambda name: None)

    assert make(str(clip), 60.0) == ""


def test_a_missing_clip_is_not_a_crash(tmp_path):
    assert make(str(tmp_path / "gone.mp4"), 60.0) == ""


def test_a_clip_with_no_duration_is_not_a_crash(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")

    assert make(str(clip), 0.0) == ""


def test_the_picture_is_not_decorated(tmp_path, monkeypatch):
    """No text, no zoom, no border. A thumbnail that looks made rather
    than captured reads as an ad, and the clip already carries its title
    across the top of the frame."""
    body = open(os.path.join(_REPO, "autoreel", "thumbnail.py"),
                encoding="utf-8").read()

    assert "drawtext" not in body
    assert "crop=" not in body


def test_the_looks_are_small_and_the_keeper_is_not(tmp_path, monkeypatch):
    """A model reads an image at a fixed token cost whatever its size, so
    full resolution for the looking is pure upload time - but the
    thumbnail itself has to be full size."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    widths = []

    def grab(source, at, out_path, width=0):
        widths.append(width)
        with open(out_path, "wb") as handle:
            handle.write(b"\xff\xd8")
        return True

    monkeypatch.setattr(thumbnail, "_grab", grab)
    monkeypatch.setattr(thumbnail.shutil, "which", lambda n: "/usr/bin/" + n)

    make(str(clip), 60.0, ask=lambda p, f: '{"frame": 1}')

    assert set(widths[:-1]) == {512}
    assert widths[-1] == 0


def test_it_is_off_unless_asked_for():
    from autoreel.clip_maker import ClipMaker

    assert ClipMaker(output_dir="/out").pick_thumbnails is False
