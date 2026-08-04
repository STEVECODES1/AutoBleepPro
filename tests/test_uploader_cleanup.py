"""
Post-upload disk cleanup: the delete contract.

Two failure directions pull opposite ways here. Delete too little and
gigabytes of re-encodes pile up; delete too much and you destroy either
the user's only copy of a video or the inputs a retry depends on. The
tests below pin both edges, plus idempotence - cleanup runs on every
processed file, including files it has already cleaned.
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

_UPLOADER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto_uploader")
sys.path.insert(0, _UPLOADER)

from utils.cleanup import (  # noqa: E402
    SOURCE_DELETE,
    SOURCE_KEEP,
    SOURCE_MOVE,
    censored_copy_is_safe_to_delete,
    censoring_platforms,
    cleanup_after_upload,
    platforms_needing_retry,
    prune_uploaded_folder,
    resolve_source_action,
    trim_log,
)
from utils.config import load_config  # noqa: E402

BOTH_OK = {"youtube": "https://youtu.be/a", "rumble": "https://rumble.com/v1"}
YT_OK_RB_FAILED = {"youtube": "https://youtu.be/a", "rumble": "FAILED: timeout"}
YT_FAILED_RB_OK = {"youtube": "FAILED: quota", "rumble": "https://rumble.com/v1"}
BOTH_FAILED = {"youtube": "FAILED: quota", "rumble": "FAILED: timeout"}


@pytest.fixture
def env(tmp_path):
    """A config on throwaway folders, plus the files one upload leaves behind."""
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
    transcript = censored / "clip_transcript.json"
    transcript.write_text("[]")
    temp_wav = censored / "_clip_audio.wav"
    temp_wav.write_bytes(b"a" * 2048)

    return {
        "cfg_path": str(cfg_path), "raw": raw, "tmp": tmp_path,
        "video": video, "censored_copy": censored_copy, "transcript": transcript,
        "temp_wav": temp_wav, "censored": censored, "logs": logs,
        "uploaded": uploaded,
    }


def load(env, **general_overrides):
    if general_overrides:
        env["raw"]["general"].update(general_overrides)
        with open(env["cfg_path"], "w") as f:
            json.dump(env["raw"], f)
    return load_config(env["cfg_path"], str(env["tmp"] / ".env"))


def make_dump(env, name="rumble_page_dump_1.html", age_seconds=0.0):
    path = env["logs"] / name
    path.write_text("dump")
    when = time.time() - age_seconds
    os.utime(path, (when, when))
    return path


# ═════════════════════════════════════════════════════════════════════════════
# Which platforms are still pending
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("results,expected", [
    (BOTH_OK, set()),
    (YT_OK_RB_FAILED, {"rumble"}),
    (YT_FAILED_RB_OK, {"youtube"}),
    (BOTH_FAILED, {"youtube", "rumble"}),
    ({}, {"youtube", "rumble"}),                       # nothing attempted yet
    ({"youtube": "https://youtu.be/a"}, {"rumble"}),   # interrupted mid-run
    (None, set()),                                     # caller passed nothing
])
def test_platforms_needing_retry(results, expected):
    assert platforms_needing_retry(results) == expected


def test_censoring_platforms_from_shipped_config(env):
    cfg = load(env)
    # YouTube censors, Rumble uploads the original - the shipped default.
    assert censoring_platforms(cfg) == {"youtube"}


# ═════════════════════════════════════════════════════════════════════════════
# Full success: the intended artifacts, and only those
# ═════════════════════════════════════════════════════════════════════════════

def test_success_removes_reencode_transcript_and_temp_audio(env):
    cfg = load(env)
    report = cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                                  results=BOTH_OK, since_ts=time.time() - 60)
    assert not env["censored_copy"].exists()
    assert not env["transcript"].exists()
    assert not env["temp_wav"].exists()
    assert report.freed_mb >= 4


def test_success_never_touches_the_source_video(env):
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert env["video"].exists()


def test_success_never_touches_uploaded_folder(env):
    kept = env["uploaded"] / "previous.mp4"
    kept.write_bytes(b"x" * 1024)
    report_md = env["uploaded"] / "clip_optimize.md"
    report_md.write_text("seo")
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert kept.exists() and report_md.exists()


def test_cleanup_finds_the_reencode_without_being_told_its_path(env):
    """On a retry run the censored copy exists but was never regenerated,
    so the caller has no path to hand over."""
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), None,
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert not env["censored_copy"].exists()


def test_reencode_of_a_similarly_named_video_is_untouched(env):
    """'clip' must not match 'clip2_CENSORED_...'."""
    other = env["censored"] / "clip2_CENSORED_silence-base.mp4"
    other.write_bytes(b"c" * 1024)
    other_wav = env["censored"] / "_clip2_audio.wav"
    other_wav.write_bytes(b"a" * 512)
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert other.exists(), "prefix collision deleted another video's re-encode"
    assert other_wav.exists()


def test_glob_metacharacters_in_the_filename_are_escaped(env):
    """yt-dlp's bracket form ('Title [dQw4w9WgXcQ].mp4') is glob syntax."""
    video = env["tmp"] / "Title [dQw4w9WgXcQ].mp4"
    video.write_bytes(b"v" * 1024)
    mine = env["censored"] / "Title [dQw4w9WgXcQ]_CENSORED_silence-base.mp4"
    mine.write_bytes(b"c" * 1024)
    decoy = env["censored"] / "Title d_CENSORED_silence-base.mp4"
    decoy.write_bytes(b"c" * 1024)

    cfg = load(env)
    cleanup_after_upload(cfg, str(video), None,
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert not mine.exists(), "the real re-encode should have been removed"
    assert decoy.exists(), "a character-class match deleted an unrelated file"


def test_source_is_never_deleted_even_if_passed_as_the_censored_path(env):
    """With censoring off, output_path IS the source video."""
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["video"]),
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert env["video"].exists()


