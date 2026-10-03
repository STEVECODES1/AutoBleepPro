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

import pytest
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
    def grab(source, at, out_path, width=0, **_k):
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


def test_a_clip_thumbnail_is_not_decorated(tmp_path, monkeypatch):
    """No text, no zoom, no border on a CLIP. A thumbnail that looks made
    rather than captured reads as an ad, and the clip already carries its
    title across the top of the frame."""
    body = open(os.path.join(_REPO, "autoreel", "thumbnail.py"),
                encoding="utf-8").read()
    assert "drawtext" not in body

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    filters = []

    def grab(source, at, out_path, width=0, vf=""):
        filters.append(vf)
        with open(out_path, "wb") as handle:
            handle.write(b"\xff\xd8")
        return True

    monkeypatch.setattr(thumbnail, "_grab", grab)
    monkeypatch.setattr(thumbnail.shutil, "which", lambda n: "/usr/bin/" + n)
    make(str(clip), 60.0, ask=lambda p, f: '{"frame": 1}')
    assert "crop=" not in filters[-1]


def test_the_looks_are_small_and_the_keeper_is_not(tmp_path, monkeypatch):
    """A model reads an image at a fixed token cost whatever its size, so
    full resolution for the looking is pure upload time - but the
    thumbnail itself has to be full size."""
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    widths = []

    def grab(source, at, out_path, width=0, **_k):
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


# ── the grade and the logo, with real ffmpeg ────────────────────────────────

def _have_ffmpeg():
    import shutil as _shutil
    return _shutil.which("ffmpeg") is not None


def _mean_saturation(path):
    """Average (max-min) over RGB, from ffmpeg's own decode."""
    import subprocess as _sp
    raw = _sp.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                   "scale=64:36", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                  capture_output=True, check=True).stdout
    px = [raw[i:i + 3] for i in range(0, len(raw), 3)]
    return sum(max(p) - min(p) for p in px) / len(px)


def _pixel(path, x, y, w=64, h=36):
    import subprocess as _sp
    raw = _sp.run(["ffmpeg", "-v", "error", "-i", path, "-vf",
                   f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                  capture_output=True, check=True).stdout
    i = (y * w + x) * 3
    return tuple(raw[i:i + 3])


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")
def test_the_thumbnail_is_graded(tmp_path):
    import subprocess as _sp
    clip = tmp_path / "clip.mp4"
    # Muted on purpose: a stream frame is dark and flat, testsrc2 is not.
    _sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "testsrc2=size=640x360:rate=10:duration=3",
             "-vf", "eq=saturation=0.35:brightness=-0.1",
             "-pix_fmt", "yuv420p", str(clip)], check=True)
    plain = make(str(clip), 3.0, str(tmp_path / "plain.jpg"),
                 ask=lambda p, f: '{"frame": 2}', grade=False)
    graded = make(str(clip), 3.0, str(tmp_path / "graded.jpg"),
                  ask=lambda p, f: '{"frame": 2}')
    assert _mean_saturation(graded) > _mean_saturation(plain) * 1.1


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")
def test_a_logo_goes_in_the_top_left_corner_only(tmp_path):
    import subprocess as _sp
    clip = tmp_path / "clip.mp4"
    _sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "color=c=black:size=640x360:rate=10:duration=3",
             "-pix_fmt", "yuv420p", str(clip)], check=True)
    logo = tmp_path / "logo.png"
    _sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "color=c=red:size=200x100", "-frames:v", "1", str(logo)],
            check=True)
    out = make(str(clip), 3.0, str(tmp_path / "t.jpg"),
               ask=lambda p, f: '{"frame": 1}', logo_path=str(logo))

    r, g, b = _pixel(out, 5, 3)
    assert r > 150 and g < 80, "no logo in the corner"
    assert max(_pixel(out, 60, 33)) < 40, "the logo spread across the frame"


def test_a_missing_logo_file_is_simply_no_logo(tmp_path, monkeypatch):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    stamped = []
    monkeypatch.setattr(thumbnail, "_grab", lambda *a, **k: (
        open(a[2], "wb").write(b"\xff\xd8") and True))
    monkeypatch.setattr(thumbnail, "_stamp_logo",
                        lambda pic, logo: stamped.append(logo))
    monkeypatch.setattr(thumbnail.shutil, "which", lambda name: "/bin/ffmpeg")

    assert make(str(clip), 10.0, str(tmp_path / "t.jpg"),
                ask=lambda p, f: None, logo_path=str(tmp_path / "nope.png"))
    assert stamped == []


# ── full-stream thumbnails ──────────────────────────────────────────────────
# The stream thumbnail that prompted these was a dark wide shot with the
# people a fifth of the height, next to channels winning on the same
# footage with one bright character filling the frame.

