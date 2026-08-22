"""The caption notes nobody ever deleted.

Six of these were sitting in the watch folder, videos long gone:

    Scammer Wop Back To Pay My Dues Sick Again Stackswopo St....txt

Every clip carries its caption, its spoken line and its tags in small
.txt files named after it. They are read at POST time, once per platform,
so they have to outlive the upload to the first one - which is why they
were never deleted alongside the video. Nothing deleted them afterwards
either, so they accumulated forever.

Two rules, because there are two ways a note is finished with:

  * every platform posted, so nothing can ever read it again - it goes
    with the rest of the working files, immediately;
  * its video no longer exists anywhere - swept, but only after the
    queue's give-up age, because until then a post could still want it.
"""

from __future__ import annotations

import os
import sys
import time
import types

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils import cleanup  # noqa: E402


def _cfg(tmp_path, **general):
    watch = tmp_path / "watch"
    censored = tmp_path / "censored"
    uploaded = tmp_path / "uploaded"
    for folder in (watch, censored, uploaded):
        folder.mkdir(exist_ok=True)
    settings = {"enabled": True}
    settings.update(general.pop("cleanup", {}))
    return types.SimpleNamespace(
        general=types.SimpleNamespace(
            watch_folder=str(watch), censored_folder=str(censored),
            uploaded_folder=str(uploaded), logs_folder=str(tmp_path),
            supported_formats=(".mp4", ".mkv"), cleanup=settings, **general),
        youtube=types.SimpleNamespace(censor_uploads=False),
        rumble=types.SimpleNamespace(censor_uploads=False))


def _old(path, hours=48):
    stamp = time.time() - hours * 3600
    os.utime(path, (stamp, stamp))


# ── which notes belong to a video ────────────────────────────────────────

def test_all_four_note_names_are_found():
    found = cleanup.sidecar_paths("/w/Clip 01.mp4")

    assert sorted(os.path.basename(p) for p in found) == [
        "Clip 01.txt", "Clip 01_caption.txt", "Clip 01_line.txt",
        "Clip 01_subject.txt"]


def test_a_vertical_copy_also_claims_the_clips_own_notes():
    """copy_sidecars puts a copy beside the re-frame; both sets are this
    video's."""
    found = [os.path.basename(p)
             for p in cleanup.sidecar_paths("/c/_vertical_Clip 01.mp4")]

    assert "Clip 01_subject.txt" in found
    assert "_vertical_Clip 01_subject.txt" in found


def test_a_note_points_back_at_its_video():
    assert cleanup._sidecar_stem("Clip 01_subject.txt") == "Clip 01"
    assert cleanup._sidecar_stem("_vertical_Clip 01_caption.txt") == "Clip 01"
    assert cleanup._sidecar_stem("Clip 01.txt") == "Clip 01"


def test_something_that_is_not_a_note_is_not_claimed():
    assert cleanup._sidecar_stem("Clip 01.mp4") == ""


# ── the sweep ────────────────────────────────────────────────────────────

def test_a_note_whose_video_is_gone_is_swept(tmp_path):
    cfg = _cfg(tmp_path)
    note = tmp_path / "watch" / "Scammer Wop - Clip 01_subject.txt"
    note.write_text("a caption", encoding="utf-8")
    _old(note)

    assert cleanup.prune_orphan_sidecars(cfg) == 1
    assert not note.exists()


def test_a_note_whose_video_is_still_there_is_left(tmp_path):
    """It has not been posted everywhere yet - the caption is read at post
    time, once per platform."""
    cfg = _cfg(tmp_path)
    (tmp_path / "watch" / "Clip 01.mp4").write_bytes(b"x")
    note = tmp_path / "watch" / "Clip 01_subject.txt"
    note.write_text("a caption", encoding="utf-8")
    _old(note)

    assert cleanup.prune_orphan_sidecars(cfg) == 0
    assert note.exists()


def test_the_video_counts_from_any_of_our_folders(tmp_path):
    """The source moves to uploaded/ after a successful run; its notes are
    not orphaned by that."""
    cfg = _cfg(tmp_path)
    (tmp_path / "uploaded" / "Clip 01.mp4").write_bytes(b"x")
    note = tmp_path / "watch" / "Clip 01_subject.txt"
    note.write_text("a caption", encoding="utf-8")
    _old(note)

    assert cleanup.prune_orphan_sidecars(cfg) == 0