# ═════════════════════════════════════════════════════════════════════════════
# Partial failure: retry inputs survive
# ═════════════════════════════════════════════════════════════════════════════

def test_censoring_platform_pending_keeps_the_reencode(env):
    """YouTube failed and censors, so the re-encode is a retry input."""
    cfg = load(env)
    report = cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                                  results=YT_FAILED_RB_OK, since_ts=time.time() - 60)
    assert env["censored_copy"].exists()
    assert env["transcript"].exists()
    assert any("retry youtube" in reason for _, reason in report.kept)


def test_both_failed_keeps_the_reencode(env):
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=BOTH_FAILED, since_ts=time.time() - 60)
    assert env["censored_copy"].exists()
    assert env["transcript"].exists()


def test_interrupted_run_keeps_the_reencode(env):
    """No result recorded for YouTube - it may never have been attempted."""
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results={}, since_ts=time.time() - 60)
    assert env["censored_copy"].exists()


def test_noncensoring_platform_pending_still_deletes_the_reencode(env):
    """The case the two requirements disagree on.

    Rumble failed, but Rumble uploads the ORIGINAL - its retry will never
    read the censored copy, so holding gigabytes for it is pure waste.
    """
    cfg = load(env)
    assert censored_copy_is_safe_to_delete(cfg, YT_OK_RB_FAILED) is True
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=YT_OK_RB_FAILED, since_ts=time.time() - 60)
    assert not env["censored_copy"].exists()
    assert env["video"].exists(), "the retry input for Rumble is the source"


def test_reencode_kept_when_rumble_also_censors(env):
    """Flip rumble.censor_uploads on and the same case reverses."""
    env["raw"]["rumble"]["censor_uploads"] = True
    with open(env["cfg_path"], "w") as f:
        json.dump(env["raw"], f)
    cfg = load(env)
    assert censoring_platforms(cfg) == {"youtube", "rumble"}
    assert censored_copy_is_safe_to_delete(cfg, YT_OK_RB_FAILED) is False
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=YT_OK_RB_FAILED, since_ts=time.time() - 60)
    assert env["censored_copy"].exists()


def test_temp_audio_is_always_removed(env):
    """Temporary either way - censor_video regenerates it from the source."""
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=BOTH_FAILED, since_ts=time.time() - 60)
    assert not env["temp_wav"].exists()


# ═════════════════════════════════════════════════════════════════════════════
# Page dumps
# ═════════════════════════════════════════════════════════════════════════════

def test_dump_from_this_run_is_removed_when_rumble_succeeded(env):
    dump = make_dump(env, age_seconds=0)
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert not dump.exists()


def test_dump_survives_while_rumble_is_still_failing(env):
    """The dump is the evidence for exactly this failure."""
    dump = make_dump(env, age_seconds=0)
    cfg = load(env)
    report = cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                                  results=YT_OK_RB_FAILED, since_ts=time.time() - 60)
    assert dump.exists()
    assert any("Rumble still pending" in reason for _, reason in report.kept)


