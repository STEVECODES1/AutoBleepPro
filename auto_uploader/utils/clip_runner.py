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
               notify: bool = True) -> ClipRun:
    """Render clips for one video and write each caption beside it."""
    from autoreel.clip_maker import ClipError, ClipMaker

    clips_cfg = dict(getattr(cfg, "clips", {}) or {})
    output_dir = clips_cfg.get("output_folder") or os.path.join(
        cfg.project_root, "clips")

    if not os.path.isfile(source_path):
        return ClipRun([], [], output_dir, f"no such file: {source_path}")

    segments = _load_segments(cfg, source_path)
    if not segments:
        return ClipRun(
            [], [], output_dir,
            "no cached transcript for this video - run the censor pass first "
            "(a normal upload does it), or the words cache has been cleaned up")

    speed = dict(getattr(cfg.general, "speed", {}) or {})
    maker = ClipMaker(
        output_dir=output_dir,
        config={"clips": clips_cfg},
        content_kind=clips_cfg.get("content_kind", "gameplay"),
        captions=bool(clips_cfg.get("burn_captions", True)),
        count=int(count or clips_cfg.get("count", 3)),
        min_seconds=float(clips_cfg.get("min_seconds", 15)),
        max_seconds=float(clips_cfg.get("max_seconds", 60)),
        encoder=_encoder_for(speed),
        preset=speed.get("encode_preset", "fast"),
    )

    base = os.path.splitext(os.path.basename(source_path))[0]
    try:
        results = maker.make(source_path, segments, basename=base)
    except ClipError as exc:
        return ClipRun([], [], output_dir, str(exc))

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