def test_a_recent_note_is_left_alone(tmp_path):
    """Under the queue's give-up age a post could still be waiting to
    read it."""
    cfg = _cfg(tmp_path)
    note = tmp_path / "watch" / "Clip 01_subject.txt"
    note.write_text("a caption", encoding="utf-8")

    assert cleanup.prune_orphan_sidecars(cfg) == 0
    assert note.exists()


def test_a_big_txt_is_somebody_elses_file(tmp_path):
    """A sidecar is one line. Anything substantial in these folders
    belongs to whoever put it there."""
    cfg = _cfg(tmp_path)
    mine = tmp_path / "watch" / "my long notes.txt"
    mine.write_text("x" * (cleanup.ORPHAN_SIDECAR_MAX_BYTES + 1),
                    encoding="utf-8")
    _old(mine)

    assert cleanup.prune_orphan_sidecars(cfg) == 0
    assert mine.exists()


def test_a_vertical_note_follows_the_clip_not_the_copy(tmp_path):
    """The re-frame is deleted by prune_vertical_copies; its note must not
    be swept while the clip it describes is still around."""
    cfg = _cfg(tmp_path)
    (tmp_path / "watch" / "Clip 01.mp4").write_bytes(b"x")
    note = tmp_path / "censored" / "_vertical_Clip 01_subject.txt"
    note.write_text("a caption", encoding="utf-8")
    _old(note)

    assert cleanup.prune_orphan_sidecars(cfg) == 0


def test_the_sweep_can_be_turned_off(tmp_path):
    cfg = _cfg(tmp_path, cleanup={"sidecars": False})
    note = tmp_path / "watch" / "Clip 01_subject.txt"
    note.write_text("a caption", encoding="utf-8")
    _old(note)

    assert cleanup.prune_orphan_sidecars(cfg) == 0
    assert note.exists()


def test_a_missing_folder_is_not_a_crash(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.general.watch_folder = str(tmp_path / "nowhere")

    assert cleanup.prune_orphan_sidecars(cfg) == 0


def test_it_reports_how_many_not_how_many_megabytes(tmp_path):
    """A hundred one-line files free nothing measurable, and "0 MB" reads
    as having done nothing at all."""
    cfg = _cfg(tmp_path)
    for index in range(6):
        note = tmp_path / "watch" / f"Scammer Wop - Clip 0{index}_subject.txt"
        note.write_text("a caption", encoding="utf-8")
        _old(note)

    assert cleanup.prune_orphan_sidecars(cfg) == 6


# ── the ordinary path: gone the moment nothing needs them ────────────────

def test_notes_go_when_every_platform_has_posted(tmp_path):
    cfg = _cfg(tmp_path)
    video = tmp_path / "watch" / "Clip 01.mp4"
    video.write_bytes(b"x")
    note = tmp_path / "watch" / "Clip 01_subject.txt"
    note.write_text("a caption", encoding="utf-8")

    cleanup.cleanup_after_upload(
        cfg, str(video), results={"youtube": "https://y", "rumble": "https://r"},
        active_platforms=("youtube", "rumble"))

    assert not note.exists(), "nothing can read it again, and it stayed"


def test_notes_stay_while_a_platform_is_still_pending(tmp_path):
    """The caption is composed at post time. Deleting it early is how a
    clip goes out titled with its filename."""
    cfg = _cfg(tmp_path)
    video = tmp_path / "watch" / "Clip 01.mp4"
    video.write_bytes(b"x")
    note = tmp_path / "watch" / "Clip 01_subject.txt"
    note.write_text("a caption", encoding="utf-8")

    report = cleanup.cleanup_after_upload(
        cfg, str(video),
        results={"youtube": "https://y", "rumble": "FAILED: timeout"},
        active_platforms=("youtube", "rumble"))

    assert note.exists()
    assert any("caption notes" in what for what, _ in report.kept)


def test_the_video_itself_is_never_taken_by_the_note_rule(tmp_path):
    cfg = _cfg(tmp_path)
    video = tmp_path / "watch" / "Clip 01.mp4"
    video.write_bytes(b"x")

    cleanup.cleanup_after_upload(cfg, str(video), results={"youtube": "https://y"},
                                 active_platforms=("youtube",))

    assert video.exists()
