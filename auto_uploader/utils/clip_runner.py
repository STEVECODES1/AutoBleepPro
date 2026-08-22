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
import time
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


def _is_placeholder(title: str, default_title: str) -> bool:
    """True when this title names no particular stream.

    Local rather than imported from main: main imports this module, and
    the placeholder rule is three lines. The generated title wraps the
    default - `"Gaming Stream" 8/14/26 Stackswopo Stream` - so the test
    is containment, not equality.
    """
    text = " ".join(str(title or "").lower().split())
    fallback = " ".join(str(default_title or "").lower().split())
    return bool(fallback) and (not text or fallback in text)


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


def source_beside(video_path: str) -> str:
    """The stream URL a recording was made from, or "".

    Written by the recorder. Also looked for under the name with the
    _CENSORED_ suffix taken off, because a clip cut from the censored
    copy is the same stream.
    """
    stem = os.path.splitext(video_path or "")[0]
    for candidate in (stem, stem.split("_CENSORED_")[0]):
        try:
            with open(candidate + ".source.txt", "r",
                      encoding="utf-8") as handle:
                found = handle.read().strip()
        except OSError:
            continue
        if found.startswith(("http://", "https://")):
            return found
    return ""


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
        started = time.time()
        result = transcriber.transcribe(audio_path)
        # Say which chip actually did it. censor_device is a REQUEST: if
        # cuDNN is missing or the driver is old, faster-whisper falls
        # back to CPU and the only difference is that a VOD takes hours
        # instead of minutes. Without this line, a silent fallback looks
        # exactly like a slow computer.
        device = getattr(transcriber, "_resolved_device", "") or "?"
        precision = getattr(transcriber, "_resolved_compute", "") or "default"
        print(f"[Clips] Transcribed on {device.upper()} ({precision}) in "
              f"{time.time() - started:.0f}s.")
        if device != "cuda":
            print("        That was the CPU. For the GPU:  pip install "
                  "nvidia-cublas-cu12 nvidia-cudnn-cu12")
            print("        Check it with:  python main.py --gpu-check")
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
    # No "link in bio" here either. There is no link in the bio for these
    # clips, the channels are named where it matters, and it is the most
    # recognisable mark of an automated repost account - see
    # clip_queue._DEAD_TEMPLATE_LINES, which kills the same slogan on the
    # other side.
    if tags:
        lines.append("\n" + " ".join(f"#{t}" for t in tags[:12]))
    return "\n".join(lines)


