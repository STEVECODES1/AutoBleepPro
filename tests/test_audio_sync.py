"""
"Audio is delayed" - a viewer comment on "Stackswopo - SHORT STREAM POP MY
SHIT", a stream censored for 277 flagged words.

Two candidate causes were checked and one was ruled out, empirically:

1. The mute rebuild (autoreel.compliance.ComplianceEngine.censor_audio)
   splices hundreds of silence spans into the extracted audio with
   pydub, working in milliseconds. A simulated 650-span rebuild across a
   1-hour track (matching another real stream's violation count) came
   back within a millisecond of the source length - not enough drift to
   notice. Ruled out below.

2. A fixed start-offset between the censored audio (its own WAV,
   produced by a separate ffmpeg + pydub pass) and the source video
   (attached via mux_audio()'s "-map ... -c:v copy") was also
   considered and is not supported: a WAV cannot encode a timestamp at
   all - ffmpeg reads its first sample as t=0 unconditionally - and
   -map without -copyts rebases the video input to roughly zero the
   same way. Confirmed with a real ffmpeg run: muxing a video against a
   deliberately -itsoffset-shifted audio input produced no measurable
   change with or without the fix below, because the offset cannot
   survive being written into the WAV in the first place.

What could not be ruled out without the source file itself: the
censored audio is rebuilt on a perfectly regular clock (pydub, exact
milliseconds), with no knowledge of whatever real-world timing
irregularity the ORIGINAL video's own timestamps carry from being a
live HLS capture running for hours - dropped frames, encoder stalls, a
declared frame rate not quite the achieved one. That would not show on
a short clip and could accumulate into a real, growing offset over a
long stream - which is what a report on a long, heavily-censored stream
looks like.

-af aresample=async=1 is ffmpeg's own documented mechanism for exactly
that shape of problem: it continuously compares the audio's timestamps
against what elapsed video time implies and pads or trims samples to
close any gap ("filling and trimming" per ffmpeg's own
-h filter=aresample output) - correcting accumulated drift, not just a
start offset. It is a no-op where there is nothing to correct, and it
costs nothing new here: audio was already being re-encoded to aac in
mux_audio(), never stream-copied.
"""


from __future__ import annotations

import os
import random
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils import ffmpeg_tools  # noqa: E402
from utils.ffmpeg_tools import have_ffmpeg, mux_audio  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg not installed")


# ═════════════════════════════════════════════════════════════════════════════
# The mute rebuild itself: ruled out
# ═════════════════════════════════════════════════════════════════════════════

def test_hundreds_of_mute_spans_do_not_drift_the_track_length():
    """The real shape of the report: a long stream, hundreds of muted
    words. pydub slices by the millisecond; if that rounding accumulated
    across hundreds of splice points it would show up as a length
    change, which is exactly what would make audio wander out of sync
    over a long file even though the pieces are individually correct."""
    pydub = pytest.importorskip("pydub")
    from pydub import AudioSegment

    from autoreel.compliance import ComplianceEngine

    class _Violation:
        def __init__(self, start_ms, end_ms):
            self.start = start_ms / 1000.0
            self.end = end_ms / 1000.0
            self.category = "hate_speech"
            self.segment_start = None
            self.segment_end = None
            self.prev_end = None
            self.next_start = None
            self.is_high_severity = True

    frame_rate = 16000
    duration_ms = 60 * 60 * 1000  # 1 hour, like the real streams reported
    base = AudioSegment.silent(duration=duration_ms, frame_rate=frame_rate)

    random.seed(42)
    spans = []
    t = 500
    for _ in range(650):  # 'halfa mill' had 650 flagged words
        span_len = random.randint(180, 700)
        gap = random.randint(1000, 6000)
        if t + span_len >= duration_ms - 1000:
            break
        spans.append(_Violation(t, t + span_len))
        t = t + span_len + gap

    engine = ComplianceEngine(mute_whole_segment=False)
    censored = engine.censor_audio(base, spans, method="silence")

    drift_ms = abs(len(censored) - len(base))
    assert drift_ms <= 5, (
        f"{len(spans)} mute spans drifted the track by {drift_ms}ms - "
        f"enough to be the reported delay")


# ═════════════════════════════════════════════════════════════════════════════
# The actual fix: sync correction at mux time
# ═════════════════════════════════════════════════════════════════════════════

