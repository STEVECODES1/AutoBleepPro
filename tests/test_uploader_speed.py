"""
Speed-path correctness for the uploader.

Fast is only useful if it's still right, so these check the two things a
speed optimisation can silently break: producing a wrong/invalid output
file, and reusing a cache that should have been invalidated.

The ffmpeg tests are skipped when ffmpeg isn't installed; everything else
runs anywhere.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

_UPLOADER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto_uploader")
sys.path.insert(0, _UPLOADER)

from utils import censor as censor_mod  # noqa: E402
from utils.censor import (  # noqa: E402
    _load_cached_words,
    transcript_cache_path,
    words_cache_path,
)
from utils.ffmpeg_tools import (  # noqa: E402
    StageTimer,
    available_encoders,
    extract_audio,
    have_ffmpeg,
    mux_audio,
    nvenc_works,
    pick_video_encoder,
)

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """A tiny real video with an audio track."""
    if not have_ffmpeg():
        pytest.skip("ffmpeg not installed")
    d = tmp_path_factory.mktemp("speed")
    path = d / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-f", "lavfi", "-i", "testsrc2=s=320x240:d=3:r=15",
         "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return path


def probe(path, *entries):
    """ffmpeg-only stream inspection (ffprobe isn't always installed)."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    return out


# ═════════════════════════════════════════════════════════════════════════════
# Encoder selection: hardware when real, CPU otherwise
# ═════════════════════════════════════════════════════════════════════════════

def test_encoder_choice_is_always_usable():
    """'auto' must never pick an encoder this machine can't actually run."""
    chosen = pick_video_encoder("auto")
    assert chosen in ("h264_nvenc", "libx264")
    if chosen == "h264_nvenc":
        assert nvenc_works()


def test_cpu_preference_is_honoured():
    assert pick_video_encoder("cpu") == "libx264"


def test_nvenc_preference_falls_back_when_unavailable():
    """Asking for NVENC on a machine without it must not fail the render."""
    chosen = pick_video_encoder("nvenc")
    assert chosen == ("h264_nvenc" if nvenc_works() else "libx264")


def test_unknown_preference_is_treated_as_auto():
    assert pick_video_encoder("banana") == pick_video_encoder("auto")
    assert pick_video_encoder("") == pick_video_encoder("auto")


@needs_ffmpeg
def test_libx264_is_always_present():
    assert "libx264" in available_encoders()


# ═════════════════════════════════════════════════════════════════════════════
# The export path stays valid
# ═════════════════════════════════════════════════════════════════════════════

@needs_ffmpeg
def test_stream_copy_produces_a_playable_file(clip, tmp_path):
    wav = tmp_path / "audio.wav"
    assert extract_audio(str(clip), str(wav))
    out = tmp_path / "out.mp4"

    strategy = mux_audio(str(clip), str(wav), str(out))
    assert strategy == "copy", "the fast path should have been taken"
    assert out.exists() and out.stat().st_size > 0

    info = probe(out)
    assert "Video: h264" in info
    assert "Audio: aac" in info


@needs_ffmpeg
def test_stream_copy_keeps_the_original_video_bitstream(clip, tmp_path):
    """The whole point: pictures are copied, not re-encoded."""
    wav = tmp_path / "a.wav"
    extract_audio(str(clip), str(wav))
    out = tmp_path / "copied.mp4"
    mux_audio(str(clip), str(wav), str(out))

    src_video = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(clip), "-map", "0:v", "-c", "copy",
         "-f", "md5", "-"], capture_output=True, text=True).stdout
    out_video = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(out), "-map", "0:v", "-c", "copy",
         "-f", "md5", "-"], capture_output=True, text=True).stdout
    assert src_video and src_video == out_video, "video stream was re-encoded"


@needs_ffmpeg
def test_faststart_moves_the_moov_atom_to_the_front(clip, tmp_path):
    """+faststart lets a player begin before the whole file arrives."""
    wav = tmp_path / "a.wav"
    extract_audio(str(clip), str(wav))
    out = tmp_path / "fs.mp4"
    mux_audio(str(clip), str(wav), str(out))

    head = out.read_bytes()[:4096]
    assert b"moov" in head, "moov atom is not near the start of the file"


@needs_ffmpeg
def test_forced_reencode_still_produces_a_valid_file(clip, tmp_path):
    wav = tmp_path / "a.wav"
    extract_audio(str(clip), str(wav))
    out = tmp_path / "re.mp4"

    strategy = mux_audio(str(clip), str(wav), str(out), allow_stream_copy=False)
    assert strategy in ("h264_nvenc", "libx264")
    assert out.exists() and out.stat().st_size > 0
    assert "Video: h264" in probe(out)


@needs_ffmpeg
def test_mux_on_a_bogus_input_reports_failure_and_leaves_no_partial(tmp_path):
    bad = tmp_path / "not-a-video.mp4"
    bad.write_bytes(b"garbage")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"garbage")
    out = tmp_path / "out.mp4"

    assert mux_audio(str(bad), str(wav), str(out)) is None
    assert not out.exists(), "a partial output would look like a successful render"


@needs_ffmpeg
def test_extract_audio_is_mono_16k(clip, tmp_path):
    wav = tmp_path / "a.wav"
    assert extract_audio(str(clip), str(wav))
    info = probe(wav)
    assert "16000 Hz" in info and "mono" in info


def test_extract_audio_on_a_missing_file(tmp_path):
    assert extract_audio(str(tmp_path / "nope.mp4"), str(tmp_path / "a.wav")) is False


