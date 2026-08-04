"""
Post-upload disk cleanup.

Two failure directions matter here and they pull opposite ways: deleting
too little leaves gigabytes of re-encodes behind, deleting too much
destroys either the user's only copy of a video or the evidence needed to
diagnose a failed upload. These pin both edges.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

_UPLOADER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto_uploader")
sys.path.insert(0, _UPLOADER)

from utils.cleanup import (  # noqa: E402
    SOURCE_DELETE,
    SOURCE_KEEP,
    SOURCE_MOVE,
    cleanup_after_upload,
    prune_uploaded_folder,
    resolve_source_action,
    trim_log,
)
from utils.config import load_config  # noqa: E402


@pytest.fixture
def env(tmp_path):
    """A config pointing at throwaway folders, plus the files one upload leaves."""
    censored = tmp_path / "censored"
    logs = tmp_path / "logs"
    uploaded = tmp_path / "uploaded"
    for d in (censored, logs, uploaded):
        d.mkdir()

    with open(os.path.join(_UPLOADER, "config.json")) as f:
        raw = json.load(f)
    raw["general"].update(
        censored_folder=str(censored), logs_folder=str(logs),
        uploaded_folder=str(uploaded), watch_folder=str(tmp_path),
        duplicate_store_path=str(tmp_path / "h.json"))
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(raw))

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v" * 4096)
    censored_copy = censored / "clip_CENSORED_silence-base.mp4"
    censored_copy.write_bytes(b"c" * (4 * 1024 * 1024))
    (censored / "clip_transcript.json").write_text("{}")
    (censored / "_clip_audio.wav").write_bytes(b"a" * 2048)
    (logs / "rumble_page_dump_1.html").write_text("dump")

    return {
        "cfg_path": str(cfg_path), "raw": raw, "tmp": tmp_path,
        "video": video, "censored_copy": censored_copy,
        "censored": censored, "logs": logs, "uploaded": uploaded,
    }


def load(env, **general_overrides):
    if general_overrides:
        env["raw"]["general"].update(general_overrides)
        with open(env["cfg_path"], "w") as f:
            json.dump(env["raw"], f)
    return load_config(env["cfg_path"], str(env["tmp"] / ".env"))


# ── What must be removed ─────────────────────────────────────────────────────

def test_censored_reencode_is_removed(env):
    cfg = load(env)
    freed = cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]))
    assert not env["censored_copy"].exists()
    assert freed >= 4, "should report the megabytes it actually freed"


def test_transcript_cache_and_temp_audio_are_removed(env):
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]))
    assert not (env["censored"] / "clip_transcript.json").exists()
    assert not (env["censored"] / "_clip_audio.wav").exists()


def test_page_dumps_removed_after_a_clean_run(env):
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         fully_uploaded=True)
    assert not (env["logs"] / "rumble_page_dump_1.html").exists()


# ── What must survive ────────────────────────────────────────────────────────

def test_source_video_survives_by_default(env):
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]))
    assert env["video"].exists(), "never delete the user's video by default"


def test_page_dumps_survive_a_partial_upload(env):
    """A failed run is exactly when the dump is needed."""
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         fully_uploaded=False)
    assert (env["logs"] / "rumble_page_dump_1.html").exists()


def test_cleanup_can_be_switched_off(env):
    cfg = load(env, cleanup={"enabled": False})
    assert cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"])) == 0.0
    assert env["censored_copy"].exists()


def test_censored_copy_kept_when_disabled(env):
    cfg = load(env, cleanup={"enabled": True, "censored_copy": False})
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]))
    assert env["censored_copy"].exists()


def test_never_deletes_the_video_as_its_own_censored_copy(env):
    """With censoring off, censored_path IS the source video."""
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["video"]))
    assert env["video"].exists()


def test_missing_files_are_not_an_error(env):
    """Cleanup runs even when a previous step already removed things."""
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["tmp"] / "gone.mp4"),
                         str(env["tmp"] / "gone_CENSORED.mp4"))
    # The real files are untouched - nothing was matched by the wrong name.
    assert env["video"].exists()
    assert env["censored_copy"].exists()
    assert (env["censored"] / "clip_transcript.json").exists()


# ── Logs are trimmed, not deleted ────────────────────────────────────────────

def test_log_is_trimmed_keeping_the_most_recent_lines(tmp_path):
    log = tmp_path / "youtube.log"
    with open(log, "w") as f:
        for i in range(200_000):
            f.write(f"2026-08-03 line {i}\n")
    before = log.stat().st_size
    freed = trim_log(str(log), max_mb=1)

    assert freed > 0
    assert log.exists(), "logs are trimmed, never deleted"
    assert log.stat().st_size < before
    text = log.read_text()
    assert "line 199999" in text, "the newest entries must survive"
    assert "line 0" not in text, "the oldest should be the ones dropped"
    assert text.startswith("[log trimmed")


def test_small_log_is_left_alone(tmp_path):
    log = tmp_path / "rumble.log"
    log.write_text("one line\n")
    assert trim_log(str(log), max_mb=5) == 0.0
    assert log.read_text() == "one line\n"


def test_trim_log_handles_a_missing_file(tmp_path):
    assert trim_log(str(tmp_path / "nope.log"), max_mb=1) == 0.0


def test_trim_disabled_with_zero(tmp_path):
    log = tmp_path / "x.log"
    log.write_text("data" * 100_000)
    assert trim_log(str(log), max_mb=0) == 0.0


# ── source_video modes ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("move", SOURCE_MOVE), ("delete", SOURCE_DELETE), ("keep", SOURCE_KEEP),
    ("DELETE", SOURCE_DELETE), ("nonsense", SOURCE_MOVE), ("", SOURCE_MOVE),
])
def test_resolve_source_action(env, value, expected):
    cfg = load(env, cleanup={"source_video": value})
    assert resolve_source_action(cfg) == expected


def test_source_action_defaults_to_move_when_unset(env):
    cfg = load(env, cleanup={})
    assert resolve_source_action(cfg) == SOURCE_MOVE


def test_shipped_config_does_not_delete_source_videos():
    """The default must never destroy a video the user may not have elsewhere."""
    with open(os.path.join(_UPLOADER, "config.json")) as f:
        shipped = json.load(f)
    assert shipped["general"]["cleanup"]["source_video"] == SOURCE_MOVE
    assert shipped["general"]["cleanup"]["keep_uploaded_videos"] == 0


# ── Pruning uploaded/ ────────────────────────────────────────────────────────

def test_prune_keeps_the_newest(env):
    for i in range(5):
        p = env["uploaded"] / f"v{i}.mp4"
        p.write_bytes(b"x" * 2048)
        os.utime(p, (i, i))
    cfg = load(env)
    freed = prune_uploaded_folder(cfg, keep_newest=2)
    assert freed > 0
    assert sorted(os.listdir(env["uploaded"])) == ["v3.mp4", "v4.mp4"]


def test_prune_zero_is_off_at_the_call_site(env):
    """keep_newest=0 empties the folder, so main.py only calls it when > 0."""
    (env["uploaded"] / "v.mp4").write_bytes(b"x")
    cfg = load(env)
    prune_uploaded_folder(cfg, keep_newest=0)
    assert os.listdir(env["uploaded"]) == []


def test_prune_ignores_non_videos(env):
    (env["uploaded"] / "report_optimize.md").write_text("keep me")
    (env["uploaded"] / "v.mp4").write_bytes(b"x")
    cfg = load(env)
    prune_uploaded_folder(cfg, keep_newest=0)
    assert os.listdir(env["uploaded"]) == ["report_optimize.md"]


def test_prune_missing_folder(env):
    cfg = load(env, uploaded_folder=str(env["tmp"] / "nope"))
    assert prune_uploaded_folder(cfg, keep_newest=1) == 0.0