def test_mux_audio_asks_ffmpeg_to_correct_drift(monkeypatch, tmp_path):
    """Unit-level: the constructed ffmpeg command carries the fix,
    checked without needing a real ffmpeg binary."""
    calls = []

    def fake_run(args, timeout=None):
        calls.append(args)
        out = args[-1]
        with open(out, "wb") as f:
            f.write(b"x")
        return True

    monkeypatch.setattr(ffmpeg_tools, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(ffmpeg_tools, "_run", fake_run)

    out_path = str(tmp_path / "out.mp4")
    strategy = mux_audio("video.ts", "clean.wav", out_path)

    assert strategy == "copy"
    assert len(calls) == 1
    args = calls[0]
    assert "-af" in args, "no audio filter was passed at all"
    assert args[args.index("-af") + 1] == "aresample=async=1"
    # Still the fast path - the fix must not have cost the video re-encode.
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "copy"


def test_the_sync_fix_applies_to_every_encoder_path(monkeypatch, tmp_path):
    """common is shared across stream-copy, NVENC and libx264 - the fix
    has to survive whichever one actually runs, not just the fast path."""
    monkeypatch.setattr(ffmpeg_tools, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(ffmpeg_tools, "pick_video_encoder", lambda pref: "libx264")

    calls = []

    def fake_run(args, timeout=None):
        calls.append(args)
        return False  # force every attempt to fail through to the next

    monkeypatch.setattr(ffmpeg_tools, "_run", fake_run)

    out_path = str(tmp_path / "out.mp4")
    result = mux_audio("video.ts", "clean.wav", out_path, allow_stream_copy=True)

    assert result is None  # every attempt failed - expected, _run always returns False
    assert len(calls) >= 2, "stream copy and the re-encode fallback should both have tried"
    for args in calls:
        assert "-af" in args and args[args.index("-af") + 1] == "aresample=async=1", (
            f"a mux attempt was missing the sync fix: {args}")


@needs_ffmpeg
def test_a_fixed_start_offset_is_not_the_mechanism(tmp_path):
    """Checked directly, with real ffmpeg, and worth pinning so nobody
    re-diagnoses this the same way twice: the censored audio is written
    as a WAV, which cannot encode a timestamp at all, so a deliberately
    -itsoffset-shifted source cannot actually arrive at mux_audio()
    carrying that offset - ffmpeg reads a WAV's first sample as t=0
    unconditionally. The video input's own PTS is rebased the same way
    by -map without -copyts. Two inputs that both start at ~0 do not
    explain a fixed delay from frame one - so this class of fix
    (aresample=async) is aimed at DRIFT, not a start offset, and this
    test is here to stop that going back to being re-investigated."""
    import subprocess

    video = tmp_path / "video.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=s=320x240:d=10:r=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(video)], check=True)

    # A "censored" audio track from a separate pass, deliberately offset
    # by 300ms via -itsoffset before being written to WAV.
    clean_audio = tmp_path / "clean.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-itsoffset", "0.3",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-ac", "1", "-ar", "16000", str(clean_audio)], check=True)

    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=start_time",
         "-of", "csv=p=0", str(clean_audio)],
        capture_output=True, text=True, timeout=30)
    wav_start = completed.stdout.strip().splitlines()[0]
    # WAV has no timestamp field at all - ffprobe reports "N/A" rather
    # than a real value. Either that, or 0.0 if some future ffmpeg
    # version starts inferring one; anything else (in particular, 0.3 -
    # the -itsoffset actually surviving into the WAV) is the reasoning
    # above being wrong and needing to be revisited.
    assert wav_start in ("N/A", "0.000000"), (
        f"WAV reported a start_time of {wav_start!r} - if that is the "
        f"-itsoffset surviving into the file, the reasoning above about "
        f"WAV needs revisiting")


# ═════════════════════════════════════════════════════════════════════════════
# The guard: verify every censored file on its way out, not just once
# ═════════════════════════════════════════════════════════════════════════════

def test_stream_durations_reads_video_and_audio_separately(monkeypatch):
    """Two numbers, not media_duration()'s one - see the docstring on
    why format=duration cannot be trusted to catch this."""
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        selector = args[args.index("-select_streams") + 1]
        out = "12.500000\n" if selector == "v:0" else "12.000000\n"

        class _Completed:
            stdout = out.encode()
        return _Completed()

    monkeypatch.setattr(ffmpeg_tools.subprocess, "run", fake_run)

    video_s, audio_s = ffmpeg_tools.stream_durations("out.mp4")
    assert video_s == 12.5
    assert audio_s == 12.0
    assert len(calls) == 2


def test_an_unmeasurable_stream_reads_as_none(monkeypatch):
    def explode(args, **kwargs):
        raise FileNotFoundError("no ffprobe")

    monkeypatch.setattr(ffmpeg_tools.subprocess, "run", explode)
    assert ffmpeg_tools.stream_durations("out.mp4") == (None, None)


def test_a_censored_file_that_drifted_is_flagged(monkeypatch, capsys):
    from utils import censor

    monkeypatch.setattr(censor, "stream_durations",
                        lambda path: (60.0, 58.5), raising=False)
    monkeypatch.setattr("utils.ffmpeg_tools.stream_durations",
                        lambda path: (60.0, 58.5))
    censor._verify_sync("halfa_mill_CENSORED.mp4")

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "disagree" in out
    assert "halfa_mill_CENSORED.mp4" in out


def test_a_censored_file_in_sync_says_nothing(monkeypatch, capsys):
    from utils import censor

    monkeypatch.setattr("utils.ffmpeg_tools.stream_durations",
                        lambda path: (60.0, 60.05))
    censor._verify_sync("fine.mp4")

    assert capsys.readouterr().out == ""


def test_an_unmeasurable_file_says_nothing_and_does_not_raise(monkeypatch, capsys):
    """Never the reason a censor pass fails - see the docstring."""
    from utils import censor

    monkeypatch.setattr("utils.ffmpeg_tools.stream_durations",
                        lambda path: (None, None))
    censor._verify_sync("mystery.mp4")

    assert capsys.readouterr().out == ""


def test_the_verify_step_is_actually_wired_into_censor_video():
    """Read out of the source: the check has to run on every censored
    file, not exist unused."""
    path = os.path.join(_UPLOADER, "utils", "censor.py")
    body = open(path, encoding="utf-8").read()
    render_at = body.index("strategy = _render(")
    verify_at = body.index("_verify_sync(output_video_path)")
    next_mark = body.index('timer.mark(f"render')
    assert render_at < next_mark < verify_at, \
        "_verify_sync must run right after the render, on the real output"
