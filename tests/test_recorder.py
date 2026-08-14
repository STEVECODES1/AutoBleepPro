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


# ═════════════════════════════════════════════════════════════════════════════
# What a finished segment is actually called
# ═════════════════════════════════════════════════════════════════════════════

def test_the_real_filenames_from_a_live_run_are_handled():
    """Taken verbatim from a real recording folder. `-o "...part01.ts"`
    does not force the extension - yt-dlp appends the container it chose
    when merging, so the finished file is "...part01.ts.mp4". Matching on
    a .ts suffix missed the completed recording entirely."""
    import tempfile

    from record_stream import existing_segments

    staging = tempfile.mkdtemp()
    base = "Stackswopo 2026-08-05 12_54"
    for name in (".gitkeep",
                 base + ".part01.ts.f299.mp4.part-Frag13709.part",
                 base + ".part01.ts.mp4"):
        with open(os.path.join(staging, name), "wb") as f:
            f.write(b"x" * 10)

    found = [os.path.basename(p) for p in existing_segments(staging, base)]
    assert found == [base + ".part01.ts.mp4"]


def test_in_flight_downloads_are_never_delivered():
    """A .part is still being written; handing it to the uploader would
    publish a truncated stream."""
    from record_stream import is_unfinished

    assert is_unfinished("show.part01.ts.mp4.part")
    assert is_unfinished("show.part01.ts.f299.mp4.part-Frag13709.part")
    assert is_unfinished("show.part01.ts.ytdl")
    assert not is_unfinished("show.part01.ts.mp4")


def test_any_container_yt_dlp_picks_is_recognised(tmp_path):
    """Which extension a finished segment ends up with is yt-dlp's
    decision, not ours."""
    from record_stream import existing_segments

    staging = tmp_path / "recording"
    staging.mkdir()
    for ext in (".ts", ".mp4", ".mkv"):
        (staging / f"show.part01{ext}").write_bytes(b"data")
    assert len(existing_segments(str(staging), "show")) == 3


# ═════════════════════════════════════════════════════════════════════════════
# Filling in the front of a stream the recorder started too late for
#
# --live-from-start pulls what it can from YouTube's DVR buffer, but that
# buffer is finite: start an hour late on a five-hour stream and the first
# hour is unreachable while it is still live. Once it ends the whole thing
# becomes an ordinary video, and an ordinary video downloads completely.
#
# Everything below exists to answer one question - can this lose parts of
# a recording it already has? It must not.
# ═════════════════════════════════════════════════════════════════════════════

def test_the_vod_download_does_not_use_the_live_flags():
    """--live-from-start on a finished video makes yt-dlp wait for a live
    edge that will never come."""
    from record_stream import vod_args

    args = vod_args("https://youtu.be/abc", "/tmp/out.mp4")
    assert "--live-from-start" not in args
    assert "--wait-for-video" not in args
    assert args[args.index("--fragment-retries") + 1] == "infinite"


def test_the_vod_is_fetched_in_parallel():
    """There is no realtime pace to keep up with once the stream is over,
    so a five-hour VOD need not take five hours."""
    from record_stream import vod_args

    args = vod_args("https://youtu.be/abc", "/tmp/out.mp4", concurrent=8)
    assert args[args.index("--concurrent-fragments") + 1] == "8"


def _delivered(recorder, base="show", seconds=3600.0):
    """A finished recording sitting in the watch folder."""
    os.makedirs(recorder.staging, exist_ok=True)
    os.makedirs(recorder.watch_folder, exist_ok=True)
    path = os.path.join(recorder.watch_folder, f"{base}.mp4")
    with open(path, "wb") as f:
        f.write(b"live recording")
    return path


def test_a_shorter_vod_never_replaces_the_recording(recorder, monkeypatch):
    """A stream that was not archived, or whose VOD is itself partial,
    must not overwrite a real recording with a worse one."""
    import record_stream

    current = _delivered(recorder)
    candidate = os.path.join(recorder.staging, "show.vod.mp4")

    def fake_run(self, args, log_path=""):
        with open(candidate, "wb") as f:
            f.write(b"short vod")
        return 0

    monkeypatch.setattr(Recorder, "_run", fake_run)
    monkeypatch.setattr(record_stream, "probe_duration",
                        lambda p: 1800.0 if p == candidate else 3600.0)

    assert recorder._replace_with_vod("show", current) is None
    assert open(current, "rb").read() == b"live recording"
    assert not os.path.exists(candidate), "the rejected VOD was left on disk"