def make_clips(cfg, source_path: str, title: str,
               count: Optional[int] = None,
               notify: bool = True,
               transcribe_if_needed: bool = False,
               source_url: str = "") -> ClipRun:
    """Render clips for one video and write each caption beside it."""
    from autoreel.clip_maker import ClipError, ClipMaker

    clips_cfg = dict(getattr(cfg, "clips", {}) or {})
    # What this video is, so clips.profile can be "auto" and the framing
    # follows the CONTENT instead of one global setting. Without this a
    # GTA stream went through the Monkey rectangle - the left 46% of a
    # gameplay frame, cropped for a call window that was not there.
    # The FILENAME joins the title here, and is not merely a fallback for
    # when the title is empty. A VOD whose real title could not be read
    # gets the configured placeholder - "Gaming Stream" - which is a
    # perfectly truthy string that says nothing. Framing was then decided
    # from it while "monkey_n_gamble_howl [v70rbpc].mp4" sat unread in
    # the same variable, and every such clip came out `whole`.
    stem = os.path.splitext(os.path.basename(source_path))[0]
    default_title = str(getattr(cfg.general, "default_title", "") or "")
    named = title and not _is_placeholder(title, default_title)
    clips_cfg["content_title"] = f"{title} {stem}" if not named else title
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
    from autoreel.crop_strategy import resolve_crop_strategy, resolve_profile

    # Remembered before the auto branch overwrites it: by the time
    # ClipMaker is built, clips_cfg["profile"] holds the resolved name and
    # there is no way left to tell whether a person chose it.
    asked_for_auto = str(clips_cfg.get("profile", "")).strip().lower() in (
        "", "auto")
    if str(clips_cfg.get("profile", "")).strip().lower() == "auto":
        from autoreel.crop_strategy import profile_for_title

        chosen = profile_for_title(clips_cfg["content_title"], fallback="")
        if chosen:
            source_of_choice = "the title"
        else:
            # "yoo_howl" and "culture" are both real recordings here and
            # neither says which kind it is. Rather than fall straight to
            # `whole` and waste most of the frame, look at the picture.
            from autoreel.content_kind import profile_for

            chosen = profile_for(source_path, title="", fallback="whole")
            source_of_choice = ("the picture" if chosen != "whole"
                                else "nothing - keeping the whole frame")
        clips_cfg["profile"] = chosen
        print(f"[Clips] Framing chosen from {source_of_choice}: {chosen} "
              f"({resolve_crop_strategy({'clips': clips_cfg})} crop)")

    maker = ClipMaker(
        output_dir=output_dir,
        config={"clips": clips_cfg},
        content_kind=clips_cfg.get("content_kind", "gameplay"),
        captions=bool(clips_cfg.get("burn_captions", True)),
        # Word-by-word highlighting rather than a static white slab -
        # see autoreel/captions for why that is the difference between
        # captions people read and captions people turn off.
        caption_style=str(clips_cfg.get("caption_style", "word")),
        burn_hook=bool(clips_cfg.get("burn_hook", False)),
        # "auto" already means "work it out from the video". Working it
        # out per clip is the same instruction, followed properly: a
        # profile named explicitly in config is a decision someone made
        # and is left alone.
        per_clip_framing=asked_for_auto,
        pick_thumbnails=bool(clips_cfg.get("pick_thumbnails", False)),
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
        # Measure where the people are per clip instead of trusting a
        # rectangle typed into config.json months ago. Only the region
        # strategy consults it, so gameplay never meets a face detector.
        find_faces=bool(clips_cfg.get("find_faces", True)),
        # Frames alongside the transcript. Free on the same Gemini tier
        # the text pass already uses.
        use_vision=bool(clips_cfg.get("use_vision", True)),
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

    # Chat replay, when the video came from a URL that serves it. The
    # log is downloaded, counted per second and deleted before this
    # returns - see autoreel/chat_energy. Nothing textual is kept.
    chat: list = []
    # A recording notes the stream it came from in a sidecar - see
    # record_stream.remember_source. Without this the chat step only ever
    # ran when clipping straight from a URL, which is not how any of this
    # channel's clips are made, so the strongest signal available was
    # never once read.
    if not source_url:
        source_url = source_beside(source_path)
    heat: list = []
    if source_url and bool(clips_cfg.get("use_replay_heat", True)):
        from autoreel.replay_heat import heat_for_url

        print("[Clips] Checking what people went back and watched again...")
        heat = heat_for_url(source_url)
        print(f"[Clips] Most-replayed curve found ({len(heat)}s of it)."
              if heat else
              "[Clips] No most-replayed curve on this one - too new or too "
              "few views for YouTube to publish it yet.")

    if source_url and bool(clips_cfg.get("use_chat", True)):
        from autoreel.chat_energy import rates_for_url

        print("[Clips] Reading chat for where it went off (not stored)...")
        chat = rates_for_url(source_url)
        print(f"[Clips] {sum(chat)} messages counted, log deleted." if chat
              else "[Clips] No chat replay on this one - the other signals "
                   "decide.")

    base = os.path.splitext(os.path.basename(source_path))[0]
    try:
        results = maker.make(source_path, segments, basename=base, chat=chat,
                             heat=heat)
    except ClipError as exc:
        _journal(cfg, "FAIL", "cut", title or base, str(exc))
        return ClipRun([], [], output_dir, str(exc))
    _journal(cfg, "ok" if results else "FAIL", "cut", title or base,
             f"{len(results)} clips" if results
             else "no clip-worthy moments found")

    tags = list(getattr(cfg.youtube, "tags", []) or [])
    caption_paths = []
    for clip in results:
        stem = os.path.splitext(clip.path)[0]
        path = stem + "_caption.txt"
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(caption_for(clip, title, tags))
            caption_paths.append(path)
        except OSError as exc:
            print(f"[Clips] could not write a caption: {exc}")
        # WHAT this clip is, written down while it is still known.
        #
        # By the time the clip posts, all that is left is its filename -
        # and "Stackswopo Love Yall - Clip 02" does not say it is Monkey
        # app footage, so every clip went out with only generic tags.
        # The stream's own title and the framing profile both say exactly
        # what it is, and both are in hand right here.
        try:
            with open(stem + "_subject.txt", "w", encoding="utf-8") as f:
                f.write(f"{clips_cfg.get('content_title', '')} "
                        f"{clips_cfg.get('profile', '')}".strip())
        except OSError:
            # Tags are a nice-to-have; losing them must not cost the clip.
            pass
        # The spoken line ON ITS OWN, in its own file.
        #
        # _caption.txt is a whole caption - hook, "From: <stream>", tags -
        # and the poster only ever wanted its first line. Reading a
        # headline out of a formatted document meant a clip with no
        # per-clip title handed back "From: Stackswopo Love Yall", or
        # nothing, and the caption fell through to the filename. One
        # value per file cannot go wrong that way.
        line = (clip.spec.title or "").strip()
        if line:
            try:
                with open(stem + "_line.txt", "w", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass

    # What was cut and why, so a later run can be told how these did.
    # Wrapped in nothing on purpose - remember_run swallows its own
    # errors, because a notebook that cannot be written must never cost
    # the user clips that are already rendered.
    from autoreel.memory import remember_run

    remember_run(
        cfg, title or base, results,
        profile=str(clips_cfg.get("profile", "")),
        picked_by=("vision" if clips_cfg.get("use_vision")
                   else "model" if clips_cfg.get("use_llm") else "scorer"),
        total_seconds=float(getattr(segments[-1], "end", 0.0)
                            if segments and not isinstance(segments[-1], dict)
                            else (segments[-1].get("end", 0.0) if segments else 0.0)),
    )

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
    # WHO named them. The model's titles read like "Gumball ass
    # animations"; the scorer's read like "Doing amazing world of gumball
    # animations". Without this line a run where the model quietly failed
    # looks exactly like one where it worked and wrote something flat -
    # and the only symptom is titles that read slightly worse than the
    # ones on the channel from last week.
    by = {getattr(c.spec, "titled_by", "scorer") for c in run.clips}
    if by == {"model"}:
        print("\n  Titles written by the model.")
    elif "model" not in by:
        print("\n  Titles picked by the SCORER, not the model - the best "
              "sentence in each clip by length and punctuation. Check the "
              "key with --check-llm; the model's titles read better.")
