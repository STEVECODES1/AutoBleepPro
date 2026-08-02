"""
Optional pre-upload censoring pass, reusing AutoReel's existing
transcription + compliance engine (../autoreel/) instead of duplicating
that logic - it's already built, tested, and used elsewhere in this repo.

Transcribes the audio, flags profanity/mature-language spans, and (if any
are found) bleeps them and produces a censored copy of the video. This is
aimed squarely at YouTube age-restricting/demonetizing streams over
spoken profanity - it doesn't touch anything visual.
"""

import os
import sys
from dataclasses import dataclass
from typing import Optional

# autoreel/ lives one level up, alongside this auto_uploader/ folder.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


@dataclass
class CensorResult:
    output_path: str  # the file to actually upload - censored copy, or the original if nothing was flagged
    was_censored: bool
    violation_count: int
    censored_words: list  # e.g. ["shit", "damn's"] - for logging/notifications, not the full Violation objects


def censor_video(
    source_path: str,
    work_dir: str,
    model_name: str = "base",
    bleep_method: str = "beep",
    custom_words: tuple = (),
    device: Optional[str] = None,
) -> CensorResult:
    """Transcribe `source_path`, bleep any flagged words, and return the
    path that should actually be uploaded (a censored copy, or the
    original untouched if nothing was flagged)."""
    from moviepy import AudioFileClip, VideoFileClip
    from pydub import AudioSegment

    from autoreel.compliance import ComplianceEngine
    from autoreel.transcription import Transcriber

    os.makedirs(work_dir, exist_ok=True)
    basename = os.path.splitext(os.path.basename(source_path))[0]
    raw_audio_path = os.path.join(work_dir, f"_{basename}_audio.wav")
    clean_audio_path = os.path.join(work_dir, f"_{basename}_audio_clean.wav")
    output_video_path = os.path.join(work_dir, f"{basename}_CENSORED.mp4")

    if os.path.exists(output_video_path):
        # Already censored on a previous attempt (e.g. an earlier run got
        # this far, then failed on the actual upload) - re-transcribing
        # with Whisper is expensive, so reuse it instead of redoing it.
        return CensorResult(output_path=output_video_path, was_censored=True, violation_count=-1, censored_words=[])

    video = VideoFileClip(source_path)
    try:
        video.audio.write_audiofile(raw_audio_path, logger=None)

        transcriber = Transcriber(model_name=model_name, device=device)
        result = transcriber.transcribe(raw_audio_path)

        engine = ComplianceEngine(custom_words=custom_words)
        violations = engine.scan_segments(result["segments"])

        if not violations:
            return CensorResult(output_path=source_path, was_censored=False, violation_count=0, censored_words=[])

        audio_segment = AudioSegment.from_wav(raw_audio_path)
        censored_audio = engine.censor_audio(audio_segment, violations, method=bleep_method)
        censored_audio.export(clean_audio_path, format="wav")

        cleaned_audio_clip = AudioFileClip(clean_audio_path)
        try:
            compliant_video = video.with_audio(cleaned_audio_clip)
            compliant_video.write_videofile(
                output_video_path, codec="libx264", audio_codec="aac", logger=None
            )
            compliant_video.close()
        finally:
            cleaned_audio_clip.close()

        return CensorResult(
            output_path=output_video_path,
            was_censored=True,
            violation_count=len(violations),
            censored_words=[v.word for v in violations],
        )
    finally:
        video.close()
        for temp_path in (raw_audio_path, clean_audio_path):
            if os.path.exists(temp_path):
                os.remove(temp_path)