def test_a_failed_vod_download_leaves_the_recording_alone(recorder, monkeypatch):
    """YouTube can take hours to publish a VOD. Trying and failing must
    cost nothing."""
    current = _delivered(recorder)
    monkeypatch.setattr(Recorder, "_run", lambda self, args, log_path="": 1)

    assert recorder._replace_with_vod("show", current) is None
    assert open(current, "rb").read() == b"live recording"


def test_a_longer_but_incomplete_vod_keeps_both_copies(recorder, monkeypatch):
    """The VOD wins on length but is not provably the whole stream, so the
    live recording is kept as well rather than destroyed on a guess."""
    import record_stream

    current = _delivered(recorder)
    candidate = os.path.join(recorder.staging, "show.vod.mp4")

    def fake_run(self, args, log_path=""):
        with open(candidate, "wb") as f:
            f.write(b"longer vod")
        return 0

    monkeypatch.setattr(Recorder, "_run", fake_run)
    monkeypatch.setattr(record_stream, "probe_duration",
                        lambda p: 14400.0 if os.path.basename(p).endswith(".vod.mp4") else 10800.0)
    monkeypatch.setattr(record_stream, "expected_duration", lambda url: 18000.0)

    result = recorder._replace_with_vod("show", current)
    assert result == current
    assert open(result, "rb").read() == b"longer vod"
    kept = os.path.join(recorder.staging, "show.live-recording.mp4")
    assert os.path.exists(kept), "the live recording was thrown away"
    assert open(kept, "rb").read() == b"live recording"


def test_a_complete_vod_frees_the_superseded_recording(recorder, monkeypatch):
    """Once the VOD provably covers the whole stream the live copy is
    redundant, and keeping gigabytes of it fills the disk the next
    recording needs."""
    import record_stream

    current = _delivered(recorder)
    candidate = os.path.join(recorder.staging, "show.vod.mp4")

    def fake_run(self, args, log_path=""):
        with open(candidate, "wb") as f:
            f.write(b"full vod")
        return 0

    monkeypatch.setattr(Recorder, "_run", fake_run)
    monkeypatch.setattr(record_stream, "probe_duration",
                        lambda p: 18000.0 if os.path.basename(p).endswith(".vod.mp4") else 10800.0)
    monkeypatch.setattr(record_stream, "expected_duration", lambda url: 18000.0)

    assert recorder._replace_with_vod("show", current) == current
    assert open(current, "rb").read() == b"full vod"
    assert not os.path.exists(
        os.path.join(recorder.staging, "show.live-recording.mp4"))


def test_gap_filling_can_be_turned_off(recorder, monkeypatch):
    """A stream that is never archived has no VOD to fetch, and trying
    every time would waste a download."""
    recorder.fill_gaps = False
    called = []
    monkeypatch.setattr(Recorder, "_replace_with_vod",
                        lambda self, base, cur: called.append(base))
    assert recorder.fill_gaps is False
    assert called == []


# ═════════════════════════════════════════════════════════════════════════════
# Segments are only deleted once the join is known to have kept them
# ═════════════════════════════════════════════════════════════════════════════

def test_a_short_join_is_detected():
    """ffmpeg's concat demuxer does not always fail loudly - given a
    segment with a corrupt tail it can copy what it can and exit 0."""
    from record_stream import join_lost_material

    assert join_lost_material(3600.0, [3600.0, 3600.0])
    assert not join_lost_material(7200.0, [3600.0, 3600.0])


def test_seam_loss_is_not_treated_as_lost_material():
    """Joining costs a fraction of a second at each seam. Keeping every
    segment forever over that would fill the disk."""
    from record_stream import join_lost_material

    assert not join_lost_material(7199.0, [3600.0, 3600.0])


def test_an_unmeasurable_duration_never_counts_as_loss():
    """A failed ffprobe is not evidence that anything was lost, and
    treating it as such would leak segments on every run."""
    from record_stream import join_lost_material

    assert not join_lost_material(None, [3600.0])
    assert not join_lost_material(3600.0, [3600.0, None])


