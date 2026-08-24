"""
Optional pre-upload censoring pass, reusing AutoReel's existing
transcription + compliance engine (../autoreel/) instead of duplicating
that logic - it's already built, tested, and used elsewhere in this repo.

Transcribes the audio, flags profanity/mature-language spans, and (if any
are found) bleeps them and produces a censored copy of the video. This is
aimed squarely at YouTube age-restricting/demonetizing streams over
spoken profanity - it doesn't touch anything visual.

Because it doesn't touch anything visual, the censored copy is produced by
muxing the new audio onto the ORIGINAL video stream rather than
re-encoding it - see utils/ffmpeg_tools.py. That is the single biggest
speed win in this pipeline and it also stops the picture being degraded.
"""

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from typing import Optional

from utils.ffmpeg_tools import StageTimer, extract_audio, have_ffmpeg, mux_audio

# autoreel/ lives one level up, alongside this auto_uploader/ folder.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@dataclass
class CensorResult:
    output_path: str  # the file to actually upload - censored copy, or the original if nothing was flagged
    was_censored: bool
    violation_count: int
    censored_words: list  # e.g. ["shit", "damn's"] - for logging/notifications, not the full Violation objects


def transcript_cache_path(work_dir: str, basename: str) -> str:
    """Segment-level cache consumed by content_optimizer. Format unchanged."""
    return os.path.join(work_dir, f"{basename}_transcript.json")


def words_cache_path(work_dir: str, basename: str) -> str:
    """Word-level cache, used to skip re-transcribing on a re-censor.

    Separate from the optimizer's file because ComplianceEngine needs each
    segment's "words" list, which that format doesn't carry - and changing
    it would break the optimizer's contract.
    """
    return os.path.join(work_dir, f"{basename}_transcript_words.json")


def _source_stamp(source_path: str) -> dict:
    """What identifies the video this transcript was made from."""
    try:
        stat = os.stat(source_path)
    except OSError:
        return {}
    return {"source_size": stat.st_size}


