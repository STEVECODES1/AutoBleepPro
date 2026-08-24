"""A 370-minute recording became "Nothing was recorded."

Full sequence from a real run: fragments started refusing mid-stream, the
recorder detected a stale manifest, called process.terminate() on
yt-dlp, waited for it to exit, and moved on to reconnect. The reconnect
attempt immediately found the channel offline - the stream had actually
ended - and gave up. finalise() then ran and printed:

    [22:00:32] Nothing was recorded.

The 370 minutes were real. They were sitting on disk the whole time,
under a name nothing was looking for.

yt-dlp writes to "<name>.part" while downloading and renames it to
"<name>" ONLY on a clean finish. download_args() never passes --no-part,
and a stale-manifest restart, a give-up after resuming, Ctrl+C, or the
keepalive loop restarting the whole recorder are all NOT clean finishes -
so the rename never runs. existing_segments() and leftover_fragments()
were each taught to recognise a different, specific shape of leftover
file, and neither one was taught about yt-dlp's own ".part" suffix on a
writer that has already exited. They are right to treat ".part" as "still
being written" for a file whose writer is still running; they were wrong
to apply that same rule to one whose writer is long gone.

--hls-use-mpegts is why this is safe to recover at all - it is the whole
reason this recorder uses that container. Every byte written so far is a
valid, playable .ts. The content was never in danger. Only its filename
was one rename short of ever being found.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_REPO, "tools")
for _path in (_REPO, _TOOLS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import record_stream as rs  # noqa: E402

NAME = "Stackswopo youtube live"
BASE = f"{NAME} 2026-08-23 15_50"


def _write(path: str, content: bytes = b"x" * 4096) -> None:
    with open(path, "wb") as handle:
        handle.write(content)


# ── recognising the abandoned file ────────────────────────────────────────

def test_yt_dlps_own_part_suffix_is_recognised(tmp_path):
    abandoned = tmp_path / f"{BASE}.part01.ts.part"
    _write(str(abandoned))

    found = rs.abandoned_part_files(str(tmp_path), BASE)

    assert found == [str(abandoned)]


def test_a_still_growing_file_is_not_touched_by_size_alone(tmp_path):
    """Zero bytes is what a segment 2 that never got any data looks like -
    a genuinely offline channel, not lost footage."""
    empty = tmp_path / f"{BASE}.part02.ts.part"
    _write(str(empty), b"")

    assert rs.abandoned_part_files(str(tmp_path), BASE) == []


def test_a_different_bases_leftovers_are_not_picked_up(tmp_path):
    _write(str(tmp_path / "Some Other Recording 2026-08-01 00_00.part01.ts.part"))

    assert rs.abandoned_part_files(str(tmp_path), BASE) == []


def test_a_genuinely_finished_segment_is_not_re_touched(tmp_path):
    """existing_segments() already finds these; abandoned_part_files must
    not also claim them - only files still wearing yt-dlp's suffix."""
    _write(str(tmp_path / f"{BASE}.part01.ts"))

    assert rs.abandoned_part_files(str(tmp_path), BASE) == []


# ── recovering it ───────────────────────────────────────────────────────

def test_recovery_strips_exactly_the_trailing_part(tmp_path):
    abandoned = tmp_path / f"{BASE}.part01.ts.part"
    _write(str(abandoned))

    recovered = rs.recover_abandoned_parts(str(tmp_path), BASE)

    assert recovered == [str(tmp_path / f"{BASE}.part01.ts")]
    assert not abandoned.exists()
    assert (tmp_path / f"{BASE}.part01.ts").exists()


def test_the_recovered_file_is_found_by_existing_segments_afterward(tmp_path):
    """This is the actual bug closing: before recovery, existing_segments
    sees nothing here. After, it does."""
    _write(str(tmp_path / f"{BASE}.part01.ts.part"))

    assert rs.existing_segments(str(tmp_path), BASE) == []

    rs.recover_abandoned_parts(str(tmp_path), BASE)

    found = rs.existing_segments(str(tmp_path), BASE)
    assert len(found) == 1


def test_content_is_preserved_exactly_a_rename_not_a_copy(tmp_path):
    payload = os.urandom(4096)
    abandoned = tmp_path / f"{BASE}.part01.ts.part"
    _write(str(abandoned), payload)

    rs.recover_abandoned_parts(str(tmp_path), BASE)

    with open(tmp_path / f"{BASE}.part01.ts", "rb") as handle:
        assert handle.read() == payload


def test_a_missing_staging_folder_is_not_a_crash():
    assert rs.abandoned_part_files("/nowhere/at/all", BASE) == []
    assert rs.recover_abandoned_parts("/nowhere/at/all", BASE) == []


# ── finalise() uses it before declaring failure ───────────────────────────