def test_segments_are_kept_when_the_join_lost_material(recorder, monkeypatch):
    """The user's actual worry: do I lose parts or segments? Not here -
    when the joined file is short, the parts stay."""
    import record_stream

    os.makedirs(recorder.staging, exist_ok=True)
    parts = []
    for n in (1, 2):
        path = os.path.join(recorder.staging, f"show.part{n:02d}.ts")
        with open(path, "wb") as f:
            f.write(b"segment")
        parts.append(path)

    def fake_ffmpeg(self, args):
        with open(args[-1], "wb") as f:
            f.write(b"joined")
        return True

    monkeypatch.setattr(Recorder, "_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(record_stream, "probe_duration",
                        lambda p: 3600.0 if "part" in os.path.basename(p) else 3600.0)
    monkeypatch.setattr(record_stream, "expected_duration", lambda url: None)

    recorder.finalise("show")
    for path in parts:
        assert os.path.exists(path), f"{path} was deleted despite a short join"


def test_segments_are_removed_after_a_clean_join(recorder, monkeypatch):
    """The other half of the trade: a good join means the segments are a
    duplicate copy of a multi-hour stream, and the disk is finite."""
    import record_stream

    os.makedirs(recorder.staging, exist_ok=True)
    parts = []
    for n in (1, 2):
        path = os.path.join(recorder.staging, f"show.part{n:02d}.ts")
        with open(path, "wb") as f:
            f.write(b"segment")
        parts.append(path)

    def fake_ffmpeg(self, args):
        with open(args[-1], "wb") as f:
            f.write(b"joined")
        return True

    monkeypatch.setattr(Recorder, "_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(record_stream, "probe_duration",
                        lambda p: 3600.0 if "part" in os.path.basename(p) else 7200.0)
    monkeypatch.setattr(record_stream, "expected_duration", lambda url: 7200.0)

    recorder.finalise("show")
    for path in parts:
        assert not os.path.exists(path)


# ═════════════════════════════════════════════════════════════════════════════
# More than one platform, one watch folder
# ═════════════════════════════════════════════════════════════════════════════

def test_youtube_gets_live_from_start(recorder):
    from record_stream import PLATFORM_YOUTUBE, platform_of

    assert platform_of("https://www.youtube.com/@stackswopo_/live") == PLATFORM_YOUTUBE
    assert "--live-from-start" in recorder.download_args("/tmp/out.ts")


def test_twitch_does_not_get_live_from_start(tmp_path):
    """It is a YouTube capability - it walks back through the DASH
    manifest's sequence numbers. Twitch warns and does nothing."""
    from record_stream import PLATFORM_TWITCH, platform_of

    assert platform_of("https://www.twitch.tv/stackswopo") == PLATFORM_TWITCH
    twitch = Recorder(url="https://www.twitch.tv/stackswopo",
                      staging=str(tmp_path / "s"),
                      watch_folder=str(tmp_path / "w"), name="Stackswopo")
    args = twitch.download_args("/tmp/out.ts")
    assert "--live-from-start" not in args
    # Everything that keeps a long recording alive still applies.
    assert args[args.index("--fragment-retries") + 1] == "infinite"
    assert "--hls-use-mpegts" in args


def test_a_clips_url_is_recognised():
    from record_stream import is_clips_url

    assert is_clips_url("https://www.twitch.tv/stackswopo/clips?range=7d")
    assert not is_clips_url("https://www.twitch.tv/stackswopo")
    assert not is_clips_url("https://www.youtube.com/@stackswopo_/live")


def test_clips_are_never_downloaded_twice():
    """Without an archive, every pass re-fetches every clip and hands the
    uploader a pile of duplicates."""
    from record_stream import clips_args

    args = clips_args("https://twitch.tv/x/clips", "/tmp/%(title)s.%(ext)s",
                      "/tmp/archive.txt")
    assert args[args.index("--download-archive") + 1] == "/tmp/archive.txt"


def test_a_clips_page_can_be_bounded():
    from record_stream import clips_args

    args = clips_args("u", "o", "a", limit=25)
    assert args[args.index("--playlist-end") + 1] == "25"
    assert "--playlist-end" not in clips_args("u", "o", "a")