def test_the_zoom_puts_the_character_big_and_left_of_centre():
    from autoreel.thumbnail import SUBJECT_X, zoom_window

    x, y, w, h = zoom_window((0.45, 0.30, 0.55, 0.70))
    assert abs(h - 0.40 / 0.80) < 1e-6          # head-to-waist fills 80%
    assert w == h                                # stays 16:9
    assert abs((0.50 - x) / w - SUBJECT_X) < 1e-6
    assert 0 <= x <= 1 - w and 0 <= y <= 1 - h


def test_the_zoom_never_goes_past_the_limit_or_off_the_frame():
    from autoreel.thumbnail import MAX_ZOOM, zoom_window

    x, y, w, h = zoom_window((0.97, 0.90, 0.99, 0.95))   # tiny, in a corner
    assert abs(w - 1 / MAX_ZOOM) < 1e-6
    assert x + w <= 1.0 + 1e-9 and y + h <= 1.0 + 1e-9


def test_no_box_is_a_gentle_zoom_on_the_middle():
    from autoreel.thumbnail import DEFAULT_ZOOM, zoom_window

    x, y, w, h = zoom_window(None)
    assert abs(w - 1 / DEFAULT_ZOOM) < 1e-6
    assert abs(x - (1 - w) / 2) < 1e-6


def test_the_models_box_is_read():
    from autoreel.thumbnail import _read_box

    assert _read_box('{"frame": 2, "box": [100, 200, 600, 400]}') == \
        (0.2, 0.1, 0.4, 0.6)
    assert _read_box('{"frame": 2}') is None
    assert _read_box('{"frame": 2, "box": [0, 0, 1000, 1000]}') is None
    assert _read_box("junk") is None


def _stream_scene(monkeypatch, stats):
    grabbed = []

    def grab(source, at, out_path, width=0, vf=""):
        grabbed.append((round(at, 1), vf))
        with open(out_path, "wb") as handle:
            handle.write(b"\xff\xd8")
        return True

    marks = thumbnail.timestamps(1000.0, thumbnail.STREAM_SAMPLES)
    lookup = dict(zip([round(m, 1) for m in marks], stats))
    order = iter(stats)
    monkeypatch.setattr(thumbnail, "_grab", grab)
    monkeypatch.setattr(thumbnail, "_measure", lambda path: next(order))
    monkeypatch.setattr(thumbnail.shutil, "which", lambda n: "/usr/bin/" + n)
    return grabbed, marks, lookup


def test_dark_frames_never_reach_the_model(tmp_path, monkeypatch):
    stats = [(30.0, 10.0)] * 15 + [(120.0, 40.0)]
    grabbed, marks, _ = _stream_scene(monkeypatch, stats)
    seen = []
    clip = tmp_path / "stream.mp4"
    clip.write_bytes(b"x")

    make(str(clip), 1000.0, str(tmp_path / "t.jpg"), style="stream",
         ask=lambda p, frames: seen.append(len(frames)) or '{"frame": 1}')

    assert seen == [1]
    assert grabbed[-1][0] == round(marks[-1], 1)


def test_with_no_model_the_brightest_most_colourful_look_wins(
        tmp_path, monkeypatch):
    stats = [(80.0, 10.0)] * 16
    stats[7] = (140.0, 60.0)
    grabbed, marks, _ = _stream_scene(monkeypatch, stats)
    clip = tmp_path / "stream.mp4"
    clip.write_bytes(b"x")

    make(str(clip), 1000.0, str(tmp_path / "t.jpg"), style="stream",
         ask=lambda p, f: "busy")

    at, vf = grabbed[-1]
    assert at == round(marks[7], 1)
    assert "crop=" in vf and "scale=1280:720" in vf


def test_a_dark_pick_is_lifted_and_a_bright_one_is_not():
    from autoreel.thumbnail import stream_filters, zoom_window

    assert "gamma=" in stream_filters(zoom_window(None), 60.0)
    assert "gamma=" not in stream_filters(zoom_window(None), 130.0)


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")
def test_a_stream_thumbnail_is_720p_with_logo_and_sticker(tmp_path):
    import subprocess as _sp
    clip = tmp_path / "s.mp4"
    _sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "color=c=0x303030:size=1920x1080:rate=2:duration=20",
             "-pix_fmt", "yuv420p", str(clip)], check=True)
    logo = tmp_path / "logo.png"
    _sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "color=c=red:size=600x300", "-frames:v", "1", str(logo)],
            check=True)
    sticker = tmp_path / "sticker.png"
    _sp.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
             "color=c=blue:size=200x400", "-frames:v", "1", str(sticker)],
            check=True)

    out = make(str(clip), 20.0, str(tmp_path / "t.jpg"), style="stream",
               ask=lambda p, f: '{"frame": 1, "box": [300,400,700,600]}',
               logo_path=str(logo), sticker_path=str(sticker))

    assert thumbnail._size(out) == (1280, 720)
    # Logo: a fifth of the width, top-left - not a speck.
    r, g, b = _pixel(out, 10, 3)
    assert r > 150 and g < 80
    # Sticker: on the right edge, standing on the bottom.
    r, g, b = _pixel(out, 62, 30)
    assert b > 150 and r < 80
