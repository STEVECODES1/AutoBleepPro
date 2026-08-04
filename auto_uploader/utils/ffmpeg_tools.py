"""
Direct ffmpeg helpers for the censor pass.

The censor pass only ever changes the AUDIO of a video. moviepy's
`clip.with_audio(...).write_videofile(...)` nonetheless decodes and
re-encodes every video frame to produce the result, which is both the
slowest stage in the pipeline and lossy - the picture is degraded for no
reason.

ffmpeg can mux the new audio onto the untouched video stream instead
(`-c:v copy`): no video decode, no video encode, no quality loss, and the
cost scales with file size rather than pixel count. Measured on a 60s
720p30 clip: 24.4s -> 4.8s, with the copied output preserving the source
quality the re-encode had thrown away. The margin grows with resolution
and duration.

A real re-encode is still needed if stream copy is rejected (an exotic
codec the target container won't hold), so this falls back: stream copy ->
NVENC -> libx264 -> caller's moviepy path.
"""

import os
import shutil
import subprocess
import time
from functools import lru_cache
from typing import Optional

# Anything longer than this and something is wrong; don't hang a batch.
_TIMEOUT = 60 * 60 * 6


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _run(args: list, timeout: int = _TIMEOUT) -> bool:
    try:
        subprocess.run(args, check=True, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        return False


@lru_cache(maxsize=1)
def available_encoders() -> frozenset:
    """The h264 encoders this ffmpeg build actually exposes."""
    if not have_ffmpeg():
        return frozenset()
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=30).stdout
    except (subprocess.SubprocessError, OSError):
        return frozenset()
    return frozenset(name for name in
                     ("h264_nvenc", "hevc_nvenc", "h264_qsv", "h264_amf", "libx264")
                     if name in out)


@lru_cache(maxsize=1)
def nvenc_works() -> bool:
    """Whether h264_nvenc can actually encode here.

    Listed-but-broken is common: the encoder shows up in `-encoders` on a
    machine with no NVIDIA driver, and only fails when used. One tiny test
    encode settles it, cached for the process.
    """
    if "h264_nvenc" not in available_encoders():
        return False
    return _run(["ffmpeg", "-y", "-hide_banner", "-f", "lavfi",
                 "-i", "color=c=black:s=128x128:d=0.1", "-c:v", "h264_nvenc",
                 "-f", "null", "-"], timeout=60)


def pick_video_encoder(preference: str = "auto") -> str:
    """Encoder for the fallback path where a real re-encode is unavoidable.

    'auto' uses NVENC when it's genuinely usable, else libx264.
    """
    preference = (preference or "auto").strip().lower()
    if preference == "cpu":
        return "libx264"
    if preference == "nvenc":
        return "h264_nvenc" if nvenc_works() else "libx264"
    return "h264_nvenc" if nvenc_works() else "libx264"


def extract_audio(video_path: str, wav_path: str,
                  sample_rate: int = 16000) -> bool:
    """16 kHz mono WAV for Whisper, straight from ffmpeg.

    moviepy's write_audiofile goes through Python for every sample block;
    ffmpeg does it in one pass.
    """
    if not have_ffmpeg():
        return False
    ok = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-i", video_path, "-vn", "-ac", "1", "-ar", str(sample_rate),
               "-c:a", "pcm_s16le", wav_path])
    return ok and os.path.exists(wav_path) and os.path.getsize(wav_path) > 0


def mux_audio(
    video_path: str,
    audio_path: str,
    out_path: str,
    encoder_preference: str = "auto",
    encode_preset: str = "fast",
    audio_bitrate: str = "192k",
    allow_stream_copy: bool = True,
) -> Optional[str]:
    """Put `audio_path` onto `video_path`'s pictures, writing `out_path`.

    Returns the strategy used ("copy", "h264_nvenc", "libx264") or None if
    ffmpeg couldn't do it at all and the caller should fall back.
    """
    if not have_ffmpeg():
        return None

    common = ["-map", "0:v:0", "-map", "1:a:0",
              "-c:a", "aac", "-b:a", audio_bitrate,
              "-movflags", "+faststart", "-shortest"]

    # 1. Stream copy: the fast path, and the only one that doesn't touch
    #    picture quality. Works whenever the target container accepts the
    #    source video codec, which for mp4 + h264 is the normal case.
    if allow_stream_copy:
        if _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", video_path, "-i", audio_path,
                 *common, "-c:v", "copy", out_path]):
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return "copy"
        # A partial file from the failed attempt would confuse the caller.
        _cleanup_partial(out_path)

    # 2. Re-encode. NVENC when it's real, otherwise libx264.
    encoder = pick_video_encoder(encoder_preference)
    quality = (["-preset", "p4", "-cq", "23"] if encoder == "h264_nvenc"
               else ["-preset", encode_preset, "-crf", "20"])
    if _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", video_path, "-i", audio_path,
             *common, "-c:v", encoder, *quality,
             "-pix_fmt", "yuv420p", out_path]):
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return encoder
    _cleanup_partial(out_path)

    # 3. libx264, if NVENC was tried and produced nothing.
    if encoder != "libx264":
        if _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-i", video_path, "-i", audio_path,
                 *common, "-c:v", "libx264", "-preset", encode_preset,
                 "-crf", "20", "-pix_fmt", "yuv420p", out_path]):
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return "libx264"
        _cleanup_partial(out_path)

    return None


def _cleanup_partial(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


class StageTimer:
    """Per-stage wall-clock timing, printed as the pipeline runs.

    Exists because "the upload is slow" is not actionable - the useful
    question is which stage owns the minutes.
    """

    def __init__(self, label: str, enabled: bool = True):
        self.label = label
        self.enabled = enabled
        self.stages: list = []
        self._t0 = time.perf_counter()

    def mark(self, stage: str) -> float:
        elapsed = time.perf_counter() - self._t0
        self._t0 = time.perf_counter()
        self.stages.append((stage, elapsed))
        if self.enabled:
            print(f"[Timing] {self.label}: {stage} took {elapsed:.1f}s")
        return elapsed

    def total(self) -> float:
        return sum(seconds for _, seconds in self.stages)

    def summary(self) -> str:
        if not self.stages:
            return f"{self.label}: nothing timed"
        parts = ", ".join(f"{name} {seconds:.1f}s" for name, seconds in self.stages)
        return f"{self.label}: {parts} (total {self.total():.1f}s)"