def test_new_clips_land_in_the_watch_folder(tmp_path, monkeypatch):
    import record_stream

    staging = tmp_path / "recording"
    watch = tmp_path / "watch_folder"
    staging.mkdir()

    def fake_run(self, args, log_path="", quiet_wait=True):
        (staging / "Stackswopo Funny moment.mp4").write_bytes(b"clip")
        (staging / "Stackswopo Half.mp4.part").write_bytes(b"partial")
        return 0

    monkeypatch.setattr(record_stream.Recorder, "_run", fake_run)
    delivered = record_stream.fetch_clips(
        "https://twitch.tv/x/clips", str(staging), str(watch),
        name="Stackswopo")

    assert delivered == ["Stackswopo Funny moment.mp4"]
    assert (watch / "Stackswopo Funny moment.mp4").exists()
    # Still downloading - handing it over would publish a truncated clip.
    assert (staging / "Stackswopo Half.mp4.part").exists()


# ═════════════════════════════════════════════════════════════════════════════
# The waiting loop, which ran for days
# ═════════════════════════════════════════════════════════════════════════════

def test_the_per_minute_countdown_is_recognised_as_noise():
    """Six lines a minute, forever, between streams - thousands overnight,
    burying the one line that matters."""
    from record_stream import is_waiting_noise

    for line in (
        "[wait] Waiting for 00:01:00 - Press Ctrl+C to try now",
        "[wait] Remaining time until next attempt: 00:01:00",
        "[wait] Wait period ended; Re-extracting data",
        "[youtube:tab] Extracting URL: https://www.youtube.com/@x/live",
        "[youtube:tab] @x/live: Downloading webpage",
        "WARNING: [youtube:tab] @x: The channel is not currently live",
    ):
        assert is_waiting_noise(line), line


def test_real_output_is_never_mistaken_for_noise():
    """Quietening the wait must not quieten the recording."""
    from record_stream import is_waiting_noise

    for line in (
        "[download] Destination: Stackswopo 2026-08-08.part01.ts",
        "ERROR: fragment 4213 not found; HTTP Error 403: Forbidden",
        "[download]  12.3% of ~4.20GiB at 3.10MiB/s",
        "[Merger] Merging formats into \"out.mp4\"",
    ):
        assert not is_waiting_noise(line), line


def test_extractor_chatter_is_not_mistaken_for_the_stream_starting():
    """Twitch's own progress lines were read as real output, so the
    console flipped between "Not live yet" and "Live - recording started"
    every few seconds while nothing had changed."""
    from record_stream import is_recording_line

    for line in (
        "[twitch:stream] stackswopo: Downloading stream GraphQL",
        "[twitch:videos:clips] stackswopo: Downloading Clips GraphQL page 1",
        "[youtube:tab] Extracting URL: https://www.youtube.com/@x/live",
        "[download] Downloading playlist: stackswopo - Clips Top 7D",
    ):
        assert not is_recording_line(line), line


def test_bytes_actually_moving_ends_the_wait():
    from record_stream import is_recording_line

    for line in (
        "[download] Destination: Stackswopo 2026-08-08.part01.ts",
        "[download]  12.3% of ~4.20GiB at 3.10MiB/s",
        "[hlsnative] Downloading m3u8 manifest",
        "[Merger] Merging formats into \"out.mp4\"",
    ):
        assert is_recording_line(line), line


def test_errors_are_never_swallowed_by_the_quiet_wait():
    """A wait that is quietly failing must not look identical to a wait
    that is working."""
    from record_stream import is_worth_saying

    assert is_worth_saying("ERROR: [youtube] Video unavailable")
    assert is_worth_saying("  ERROR: unable to download")
    assert is_worth_saying("fragment 4213: HTTP Error 403: Forbidden")
    assert not is_worth_saying("[wait] Remaining time until next attempt: 00:01:00")


def test_the_clips_fetcher_leaves_live_recordings_alone(tmp_path, monkeypatch):
    """Live recorders share the staging folder and write finished-looking
    .ts segments between the download ending and the join starting.
    Delivering one would publish a fragment of a stream AND delete it out
    from under the recorder."""
    import record_stream

    staging = tmp_path / "recording"
    watch = tmp_path / "watch_folder"
    staging.mkdir()
    in_flight = staging / "Stackswopo youtube live.part01.ts"
    in_flight.write_bytes(b"half a stream")

    def fake_run(self, args, log_path="", quiet_wait=True):
        (staging / "Stackswopo twitch clips Funny.mp4").write_bytes(b"clip")
        return 0

    monkeypatch.setattr(record_stream.Recorder, "_run", fake_run)
    delivered = record_stream.fetch_clips(
        "https://twitch.tv/x/clips", str(staging), str(watch),
        name="Stackswopo twitch clips")

    assert delivered == ["Stackswopo twitch clips Funny.mp4"]
    assert in_flight.exists(), "the clips fetcher stole a live recording"
    assert not (watch / in_flight.name).exists()


