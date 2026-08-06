"""
Stream recorder: the parts that decide whether a stream is saved or lost.

Nothing here runs yt-dlp or ffmpeg. What is tested is the logic that was
getting recordings truncated: the flags that keep a long download alive,
whether a drop-out is treated as a blip or as the stream ending, and
segment ordering - concatenating segments in the wrong order would be
silent and would ruin the recording.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_REPO, "tools")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

from record_stream import (  # noqa: E402
    MAX_RESUMES,
    RESUME_WINDOW_S,
    Recorder,
    build_concat_list,
    existing_segments,
    safe_name,
    segment_path,
    should_resume,
)


@pytest.fixture
def recorder(tmp_path):
    return Recorder(url="https://www.youtube.com/@stackswopo_/live",
                    staging=str(tmp_path / "recording"),
                    watch_folder=str(tmp_path / "watch_folder"),
                    name="Stackswopo")


# ═════════════════════════════════════════════════════════════════════════════
# The flags that keep a long recording alive
# ═════════════════════════════════════════════════════════════════════════════

def test_fragments_are_retried_forever(recorder):
    """The default of ten gives up partway through a four-hour stream, and
    the rest of the stream is then simply gone."""
    args = recorder.download_args("/tmp/out.ts")
    assert args[args.index("--fragment-retries") + 1] == "infinite"
    assert args[args.index("--retries") + 1] == "infinite"


def test_output_is_mpegts_so_an_interrupted_file_still_plays(recorder):
    """An interrupted .mp4 usually will not open at all - the index that
    makes it playable is written last."""
    assert "--hls-use-mpegts" in recorder.download_args("/tmp/out.ts")


def test_recording_starts_from_the_beginning_of_the_stream(recorder):
    assert "--live-from-start" in recorder.download_args("/tmp/out.ts")


def test_the_first_attempt_waits_for_the_channel_to_go_live(recorder):
    args = recorder.download_args("/tmp/out.ts", wait=True)
    assert "--wait-for-video" in args


def test_a_resume_does_not_wait(recorder):
    """On a resume the stream is already live; waiting would lose minutes
    of it for no reason."""
    assert "--wait-for-video" not in recorder.download_args("/tmp/out.ts", wait=False)


def test_a_socket_timeout_is_set(recorder):
    """Without one, a stalled connection hangs instead of retrying."""
    assert "--socket-timeout" in recorder.download_args("/tmp/out.ts")


def test_the_url_is_last_and_the_output_is_passed(recorder):
    args = recorder.download_args("/tmp/out.ts")
    assert args[-1] == recorder.url
    assert args[args.index("-o") + 1] == "/tmp/out.ts"


# ═════════════════════════════════════════════════════════════════════════════
# Blip, or the stream actually ending?
# ═════════════════════════════════════════════════════════════════════════════

def test_a_drop_after_a_long_run_is_resumed():
    """The stream is almost certainly still live - reconnecting costs a
    small gap instead of the entire remainder."""
    assert should_resume(started_at=0, ended_at=3600, resumes=0)


def test_an_immediate_exit_is_not_resumed():
    """A channel that is simply offline must not be retried in a tight
    loop."""
    assert not should_resume(started_at=0, ended_at=5, resumes=0)


def test_the_resume_threshold_is_the_documented_window():
    assert should_resume(0, RESUME_WINDOW_S + 1, 0)
    assert not should_resume(0, RESUME_WINDOW_S - 1, 0)


def test_resuming_stops_eventually():
    """Reconnecting forever would fill the disk with fragments."""
    assert not should_resume(0, 3600, resumes=MAX_RESUMES)
    assert should_resume(0, 3600, resumes=MAX_RESUMES - 1)


# ═════════════════════════════════════════════════════════════════════════════
# Segments: order matters, silently
# ═════════════════════════════════════════════════════════════════════════════

def test_segments_are_zero_padded_so_they_sort_correctly():
    """part10 sorting before part2 would join the stream out of order and
    nothing would report it."""
    paths = [os.path.basename(segment_path("/s", "x", i)) for i in (1, 2, 10, 11)]
    assert paths == sorted(paths)


def test_segments_are_found_in_order(tmp_path):
    staging = tmp_path / "recording"
    staging.mkdir()
    for i in (3, 1, 2):
        (staging / os.path.basename(segment_path("", "show", i))).write_bytes(b"x")
    found = existing_segments(str(staging), "show")
    assert [os.path.basename(p) for p in found] == sorted(
        os.path.basename(p) for p in found)
    assert len(found) == 3


def test_empty_segments_are_ignored(tmp_path):
    """A zero-byte segment from a connection that never delivered anything
    would make the concat fail."""
    staging = tmp_path / "recording"
    staging.mkdir()
    (staging / os.path.basename(segment_path("", "show", 1))).write_bytes(b"data")
    (staging / os.path.basename(segment_path("", "show", 2))).write_bytes(b"")
    assert len(existing_segments(str(staging), "show")) == 1


def test_segments_of_another_recording_are_not_picked_up(tmp_path):
    staging = tmp_path / "recording"
    staging.mkdir()
    (staging / os.path.basename(segment_path("", "showA", 1))).write_bytes(b"x")
    (staging / os.path.basename(segment_path("", "showB", 1))).write_bytes(b"x")
    assert len(existing_segments(str(staging), "showA")) == 1


def test_missing_staging_folder_is_not_an_error():
    assert existing_segments("/nonexistent/path", "show") == []


# ═════════════════════════════════════════════════════════════════════════════
# The concat list ffmpeg reads
# ═════════════════════════════════════════════════════════════════════════════

def test_concat_list_quotes_each_file(tmp_path):
    list_path = str(tmp_path / "list.txt")
    build_concat_list([str(tmp_path / "a.ts"), str(tmp_path / "b.ts")], list_path)
    lines = open(list_path, encoding="utf-8").read().splitlines()
    assert len(lines) == 2
    assert all(line.startswith("file '") and line.endswith("'") for line in lines)


def test_concat_list_escapes_an_apostrophe(tmp_path):
    """A stream titled "it's over" would otherwise break the parse."""
    list_path = str(tmp_path / "list.txt")
    build_concat_list([str(tmp_path / "it's over.ts")], list_path)
    assert r"'\''" in open(list_path, encoding="utf-8").read()


def test_concat_list_preserves_order(tmp_path):
    list_path = str(tmp_path / "list.txt")
    paths = [str(tmp_path / f"part{i:02d}.ts") for i in (1, 2, 3)]
    build_concat_list(paths, list_path)
    body = open(list_path, encoding="utf-8").read()
    assert body.index("part01") < body.index("part02") < body.index("part03")


# ═════════════════════════════════════════════════════════════════════════════
# Filenames Windows will accept
# ═════════════════════════════════════════════════════════════════════════════

def test_illegal_characters_are_replaced():
    name = safe_name('LOL NO / "DAMN" : 3/12/26 <live>')
    assert not any(c in name for c in '/\\:*?"<>|')


def test_a_readable_title_survives():
    assert safe_name("!howl 3-12-26 Stackswopo Kick Stream") == \
        "!howl 3-12-26 Stackswopo Kick Stream"


def test_an_empty_title_still_produces_a_name():
    assert safe_name("") == "stream"
    assert safe_name("///") not in ("", None)


def test_very_long_titles_are_truncated():
    assert len(safe_name("x" * 500)) <= 120


def test_a_name_never_ends_in_a_dot_or_space():
    """Windows silently strips those and the file ends up somewhere else."""
    assert not safe_name("trailing dot.").endswith(".")
    assert not safe_name("trailing space ").endswith(" ")


# ═════════════════════════════════════════════════════════════════════════════
# Delivery
# ═════════════════════════════════════════════════════════════════════════════

def test_nothing_recorded_delivers_nothing(recorder):
    os.makedirs(recorder.staging, exist_ok=True)
    assert recorder.finalise("show") is None


def test_a_failed_join_keeps_the_segments(recorder, monkeypatch, tmp_path):
    """Losing the join is annoying; losing the recording because the join
    failed would not be."""
    os.makedirs(recorder.staging, exist_ok=True)
    kept = segment_path(recorder.staging, "show", 1)
    with open(kept, "wb") as f:
        f.write(b"data")

    monkeypatch.setattr(Recorder, "_ffmpeg", lambda self, args: False)
    assert recorder.finalise("show") is None
    assert os.path.exists(kept), "segments were deleted after a failed join"


# ═════════════════════════════════════════════════════════════════════════════
# The failures that look like "it just stopped"
# ═════════════════════════════════════════════════════════════════════════════

def test_keep_awake_is_a_no_op_off_windows():
    """It must not crash the recorder on any other platform."""
    from record_stream import KeepAwake

    with KeepAwake() as awake:
        assert awake.active in (True, False)


def test_a_full_disk_is_warned_about_before_recording(tmp_path, monkeypatch):
    """Running out of disk four hours in looks exactly like the stream
    ending early, and loses the same amount of footage."""
    import record_stream

    monkeypatch.setattr(record_stream, "free_bytes", lambda p: 2_000_000_000)
    warning = record_stream.disk_warning(str(tmp_path))
    assert "GB free" in warning


def test_plenty_of_disk_warns_about_nothing(tmp_path, monkeypatch):
    import record_stream

    monkeypatch.setattr(record_stream, "free_bytes", lambda p: 900_000_000_000)
    assert record_stream.disk_warning(str(tmp_path)) == ""


def test_yt_dlp_output_is_written_to_a_log(recorder, tmp_path, monkeypatch):
    """Without this there is nothing to look at after a recording stops
    early, which is why "it only got 3 of 5 hours" had no explanation."""
    log_path = str(tmp_path / "rec" / "run.log")
    script = ("import sys; print('[download] frag 1'); "
              "print('ERROR: fragment 4210 not found'); sys.exit(1)")

    monkeypatch.setattr(recorder, "download_args",
                        lambda out, wait=True: [sys.executable, "-c", script])
    code = recorder._run(recorder.download_args("x"), log_path)

    assert code == 1
    body = open(log_path, encoding="utf-8").read()
    assert "fragment 4210 not found" in body


def test_the_reason_it_stopped_is_printed(recorder, tmp_path, capsys):
    """Reading a log file is a step; seeing the reason on screen is not."""
    script = ("import sys; print('ERROR: HTTP Error 403: Forbidden'); "
              "sys.exit(1)")
    recorder._run([sys.executable, "-c", script],
                  str(tmp_path / "rec" / "run.log"))
    assert "403" in capsys.readouterr().out


def test_a_clean_run_does_not_print_a_scary_tail(recorder, tmp_path, capsys):
    recorder._run([sys.executable, "-c", "print('done')"],
                  str(tmp_path / "rec" / "run.log"))
    assert "Last thing yt-dlp said" not in capsys.readouterr().out


# ═════════════════════════════════════════════════════════════════════════════
# Did we actually get the whole stream?
# ═════════════════════════════════════════════════════════════════════════════

def test_a_complete_recording_reports_complete():
    from record_stream import coverage_report

    assert "complete" in coverage_report(5 * 3600, 5 * 3600)


def test_a_short_recording_is_called_out_with_the_gap():
    """The exact case that went unnoticed: five hours streamed, three
    recorded, and no indication until someone watched it."""
    from record_stream import coverage_report

    report = coverage_report(3 * 3600, 5 * 3600)
    assert report.startswith("SHORT")
    assert "120 min missing" in report
    assert "60%" in report


def test_small_losses_at_the_seams_are_not_flagged():
    """Joining segments drops a second or two each time; calling that a
    failure would make the check noise."""
    from record_stream import coverage_report

    assert not coverage_report(3599, 3600).startswith("SHORT")


def test_an_unknown_stream_length_still_reports_what_was_recorded():
    from record_stream import coverage_report

    assert "1.00h" in coverage_report(3600, None)


def test_an_unmeasurable_recording_says_so():
    from record_stream import coverage_report

    assert "could not measure" in coverage_report(None, 3600)


def test_probe_survives_a_missing_ffprobe(monkeypatch):
    import record_stream

    monkeypatch.setattr(record_stream.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert record_stream.probe_duration("x.mp4") is None
    assert record_stream.expected_duration("url") is None


# ═════════════════════════════════════════════════════════════════════════════
# yt-dlp's unmerged halves ARE the recording
# ═════════════════════════════════════════════════════════════════════════════

def test_format_fragments_are_recognised():
    """yt-dlp names the video-only and audio-only halves .f299/.f140 while
    it works; they are not finished segments."""
    from record_stream import is_format_fragment

    assert is_format_fragment("Stackswopo 2026-08-05 12_54.part01.ts.f299")
    assert is_format_fragment("show.part01.ts.f140")
    assert not is_format_fragment("show.part01.ts")


def test_fragments_are_not_mistaken_for_finished_segments(tmp_path):
    """Counting a video-only half as a segment would deliver a silent
    video to the uploader."""
    staging = tmp_path / "recording"
    staging.mkdir()
    (staging / "show.part01.ts.f299").write_bytes(b"video")
    (staging / "show.part01.ts.f140").write_bytes(b"audio")
    assert existing_segments(str(staging), "show") == []


def test_leftover_halves_are_found_so_the_recording_is_not_lost(tmp_path):
    """These hold the whole stream. Reporting "nothing was recorded"
    because the merge did not run would throw away hours of footage."""
    from record_stream import leftover_fragments

    staging = tmp_path / "recording"
    staging.mkdir()
    (staging / "show.part01.ts.f299").write_bytes(b"video")
    (staging / "show.part01.ts.f140").write_bytes(b"audio")
    (staging / "show.part01.ts.f000").write_bytes(b"")      # empty, ignored
    assert len(leftover_fragments(str(staging), "show")) == 2


def test_a_recording_is_recovered_from_its_halves(recorder, monkeypatch):
    """The exact case seen in the wild: two .ts.fNNN files and no .ts."""
    os.makedirs(recorder.staging, exist_ok=True)
    for suffix in (".ts.f299", ".ts.f140"):
        with open(os.path.join(recorder.staging, f"show.part01{suffix}"), "wb") as f:
            f.write(b"data")

    merged = []

    def fake_ffmpeg(self, args):
        merged.append(args)
        open(args[-1], "wb").write(b"joined")
        return True

    monkeypatch.setattr(Recorder, "_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(Recorder, "_remux", lambda self, src, dst:
                        (open(dst, "wb").write(b"mp4"), True)[1])
    result = recorder.finalise("show")

    assert result is not None, "the recording was declared lost"
    assert merged, "ffmpeg was never asked to join the halves"


def test_a_failed_recovery_keeps_the_halves(recorder, monkeypatch):
    os.makedirs(recorder.staging, exist_ok=True)
    kept = os.path.join(recorder.staging, "show.part01.ts.f299")
    with open(kept, "wb") as f:
        f.write(b"data")

    monkeypatch.setattr(Recorder, "_ffmpeg", lambda self, args: False)
    assert recorder.finalise("show") is None
    assert os.path.exists(kept), "the only copy of the recording was deleted"
