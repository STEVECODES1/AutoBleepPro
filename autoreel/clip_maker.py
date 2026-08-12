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
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .captions import caption_file_for_clip
from . import face_region, motion_region
from .crop_strategy import (
    CROP_CENTER,
    CROP_FACE,
    CROP_FIT,
    CROP_MOTION,
    CROP_REGION,
    GAMEPLAY_CONTENT,
    resolve_crop_strategy,
    resolve_region,
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

# Noise patterns stripped from basenames before they become clip names.
# Matches things like: "5-12-26", "2026-08-10", "howl ", "_CLEAN",
# "[v70rbpc]"
#
# The bracketed ID is the platform's own key for the video. It has to be
# in the DOWNLOAD filename - it is what tells two streams with the same
# title apart, and what the archive matches on - but nothing downstream
# reads it, and it survived into clip names and from there into titles:
# "Monkey N Gamble Howl V70Rbpc - Clip 01". Nobody writes that. It reads
# as a database key that escaped, which is exactly the tell that a human
# did not make this.
_NOISE = re.compile(
    r"\[[^\]]{1,32}\]"                     # platform IDs: [v70rbpc]
    r"|\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}\b"  # dates: 5-12-26, 08/10/2026
    r"|\b\d{4}[-/]\d{2}[-/]\d{2}\b"        # ISO dates: 2026-08-10
    r"|\bCLEAN\b",                          # censor suffix
    re.IGNORECASE,
)

# The recorder's own prefix, and ONLY when it leads and a date follows:
# "howl 5-12-26 Stackswopo Stream". A bare \bhowl\b used to be stripped
# anywhere, which is wrong here - howl.gg is what the streamer is
# actually playing, so "Yoo Howl" and "Monkey N Gamble Howl" are the real
# titles and losing the word makes the clip name say less, not more.
_RECORDER_PREFIX = re.compile(r"^howl\b(?=\s+\d)", re.IGNORECASE)


# ── Watermark ────────────────────────────────────────────────────────────

# Burned into every clip bottom-left so viewers know where to find us.
WATERMARK_TEXT = "rumble.com/c/BinScripts"

# font size relative to 1080px wide frame — small but legible on phone.
WATERMARK_FONTSIZE = 28

# distance from the bottom-left corner (in pixels)
WATERMARK_MARGIN_X = 18
WATERMARK_MARGIN_Y = 22

# 0 = fully transparent, 1 = fully opaque.  0.55 keeps it unobtrusive.
WATERMARK_ALPHA = 0.55


# Font files to try, most-preferred first. An EXPLICIT file is the whole
# point: `font=monospace` asks fontconfig to resolve a name, and Windows
# ships no fontconfig config, so every render that touched text died with
# "Fontconfig error: Cannot load default config file: File not found".
# With fontfile= the resolver is never consulted at all.
_FONT_CANDIDATES = (
    # Windows
    "C:/Windows/Fonts/consola.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    # macOS
    "/System/Library/Fonts/Menlo.ttc",
    "/Library/Fonts/Arial.ttf",
)


def font_file() -> str:
    """A real font file on this machine, or "" if there is none.

    Checked rather than assumed, because the failure mode of guessing is
    a render that dies at the very end of the pipeline - after the cut,
    the crop and the encode have all been paid for.
    """
    override = os.environ.get("AUTOREEL_FONT_FILE", "").strip()
    if override and os.path.isfile(override):
        return override
    windir = os.environ.get("WINDIR", "").replace("\\", "/")
    for candidate in _FONT_CANDIDATES:
        if windir and candidate.startswith("C:/Windows"):
            candidate = candidate.replace("C:/Windows", windir, 1)
        if os.path.isfile(candidate):
            return candidate
    return ""


def escape_font_path(path: str) -> str:
    """A font path as drawtext will accept it.

    The drive colon in C:/Windows/... reads as the start of the next
    filter option unless it is escaped, which is the same trap the
    subtitles filter sets - see escape_filter_path.
    """
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def watermark_filter() -> str:
    """The channel watermark, bottom-left. "" when no font file exists.

    Returning empty rather than a best-effort chain is deliberate: a
    watermark is a nice-to-have and a clip is not, so a machine with no
    usable font ships the clip without the text instead of shipping
    nothing.
    """
    path = font_file()
    if not path:
        return ""
    return (
        f"drawtext="
        f"fontfile='{escape_font_path(path)}':"
        f"text='{WATERMARK_TEXT}':"
        f"fontsize={WATERMARK_FONTSIZE}:"
        f"fontcolor=white@{WATERMARK_ALPHA:.2f}:"
        f"x={WATERMARK_MARGIN_X}:"
        f"y=h-th-{WATERMARK_MARGIN_Y}"
    )


def _ffmpeg_env() -> dict:
    """The environment ffmpeg runs in.

    FONTCONFIG_FILE names a fonts.conf FILE, not a directory - an earlier
    fix pointed it at C:/Windows/Fonts and fontconfig kept failing,
    because a directory is what FONTCONFIG_PATH takes. The real fix is
    upstream of this: watermark_filter passes an explicit fontfile=, so
    fontconfig is never asked to resolve anything. Any value the user has
    genuinely set is left alone.
    """
    return os.environ.copy()


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


def crop_filter(strategy: str = CROP_CENTER, region: Optional[dict] = None) -> str:
    """The 16:9 -> 9:16 re-frame, expressed for ffmpeg.

    Written against iw/ih rather than fixed numbers so it is correct for
    1080p, 1440p and 4K sources without branching. The min() pair keeps it
    valid for a source that is already tall - cropping to a width larger
    than the input is an ffmpeg error, not a no-op.

    When *region* is supplied (a dict with fractional x/y/width/height
    keys) the crop is applied to that specific rectangle instead of the
    default centre.
    """
    if strategy == CROP_FACE:
        # Face tracking needs a per-frame window, which a static filter
        # cannot express; the caller routes that to the moviepy renderer.
        raise ClipError("face tracking is not a static crop - use ClipRenderer")
    if strategy == CROP_FIT:
        return fit_filter()
    if region:
        x = f"iw*{region['x']:.4f}"
        y = f"ih*{region['y']:.4f}"
        w = f"iw*{region['width']:.4f}"
        h = f"ih*{region['height']:.4f}"
        return (f"crop={w}:{h}:{x}:{y},"
                f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:flags=bicubic,"
                "setsar=1")
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


# The crop width for a 9:16 cut out of any source, as an ffmpeg
# expression. Named because the motion path has to compute its left edge
# from the same value the crop uses, and the two drifting apart would
# put the pan half a crop off.
CROP_WIDTH_EXPR = "min(iw,ih*9/16)"


def motion_crop_filter(commands_path: str) -> str:
    """A 9:16 crop whose x is driven along a path by sendcmd.

    The height and width are fixed - only the horizontal position moves,
    because a 16:9 source cut to 9:16 has no vertical slack to pan into.
    """
    return (f"sendcmd=f='{escape_filter_path(commands_path)}',"
            f"crop={CROP_WIDTH_EXPR}:'min(ih,iw*16/9)':"
            f"(iw-{CROP_WIDTH_EXPR})/2:0,"
            f"scale={VERTICAL_WIDTH}:{VERTICAL_HEIGHT}:flags=bicubic,"
            "setsar=1")


def build_filter(strategy: str = CROP_CENTER,
                 caption_path: Optional[str] = None,
                 region: Optional[dict] = None,
                 watermark: bool = True,
                 motion_commands: str = "") -> str:
    """Compose the full ffmpeg -vf filter chain for one clip.

    Order: crop/fit  ->  captions (optional)  ->  watermark.
    """
    if motion_commands:
        chain = motion_crop_filter(motion_commands)
    else:
        chain = crop_filter(strategy, region)
    if caption_path:
        chain += f",subtitles='{escape_filter_path(caption_path)}'"
    if watermark:
        # Last, so it sits on top of captions too. Skipped entirely when
        # there is no font file - an empty filter appended after a comma
        # is a syntax error, and losing ten clips to a watermark is not a
        # trade anyone would make.
        mark = watermark_filter()
        if mark:
            chain += f",{mark}"
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
                crf: int = 20,
                region: Optional[dict] = None,
                watermark: bool = True,
                motion_commands: str = "") -> str:
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
        "-vf", build_filter(strategy, caption_path, region, watermark,
                            motion_commands),
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
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=_ffmpeg_env())
    except subprocess.TimeoutExpired:
        _remove(partial)
        raise ClipError(f"clip {spec.index} timed out after {_TIMEOUT}s")

    if completed.returncode != 0 or not os.path.exists(partial):
        _remove(partial)
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        if watermark:
            # A whole stream's clips were lost to a watermark once: the
            # drawtext filter needs a font, and a machine without one
            # failed every render at the last step. The text is a
            # nice-to-have; the clip is the thing.
            print(f"[Clips] clip {spec.index}: retrying without the "
                  f"watermark ({detail.splitlines()[-1][:120] if detail else 'no detail'})")
            return render_clip(source_path, spec, output_path, strategy,
                               caption_path, encoder, preset, crf, region,
                               watermark=False,
                               motion_commands=motion_commands)
        # An empty stderr with a non-zero exit is what "suppressing" the
        # fontconfig message produced last time: ten clips failed and the
        # reason printed was ";". Say the exit code and the filter chain
        # so there is always something to work from.
        raise ClipError(
            f"ffmpeg failed on clip {spec.index} (exit {completed.returncode}): "
            + (detail[-800:] if detail
               else f"no output from ffmpeg. Filter chain was: "
                    f"{build_filter(strategy, caption_path, region, watermark, motion_commands)[:300]}"))

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
                        energy: Optional[list] = None,
                        chat: Optional[list] = None,
                        llm_rank: bool = True,
                        llm_provider: str = "",
                        llm_model: str = "",
                        source_path: str = "",
                        use_vision: bool = True) -> list:
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
                             skip_outro_seconds=skip_outro_seconds,
                             energy=list(energy or []),
                             chat=list(chat or []))
    pool = count * CANDIDATE_MULTIPLIER if llm_rank else count
    shortlist = scorer.select_clips(list(segments), count=pool,
                                    min_gap=min_gap_seconds)

    highlights = rank(shortlist, count, llm_provider, llm_model,
                      source_path=source_path if use_vision else "") \
        if llm_rank else None
    if highlights is None:
        if llm_rank:
            print("[Clips] No model opinion - picking on the transcript "
                  "score alone. Check the key with --check-llm.")
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