def _load_cached_words(path: str, source_path: str):
    """Cached segments, or None if absent/stale/unreadable.

    Stale means a DIFFERENT video under the same filename - a
    re-download or a re-encode - which must not silently reuse the
    previous transcript.

    Judged by SIZE, not by which file is newer. Comparing timestamps
    meant anything that touched the video after transcribing threw the
    transcript away: the upload, moving it into uploaded/, Windows
    updating an mtime on a copy. The run then said "Kept transcript
    cache: clips are still to be cut from it" and, seconds later,
    "Nothing rendered - no transcript" - and no clips came out of a
    stream that had just been transcribed for six minutes.

    Size cannot be changed by touching a file and always changes when
    the video does, which is exactly the question being asked.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data.get("source_size") is not None:
        if data.get("source_size") != _source_stamp(source_path).get("source_size"):
            return None
    else:
        # Written before the stamp existed: fall back to the old rule
        # rather than trusting a transcript nothing can vouch for.
        try:
            if os.path.getmtime(path) < os.path.getmtime(source_path):
                return None
        except OSError:
            return None
    segments = data.get("segments") if isinstance(data, dict) else None
    if not isinstance(segments, list) or not segments:
        return None
    # Must actually carry word timings, or the scan below finds nothing.
    if not any(seg.get("words") for seg in segments if isinstance(seg, dict)):
        return None
    return segments


# One loaded Whisper model per (model_name, device), reused across files.
# Whisper reloads the weights on every Transcriber.transcribe() call
# otherwise, so a 20-file batch paid that cost 20 times.
_MODEL_CACHE: dict = {}


def _get_transcriber(model_name: str, device: Optional[str], reuse: bool = True):
    from autoreel.transcription import Transcriber

    if not reuse:
        return Transcriber(model_name=model_name, device=device)
    key = (model_name, device)
    if key not in _MODEL_CACHE:
        _MODEL_CACHE[key] = Transcriber(model_name=model_name, device=device)
    return _MODEL_CACHE[key]


def release_models() -> None:
    """Drop cached models (frees GPU/RAM between long-running modes)."""
    for transcriber in _MODEL_CACHE.values():
        # Clearing the dict alone only drops the wrapper; the weights are
        # held by the Transcriber, which is what actually costs the RAM.
        try:
            transcriber.release()
        except Exception:
            pass
    _MODEL_CACHE.clear()


def _extract_audio(source_path: str, raw_audio_path: str) -> None:
    """ffmpeg if it's on PATH, moviepy otherwise.

    moviepy pushes every sample block through Python; ffmpeg does it in a
    single native pass.
    """
    if have_ffmpeg() and extract_audio(source_path, raw_audio_path):
        return

    from moviepy import VideoFileClip

    clip = VideoFileClip(source_path)
    try:
        if clip.audio is None:
            raise RuntimeError(
                f"{os.path.basename(source_path)} has no audio track - nothing to censor.")
        clip.audio.write_audiofile(raw_audio_path, logger=None)
    finally:
        clip.close()


def _render(source_path: str, clean_audio_path: str, output_video_path: str,
            speed: dict) -> str:
    """Attach the censored audio to the video. Returns the strategy used."""
    strategy = mux_audio(
        source_path, clean_audio_path, output_video_path,
        encoder_preference=str(speed.get("hardware_encode", "auto")),
        encode_preset=str(speed.get("encode_preset", "fast")),
        allow_stream_copy=bool(speed.get("stream_copy_video", True)),
    )
    if strategy:
        return strategy

    # ffmpeg unavailable or refused the file - fall back to the original
    # moviepy path so censoring still works, just slower.
    from moviepy import AudioFileClip, VideoFileClip

    video = VideoFileClip(source_path)
    cleaned_audio_clip = AudioFileClip(clean_audio_path)
    try:
        compliant_video = video.with_audio(cleaned_audio_clip)
        compliant_video.write_videofile(
            output_video_path, codec="libx264", audio_codec="aac", logger=None)
        compliant_video.close()
    finally:
        cleaned_audio_clip.close()
        video.close()
    return "moviepy"


def _report_risk(violations, mute_whole_segment: bool = False) -> None:
    """Print what was flagged, worst category first.

    A muted slur is still worth seeing before publishing: YouTube acts on
    the surrounding context, which no audio filter can remove.

    `mute_whole_segment` decides the WORDING, not just a detail: this
    used to print "(whole sentence muted)" unconditionally for every
    high-severity hit, which was accurate while that setting defaulted
    to True and became a flat lie the moment it defaulted to False - the
    word gets a normal ~1s padded mute now, same as any other flagged
    word, and the log kept claiming otherwise. Reading a log that says
    "whole sentence muted" while the audio only dips for a second is
    exactly how "the fix didn't do anything" gets reported back.
    """
    from autoreel.compliance import HIGH_SEVERITY_CATEGORIES

    if not violations:
        return
    by_category: dict = {}
    for v in violations:
        by_category.setdefault(v.category, []).append(v)

    for category in sorted(by_category, key=lambda c: (c not in HIGH_SEVERITY_CATEGORIES, c)):
        hits = by_category[category]
        marker = "!! HIGH RISK" if category in HIGH_SEVERITY_CATEGORIES else "  "
        print(f"[Censor] {marker} {category}: {len(hits)} hit(s)")
        if category in HIGH_SEVERITY_CATEGORIES:
            extent = ("whole sentence muted" if mute_whole_segment
                      else "word muted")
            for v in hits[:10]:
                stamp = f"{int(v.start // 60):02d}:{int(v.start % 60):02d}"
                print(f"[Censor]      {stamp}  {v.word.strip()!r}  ({extent})")
            if len(hits) > 10:
                print(f"[Censor]      ... and {len(hits) - 10} more")
            print("[Censor]      Muting the audio does NOT make the video policy-safe - "
                  "YouTube judges context. Review these timestamps before publishing.")


def _settings_fingerprint(padding_ms: int, mute_whole_segment: bool,
                          only_categories: tuple, custom_words: tuple) -> str:
    """A short, stable fingerprint of every setting that changes what
    actually gets muted - not just which model transcribed it or whether
    the method is a beep or silence.

    Only bleep_method and model_name were ever in the cache key.
    padding_ms and mute_whole_segment were not - so the fix that made
    mute_whole_segment default to False instead of True (word-level
    mutes instead of whole sentences) changed NOTHING for a video that
    had already been censored once: censor_video saw the same cached
    filename still sitting on disk and reused it, over-muted audio and
    all, forever. "Changing the config appears to do nothing on any file
    that was already processed" is the exact failure this project's own
    comments already warned about, for the two settings it forgot to
    cover - and a CLIP cut from that stale source inherits the same
    over-muted audio even on a run built from the fixed code.

    Included, not the raw strings: only_categories and custom_words can
    both change what gets flagged, and belong here for the same reason.
    """
    payload = repr((
        int(padding_ms),
        bool(mute_whole_segment),
        tuple(sorted(str(c).lower() for c in (only_categories or ()))),
        tuple(sorted(str(w).lower() for w in (custom_words or ()))),
    ))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def censor_video(
    source_path: str,
    work_dir: str,
    model_name: str = "base",
    # See the note in config.py: silence, not beep.
    bleep_method: str = "silence",
    custom_words: tuple = (),
    device: Optional[str] = None,
    speed: Optional[dict] = None,
    padding_ms: int = 250,
    mute_whole_segment: bool = True,
    only_categories: tuple = (),
) -> CensorResult:
    """Transcribe `source_path`, bleep any flagged words, and return the
    path that should actually be uploaded (a censored copy, or the
    original untouched if nothing was flagged)."""
    from pydub import AudioSegment

    from autoreel.compliance import ComplianceEngine

    speed = speed or {}
    timer = StageTimer(os.path.basename(source_path),
                       enabled=bool(speed.get("stage_timings", True)))

    os.makedirs(work_dir, exist_ok=True)
    basename = os.path.splitext(os.path.basename(source_path))[0]
    raw_audio_path = os.path.join(work_dir, f"_{basename}_audio.wav")
    clean_audio_path = os.path.join(work_dir, f"_{basename}_audio_clean.wav")
    # The censoring settings are part of the cache key: a copy made with
    # bleep_method="beep" must NOT be silently reused after switching to
    # "silence" (nor a "tiny"-model pass reused after switching to "base").
    # Without this, changing the config appears to do nothing on any file
    # that was already processed.
    cache_key = (f"{bleep_method}-{model_name}-"
                f"{_settings_fingerprint(padding_ms, mute_whole_segment, only_categories, custom_words)}")
    output_video_path = os.path.join(work_dir, f"{basename}_CENSORED_{cache_key}.mp4")

    if os.path.exists(output_video_path) and os.path.getsize(output_video_path) > 0:
        # Already censored on a previous attempt with these exact settings
        # (e.g. an earlier run got this far, then failed on the actual
        # upload) - re-transcribing with Whisper is expensive, so reuse it.
        print(f"[Censor] Reusing existing censored copy "
              f"(model={model_name}, method={bleep_method}) - no re-render.")
        return CensorResult(output_path=output_video_path, was_censored=True,
                            violation_count=-1, censored_words=[])

    try:
        words_path = words_cache_path(work_dir, basename)
        cached = (_load_cached_words(words_path, source_path)
                  if speed.get("reuse_transcript", True) else None)

        if cached is not None:
            print("[Censor] Reusing cached transcript - skipping Whisper.")
            result = {"segments": cached}
            timer.mark("transcript cache hit")
        else:
            _extract_audio(source_path, raw_audio_path)
            timer.mark("audio extract")

            transcriber = _get_transcriber(model_name, device,
                                           reuse=bool(speed.get("reuse_model", True)))
            # Tell it the words it is about to hear. The censor can only
            # mute what the transcript contains, and shouted overlapping
            # gameplay speech is exactly where Whisper drops or softens a
            # slur - see autoreel/hotwords.py.
            #
            # Best-effort: a hotword list that cannot be built is a
            # slightly worse transcript, not a failed censor pass.
            try:
                from autoreel.hotwords import build as build_hotwords

                transcriber.hotwords = build_hotwords(
                    config=None,
                    engine=ComplianceEngine(custom_words=tuple(custom_words),
                                            only_categories=tuple(only_categories)))
            except Exception as exc:
                print(f"[Censor] Could not build the hotword list "
                      f"({type(exc).__name__}: {exc}). Transcribing without "
                      f"it - this is slightly less accurate, not broken.")
            result = transcriber.transcribe(raw_audio_path)
            timer.mark("transcribe")
            try:
                with open(words_path, "w", encoding="utf-8") as f:
                    json.dump({"segments": result["segments"],
                               **_source_stamp(source_path)}, f)
            except Exception:
                pass  # a failed cache write must never fail the censor pass

        # Cache the transcript so content_optimizer can build its report
        # without a second multi-minute Whisper pass over the same video.
        transcript_path = transcript_cache_path(work_dir, basename)
        try:
            with open(transcript_path, "w", encoding="utf-8") as f:
                json.dump(
                    [{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)),
                      "text": s.get("text", "")} for s in result["segments"]],
                    f,
                )
        except Exception:
            pass  # a failed cache write must never fail the censor pass

        engine = ComplianceEngine(custom_words=custom_words,
                                  padding_ms=padding_ms,
                                  mute_whole_segment=mute_whole_segment,
                                  only_categories=tuple(only_categories))
        violations = engine.scan_segments(result["segments"])

        if not violations:
            timer.mark("scan")
            return CensorResult(output_path=source_path, was_censored=False,
                                violation_count=0, censored_words=[])

        if not os.path.exists(raw_audio_path):
            _extract_audio(source_path, raw_audio_path)   # cache hit skipped it
            timer.mark("audio extract")
        _report_risk(violations, mute_whole_segment=mute_whole_segment)

        audio_segment = AudioSegment.from_wav(raw_audio_path)
        censored_audio = engine.censor_audio(audio_segment, violations, method=bleep_method)
        censored_audio.export(clean_audio_path, format="wav")
        timer.mark("censor audio")

        strategy = _render(source_path, clean_audio_path, output_video_path, speed)
        timer.mark(f"render [{strategy}]")
        if timer.enabled:
            print(f"[Timing] {timer.summary()}")

        return CensorResult(
            output_path=output_video_path,
            was_censored=True,
            violation_count=len(violations),
            censored_words=[v.word for v in violations],
        )
    finally:
        for temp_path in (raw_audio_path, clean_audio_path):
            if os.path.exists(temp_path):
                os.remove(temp_path)