def test_older_dump_from_another_video_survives(env):
    """Dumps are global; one file succeeding must not wipe another's."""
    theirs = make_dump(env, "rumble_page_dump_old.html", age_seconds=3600)
    mine = make_dump(env, "rumble_page_dump_new.html", age_seconds=0)
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert theirs.exists(), "deleted a dump belonging to a different video"
    assert not mine.exists()


def test_no_run_window_means_no_dump_is_eligible(env):
    dump = make_dump(env, age_seconds=0)
    cfg = load(env)
    report = cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                                  results=BOTH_OK, since_ts=None)
    assert dump.exists()
    assert any("run window" in reason for _, reason in report.kept)


# ═════════════════════════════════════════════════════════════════════════════
# Idempotence
# ═════════════════════════════════════════════════════════════════════════════

def test_running_twice_does_not_fail_and_frees_nothing_extra(env):
    cfg = load(env)
    first = cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                                 results=BOTH_OK, since_ts=time.time() - 60)
    second = cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                                  results=BOTH_OK, since_ts=time.time() - 60)
    assert first.freed_mb > 0
    assert second.freed_mb == 0.0
    assert second.removed == []
    assert env["video"].exists()


def test_three_runs_leave_the_same_state(env):
    cfg = load(env)
    survivor = env["uploaded"] / "keepme.mp4"
    survivor.write_bytes(b"x")
    for _ in range(3):
        cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                             results=BOTH_OK, since_ts=time.time() - 60)
    assert env["video"].exists() and survivor.exists()
    assert os.listdir(env["censored"]) == []


def test_cleanup_on_a_file_that_never_existed(env):
    cfg = load(env)
    report = cleanup_after_upload(cfg, str(env["tmp"] / "ghost.mp4"), None,
                                  results=BOTH_OK, since_ts=time.time() - 60)
    assert report.removed == []
    # Nothing belonging to the real video was matched by the wrong name.
    assert env["censored_copy"].exists() and env["transcript"].exists()


# ═════════════════════════════════════════════════════════════════════════════
# Switches
# ═════════════════════════════════════════════════════════════════════════════

def test_cleanup_can_be_switched_off(env):
    cfg = load(env, cleanup={"enabled": False})
    report = cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                                  results=BOTH_OK, since_ts=time.time() - 60)
    assert report.freed_mb == 0.0
    assert env["censored_copy"].exists() and env["transcript"].exists()


def test_reencode_kept_when_that_switch_is_off(env):
    cfg = load(env, cleanup={"enabled": True, "censored_copy": False,
                             "transcript_cache": True})
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert env["censored_copy"].exists()
    assert not env["transcript"].exists()


def test_page_dumps_kept_when_that_switch_is_off(env):
    dump = make_dump(env, age_seconds=0)
    cfg = load(env, cleanup={"enabled": True, "page_dumps": False})
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert dump.exists()


# ═════════════════════════════════════════════════════════════════════════════
# Logs: bounded, never deleted
# ═════════════════════════════════════════════════════════════════════════════

def big_log(path, lines=200_000):
    with open(path, "w") as f:
        for i in range(lines):
            f.write(f"2026-08-03 12:00:00 [INFO] line {i}\n")
    return os.path.getsize(path)


def test_log_is_trimmed_keeping_the_newest_entries(tmp_path):
    log = tmp_path / "youtube.log"
    before = big_log(log)
    freed = trim_log(str(log), max_mb=1)

    assert freed > 0
    assert log.exists(), "logs are trimmed, never deleted"
    assert log.stat().st_size < before
    text = log.read_text()
    assert "line 199999" in text, "the newest entries must survive"
    assert "line 0\n" not in text, "the oldest should be the ones dropped"


def test_trimmed_log_stays_readable_line_by_line(tmp_path):
    log = tmp_path / "rumble.log"
    big_log(log)
    trim_log(str(log), max_mb=1)
    lines = log.read_text().splitlines()
    assert lines[0].startswith("[log trimmed")
    # Every surviving log line is whole - no truncated first entry.
    for line in lines[1:]:
        assert line.startswith("2026-08-03 12:00:00 [INFO] line "), line


def test_trimming_is_idempotent(tmp_path):
    log = tmp_path / "youtube.log"
    big_log(log)
    assert trim_log(str(log), max_mb=1) > 0
    size_after_first = log.stat().st_size
    assert trim_log(str(log), max_mb=1) == 0.0, "a second trim must be a no-op"
    assert log.stat().st_size == size_after_first


