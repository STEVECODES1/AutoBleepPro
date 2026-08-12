"""
Renders short-form clips from a finished video and parks them to post by hand.

WHY NOT AUTO-POST THEM
----------------------
Instagram is the only platform here with a real audience, and its API
cannot post anything without a publicly hosted file. Every free way to
provide that either wants a card on file or means using something as a
CDN that was not meant to be one. Neither is worth it, because the
alternative is genuinely better: a Reel uploaded from the phone app costs
nothing, needs no credentials, cannot get an account flagged, and is
treated at least as well by the platform as one pushed through the API.

So this renders the clips and writes the caption next to each one. The
work that is actually hard - finding the moments, cropping to 9:16,
burning the captions - is done automatically. What is left is a drag and
a paste.

The transcript comes from the censor pass's cache, so clipping a video the
uploader already handled costs no transcription at all.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Optional

# autoreel/ lives one level up, alongside auto_uploader/.
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))


@dataclass
class ClipRun:
    clips: list           # ClipResult
    caption_paths: list   # the .txt written beside each clip
    output_dir: str
    skipped_reason: str = ""


def _load_segments(cfg, source_path: str) -> Optional[list]:
    """Word-level segments for this video, from the censor pass's cache."""
    from utils.censor import _load_cached_words, words_cache_path

    base = os.path.splitext(os.path.basename(source_path))[0]
    # The censored copy is named "<base>_CENSORED_<method>.mp4", so a clip
    # made from it has to look under the ORIGINAL name to find the cache.
    for candidate in (base, base.split("_CENSORED_")[0]):
        cached = _load_cached_words(
            words_cache_path(cfg.general.censored_folder, candidate), source_path)
        if cached:
            return cached
    return None


def _journal_for(cfg):
    """A record() bound to this config's logs folder, or a no-op."""
    def write(_cfg, status: str, stage: str, name: str, detail: str = "") -> None:
        try:
            from utils.clip_log import record

            record(cfg.general.logs_folder, stage, status, name, detail)
        except Exception:
            pass
    return write


def transcribe_for_clips(cfg, source_path: str) -> Optional[list]:
    """Segments for a video that has never been through the censor pass.

    A stream the uploader handled already has its transcript cached, and
    clipping it is nearly free. An old VOD sitting in a folder does not,
    so the words have to be produced - and then cached in exactly the
    place the censor pass would have put them, so a later censor run over
    the same file costs nothing.

    The source file is only ever READ. Nothing here writes to, moves or
    deletes it.
    """
    import json

    from utils.censor import _get_transcriber, words_cache_path
    from utils.ffmpeg_tools import extract_audio

    work_dir = cfg.general.censored_folder
    os.makedirs(work_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(source_path))[0]
    words_path = words_cache_path(work_dir, base)

    speed = dict(getattr(cfg.general, "speed", {}) or {})
    audio_path = os.path.join(work_dir, f"{base}_clipaudio.wav")
    try:
        extracted = extract_audio(source_path, audio_path)
    except Exception as exc:
        print(f"[Clips] could not read the audio: {exc}")
        return None
    if not extracted:
        print("[Clips] no audio track to transcribe.")
        return None

    try:
        transcriber = _get_transcriber(cfg.general.censor_model,
                                       cfg.general.censor_device,
                                       reuse=bool(speed.get("reuse_model", True)))
        result = transcriber.transcribe(audio_path)
    except Exception as exc:
        print(f"[Clips] transcription failed: {exc}")
        return None
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass

    segments = (result or {}).get("segments") or []
    if not segments:
        return None
    try:
        with open(words_path, "w", encoding="utf-8") as f:
            json.dump({"segments": segments}, f)
    except OSError:
        pass  # a failed cache write must not cost the clips
    return segments


# Words a clip's first line often opens with that say nothing about it.
_FILLER = ("uh", "um", "like", "so", "and", "but", "okay", "ok", "yeah",
           "yo", "bro", "i mean", "you know")

# Words that leave a truncated title hanging. Cutting at 70 characters
# lands on one of these often enough that it is the single thing that
# made titles read as machine-made: "He walked in there and" is not a
# sentence a person would have typed.
_DANGLING = ("and", "but", "so", "or", "the", "a", "an", "to", "of", "in",
             "on", "at", "for", "with", "that", "this", "is", "was", "it",
             "he", "she", "they", "we", "i", "you", "my", "his", "her",
             "their", "because", "when", "if", "then", "just", "like")

# Under this, the line says nothing - "Wow!" is not a title, it is a
# noise. Falls back to the stream's own title instead.
_MIN_TITLE_CHARS = 14
_MIN_TITLE_WORDS = 3
_MAX_TITLE_CHARS = 70

