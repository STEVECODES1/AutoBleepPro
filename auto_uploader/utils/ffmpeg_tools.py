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
    # 320x240, not 128x128: NVENC refuses frames under ~145px wide
    # ("Frame Dimension less than the minimum supported value"), so the
    # old 128x128 probe failed on a working RTX 4060 and every color-graded
    # render silently fell back to CPU libx264 - about 4x slower.
    return _run(["ffmpeg", "-y", "-hide_banner", "-f", "lavfi",
                 "-i", "color=c=black:s=320x240:d=0.1", "-c:v", "h264_nvenc",
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
    video_filter: str = "",
) -> Optional[str]:
    """Put `audio_path` onto `video_path`'s pictures, writing `out_path`.

    Returns the strategy used ("copy", "h264_nvenc", "libx264") or None if
    ffmpeg couldn't do it at all and the caller should fall back.
    """
    if not have_ffmpeg():
        return None

    # A viewer reported "audio is delayed" on a stream censored for 277
    # flagged words. Checked and ruled out: the mute rebuild itself does
    # not drift - a simulated 650-span rebuild (matching another real
    # stream's violation count) came back within a millisecond of the
    # source length, so hundreds of pydub splices are not the cause.
    #
    # A fixed start offset was also checked and is not the mechanism
    # either: the censored audio is a WAV, which cannot encode a
    # timestamp at all - ffmpeg always reads its first sample as t=0 -
    # and -map ... without -copyts rebases the video input's own PTS to
    # roughly zero the same way. Two inputs that both start at ~0 do not
    # explain a delay from the first frame.
    #
    # What remains, and could not be verified further without the
    # source file itself: the audio here is rebuilt on a perfectly
    # regular clock - pydub, working in exact milliseconds from Whisper
    # timestamps - with no knowledge of whatever real-world irregularity
    # the ORIGINAL video's own timestamps carry from being a live HLS
    # capture running for hours (dropped frames, encoder stalls, a
    # declared frame rate that is not quite the achieved one). None of
    # that shows up over a short clip. Over a long stream it can
    # accumulate into exactly a growing, increasingly audible offset -
    # which is what a report on a long stream, rather than a short one,
    # would look like.
    #
    # -af aresample=async=1 is ffmpeg's own documented answer to that
    # shape of problem: it continuously compares the audio's timestamps
    # against what elapsed video time implies and pads or trims samples
    # to close any gap ("filling and trimming" per ffmpeg's own
    # -h filter=aresample output) - correcting accumulated drift, not
    # just a fixed start offset. Only audio is filtered; -c:v copy is
    # untouched, so this costs nothing in picture quality, and nothing
    # measurable in speed - audio was already being re-encoded to aac
    # here, never stream-copied. Where there is no drift to correct it
    # is a no-op.
    common = ["-map", "0:v:0", "-map", "1:a:0",
              "-c:a", "aac", "-b:a", audio_bitrate,
              "-af", "aresample=async=1",
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
    # p5 / cq 19 measured against libx264 crf 18 on a color-graded 1080p30
    # sample: SSIM 0.984, same speed as p4 (the filters, not the encoder,
    # set the pace), so take the better quality.
    # Capped at 6 Mbit/s: the stream recordings are only ~4 Mbit/s to begin
    # with, so spending more adds upload time, not detail. Uncapped, a 2h
    # VOD came out 7.7 GB and took ~1.5h to upload.
    quality = (["-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                "-maxrate", "6M", "-bufsize", "12M"]
               if encoder == "h264_nvenc"
               else ["-preset", encode_preset, "-crf", "18"])
    vf_args = (["-vf", video_filter] if video_filter else [])
    if _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", video_path, "-i", audio_path,
             *common, "-c:v", encoder, *quality, *vf_args,
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


def start_video_render(video_path: str, out_path: str,
                       encoder_preference: str = "auto",
                       encode_preset: str = "fast",
                       video_filter: str = ""):
    """Start rendering the PICTURE ONLY of `video_path` (graded with
    `video_filter`) into `out_path`, in the background. Returns
    (process, encoder), or None if it cannot start.

    The censor pass used to grade the picture only after Whisper had
    finished listening, though the picture never depends on what was
    heard - on 10/8 that was 15 minutes of transcription and then 50 of
    rendering, one after the other. Started first, the grade runs on
    the processor and the encoder while Whisper uses the GPU, and the
    censored sound is laid over it with a stream copy at the end.
    Same encoder settings as mux_audio, so the picture is identical.
    """
    if not have_ffmpeg():
        return None
    encoder = pick_video_encoder(encoder_preference)
    quality = (["-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0",
                "-maxrate", "6M", "-bufsize", "12M"]
               if encoder == "h264_nvenc"
               else ["-preset", encode_preset, "-crf", "18"])
    vf_args = (["-vf", video_filter] if video_filter else [])
    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", video_path, "-map", "0:v:0", "-an",
             "-c:v", encoder, *quality, *vf_args, "-pix_fmt", "yuv420p",
             out_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    return proc, encoder


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


def stream_durations(path: str) -> tuple:
    """(video_seconds, audio_seconds) for the file's first video and
    first audio stream, or None for either that cannot be read.

    Deliberately two numbers, not media_duration()'s one. mux_audio()
    always passes -shortest, which clamps the CONTAINER's overall
    duration to whichever stream is shorter - so a real picture/sound
    mismatch does not reliably show up in format=duration, the container
    could report a clean, unremarkable length while the two streams
    inside it disagree. This reads each stream's own declared duration
    directly, which is what actually catches it.
    """
    def _one(selector: str) -> Optional[float]:
        try:
            completed = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", selector,
                 "-show_entries", "stream=duration", "-of", "csv=p=0", path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return None
        try:
            return float(completed.stdout.decode().strip().splitlines()[0])
        except (ValueError, IndexError):
            return None
    return _one("v:0"), _one("a:0")


def media_duration(path: str) -> Optional[float]:
    """Seconds of media in a file, or None if it cannot be determined.

    None rather than 0 on failure, and the distinction matters: callers
    use this to decide whether a video is a short clip, and an
    unmeasurable file must fall through to the normal path rather than be
    treated as zero seconds long.
    """
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    try:
        return float(completed.stdout.decode().strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def video_dimensions(path: str):
    """(width, height), or None if they cannot be read.

    None rather than a guess: callers use this to decide whether a file
    already has the shape they want, and guessing wrong means either a
    needless re-encode or a video posted in the wrong aspect.
    """
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
             path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    try:
        width, height = completed.stdout.decode().strip().splitlines()[0].split("x")[:2]
        return int(width), int(height)
    except (ValueError, IndexError):
        return None


# How far from 9:16 a video can be and still count as vertical. Loose
# enough to cover a platform's own re-encode rounding a dimension by a
# pixel or two, tight enough that a square or a 16:9 never passes.
VERTICAL_TOLERANCE = 0.02


def is_already_vertical(path: str) -> bool:
    """True when this file is already the 9:16 shape a Reel wants.

    Every clip out of ClipMaker is 1080x1920 already. Re-framing one is a
    second full encode that costs time and a generation of quality to
    produce a file the same shape as the one it started from.
    """
    size = video_dimensions(path)
    if not size:
        return False
    width, height = size
    if not width or not height:
        return False
    return abs((width / height) - (9 / 16)) <= VERTICAL_TOLERANCE


# ---------------------------------------------------------------------------
# Intro prepend
#
# The old prepend was a plain `-f concat -c copy` of intro.mp4 + the VOD.
# That only works when both files share frame rate, timebase, and audio
# format. The CapCut intro is 60fps / timebase 1/60 / 44.1kHz stereo; the
# VOD is 30fps / timebase 1/90000. Stream-copying across that mismatch
# corrupts the timestamps: a 69 second test came out "103,513 seconds"
# long, so YouTube rejected the 2h VOD with "Video too long" (>12h).
#
# Fix: re-encode the (9 second) intro to match the VOD exactly, then
# concat. Then VERIFY the result's length before handing it to the
# uploader. If anything is off, return False and the caller uploads
# without the intro rather than shipping a broken file.
# ---------------------------------------------------------------------------

def _probe_json(path: str) -> dict:
    import json
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,r_frame_rate,time_base,width,height,"
             "sample_rate,channels",
             "-of", "json", path],
            capture_output=True, text=True, timeout=300)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}


def prepend_intro(intro_path: str, main_path: str, out_path: str) -> bool:
    """Write intro + main to out_path. True only if the result checks out."""
    import hashlib
    import tempfile

    if not have_ffmpeg():
        return False
    main = _probe_json(main_path)
    intro = _probe_json(intro_path)
    try:
        v = next(s for s in main["streams"] if s["codec_type"] == "video")
        a = next(s for s in main["streams"] if s["codec_type"] == "audio")
        main_dur = float(main["format"]["duration"])
        intro_dur = float(intro["format"]["duration"])
        fps = v["r_frame_rate"]
        w, h = int(v["width"]), int(v["height"])
        timescale = int(v["time_base"].split("/")[1])
        rate, ch = int(a["sample_rate"]), int(a["channels"])
    except (KeyError, StopIteration, ValueError, TypeError) as exc:
        print(f"[Intro] could not read stream info ({exc}) - skipping intro")
        return False

    # The matched intro is cached per (intro file, target format), so it is
    # encoded once, not on every upload.
    st = os.stat(intro_path)
    key = hashlib.sha1(f"{os.path.abspath(intro_path)}|{st.st_mtime}|{st.st_size}|"
                       f"{fps}|{w}x{h}|{timescale}|{rate}|{ch}".encode()).hexdigest()[:12]
    matched = os.path.join(tempfile.gettempdir(), f"autobleep_intro_{key}.mp4")
    if not (os.path.exists(matched) and os.path.getsize(matched) > 0):
        partial = matched + ".partial.mp4"
        ok = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                   "-i", intro_path,
                   "-vf", (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                           f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p"),
                   *(["-c:v", "h264_nvenc", "-profile:v", "high", "-preset", "p5",
                      "-rc", "vbr", "-cq", "19", "-b:v", "0"] if nvenc_works() else
                     ["-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", "18"]),
                   "-video_track_timescale", str(timescale),
                   "-c:a", "aac", "-ar", str(rate), "-ac", str(ch), "-b:a", "192k",
                   "-movflags", "+faststart", partial], timeout=600)
        if not ok:
            _cleanup_partial(partial)
            print("[Intro] could not re-encode the intro to match - skipping intro")
            return False
        os.replace(partial, matched)

    list_fd, list_path = tempfile.mkstemp(suffix="_concat.txt")
    try:
        with os.fdopen(list_fd, "w", encoding="utf-8") as fh:
            fh.write("file '" + matched.replace(os.sep, "/") + "'\n")
            fh.write("file '" + os.path.abspath(main_path).replace(os.sep, "/") + "'\n")
        ok = _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                   "-f", "concat", "-safe", "0", "-i", list_path,
                   "-c", "copy", "-movflags", "+faststart", out_path])
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
    if not ok:
        _cleanup_partial(out_path)
        print("[Intro] concat failed - skipping intro")
        return False

    # Verify before upload. This is the check that would have caught the
    # 28-hour file.
    got = _probe_json(out_path).get("format", {}).get("duration")
    expected = main_dur + intro_dur
    if got is None or abs(float(got) - expected) > 5:
        print(f"[Intro] result is {got}s but should be ~{expected:.0f}s - "
              f"discarding it and uploading without intro")
        _cleanup_partial(out_path)
        return False
    seam = max(0.0, intro_dur - 3)
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{seam:.2f}", "-i", out_path,
                        "-t", "10", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        print("[Intro] the intro/stream join does not decode cleanly - "
              "uploading without intro")
        _cleanup_partial(out_path)
        return False
    print(f"[Intro] prepended and verified ({float(got):.0f}s total)")
    return True