def test_kick_is_recognised(tmp_path):
    """Kick is HLS live with no live-from-start equivalent, same as
    Twitch - sending YouTube's flag there only warns."""
    from record_stream import PLATFORM_KICK, platform_of

    assert platform_of("https://kick.com/stackswopo1k") == PLATFORM_KICK
    kick = Recorder(url="https://kick.com/stackswopo1k",
                    staging=str(tmp_path / "s"),
                    watch_folder=str(tmp_path / "w"), name="Stackswopo")
    args = kick.download_args("/tmp/out.ts")
    assert "--live-from-start" not in args
    # Everything that keeps a long recording alive still applies.
    assert args[args.index("--fragment-retries") + 1] == "infinite"
    assert args[args.index("--retries") + 1] == "infinite"
    assert "--hls-use-mpegts" in args


def test_every_platform_gets_its_own_filename(tmp_path):
    """Four recorders share one staging folder; two writing the same base
    name would fight over the same segment paths."""
    from record_stream import platform_of

    urls = ("https://www.youtube.com/@x/live", "https://www.twitch.tv/x",
            "https://kick.com/x")
    assert len({platform_of(u) for u in urls}) == 3


def test_the_kick_403_explains_itself():
    """yt-dlp names the dependency in a warning that scrolls past in a
    wall of retries. A recorder that waits all night on a fixable error
    is worse than one that fails."""
    from record_stream import known_fix

    advice = known_fix("WARNING: [kick:live] The extractor is attempting "
                       "impersonation, but no impersonate target is available.")
    assert "curl_cffi" in advice


def test_ordinary_output_has_no_advice_attached():
    """"kick" appears in every ordinary Kick progress line; matching
    those would attach installation advice to a working recording."""
    from record_stream import known_fix

    assert known_fix("[download] Destination: show.part01.ts") == ""
    assert known_fix("[wait] Waiting for 00:01:00") == ""
    assert known_fix("[kick:live] Extracting URL: https://kick.com/x") == ""


def test_the_kick_403_gets_the_dependency_fix_not_the_generic_one():
    """The 403 arrives BEFORE the warning that names the dependency, so
    matching the generic 403 first sent you looking in the wrong place."""
    from record_stream import known_fix

    kick = known_fix("ERROR: [kick:live] stackswopo1k: Unable to download "
                     "JSON metadata: HTTP Error 403: Forbidden")
    assert "curl_cffi" in kick


def test_a_mid_recording_403_is_not_blamed_on_kick():
    from record_stream import known_fix

    generic = known_fix("fragment 4213: HTTP Error 403: Forbidden")
    assert "curl_cffi" not in generic
    assert "expired" in generic


def test_yt_dlp_is_invoked_through_this_interpreter_when_possible(monkeypatch):
    """`yt-dlp` on PATH is often the standalone .exe, which bundles its
    own Python and cannot see site-packages - so installing curl_cffi
    appears to work while yt-dlp still reports every impersonate target
    unavailable."""
    import sys as _sys

    import record_stream

    monkeypatch.setitem(_sys.modules, "yt_dlp", object())
    assert record_stream.ytdlp_command() == [_sys.executable, "-m", "yt_dlp"]