def test_finalise_recovers_before_checking_existing_segments(tmp_path, monkeypatch):
    """This is the fix in place: a session that would have printed
    "Nothing was recorded" now finds its footage first."""
    _write(str(tmp_path / f"{BASE}.part01.ts.part"))

    recorder = rs.Recorder(url="https://www.youtube.com/@x/live",
                           staging=str(tmp_path), watch_folder=str(tmp_path / "watch"))
    monkeypatch.setattr(recorder, "_remux", lambda source, target: (
        os.rename(source, target) or True))
    monkeypatch.setattr(rs, "probe_duration", lambda path: 22_200.0)
    monkeypatch.setattr(rs, "coverage_report", lambda have, expected: "ok")
    monkeypatch.setattr(rs, "sync_report", lambda path: "ok")

    result = recorder.finalise(BASE)

    assert result is not None
    assert os.path.exists(result)


def test_finalise_says_nothing_was_recorded_only_when_truly_nothing_is_there(tmp_path):
    recorder = rs.Recorder(url="https://www.youtube.com/@x/live",
                           staging=str(tmp_path), watch_folder=str(tmp_path / "watch"))

    assert recorder.finalise(BASE) is None


# ── the startup sweep, matched by the recorder's own name ────────────────

def test_sweep_finds_an_orphaned_base(tmp_path):
    _write(str(tmp_path / f"{BASE}.part01.ts.part"))

    assert rs.sweep_abandoned_recordings(str(tmp_path), NAME) == [BASE]


def test_sweep_ignores_another_recorders_files(tmp_path):
    """One platform's sweep must never pick up another's - twitch and
    youtube recorders share a staging folder."""
    _write(str(tmp_path / "Stackswopo twitch live 2026-08-23 15_50.part01.ts.part"))

    assert rs.sweep_abandoned_recordings(str(tmp_path), NAME) == []


def test_sweep_finds_nothing_when_nothing_is_abandoned(tmp_path):
    assert rs.sweep_abandoned_recordings(str(tmp_path), NAME) == []


def test_sweep_can_recover_more_than_one_leftover_session(tmp_path):
    other_base = f"{NAME} 2026-08-20 09_00"
    _write(str(tmp_path / f"{BASE}.part01.ts.part"))
    _write(str(tmp_path / f"{other_base}.part01.ts.part"))

    found = rs.sweep_abandoned_recordings(str(tmp_path), NAME)

    assert sorted(found) == sorted([BASE, other_base])


def test_the_sweep_runs_once_per_recorder_not_once_per_poll(tmp_path):
    """A recovery that keeps failing - a full disk, a corrupt file - must
    not run ffmpeg over a multi-gigabyte file again every poll interval
    forever."""
    source = open(os.path.join(_REPO, "tools", "record_stream.py"),
                  encoding="utf-8").read()

    assert "_swept_orphans" in source
    assert "self._swept_orphans = True" in source


def test_the_sweep_runs_before_the_channel_is_polled():
    """Recovering old footage should not be delayed behind waiting for a
    stream to start."""
    source = open(os.path.join(_REPO, "tools", "record_stream.py"),
                  encoding="utf-8").read()
    sweep_spot = source.index("sweep_abandoned_recordings(self.staging")
    wait_spot = source.index('self.say(f"Waiting for {self.name} to go live')

    assert sweep_spot < wait_spot


# ── end to end, with real ffmpeg ──────────────────────────────────────────

def _have_ffmpeg() -> bool:
    from shutil import which
    return which("ffmpeg") is not None and which("ffprobe") is not None


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")
def test_a_real_abandoned_ts_is_recovered_and_delivered(tmp_path):
    """The scenario from the log, with a real playable MPEG-TS file and
    the real remux path - not a stand-in."""
    staging = tmp_path / "recording"
    watch = tmp_path / "watch_folder"
    staging.mkdir()

    abandoned = staging / f"{BASE}.part01.ts.part"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=10:duration=2",
         "-f", "lavfi", "-i", "sine=duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-f", "mpegts", str(abandoned)],
        check=True)

    recorder = rs.Recorder(url="https://www.youtube.com/@stackswopo_/live",
                           staging=str(staging), watch_folder=str(watch),
                           name=NAME)

    for orphan_base in rs.sweep_abandoned_recordings(recorder.staging,
                                                      recorder.name):
        recorder.finalise(orphan_base)

    delivered = watch / f"{BASE}.mp4"
    assert delivered.exists(), "the recovered footage was never delivered"

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(delivered)],
        capture_output=True, text=True)
    assert float(probe.stdout.strip()) > 1.5, "the delivered file is not playable"
    assert not abandoned.exists()
    assert not (staging / f"{BASE}.part01.ts").exists(), (
        "the segment should have been consumed by finalise, not left behind")