# ═════════════════════════════════════════════════════════════════════════════
# Model reuse
# ═════════════════════════════════════════════════════════════════════════════

def test_transcriber_is_reused_for_identical_settings():
    censor_mod.release_models()
    first = censor_mod._get_transcriber("base", None, reuse=True)
    second = censor_mod._get_transcriber("base", None, reuse=True)
    assert first is second, "the model should be loaded once per batch"


def test_changing_settings_gets_a_different_model():
    censor_mod.release_models()
    base = censor_mod._get_transcriber("base", None, reuse=True)
    small = censor_mod._get_transcriber("small", None, reuse=True)
    cuda = censor_mod._get_transcriber("base", "cuda", reuse=True)
    assert base is not small and base is not cuda


def test_reuse_can_be_switched_off():
    censor_mod.release_models()
    a = censor_mod._get_transcriber("base", None, reuse=False)
    b = censor_mod._get_transcriber("base", None, reuse=False)
    assert a is not b


def test_release_models_clears_the_cache():
    censor_mod._get_transcriber("base", None, reuse=True)
    assert censor_mod._MODEL_CACHE
    censor_mod.release_models()
    assert censor_mod._MODEL_CACHE == {}


# ═════════════════════════════════════════════════════════════════════════════
# Transcript cache: hit skips work, stale does not
# ═════════════════════════════════════════════════════════════════════════════

def write_words_cache(path, source, *, newer=True):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"segments": [
            {"start": 0.0, "end": 1.0, "text": "hello there",
             "words": [{"word": "hello", "start": 0.0, "end": 0.4},
                       {"word": "there", "start": 0.5, "end": 1.0}]}]}, f)
    src_mtime = os.path.getmtime(source)
    when = src_mtime + (10 if newer else -10)
    os.utime(path, (when, when))


def test_valid_cache_is_reused(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"v")
    cache = tmp_path / "clip_transcript_words.json"
    write_words_cache(cache, source, newer=True)

    segments = _load_cached_words(str(cache), str(source))
    assert segments and segments[0]["words"][0]["word"] == "hello"


def test_cache_older_than_the_video_is_rejected(tmp_path):
    """A re-download under the same name must not reuse the old transcript."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"v")
    cache = tmp_path / "clip_transcript_words.json"
    write_words_cache(cache, source, newer=False)

    assert _load_cached_words(str(cache), str(source)) is None


def test_missing_cache(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"v")
    assert _load_cached_words(str(tmp_path / "nope.json"), str(source)) is None


def test_corrupt_cache_is_rejected(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"v")
    cache = tmp_path / "c.json"
    cache.write_text("{not json")
    os.utime(cache, (os.path.getmtime(source) + 10,) * 2)
    assert _load_cached_words(str(cache), str(source)) is None


def test_cache_without_word_timings_is_rejected(tmp_path):
    """The optimizer's segment-only format can't drive the censor scan."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"v")
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": "hi"}]}))
    os.utime(cache, (os.path.getmtime(source) + 10,) * 2)
    assert _load_cached_words(str(cache), str(source)) is None


def test_empty_cache_is_rejected(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"v")
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({"segments": []}))
    os.utime(cache, (os.path.getmtime(source) + 10,) * 2)
    assert _load_cached_words(str(cache), str(source)) is None


def test_the_two_cache_files_are_distinct():
    """The optimizer's file and the censor's file must not collide."""
    assert transcript_cache_path("/w", "clip") != words_cache_path("/w", "clip")


# ═════════════════════════════════════════════════════════════════════════════
# Timing instrumentation must not break anything
# ═════════════════════════════════════════════════════════════════════════════

def test_stage_timer_records_and_totals(capsys):
    timer = StageTimer("clip.mp4", enabled=True)
    timer.mark("transcribe")
    timer.mark("render")
    printed = capsys.readouterr().out

    assert "clip.mp4: transcribe took" in printed
    assert "clip.mp4: render took" in printed
    assert [name for name, _ in timer.stages] == ["transcribe", "render"]
    assert timer.total() >= 0
    assert "transcribe" in timer.summary() and "total" in timer.summary()


def test_stage_timer_can_be_silent(capsys):
    timer = StageTimer("clip.mp4", enabled=False)
    timer.mark("transcribe")
    assert capsys.readouterr().out == ""
    assert timer.stages, "timings are still recorded, just not printed"


def test_stage_timer_summary_with_no_stages():
    assert "nothing timed" in StageTimer("x", enabled=False).summary()


# ═════════════════════════════════════════════════════════════════════════════
# Upload chunk size
# ═════════════════════════════════════════════════════════════════════════════

def test_shipped_chunk_size_is_conservative_and_resumable():
    with open(os.path.join(_UPLOADER, "config.json")) as f:
        shipped = json.load(f)
    chunk = shipped["youtube"]["upload_chunk_mb"]
    assert 1 <= chunk <= 64, "default should stay modest enough to resume cheaply"


def test_speed_defaults_are_safe():
    with open(os.path.join(_UPLOADER, "config.json")) as f:
        speed = json.load(f)["general"]["speed"]
    # Stream copy is both faster AND lossless, so it's on; hardware encode
    # only ever applies to the fallback and auto-detects.
    assert speed["stream_copy_video"] is True
    assert speed["hardware_encode"] == "auto"
    assert speed["reuse_model"] is True
    assert speed["reuse_transcript"] is True
