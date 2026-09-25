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


# ── the halves of a live-from-start recording ────────────────────────────
# A real 115-minute stream: yt-dlp was stopped at the end of the broadcast
# before merging, leaving "...part01.ts.f299.mp4" (video) and
# "...part01.ts.f140.mp4" (audio). Neither was recognised as a half, so
# finalise printed "Joining 2 segments (the recording was interrupted and
# resumed)" and concatenated the audio AFTER the video.

VIDEO = f"{BASE}.part01.ts.f299.mp4"
AUDIO = f"{BASE}.part01.ts.f140.mp4"


def test_halves_with_an_extension_are_halves():
    assert rs.is_format_fragment(VIDEO)
    assert rs.is_format_fragment(AUDIO)
    assert not rs.is_format_fragment(f"{BASE}.part01.ts.mp4")
    assert not rs.is_format_fragment(f"{BASE}.part01.ts")


def test_video_and_audio_are_never_counted_as_two_segments(tmp_path):
    _write(str(tmp_path / VIDEO))
    _write(str(tmp_path / AUDIO))

    assert rs.existing_segments(str(tmp_path), BASE) == []
    assert len(rs.leftover_fragments(str(tmp_path), BASE)) == 2


def _finalising_recorder(tmp_path, monkeypatch):
    recorder = rs.Recorder(url="https://www.youtube.com/@x/live",
                           staging=str(tmp_path), watch_folder=str(tmp_path / "watch"))
    calls = {"ffmpeg": [], "concat": []}

    def ffmpeg(args):
        calls["ffmpeg"].append(args)
        _write(args[-1])
        return True

    monkeypatch.setattr(recorder, "_ffmpeg", ffmpeg)
    monkeypatch.setattr(recorder, "_remux", lambda source, target: (
        os.rename(source, target) or True))
    monkeypatch.setattr(recorder, "_concat", lambda segments, target: (
        calls["concat"].append(list(segments)) or _write(target) or True))
    monkeypatch.setattr(rs, "probe_duration", lambda path: 100.0)
    monkeypatch.setattr(rs, "coverage_report", lambda have, expected: "ok")
    monkeypatch.setattr(rs, "sync_report", lambda path: "ok")
    monkeypatch.setattr(rs, "channel_is_live", lambda url: False)
    return recorder, calls


def test_the_real_sequence_merges_side_by_side(tmp_path, monkeypatch):
    """Still wearing .part, exactly as yt-dlp left them."""
    _write(str(tmp_path / (VIDEO + ".part")))
    _write(str(tmp_path / (AUDIO + ".part")))
    recorder, calls = _finalising_recorder(tmp_path, monkeypatch)

    assert recorder.finalise(BASE) is not None

    assert calls["concat"] == [], "video and audio were joined end to end"
    merge = calls["ffmpeg"][0]
    assert merge.count("-i") == 2
    assert merge[-1] == str(tmp_path / f"{BASE}.part01.ts")


def test_a_resumed_recording_keeps_its_first_section(tmp_path, monkeypatch):
    """Section 1 left as halves, section 2 finished cleanly: both go in,
    in order."""
    _write(str(tmp_path / VIDEO))
    _write(str(tmp_path / AUDIO))
    _write(str(tmp_path / f"{BASE}.part02.ts.mp4"))
    recorder, calls = _finalising_recorder(tmp_path, monkeypatch)

    recorder.finalise(BASE)

    assert calls["concat"] == [[str(tmp_path / f"{BASE}.part01.ts"),
                                str(tmp_path / f"{BASE}.part02.ts.mp4")]]


def test_halves_yt_dlp_already_merged_are_not_merged_twice(tmp_path, monkeypatch):
    _write(str(tmp_path / VIDEO))
    _write(str(tmp_path / AUDIO))
    _write(str(tmp_path / f"{BASE}.part01.ts.mp4"))
    recorder, calls = _finalising_recorder(tmp_path, monkeypatch)

    recorder.finalise(BASE)

    assert not any("-i" in args and VIDEO in " ".join(args)
                   for args in calls["ffmpeg"])


def test_sweep_finds_orphaned_halves(tmp_path):
    """Including ones an interrupted finalise had already renamed - the
    state the real recording was left in after Ctrl+C."""
    _write(str(tmp_path / VIDEO))
    _write(str(tmp_path / AUDIO))
    assert rs.sweep_abandoned_recordings(str(tmp_path), NAME) == [BASE]


def test_sweep_finds_orphaned_halves_still_wearing_part(tmp_path):
    _write(str(tmp_path / (VIDEO + ".part")))
    assert rs.sweep_abandoned_recordings(str(tmp_path), NAME) == [BASE]


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg not installed")
def test_real_halves_become_one_file_with_picture_and_sound(tmp_path):
    staging = tmp_path / "recording"
    watch = tmp_path / "watch_folder"
    staging.mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc=size=320x240:rate=10:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    str(staging / VIDEO)], check=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "sine=duration=2", "-c:a", "aac",
                    str(staging / AUDIO)], check=True)

    recorder = rs.Recorder(url="https://www.youtube.com/@stackswopo_/live",
                           staging=str(staging), watch_folder=str(watch),
                           name=NAME)
    for orphan_base in rs.sweep_abandoned_recordings(recorder.staging,
                                                      recorder.name):
        recorder.finalise(orphan_base)

    delivered = watch / f"{BASE}.mp4"
    assert delivered.exists()
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type:format=duration", "-of", "csv=p=0",
         str(delivered)], capture_output=True, text=True).stdout
    assert "video" in probe and "audio" in probe
    duration = float([l for l in probe.splitlines()
                      if l and l[0].isdigit()][-1])
    assert 1.5 < duration < 3.0, f"{duration}s - joined end to end?"
