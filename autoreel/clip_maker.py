"""
Turns a long VOD into vertical clips, with ffmpeg doing the work.

WHY NOT THE EXISTING moviepy RENDERER
-------------------------------------
`clipper.ClipRenderer` pulls every frame into Python to crop it. That is
the right shape for the face-tracked mode, where the crop window moves per
frame and something has to decide where. For a fixed centre crop - the
default, and the case that matters for gameplay - the crop is a constant,
and ffmpeg applies it in a single native pass with GPU encoding available.
The moviepy path stays as the fallback and as the face-tracking
implementation; it is not the default road.

WHAT A CLIP IS HERE
-------------------
A window chosen by HighlightScorer, cut from the source, cropped 9:16,
scaled to 1080x1920, with the words burned in. The transcript that drives
both the highlight choice and the captions is the one the censor pass
already cached, so clipping a video that has been through the uploader
costs no transcription at all.

The source is the CENSORED copy when one exists. A clip is the part of a
stream most likely to be watched by someone who has never seen the
channel, on platforms with less patience than YouTube - so it should not
be the one carrying uncensored audio.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .captions import caption_file_for_clip
from .crop_strategy import (
    CROP_CENTER,
    CROP_FACE,
    CROP_FIT,
    CROP_MOTION,
    GAMEPLAY_CONTENT,
    resolve_crop_strategy,
)
from .highlights import Highlight, HighlightScorer

VERTICAL_WIDTH = 1080
VERTICAL_HEIGHT = 1920

# Long enough to land a moment, short enough to be watched to the end.
DEFAULT_MIN_SECONDS = 15.0
DEFAULT_MAX_SECONDS = 60.0
DEFAULT_CLIP_COUNT = 3

# Ten clips out of one good minute is ten versions of the same clip. This
# is how far apart two chosen windows have to start on a long stream.
DEFAULT_MIN_GAP = 90.0

# A stream opens on a waiting screen and closes on goodbyes; neither is a
# clip, and both score well because people talk over them.
DEFAULT_SKIP_INTRO = 120.0
DEFAULT_SKIP_OUTRO = 60.0

_TIMEOUT = 60 * 30


class ClipError(RuntimeError):
    """Rendering failed in a way the caller should hear about."""


@dataclass
class ClipSpec:
    """One clip to render."""
    start: float
    end: float
    index: int = 1
    title: str = ""
    score: float = 0.0
    # Everything said in the window. `title` is one line out of this -
    # the line worth putting on a thumbnail - and a caption wants the
    # rest, so both are carried rather than one being derived twice.
    transcript: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class ClipResult:
    path: str
    spec: ClipSpec
    captioned: bool
    strategy: str
    encoder: str


def have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


# ── Filter construction ──────────────────────────────────────────────────

# How hard the background copy is blurred in the FIT layout. Enough that
# it reads as texture rather than a second, smaller video competing with
# the real one.
FIT_BLUR_SIGMA = 24


def fit_filter() -> str:
    """The whole 16:9 frame on a blurred 9:16 canvas. Nothing cropped.

    Two copies of the same input: one scaled up past the canvas, cropped
    to it and blurred to make a background; the other scaled to the
    canvas WIDTH and laid over the middle at its true shape. Both people
    in a webcam call survive, which a centre crop cannot promise - it
    keeps the middle third of the width and whoever is standing in it.

    One input, one output, so this is still a valid -vf chain despite the
    labels; it does not need -filter_complex.
    """
    return (
        "split[fitbg][fitfg];"
        f"[fitbg]scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={VERTICAL_WIDTH}:{VERTICAL_HEIGHT},"
        f"gblur=sigma={FIT_BLUR_SIGMA}[fitbg2];"
        f"[fitfg]scale={VERTICAL_WIDTH}:-2:flags=bicubic[fitfg2];"
        "[fitbg2][fitfg2]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )


def crop_filter(strategy: str = CROP_CENTER) -> str:
    """The 16:9 -> 9:16 re-frame, expressed for ffmpeg.

    Written against iw/ih rather than fixed numbers so it is correct for
    1080p, 1440p and 4K sources without branching. The min() pair keeps it
    valid for a source that is already tall - cropping to a width larger
    than the input is an ffmpeg error, not a no-op.
    """
    if strategy == CROP_FACE:
        # Face tracking needs a per-frame window, which a static filter
        # cannot express; the caller routes that to the moviepy renderer.
        raise ClipError("face tracking is not a static crop - use ClipRenderer")
    if strategy == CROP_FIT:
        return fit_filter()
    return ("crop='min(iw,ih*9/16)':'min(ih,iw*16/9)',"
            f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:flags=bicubic,"
            "setsar=1")


def escape_filter_path(path: str) -> str:
    """Escape a path for use INSIDE an ffmpeg filter argument.

    The subtitles filter parses its argument, so a Windows path breaks it
    twice over: the backslashes read as escapes and the drive colon reads
    as the start of the next filter option. `D:\\clips\\a.ass` has to
    become `D\\:/clips/a.ass` or ffmpeg reports a missing file for a file
    that is right there.
    """
    path = os.path.abspath(path).replace("\\", "/")
    path = path.replace(":", r"\:")
    return path.replace("'", r"\'").replace("[", r"\[").replace("]", r"\]")


def build_filter(strategy: str = CROP_CENTER,
                 caption_path: Optional[str] = None) -> str:
    chain = crop_filter(strategy)
    if caption_path:
        chain += f",subtitles='{escape_filter_path(caption_path)}'"
    return chain


def _encoder_args(encoder: str, preset: str = "fast", crf: int = 20) -> list:
    if encoder == "h264_nvenc":
        # NVENC spells quality as -cq, not -crf, and ignores libx264's
        # preset names.
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", str(crf)]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]


# ── Rendering ────────────────────────────────────────────────────────────

def render_clip(source_path: str, spec: ClipSpec, output_path: str,
                strategy: str = CROP_CENTER,
                caption_path: Optional[str] = None,
                encoder: str = "libx264",
                preset: str = "fast",
                crf: int = 20) -> str:
    """Cut, crop and (optionally) caption one clip. Returns the path."""
    if not have_ffmpeg():
        raise ClipError("ffmpeg is not on PATH - clip rendering needs it")
    if spec.duration <= 0:
        raise ClipError(f"clip {spec.index} has no duration")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    # Write to a temp name and rename on success, so an interrupted render
    # never leaves a truncated .mp4 that looks like a finished clip.
    partial = f"{output_path}.partial.mp4"

    args = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        # -ss before -i seeks by keyframe index instead of decoding up to
        # the cut, which on a multi-hour VOD is the difference between
        # instant and minutes. -accurate_seek keeps the frame exact.
        "-accurate_seek", "-ss", f"{spec.start:.3f}",
        "-i", source_path,
        "-t", f"{spec.duration:.3f}",
        "-vf", build_filter(strategy, caption_path),
        *_encoder_args(encoder, preset, crf),
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        # Vertical feeds are 30fps; leaving a 60fps source at 60 doubles
        # the file for no visible gain after the platform re-encodes it.
        "-r", "30",
        "-movflags", "+faststart",
        partial,
    ]

    try:
        completed = subprocess.run(
            args, timeout=_TIMEOUT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired:
        _remove(partial)
        raise ClipError(f"clip {spec.index} timed out after {_TIMEOUT}s")

    if completed.returncode != 0 or not os.path.exists(partial):
        _remove(partial)
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise ClipError(f"ffmpeg failed on clip {spec.index}: {detail[-400:]}")

    os.replace(partial, output_path)
    return output_path


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ── Choosing what to clip ────────────────────────────────────────────────

def specs_from_segments(segments: Iterable[dict], count: int = DEFAULT_CLIP_COUNT,
                        min_seconds: float = DEFAULT_MIN_SECONDS,
                        max_seconds: float = DEFAULT_MAX_SECONDS,
                        skip_intro_seconds: float = 0.0,
                        skip_outro_seconds: float = 0.0,
                        min_gap_seconds: float = DEFAULT_MIN_GAP,
                        llm_rank: bool = True,
                        llm_provider: str = "",
                        llm_model: str = "") -> list:
    """Pick clip windows from a transcript.

    Two stages, and the second is optional. The scorer shortlists on what
    a transcript looks like - reactions, density, shape - which is free
    and gets the obvious dead ends out of the way. A language model then
    reads the shortlist and says which of them a person would actually
    post, because that is the part scoring cannot do. With no key
    configured, or on any failure, the scorer's own order stands.
    """
    from .llm_highlights import CANDIDATE_MULTIPLIER, rank

    scorer = HighlightScorer(min_duration=min_seconds, max_duration=max_seconds,
                             skip_intro_seconds=skip_intro_seconds,
                             skip_outro_seconds=skip_outro_seconds)
    pool = count * CANDIDATE_MULTIPLIER if llm_rank else count
    shortlist = scorer.select_clips(list(segments), count=pool,
                                    min_gap=min_gap_seconds)

    highlights = rank(shortlist, count, llm_provider, llm_model) if llm_rank else None
    if highlights is None:
        # The scorer's own verdict: best first, then back into timeline
        # order so clip 01 is the earliest.
        highlights = sorted(shortlist, key=lambda h: h.score,
                            reverse=True)[:count]
        highlights.sort(key=lambda h: h.start)
    else:
        print(f"[Clips] A model read {len(shortlist)} candidates and chose "
              f"{len(highlights)}.")

    return [
        ClipSpec(start=h.start, end=h.end, index=i,
                 title=(h.hook or h.text).strip(),
                 score=h.score, transcript=h.text.strip())
        for i, h in enumerate(highlights, start=1)
    ]


def clip_filename(basename: str, spec: ClipSpec) -> str:
    safe = "".join(c if c.isalnum() or c in " -_" else "_"
                   for c in basename).strip() or "clip"
    return f"{safe}_clip{spec.index:02d}.mp4"


@dataclass
class ClipMaker:
    """Renders a video's highlights as vertical clips."""

    output_dir: str
    config: dict = field(default_factory=dict)
    content_kind: str = GAMEPLAY_CONTENT
    captions: bool = True
    count: int = DEFAULT_CLIP_COUNT
    min_seconds: float = DEFAULT_MIN_SECONDS
    max_seconds: float = DEFAULT_MAX_SECONDS
    encoder: str = "libx264"
    preset: str = "fast"
    crf: int = 20
    skip_intro_seconds: float = DEFAULT_SKIP_INTRO
    skip_outro_seconds: float = DEFAULT_SKIP_OUTRO
    min_gap_seconds: float = DEFAULT_MIN_GAP
    llm_rank: bool = True
    llm_provider: str = ""
    llm_model: str = ""

    @property
    def strategy(self) -> str:
        return resolve_crop_strategy(self.config, self.content_kind)

    def make(self, source_path: str, segments: Iterable[dict],
             basename: str = "") -> list:
        """Render clips for one video. Returns ClipResults, newest last.

        A clip that fails to render does not abort the rest: three clips
        where one had a bad window is a better outcome than none, and the
        failure is raised only if every clip failed.
        """
        segments = list(segments or ())
        specs = specs_from_segments(segments, self.count,
                                    self.min_seconds, self.max_seconds,
                                    self.skip_intro_seconds,
                                    self.skip_outro_seconds,
                                    self.min_gap_seconds,
                                    self.llm_rank, self.llm_provider,
                                    self.llm_model)
        if not specs:
            return []

        strategy = self.strategy
        if strategy == CROP_FACE:
            raise ClipError(
                "crop_strategy is 'face', which needs the per-frame "
                "ClipRenderer. Set clips.crop_strategy to 'center' for "
                "gameplay, or call ClipRenderer directly for face-cam.")
        if strategy == CROP_MOTION:
            # Not built yet; centre is the honest fallback rather than
            # pretending motion tracking happened.
            strategy = CROP_CENTER

        basename = basename or os.path.splitext(os.path.basename(source_path))[0]
        os.makedirs(self.output_dir, exist_ok=True)

        results = []
        failures = []
        for spec in specs:
            output_path = os.path.join(self.output_dir, clip_filename(basename, spec))
            caption_path = None
            if self.captions:
                caption_path = caption_file_for_clip(
                    os.path.join(self.output_dir, f".{basename}_clip{spec.index:02d}.ass"),
                    segments, spec.start, spec.end)
            try:
                render_clip(source_path, spec, output_path, strategy,
                            caption_path, self.encoder, self.preset, self.crf)
            except ClipError as exc:
                failures.append(str(exc))
                continue
            finally:
                if caption_path:
                    _remove(caption_path)
            results.append(ClipResult(output_path, spec, bool(caption_path),
                                      strategy, self.encoder))

        if not results and failures:
            raise ClipError("; ".join(failures))
        return results


def make_vertical(source_path: str, output_path: str,
                  strategy: str = CROP_CENTER,
                  encoder: str = "libx264", preset: str = "fast") -> Optional[str]:
    """Re-frame a 16:9 clip as a full-bleed 9:16 video. Path, or None.

    Instagram accepts a landscape video and letterboxes it - black bars
    top and bottom, the picture a third of the height, thumbnail mostly
    empty. That is not a Reel, it is a video someone forgot to crop, and
    it is what a raw Twitch clip looks like posted straight through.

    Centre crop rather than fit-with-bars: for gameplay the crosshair,
    the HUD and the action are all centre-screen, so the crop keeps
    everything that matters and fills the frame. Same filter the clip
    renderer uses.

    Audio is stream-copied - only the framing changes.
    """
    if not have_ffmpeg():
        return None
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", source_path, "-vf", crop_filter(strategy),
            *_encoder_args(encoder, preset),
            "-c:a", "copy", "-movflags", "+faststart", output_path]
    try:
        completed = subprocess.run(args, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
    except (FileNotFoundError, OSError):
        return None
    if completed.returncode != 0 or not os.path.exists(output_path) \
            or os.path.getsize(output_path) == 0:
        _remove(output_path)
        return None
    return output_path