# A line ending in one of these finished; anything else ran out.
_END_PUNCT = (".", "!", "?", "\u2026")


def _strip_filler(spoken: str) -> str:
    """Drop leading filler, repeatedly - speech opens "uh so like ..."."""
    stripping = True
    while stripping and spoken:
        stripping = False
        lowered = spoken.lower()
        for word in sorted(_FILLER, key=len, reverse=True):
            if lowered.startswith(word + " "):
                spoken = spoken[len(word) + 1:].lstrip(" ,.")
                stripping = True
                break
    return spoken


def _trim_to_length(spoken: str) -> str:
    """Cut to title length on a word boundary."""
    if len(spoken) <= _MAX_TITLE_CHARS:
        return spoken
    return spoken[:_MAX_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-")


def _drop_dangling(spoken: str) -> str:
    """Walk back off a line that stops mid-thought.

    Only when it does not end in real punctuation: "he actually said that
    to you." is a finished sentence and "to you" belongs there, while
    "the whole lobby started screaming at him because he" is a transcript
    that ran out - and that is what titles were reading like. It happens
    with or without truncation, because the line comes from speech and
    speech does not stop where a title should.
    """
    if spoken.endswith(_END_PUNCT):
        return spoken
    words = spoken.split()
    while len(words) > _MIN_TITLE_WORDS and \
            words[-1].strip(",.;:-!?").lower() in _DANGLING:
        words.pop()
    return " ".join(words).rstrip(" ,;:-")


def headline_for(clip, fallback: str = "") -> str:
    """A TITLE for one clip, from what is actually said in it.

    The alternative was the filename, which produced titles like
    "live 2026-08-08 19 08 clip06" - identical in shape across every
    clip, meaningless to a viewer, and worthless in search.

    What arrives here is already the best single SENTENCE in the clip
    rather than whatever segment the window happened to open on - the
    scorer picks it. This is the cleanup: leading filler goes because a
    title starting "uh so like" reads as a transcript, a trailing full
    stop goes because titles do not carry one, and a line too short to
    say anything is dropped in favour of the stream's own title rather
    than shipped as "Wow!".
    """
    spoken = _strip_filler(" ".join(
        (getattr(clip.spec, "title", "") or "").split()))
    if not spoken:
        return fallback

    spoken = _drop_dangling(_trim_to_length(spoken))
    # A title does not end in a full stop; ? and ! are doing work, so
    # they stay.
    if not spoken.endswith(("?", "!")):
        spoken = spoken.rstrip(".\u2026").strip(" ,;:-")
    if len(spoken) < _MIN_TITLE_CHARS or len(spoken.split()) < _MIN_TITLE_WORDS:
        return fallback
    return spoken[0].upper() + spoken[1:]


def caption_for(clip, title: str, tags: list) -> str:
    """The caption to paste with a clip.

    Built from what the clip actually contains rather than a fixed
    template: the transcript line is what makes one clip's caption
    different from another's, and identical captions across posts are
    themselves a spam signal.
    """
    spoken = (clip.spec.title or "").strip()
    lines = []
    if spoken:
        # One line of what is actually said, as the hook.
        hook = spoken if len(spoken) <= 120 else spoken[:119].rsplit(" ", 1)[0] + "…"
        lines.append(hook)
    lines.append(f"\nFrom: {title}")
    lines.append("Full stream on YouTube - link in bio")
    if tags:
        lines.append("\n" + " ".join(f"#{t}" for t in tags[:12]))
    return "\n".join(lines)


def make_clips(cfg, source_path: str, title: str,
               count: Optional[int] = None,
               notify: bool = True,
               transcribe_if_needed: bool = False) -> ClipRun:
    """Render clips for one video and write each caption beside it."""
    from autoreel.clip_maker import ClipError, ClipMaker

    clips_cfg = dict(getattr(cfg, "clips", {}) or {})
    output_dir = clips_cfg.get("output_folder") or os.path.join(
        cfg.project_root, "clips")

    if not os.path.isfile(source_path):
        return ClipRun([], [], output_dir, f"no such file: {source_path}")

    _journal = _journal_for(cfg)
    segments = _load_segments(cfg, source_path)
    if not segments and transcribe_if_needed:
        print(f"[Clips] No transcript for {os.path.basename(source_path)} - "
              f"transcribing it now (model={cfg.general.censor_model}). This is "
              "the slow part; it is cached afterwards.")
        segments = transcribe_for_clips(cfg, source_path)
    if not segments:
        reason = ("no transcript - the censor pass has not run on this video "
                  "(use --clips-from to transcribe it)")
        _journal(cfg, "FAIL", "cut", title or os.path.basename(source_path),
                 reason)
        return ClipRun([], [], output_dir, reason)

    speed = dict(getattr(cfg.general, "speed", {}) or {})
    maker = ClipMaker(
        output_dir=output_dir,
        config={"clips": clips_cfg},
        content_kind=clips_cfg.get("content_kind", "gameplay"),
        captions=bool(clips_cfg.get("burn_captions", True)),
        # Word-by-word highlighting rather than a static white slab -
        # see autoreel/captions for why that is the difference between
        # captions people read and captions people turn off.
        caption_style=str(clips_cfg.get("caption_style", "word")),
        caption_uppercase=bool(clips_cfg.get("caption_uppercase", True)),
        count=int(count or clips_cfg.get("count", 3)),
        min_seconds=float(clips_cfg.get("min_seconds", 15)),
        max_seconds=float(clips_cfg.get("max_seconds", 60)),
        encoder=_encoder_for(speed),
        preset=speed.get("encode_preset", "fast"),
        # The waiting screen at the top of a stream and the goodbyes at
        # the end both transcribe as people talking, so both score - and
        # neither is a clip anyone watches.
        skip_intro_seconds=float(clips_cfg.get("skip_intro_seconds", 120)),
        skip_outro_seconds=float(clips_cfg.get("skip_outro_seconds", 60)),
        # Without this, one genuinely good minute produces every clip:
        # overlapping windows over the same moment all score highly.
        min_gap_seconds=float(clips_cfg.get("min_gap_seconds", 90)),
        # A model reads the shortlist and says which of them a person
        # would post - the one thing keyword scoring cannot do. Costs
        # nothing without a key: it falls back to the scores silently.
        # Laughter and shouting are invisible in a transcript; this is
        # the signal that catches them.
        use_audio_energy=bool(clips_cfg.get("use_audio_energy", True)),
        llm_rank=bool(clips_cfg.get("llm_rank", True)),
        llm_provider=str(clips_cfg.get("llm_provider", "")),
        llm_model=str(clips_cfg.get("llm_model", "")),
    )

    base = os.path.splitext(os.path.basename(source_path))[0]
    try:
        results = maker.make(source_path, segments, basename=base)
    except ClipError as exc:
        _journal(cfg, "FAIL", "cut", title or base, str(exc))
        return ClipRun([], [], output_dir, str(exc))
    _journal(cfg, "ok" if results else "FAIL", "cut", title or base,
             f"{len(results)} clips" if results
             else "no clip-worthy moments found")

    tags = list(getattr(cfg.youtube, "tags", []) or [])
    caption_paths = []
    for clip in results:
        path = os.path.splitext(clip.path)[0] + "_caption.txt"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(caption_for(clip, title, tags))
            caption_paths.append(path)
        except OSError as exc:
            print(f"[Clips] could not write a caption: {exc}")

    if notify and results:
        _notify(results, output_dir)
    return ClipRun(results, caption_paths, output_dir)


def _encoder_for(speed: dict) -> str:
    """NVENC when it genuinely works, else libx264."""
    from utils.ffmpeg_tools import pick_video_encoder

    try:
        return pick_video_encoder(speed.get("hardware_encode", "auto"))
    except Exception:
        return "libx264"


def _notify(results: list, output_dir: str) -> None:
    """Ping Discord so the clips get posted while they're still topical."""
    from utils.social_promoter import _post_discord

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook:
        return
    lines = [f"🎞️ **{len(results)} clip(s) ready to post** — {output_dir}"]
    for clip in results:
        lines.append(f"• `{os.path.basename(clip.path)}` "
                     f"({clip.spec.duration:.0f}s) — caption saved beside it")
    lines.append("\nUpload from the Instagram app: better reach than the API, "
                 "and no hosting needed.")
    try:
        _post_discord(webhook, "\n".join(lines))
    except Exception as exc:
        print(f"[Clips] could not ping Discord: {exc}")


def print_run(run: ClipRun) -> None:
    if run.skipped_reason:
        print(f"[Clips] Nothing rendered - {run.skipped_reason}")
        return
    if not run.clips:
        print("[Clips] No clip-worthy moments found in this video.")
        return
    print(f"\n[Clips] {len(run.clips)} clip(s) -> {run.output_dir}")
    for clip in run.clips:
        print(f"  {os.path.basename(clip.path)}  "
              f"{clip.spec.duration:.0f}s  "
              f"(from {clip.spec.start / 60:.0f}m{clip.spec.start % 60:02.0f}s, "
              f"{clip.strategy} crop"
              f"{', captions burned in' if clip.captioned else ''})")
    print("\n  Each clip has a *_caption.txt beside it. Post from the "
          "Instagram/TikTok app - no hosting, no API, better reach.")