def test_trimmed_log_lands_under_the_limit(tmp_path):
    log = tmp_path / "youtube.log"
    big_log(log)
    trim_log(str(log), max_mb=1)
    assert log.stat().st_size <= 1024 * 1024


def test_log_exactly_at_the_limit_is_left_alone(tmp_path):
    log = tmp_path / "x.log"
    log.write_bytes(b"x" * (1024 * 1024))
    assert trim_log(str(log), max_mb=1) == 0.0


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
    before = log.stat().st_size
    assert trim_log(str(log), max_mb=0) == 0.0
    assert log.stat().st_size == before


def test_cleanup_never_deletes_a_log(env):
    (env["logs"] / "youtube.log").write_text("history\n")
    (env["logs"] / "rumble.log").write_text("history\n")
    cfg = load(env)
    cleanup_after_upload(cfg, str(env["video"]), str(env["censored_copy"]),
                         results=BOTH_OK, since_ts=time.time() - 60)
    assert (env["logs"] / "youtube.log").read_text() == "history\n"
    assert (env["logs"] / "rumble.log").read_text() == "history\n"


# ═════════════════════════════════════════════════════════════════════════════
# source_video: opt-in only
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("value,expected", [
    ("move", SOURCE_MOVE), ("delete", SOURCE_DELETE), ("keep", SOURCE_KEEP),
    ("DELETE", SOURCE_DELETE), (" delete ", SOURCE_DELETE),
    ("nonsense", SOURCE_MOVE), ("", SOURCE_MOVE), (None, SOURCE_MOVE),
])
def test_resolve_source_action(env, value, expected):
    cfg = load(env, cleanup={"source_video": value})
    assert resolve_source_action(cfg) == expected


def test_source_action_defaults_to_move_when_unset(env):
    cfg = load(env, cleanup={})
    assert resolve_source_action(cfg) == SOURCE_MOVE


def test_shipped_config_never_deletes_source_videos():
    """The default must not destroy a video the user may have nowhere else."""
    with open(os.path.join(_UPLOADER, "config.json")) as f:
        shipped = json.load(f)
    assert shipped["general"]["cleanup"]["source_video"] == SOURCE_MOVE
    assert shipped["general"]["cleanup"]["keep_uploaded_videos"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# Pruning uploaded/ - also opt-in
# ═════════════════════════════════════════════════════════════════════════════

def test_prune_keeps_the_newest(env):
    for i in range(5):
        p = env["uploaded"] / f"v{i}.mp4"
        p.write_bytes(b"x" * 2048)
        os.utime(p, (i + 1, i + 1))
    cfg = load(env)
    assert prune_uploaded_folder(cfg, keep_newest=2) > 0
    assert sorted(os.listdir(env["uploaded"])) == ["v3.mp4", "v4.mp4"]


@pytest.mark.parametrize("keep", [0, None, -1])
def test_prune_refuses_to_empty_the_folder(env, keep):
    """0 is the shipped default; it must mean 'off', not 'delete all'."""
    (env["uploaded"] / "v.mp4").write_bytes(b"x")
    cfg = load(env)
    assert prune_uploaded_folder(cfg, keep_newest=keep) == 0.0
    assert os.listdir(env["uploaded"]) == ["v.mp4"]


def test_prune_ignores_non_videos(env):
    (env["uploaded"] / "clip_optimize.md").write_text("keep me")
    for i in range(3):
        p = env["uploaded"] / f"v{i}.mp4"
        p.write_bytes(b"x")
        os.utime(p, (i + 1, i + 1))
    cfg = load(env)
    prune_uploaded_folder(cfg, keep_newest=1)
    assert sorted(os.listdir(env["uploaded"])) == ["clip_optimize.md", "v2.mp4"]


def test_prune_is_idempotent(env):
    for i in range(4):
        p = env["uploaded"] / f"v{i}.mp4"
        p.write_bytes(b"x" * 1024)
        os.utime(p, (i + 1, i + 1))
    cfg = load(env)
    prune_uploaded_folder(cfg, keep_newest=2)
    after = sorted(os.listdir(env["uploaded"]))
    assert prune_uploaded_folder(cfg, keep_newest=2) == 0.0
    assert sorted(os.listdir(env["uploaded"])) == after


def test_prune_missing_folder(env):
    cfg = load(env, uploaded_folder=str(env["tmp"] / "nope"))
    assert prune_uploaded_folder(cfg, keep_newest=1) == 0.0