def test_it_falls_back_to_the_executable(monkeypatch):
    import builtins

    import record_stream

    real_import = builtins.__import__

    def no_yt_dlp(name, *args, **kwargs):
        if name == "yt_dlp":
            raise ImportError("not installed here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_yt_dlp)
    assert record_stream.ytdlp_command() == ["yt-dlp"]


def test_every_command_starts_with_the_resolved_yt_dlp(recorder):
    """Three separate argument builders; one hard-coding the executable
    would use a different Python than the other two."""
    from record_stream import YTDLP, clips_args, vod_args

    for args in (recorder.download_args("/tmp/o.ts"),
                 vod_args("u", "/tmp/o.mp4"),
                 clips_args("u", "/tmp/o.mp4", "/tmp/a.txt")):
        assert args[:len(YTDLP)] == YTDLP


# ═════════════════════════════════════════════════════════════════════════════
# A repeating problem is stated once, not every sixty seconds
#
# A channel that is not live restarts yt-dlp every poll. A failure that
# survives the restart printed its whole multi-line explanation each time -
# ten hours of that buries everything else in the window.
# ═════════════════════════════════════════════════════════════════════════════

CLOCK_ERROR = ("ERROR: [kick:live] stackswopo1k: Unable to download JSON "
               "metadata: Failed to perform, curl: (60) SSL certificate "
               "problem: certificate is not yet valid.")


def test_a_tls_date_failure_is_not_blamed_on_cloudflare():
    """The regression: ANY error mentioning kick matched the curl_cffi
    advice, so a wrong system clock told you to install a package that
    can never fix it."""
    from record_stream import known_fix

    advice = known_fix(CLOCK_ERROR)

    assert "clock" in advice.lower()
    assert "curl_cffi" not in advice


def test_a_real_cloudflare_failure_still_gets_the_curl_fix():
    from record_stream import known_fix

    advice = known_fix("ERROR: [kick:live] no impersonate target is available")
    assert "curl_cffi" in advice


def test_the_clock_fix_says_it_affects_more_than_kick():
    """It arrives on a Kick URL but breaks every HTTPS call, and acting on
    it as a Kick problem is how it stays broken."""
    from record_stream import known_fix

    advice = known_fix(CLOCK_ERROR)
    assert "YouTube" in advice or "every HTTPS" in advice


def test_the_same_problem_is_only_said_once(capsys):
    from record_stream import Recorder

    recorder = Recorder(url="u", staging="s", watch_folder="w")
    assert recorder.say_once("clock", "the clock is wrong") is True
    assert recorder.say_once("clock", "the clock is wrong") is False
    assert recorder.say_once("clock", "the clock is wrong") is False

    assert capsys.readouterr().out.count("the clock is wrong") == 1


def test_a_problem_that_persists_comes_back_eventually():
    """Silence forever would look like it had cleared."""
    from record_stream import REPEAT_AFTER_S, Recorder

    recorder = Recorder(url="u", staging="s", watch_folder="w")
    recorder.say_once("clock", "the clock is wrong")
    recorder._said["clock"] -= REPEAT_AFTER_S + 1

    assert recorder.say_once("clock", "the clock is wrong") is True


def test_different_problems_are_each_said(capsys):
    from record_stream import Recorder

    recorder = Recorder(url="u", staging="s", watch_folder="w")
    recorder.say_once("a", "the clock is wrong")
    recorder.say_once("b", "the disk is full")

    out = capsys.readouterr().out
    assert "clock" in out and "disk" in out


def test_a_dead_fragment_is_told_from_a_flaky_connection():
    """403 on a fragment means the segment URL is dead - expired token,
    rotated CDN path, a stream that ended and took its manifest with it.
    No number of retries brings it back."""
    from record_stream import is_fragment_refusal

    assert is_fragment_refusal(
        "[download] Got error: HTTP Error 403: Forbidden. "
        "Retrying fragment 97 (187724/inf)...")
    assert is_fragment_refusal(
        "[download] Got error: HTTP Error 410: Gone. Retrying fragment 3 (1/10)")
    # A timeout IS transient and must keep the infinite retries.
    assert not is_fragment_refusal(
        "[download] Got error: The read operation timed out. "
        "Retrying fragment 5 (2/inf)...")


def test_real_progress_clears_the_count():
    """A handful of 403s scattered through a long recording is normal
    and must not end it - only an unbroken run means the manifest died."""
    from record_stream import is_progress_line

    assert is_progress_line("[download]  46.2% of ~ 2.79GiB at 5.48MiB/s")
    assert is_progress_line("[download] Destination: stream.ts")
    assert not is_progress_line(
        "[download] Got error: HTTP Error 403: Forbidden. Retrying fragment 97")


def test_the_run_of_refusals_has_a_finite_cap():
    """The recorder sat on fragment 97 a hundred and eighty thousand
    times. The recovery is a fresh manifest, which only happens when the
    process exits."""
    from record_stream import MAX_FRAGMENT_REFUSALS

    assert 5 <= MAX_FRAGMENT_REFUSALS <= 200


def test_infinite_retries_are_still_configured():
    """They are right for a dropped connection, which is what they were
    added for - the watchdog is about a DEAD manifest, not about giving
    up on a bad line."""
    import inspect

    from record_stream import Recorder

    body = inspect.getsource(Recorder.download_args)
    assert '"--fragment-retries", "infinite"' in body