def _clean_basename(raw: str) -> str:
    """Strip dates, noise tokens and extra whitespace from a VOD basename.

    Examples
    --------
    "howl 5-12-26 Stackswopo Stream"  ->  "Stackswopo Stream"
    "Stackswopo_2026-08-10_CLEAN"     ->  "Stackswopo"
    "stackswopo stream clip01"        ->  "Stackswopo Stream Clip01"
    """
    # Underscores FIRST. The date and suffix patterns are anchored on
    # word boundaries, and an underscore is a word character - so
    # "Stackswopo_2026-08-10_CLEAN" had no boundary before the date and
    # kept it, which the docstring above claimed it did not.
    name = re.sub(r"[_]+", " ", raw)
    name = _RECORDER_PREFIX.sub(" ", name)
    name = _NOISE.sub(" ", name)
    name = re.sub(r"\s{2,}", " ", name)  # collapse runs of spaces
    name = name.strip(" -")              # trim leading/trailing dashes
    return name.title() if name else "Clip"


def clip_filename(basename: str, spec: ClipSpec) -> str:
    """Return a clean, human-readable filename for one clip.

    Format: ``<Channel Name> - Clip 01.mp4``

    The raw VOD basename is stripped of dates, recorder prefixes and
    censor suffixes before it is used, so the result stays tidy regardless
    of how the source file was named.
    """
    label = _clean_basename(basename)
    # Filesystem-safe: keep letters, digits, spaces, hyphens, apostrophes.
    label = re.sub(r"[^\w\s\-']", "", label)
    # Stripping a date out of the middle leaves the gap it was sitting
    # in - "Damn  Stream" rather than "Damn Stream".
    label = " ".join(label.split()) or "Clip"
    return f"{label} - Clip {spec.index:02d}.mp4"


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
    caption_style: str = "word"
    caption_uppercase: bool = True
    use_audio_energy: bool = True
    skip_intro_seconds: float = DEFAULT_SKIP_INTRO
    skip_outro_seconds: float = DEFAULT_SKIP_OUTRO
    min_gap_seconds: float = DEFAULT_MIN_GAP
    llm_rank: bool = True
    llm_provider: str = ""
    llm_model: str = ""
    # Measure where the people are, per clip, instead of trusting the
    # rectangle in config.json. Only consulted by the region strategy -
    # gameplay never goes near a face detector, which is the whole point
    # of keeping face TRACKING off by default.
    find_faces: bool = True
    # Send the model two frames per candidate as well as the words. On
    # this channel the funniest moments are visual - a face reaction, a
    # fight starting - and the transcript for those says "...what" or
    # nothing at all.
    use_vision: bool = True

    @property
    def strategy(self) -> str:
        return resolve_crop_strategy(self.config, self.content_kind)

    @property
    def region(self) -> dict:
        """The rectangle REGION cuts, from the configured profile."""
        return resolve_region(self.config)

    def make(self, source_path: str, segments: Iterable[dict],
             basename: str = "", chat: Optional[list] = None) -> list:
        """Render clips for one video. Returns ClipResults, newest last.

        A clip that fails to render does not abort the rest: three clips
        where one had a bad window is a better outcome than none, and the
        failure is raised only if every clip failed.
        """
        segments = list(segments or ())
        energy = []
        if self.use_audio_energy:
            from .audio_energy import measure

            print("[Clips] Listening for where the room got loud...")
            energy = measure(source_path)
            print(f"[Clips] {len(energy)}s of audio measured."
                  if energy else
                  "[Clips] Could not measure the audio - going on the words "
                  "alone.")

        specs = specs_from_segments(segments, self.count,
                                    self.min_seconds, self.max_seconds,
                                    self.skip_intro_seconds,
                                    self.skip_outro_seconds,
                                    self.min_gap_seconds,
                                    energy, chat,
                                    self.llm_rank, self.llm_provider,
                                    self.llm_model,
                                    source_path, self.use_vision)
        if not specs:
            return []

        strategy = self.strategy
        if strategy == CROP_FACE:
            raise ClipError(
                "crop_strategy is 'face', which needs the per-frame "
                "ClipRenderer. Set clips.crop_strategy to 'center' for "
                "gameplay, or call ClipRenderer directly for face-cam.")
        # CROP_MOTION is handled per clip below - it needs a path
        # measured from that clip's own frames, and falls back to centre
        # on its own when there is nothing moving.

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
                    segments, spec.start, spec.end,
                    style=self.caption_style,
                    uppercase=self.caption_uppercase)
            # Gameplay that follows the action: measure where the frame
            # is CHANGING and walk the crop toward it, slowly. Faces are
            # the wrong signal here - GTA is full of NPC faces and none
            # of them are the point.
            motion_commands = ""
            if strategy == CROP_MOTION:
                path = motion_region.path_for(source_path, spec.start,
                                              spec.end - spec.start)
                if motion_region.is_worth_moving(path):
                    motion_commands = os.path.join(
                        self.output_dir,
                        f".{basename}_clip{spec.index:02d}.cmds")
                    with open(motion_commands, "w", encoding="utf-8") as f:
                        f.write(motion_region.commands_file(
                            path, CROP_WIDTH_EXPR))
                # Anything else - a static scene, no numpy, a read that
                # failed - renders as a plain centre crop, which is what
                # it would have been anyway.

            # A region typed into config.json is a guess, and it goes
            # stale the moment the call window moves - which is how
            # twenty clips came out framed on a browser showing a slots
            # game while the two people talking were off-crop. Measuring
            # per clip cannot go stale that way. None means no faces in
            # this stretch, which is normal, and the configured rectangle
            # stands.
            region = self.region
            if strategy == CROP_REGION and self.find_faces:
                measured = face_region.region_for(
                    source_path, spec.start, spec.end - spec.start)
                if measured:
                    region = measured
                else:
                    print(f"[Clips] Clip {spec.index:02d}: no faces found, "
                          f"using the configured rectangle.")
            try:
                render_clip(source_path, spec, output_path,
                            CROP_CENTER if strategy == CROP_MOTION else strategy,
                            caption_path, self.encoder, self.preset,
                            self.crf, region,
                            motion_commands=motion_commands)
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
    # Watermark is applied here too so standalone vertical conversions
    # are branded the same as clips produced by ClipMaker.
    # Same trap as build_filter: an empty watermark appended after a
    # comma is a filter-chain syntax error, and a machine with no font
    # file would fail every conversion at the last step.
    mark = watermark_filter()
    vf = crop_filter(strategy) + (f",{mark}" if mark else "")
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", source_path, "-vf", vf,
            *_encoder_args(encoder, preset),
            "-c:a", "copy", "-movflags", "+faststart", output_path]
    try:
        completed = subprocess.run(args, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   # Without this a wedged ffmpeg holds the
                                   # watcher thread forever, and nothing
                                   # else in the folder ever gets processed.
                                   timeout=_TIMEOUT,
                                   env=_ffmpeg_env())
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        _remove(output_path)
        return None
    if completed.returncode != 0 or not os.path.exists(output_path) \
            or os.path.getsize(output_path) == 0:
        _remove(output_path)
        return None
    return output_path
