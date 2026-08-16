"""
Auto-Upload System for Stackswopo streams -> YouTube + Rumble.

Usage:
    python main.py --watch                       # watch watch_folder/, upload new videos as they arrive
    python main.py --watch "D:\\Videos"           # ...or watch some other folder, no config edit needed
    python main.py --dry-run                      # same as --watch but previews only, uploads nothing
    python main.py --file "video.mp4" --title "My Stream"   # upload one specific file now
    python main.py --batch                         # process every video already sitting in watch_folder/
    python main.py --batch "D:\videos stizz"      # ...or in some other folder, just this once
    python main.py --test-config                   # validate config.json/.env without uploading anything
"""

import os

# BEFORE anything imports mediapipe. These are read by the C++ logging
# layer when the shared library loads, not when Python imports it, so
# setting them next to the import was too late and the "Feedback manager
# requires a model with a single signature" block kept printing - six
# lines per clip in the window that also carries the real failures.
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("ABSL_MIN_LOG_LEVEL", "2")

import argparse
import json
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

# Bump when shipping user-visible changes, so --test-config can prove
# which build is actually running (stale extracts have silently caused
# several confusing "the fix did nothing" runs).
BUILD = "2026-08-16.30 measure whether the captions land on the words"

# How often --watch checks whether a deferred clip's wait is up. A minute
# is fine: the waits themselves are 25 to 80 minutes, so the resolution
# that matters is "well under the shortest spacing", not "immediate".
CLIP_DRAIN_SECONDS = 60

# How often --watch looks for a VOD nobody has clipped yet. Slow on
# purpose: the answer is almost always "none", and the work it triggers
# is measured in minutes.
AUTOCLIP_SECONDS = 300

# Size observations for files still being copied in, kept between passes.
_AUTOCLIP_SEEN: dict = {}

# Notices that are true for the whole run and would otherwise print on
# every pass. --batch followed by --watch is one process saying the same
# paragraph twice before it has done anything, which trains you to skim
# past the window that also carries the real failures.
_SAID: set = set()


def _say_once(key: str, message: str) -> None:
    if key in _SAID:
        return
    _SAID.add(key)
    print(message)

from utils.censor import censor_video
from utils.ffmpeg_tools import StageTimer, media_duration

# Anything at or under this many seconds is a clip rather than a stream.
# Twitch clips top out around 90 seconds; the margin covers a highlight
# exported by hand. A five-hour VOD is never close to this.
CLIP_MAX_SECONDS = 180
from utils.cleanup import (
    SOURCE_DELETE,
    SOURCE_KEEP,
    cleanup_after_upload,
    prune_uploaded_folder,
    resolve_source_action,
)
from utils.config import load_config, validate_config
from utils.duplicate_checker import DuplicateChecker, hash_file
from utils.file_watcher import FolderWatcher, is_intermediate_download, is_sidecar_file
from utils.logging_setup import setup_logger, setup_publisher_logging
from utils.notifier import notify
from utils.retry import retry_with_backoff
from utils.rumble_checker import fetch_rumble_videos
from utils.self_healing import run_health_check
from utils.rumble_uploader import RumbleUploader
from utils.templating import (
    build_description,
    build_title,
    extract_date_from_filename,
    extract_title_from_filename,
    format_date,
)
from utils.youtube_checker import (
    fetch_existing_videos,
    find_existing_video,
    find_same_date_videos,
)
from utils.youtube_uploader import YouTubeUploader


def _suggest_paths(cfg, basename: str, limit: int = 5) -> list:
    """Where a missing file actually is, if it's somewhere we know about.

    "File not found" on its own isn't actionable when the tool itself
    moves files between watch_folder and uploaded/ as part of normal
    operation - this says where it went.
    """
    if not basename:
        return []
    seen, found = set(), []
    for folder in (cfg.general.watch_folder, cfg.general.uploaded_folder,
                   cfg.general.censored_folder, os.getcwd()):
        folder = os.path.abspath(folder or "")
        if not folder or folder in seen or not os.path.isdir(folder):
            continue
        seen.add(folder)
        candidate = os.path.join(folder, basename)
        if os.path.isfile(candidate):
            found.append(candidate)
        if len(found) >= limit:
            break
    return found


def _words_in(text: str) -> list:
    """The words worth matching a filename on.

    Short words are dropped because they are what every recording
    filename already shares - "all", "the", the date, "Full Live
    Stream". Matching on those would make every VOD look like a hit.
    """
    parts = re.split(r"[^0-9a-z]+", (text or "").lower())
    return [p for p in parts if len(p) >= 3]


def _find_video(cfg, wanted: str, limit: int = 6) -> list:
    """Resolve a path OR a remembered title to real files on disk.

    A stream is known by its title long before anyone knows which folder
    it is sitting in - the tool itself moves it from watch_folder to
    uploaded/ - so asking for the full path back is asking the user to
    go find what the tool already knows.
    """
    if not wanted:
        return []
    if os.path.isfile(wanted):
        return [wanted]

    exact = _suggest_paths(cfg, os.path.basename(wanted), limit)
    if exact:
        return exact

    words = _words_in(wanted)
    if not words:
        return []
    seen, found = set(), []
    for folder in (cfg.general.watch_folder, cfg.general.uploaded_folder,
                   cfg.general.censored_folder, os.getcwd()):
        folder = os.path.abspath(folder or "")
        if not folder or folder in seen or not os.path.isdir(folder):
            continue
        seen.add(folder)
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            if os.path.splitext(name)[1].lower() not in cfg.general.supported_formats:
                continue
            haystack = _words_in(name)
            if all(w in haystack for w in words):
                found.append(path)
                if len(found) >= limit:
                    return found
    return found


def _no_target(parser, args) -> int:
    """Nothing to upload was named. Say which word is missing.

    --only, --mode and friends say HOW to upload, never WHAT. On their
    own they used to fall through to the full help, which buries the one
    missing word in sixty lines of options.
    """
    modifiers = [name for name, value in (
        ("--only", args.only), ("--mode", args.mode),
        ("--title", args.title), ("--keep-source", args.keep_source),
        ("--trim-silence", getattr(args, "trim_silence", False)),
    ) if value]
    if not modifiers:
        parser.print_help()
        return 0

    only = f" --only {args.only}" if args.only else ""
    print(f"[ERROR] {', '.join(modifiers)} says how to upload, not what.")
    print("        Add one of these:")
    print(f'          --file "path\\to\\video.ts"{only}'.ljust(52)
          + "one video")
    print(f"          --batch{only}".ljust(52)
          + "every video in the watch folder")
    print(f"          --watch{only}".ljust(52)
          + "keep running and upload what arrives")
    return 1


def _fill_missing(live: dict, example: dict, path: str = "") -> list:
    """Add keys the example has and `live` does not. Returns their names.

    Values already present are NEVER touched, at any depth. The whole
    point of an untracked config.json is that the settings in it are the
    operator's; this only carries across settings that did not exist
    when they last looked.
    """
    added = []
    for key, value in (example or {}).items():
        where = f"{path}.{key}" if path else key
        if key not in live:
            live[key] = value
            added.append(where)
        elif isinstance(value, dict) and isinstance(live.get(key), dict):
            added += _fill_missing(live[key], value, where)
    return added


def merge_new_settings(config_file: str, example_file: str) -> list:
    """Carry settings added since this config.json was written.

    Untracking config.json stopped `git pull` colliding with a switch the
    operator flipped. It also stopped new settings ever reaching them: a
    config restored from a backup had no clips.auto_clip_folder, so the
    auto-clip pass read "" and did nothing, silently, on a build that
    supported it.

    A setting that exists in the example and not here is a feature that
    shipped after this file was last written, and copying it across is
    the only thing that makes an untracked config safe to keep.
    """
    if not os.path.isfile(config_file) or not os.path.isfile(example_file):
        return []
    try:
        with open(config_file, "r", encoding="utf-8") as handle:
            live = json.load(handle)
        with open(example_file, "r", encoding="utf-8") as handle:
            example = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(live, dict) or not isinstance(example, dict):
        return []

    added = _fill_missing(live, example)
    if not added:
        return []
    try:
        temporary = config_file + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(live, handle, indent=2, ensure_ascii=False)
        os.replace(temporary, config_file)
    except OSError as exc:
        return [f"could not save new settings: {exc}"]

    shown = ", ".join(a for a in added if not a.split(".")[-1].startswith("_"))
    if not shown:
        return []
    return [f"Added {len(added)} new setting(s) from this version: {shown}",
            "Your existing settings were not changed."]


def _retire_duplicate(cfg, video_path: str) -> None:
    """Get an already-uploaded video out of the watch folder.

    Honours cleanup.source_video rather than always deleting. `move` is
    the shipped default and the reversible one: the video is published,
    but a local copy is the only thing that can re-cut a clip if a later
    pass frames one badly, and deleting it is not undoable.
    """
    # SOURCE_DELETE, SOURCE_KEEP and resolve_source_action are imported
    # at module level. Re-importing them here would bind them as locals
    # for the WHOLE function - the shadowing bug the AST test guards,
    # which it caught on this very function.
    if not os.path.isfile(video_path):
        return
    watch = os.path.abspath(cfg.general.watch_folder or "")
    if os.path.dirname(os.path.abspath(video_path)) != watch:
        # Already somewhere else - uploaded/, a library folder pointed at
        # by --batch. Nothing to tidy, and moving a file out of a folder
        # the user named is not this function's business.
        return

    name = os.path.basename(video_path)
    action = resolve_source_action(cfg)
    if action == SOURCE_KEEP:
        print(f"[Cleanup] {name} stays in the watch folder "
              f"(cleanup.source_video is 'keep').")
        return
    if action == SOURCE_DELETE:
        try:
            size_mb = os.path.getsize(video_path) / (1024 ** 2)
            os.remove(video_path)
            print(f"[Cleanup] Already uploaded - deleted {name} "
                  f"({size_mb:.0f} MB).")
        except OSError as exc:
            print(f"[WARN] Could not delete {name}: {exc}")
        return
    destination = os.path.join(cfg.general.uploaded_folder, name)
    try:
        os.makedirs(cfg.general.uploaded_folder, exist_ok=True)
        if os.path.exists(destination):
            # The same video is already filed. Two copies of a published
            # VOD is the thing being cleaned up, so drop this one.
            os.remove(video_path)
            print(f"[Cleanup] Already uploaded and already filed - "
                  f"removed the copy in the watch folder.")
            return
        shutil.move(video_path, destination)
        print(f"[Cleanup] Already uploaded - moved {name} to "
              f"{os.path.basename(cfg.general.uploaded_folder)}/.")
    except OSError as exc:
        print(f"[WARN] Could not move {name}: {exc}")


def _clip_already_uploaded(cfg, video_path: str, is_clip: bool) -> int:
    """Cut clips from a VOD that was uploaded on an earlier run.

    The dedup check answers "has this been UPLOADED", and the answer was
    being used to skip everything - including clipping, which had never
    happened for that file. Clips are the reason to keep a stream around
    after it is published, so being already-uploaded is precisely the
    state where clipping is still owed.

    Clips are never clipped. A clip of a clip is not a thing.
    """
    if is_clip:
        return 0
    clips_cfg = dict(getattr(cfg, "clips", {}) or {})
    if not clips_cfg.get("auto_from_streams", False):
        return 0

    from utils.clip_watch import remember, was_clipped

    # The record lives with the logs; the key is the video's own name and
    # size, so a VOD that has moved to uploaded/ since is still known.
    archive = cfg.general.logs_folder
    source = video_path
    if not os.path.isfile(source):
        moved = _suggest_paths(cfg, os.path.basename(video_path))
        if not moved:
            return 0
        source = moved[0]
    if was_clipped(archive, source):
        return 0

    print(f"[Clips] Already uploaded, but never clipped. Cutting clips from "
          f"{os.path.basename(source)} now.")
    from utils.clip_runner import make_clips, print_run

    title = get_stream_title(source, "", cfg, allow_prompt=False)
    try:
        run = make_clips(cfg, source, title,
                         count=clips_cfg.get("count") or None,
                         notify=False, transcribe_if_needed=True)
    except Exception as exc:
        print(f"[Clips] could not clip {os.path.basename(source)}: {exc}")
        return 0
    if run.skipped_reason:
        print(f"[Clips] {run.skipped_reason}")
        return 0
    print_run(run)
    delivered = _deliver_clips(run, cfg)
    remember(archive, source, delivered)
    return delivered


def _autoclip_one(cfg) -> int:
    """Cut clips from ONE unclipped VOD in the auto-clip folder.

    One per pass, deliberately. Transcribing a two-hour VOD took 663
    seconds on this machine, and the loop calling this is also what
    posts the queue and uploads finished videos - ten new VODs must be
    ten passes, not one long freeze during which nothing else happens.
    """
    from utils.clip_watch import (MAX_ATTEMPTS as MAX_CLIP_ATTEMPTS,
                                  attempts_for, next_vod, remember)

    clips_cfg = dict(getattr(cfg, "clips", {}) or {})
    folder = str(clips_cfg.get("auto_clip_folder", "") or "").strip()
    if not folder:
        return 0
    if not os.path.isabs(folder):
        folder = os.path.join(cfg.project_root, folder)
    if not os.path.isdir(folder):
        return 0

    # The size observations live across passes, so a file that is still
    # being copied in is seen growing rather than judged by an mtime the
    # copy brought with it.
    source = next_vod(folder, cfg.general.supported_formats,
                      seen=_AUTOCLIP_SEEN)
    if not source:
        return 0

    name = os.path.basename(source)
    print(f"\n[Clips] New VOD in {os.path.basename(folder)}: {name}")
    print("[Clips] Cutting clips from it now. This transcribes the whole "
          "video, so it is the slow part; posting carries on around it.")

    from utils.clip_runner import make_clips, print_run

    title = get_stream_title(source, "", cfg, allow_prompt=False)
    try:
        run = make_clips(cfg, source, title,
                         count=clips_cfg.get("auto_clip_count") or None,
                         notify=False, transcribe_if_needed=True)
    except Exception as exc:
        # Failed, not answered. An HTTP 503 from the model is not a
        # verdict about the video, so this comes round again - bounded,
        # because a genuinely broken file must stop costing a
        # transcription pass every five minutes.
        tries = attempts_for(folder, source) + 1
        remember(folder, source, 0, failed=True, attempts=tries)
        print(f"[Clips] {name} failed (attempt {tries}/{MAX_CLIP_ATTEMPTS}): {exc}")
        if tries >= MAX_CLIP_ATTEMPTS:
            print(f"[Clips] Giving up on {name}. Cut it by hand with "
                  f"--clips-from if you want it.")
        return 0

    if run.skipped_reason:
        tries = attempts_for(folder, source) + 1
        remember(folder, source, 0, failed=True, attempts=tries)
        print(f"[Clips] {name}: {run.skipped_reason} "
              f"(attempt {tries}/{MAX_CLIP_ATTEMPTS})")
        return 0

    print_run(run)
    delivered = _deliver_clips(run, cfg)
    # A successful run with no clips IS an answer - that VOD had nothing
    # clip-worthy in it - so it is not retried.
    remember(folder, source, delivered)
    print(f"[Clips] {delivered} clip(s) from {name} delivered to "
          f"{cfg.general.watch_folder}.")
    return delivered


def _deliver_clips(run, cfg) -> int:
    """Move rendered clips into the watch folder. Returns how many.

    Delivered rather than posted directly: the watch folder is where the
    queue, the one-at-a-time worker and the per-platform spacing already
    live, so a clip that arrives there is handled exactly like a Twitch
    clip and needs no second code path.
    """
    moved = 0
    os.makedirs(cfg.general.watch_folder, exist_ok=True)
    for clip in getattr(run, "clips", []) or []:
        source = getattr(clip, "path", "")
        if not source or not os.path.isfile(source):
            continue
        try:
            target = os.path.join(cfg.general.watch_folder,
                                  os.path.basename(source))
            shutil.move(source, target)
            # A .txt beside the video is how a title reaches the uploader
            # without a prompt - get_stream_title reads it first. Without
            # one the title came from the FILENAME, which produced
            # "live 2026-08-08 19 08 clip06": identical in shape across
            # every clip and meaningless to a viewer.
            from utils.clip_runner import headline_for
            headline = headline_for(clip)
            if headline:
                with open(os.path.splitext(target)[0] + ".txt", "w",
                          encoding="utf-8") as f:
                    f.write(headline + "\n")
            moved += 1
        except OSError as exc:
            print(f"[Clips] could not deliver {os.path.basename(source)}: {exc}")
    return moved


def _report_clip_brain(cfg) -> None:
    """Say out loud which pass will choose the clips.

    Without this the difference between "a model read the stream" and
    "keyword scoring guessed" is invisible until the clips come out, and
    a missing API key looks exactly like a working one.
    """
    clips = cfg.clips or {}
    if not clips.get("llm_rank", True):
        print("  Clip picking         : scorer only (clips.llm_rank is off)")
        return
    from autoreel.llm_highlights import available

    provider, _ = available(str(clips.get("llm_provider", "")))
    if provider:
        print(f"  Clip picking         : {provider} reads the shortlist and "
              "picks + titles the clips")
    else:
        print("  Clip picking         : scorer only - no GEMINI_API_KEY or "
              "OPENAI_API_KEY in .env")
        print("                         (Gemini's free tier covers this: "
              "aistudio.google.com/apikey, then")
        print("                          python main.py --set-env "
              "GEMINI_API_KEY=yourkey)")


DOWNLOADED_VODS = "downloaded_vods"


def tidy_downloaded_vods(paths: list, folder: str, project_root: str) -> str:
    """Delete VODs this tool downloaded, once they have been clipped.

    Left alone, a daily run is three VODs a day at three to five
    gigabytes each and the drive is full inside a week. The download
    archive still remembers them, so nothing is ever fetched twice.

    THE FOLDER IS CHECKED HERE, not by the caller. --clips-from is also
    how a library folder gets clipped - "D:\\videos stizz" - and the
    promise made about those is that they are only ever read. A caller
    that forgets to check would break that promise silently and there
    would be no way to get the files back, so the refusal lives next to
    the delete rather than one level up from it.
    """
    expected = os.path.abspath(os.path.join(project_root, DOWNLOADED_VODS))
    if os.path.abspath(folder) != expected:
        return ("[Clips] Not deleting anything: that folder is yours, not "
                "this tool's download folder. Only " + expected + " is "
                "tidied.")

    freed, removed = 0, 0
    problems = []
    for path in paths:
        # A file outside the folder cannot be reached by way of a name
        # in it; checked per file so a crafted name cannot walk out.
        if os.path.dirname(os.path.abspath(path)) != expected:
            continue
        try:
            size = os.path.getsize(path)
            os.remove(path)
        except OSError as exc:
            problems.append(f"{os.path.basename(path)}: {exc}")
            continue
        freed += size
        removed += 1

    told = (f"[Clips] Removed {removed} clipped VOD(s), "
            f"{freed / (1024 ** 3):.1f} GB. The archive still remembers "
            f"them, so they will not be downloaded again.")
    for problem in problems:
        told += f"\n[Clips] Could not remove {problem}"
    return told


# Recorded when Rumble finished but the video could not be found on the
# channel afterwards. Deliberately starts with FAILED: so the duplicate
# store treats it as NOT uploaded and a re-run tries again - the opposite
# of the old behaviour, which believed the upload and skipped forever.
RUMBLE_UNCONFIRMED = ("FAILED: Rumble finished but the video is not on the "
                      "channel - it did not publish. Run again to retry.")


def _is_link(value: str) -> bool:
    """A real URL, not a status marker.

    Deliberately local rather than imported: --clips-from imports is_url
    from channel_vods INSIDE a function, and a module-level name of the
    same spelling would become a local for that whole function and break
    it. See test_no_function_shadows_a_module_level_import.
    """
    return str(value or "").strip().lower().startswith(("http://", "https://"))


def _confirm_on_rumble(url: str, title: str, cfg) -> str:
    """Turn an unconfirmed Rumble upload into a verified answer.

    Returns the real URL when the video is on the channel, and a FAILED:
    marker when it is not. A confirmed URL is passed straight through.

    The cost of being wrong is asymmetric and that decides the default:
    a duplicate can be deleted in ten seconds, a stream that silently
    never published is gone until someone notices weeks later.
    """
    if _is_link(url):
        # A link is proof something is on Rumble - not proof it is OUR
        # video. The uploader used to hand back a link scraped off the
        # sidebar of the upload page.
        try:
            from utils.channel_vods import slug_matches_title

            looks_right = slug_matches_title(url, title)
        except Exception:
            looks_right = None
        if looks_right is not False:
            return url
        print("[Rumble] The returned link does not match the title that was "
              "sent - not trusting it.")
        print(f"           sent: {title}")
        print(f"           got:  {url}")

    channel = str(getattr(cfg.rumble, "channel_url", "") or "").strip()
    if not channel:
        # Derived from the configured feed address, which is the only
        # place the channel is named today.
        channel = str(getattr(cfg.rumble, "rss_url", "") or "").replace(
            "/index.xml", "")
    if not channel:
        print("[Rumble] Cannot verify - no channel URL configured. Treating "
              "as uploaded; check https://rumble.com/account/content.")
        return url

    print(f"[Rumble] No link came back. Checking {channel} for the video...")
    try:
        from utils.channel_vods import find_on_channel

        found = find_on_channel(channel, title)
    except Exception as exc:
        print(f"[Rumble] Could not check the channel ({exc}). Treating as "
              f"uploaded; verify by hand.")
        return url

    if found:
        print(f"[Rumble] Confirmed on the channel -> {found}")
        return found

    print("[Rumble] NOT on the channel. The upload did not publish, so this "
          "is recorded as a failure and the next run will retry it.")
    return RUMBLE_UNCONFIRMED


def _trim_dead_air(path: str, cfg, words=None) -> str:
    """Remove dead air from a censored copy. Returns the path to upload.

    Never fatal, and never destructive: on any failure the untrimmed
    file is returned untouched, because a complete video is always
    better than a pacing feature.
    """
    try:
        # media_duration is imported at module level - re-importing it
        # here would bind it as a local for this whole function and
        # break the module-level one everywhere else in the file.
        from autoreel import silence_trim
        from autoreel.audio_energy import measure
    except Exception as exc:
        print(f"[Trim] Unavailable ({exc}) - keeping the full length.")
        return path

    duration = media_duration(path) or 0.0
    if duration <= 0:
        print("[Trim] Could not measure the video - keeping the full length.")
        return path

    if not words:
        # The censor pass caches word timings beside the video; without
        # them there is no way to tell a pause from a laugh, and cutting
        # on loudness alone would remove exactly the moments worth
        # keeping. Skipping is the correct answer, not a fallback.
        words = _cached_words(path, cfg)
    if not words:
        print("[Trim] No word timings for this video - keeping the full "
              "length. (Loudness alone cannot tell a pause from a laugh.)")
        return path

    clips = cfg.clips or {}
    cuts = silence_trim.find_dead_air(
        words, duration, levels=measure(path),
        min_silence_s=float(clips.get("min_silence_seconds", 2.5)))
    print(f"[Trim] {silence_trim.describe(cuts, duration)}")
    if not cuts:
        return path

    trimmed = os.path.join(
        os.path.dirname(path),
        os.path.splitext(os.path.basename(path))[0] + "_TRIMMED.mp4")
    try:
        return silence_trim.apply_trim(path, trimmed, cuts, duration)
    except silence_trim.TrimError as exc:
        print(f"[Trim] Skipped: {exc}")
        return path


def _cached_words(path: str, cfg) -> list:
    """Word timings the censor pass already wrote for this video."""
    try:
        from utils.censor import _load_cached_words, words_cache_path

        base = os.path.splitext(os.path.basename(path))[0]
        for candidate in (base, base.split("_CENSORED_")[0]):
            cached = _load_cached_words(
                words_cache_path(cfg.general.censored_folder, candidate), path)
            if cached:
                return [w for seg in cached for w in (seg.get("words") or [])]
    except Exception:
        pass
    return []


def _apply_mode(cfg, name: str, settings: dict):
    """Apply a named routing mode to the loaded config, loudly.

    Every key here maps onto a setting that already exists. The mode is
    a way of setting several of them together and having the run SAY so,
    which matters because "uncensored to Rumble" and "censored to
    YouTube" are two config values a long way apart in the file and
    getting one of them wrong publishes the wrong audio.
    """
    print(f"[Mode] {name}")
    if "rumble_censor_uploads" in settings:
        cfg.rumble.censor_uploads = bool(settings["rumble_censor_uploads"])
        print(f"       Rumble  <- {'censored' if cfg.rumble.censor_uploads else 'UNCENSORED'} full VOD")
    if settings.get("rumble_title_format"):
        cfg.rumble.title_format = str(settings["rumble_title_format"])
    if "youtube_censor_uploads" in settings:
        cfg.youtube.censor_uploads = bool(settings["youtube_censor_uploads"])
        print(f"       YouTube <- {'censored' if cfg.youtube.censor_uploads else 'UNCENSORED'} copy")

    clips = dict(cfg.clips or {})
    if "trim_silence" in settings:
        clips["trim_silence"] = bool(settings["trim_silence"])
        if clips["trim_silence"]:
            print("       Dead air trimmed from the YouTube copy")
    if settings.get("clips_to_shorts"):
        # Only a request. youtube_shorts still has to be enabled, signed
        # in and inside its cap - the mode does not get to bypass the
        # guard, and saying so here stops it looking broken when a clip
        # does not appear.
        clips["clips_to_shorts"] = True
        print("       Clips   -> YouTube Shorts (if enabled and signed in)")
    cfg.clips = clips
    return cfg


def _newest_video(cfg) -> str:
    """The most recently written video in any folder this tool uses.

    Looked up rather than asked for. The VOD names are long, have spaces
    in them and live in a different folder on every machine, so typing
    one is the step most likely to go wrong - and "the one I just
    recorded" is nearly always the one meant.
    """
    clips_folder = str((cfg.clips or {}).get("auto_clip_folder", "") or "")
    if clips_folder and not os.path.isabs(clips_folder):
        clips_folder = os.path.join(cfg.project_root, clips_folder)

    formats = tuple(cfg.general.supported_formats or (".mp4",))
    newest, newest_at = "", -1.0
    for folder in (clips_folder, cfg.general.watch_folder,
                   cfg.general.uploaded_folder):
        if not folder or not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            if not name.lower().endswith(formats):
                continue
            path = os.path.join(folder, name)
            try:
                when = os.path.getmtime(path)
            except OSError:
                continue
            if when > newest_at:
                newest, newest_at = path, when
    return newest


def _check_sync(cfg, source: str) -> int:
    """Measure caption/audio sync on a real video and name the cause.

    Two test points rather than one, and they are the whole design: an
    error that is the SAME at both is a clock or a seek problem, and one
    that grows is the frame rate. One sample cannot tell those apart, and
    they need opposite fixes.
    """
    import tempfile

    from autoreel import clip_sync
    from autoreel.clip_maker import ClipSpec, have_ffmpeg, render_clip

    if not (have_ffmpeg() and clip_sync.have_tools()):
        print("[Sync] ffmpeg and ffprobe are both needed for this.")
        return 1

    streams = clip_sync.probe_streams(source)
    if not streams.get("audio"):
        print("[Sync] This file has no audio track, so there is nothing "
              "for captions to line up with.")
        return 1

    video = streams.get("video") or {}
    audio = streams["audio"]
    print(f"\n[Sync] {os.path.basename(source)}\n")
    print(f"  container starts at   {streams.get('container_start')}")
    print(f"  video  starts at      {video.get('start_time')}   "
          f"({video.get('codec', '?')})")
    print(f"  audio  starts at      {audio.get('start_time')}   "
          f"({audio.get('codec', '?')})")
    print(f"  audio/video clock gap {clip_sync.clock_gap(streams):+.3f}s")
    print(f"  frame rate            listed {video.get('r_frame_rate')}, "
          f"average {video.get('avg_frame_rate')}"
          f"{'   <- VARIABLE' if clip_sync.is_variable_rate(streams) else ''}")
    v_len, a_len = video.get("duration"), audio.get("duration")
    if v_len and a_len:
        print(f"  length                video {v_len:.2f}s, audio {a_len:.2f}s"
              f"{'   <- MISMATCH' if abs(v_len - a_len) > 1.0 else ''}")

    # Where to sample. Early enough to be quick, late enough that drift
    # has had room to accumulate - the reference envelope is decoded from
    # the start with no seek, so a point an hour in costs an hour of
    # audio decoding and the late sample is deliberately not the end.
    length = a_len or v_len or 0.0
    if length < 90:
        print("\n[Sync] This video is too short to sample twice; measuring "
              "once.")
        points = [max(5.0, length * 0.25)]
    else:
        points = [min(180.0, length * 0.1), min(1200.0, length * 0.6)]

    span = 12.0
    cuts = []
    workspace = tempfile.mkdtemp(prefix="sync_")
    try:
        for point in points:
            reference = clip_sync.envelope(source, point, span)
            probe_path = os.path.join(workspace, f"probe_{int(point)}.mp4")
            try:
                render_clip(source, ClipSpec(point, point + span, 1),
                            probe_path, "center", None, "libx264",
                            "ultrafast", 30, watermark=False)
            except Exception as exc:
                print(f"\n[Sync] Could not render a test clip at "
                      f"{point:.0f}s: {exc}")
                continue
            offset, score = clip_sync.best_offset(
                reference, clip_sync.envelope(probe_path))
            cuts.append((offset, score))
            print(f"\n  at {point / 60:5.1f} min: the cut is {offset:+.3f}s "
                  f"out (confidence {score:.2f})")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    # And the other half: does the TRANSCRIPT agree with the sound? A
    # perfect cut still produces wrong captions when the words are
    # written down in the wrong place, and that failure looks identical
    # from the outside.
    words = (0.0, 0.0)
    try:
        from utils.clip_runner import _load_segments

        segments = _load_segments(cfg, source)
    except Exception:
        segments = None
    if segments:
        words = clip_sync.transcript_offset(source, segments, points[0], span)
        print(f"\n  transcript vs sound: {words[0]:+.3f}s "
              f"(confidence {words[1]:.2f})")
    else:
        print("\n  transcript: none cached for this video, so its word "
              "timings could not be checked. Run --clips-from on it first "
              "if the cut below looks fine but captions still do not.")

    print("\n" + "-" * 68)
    print(clip_sync.verdict(streams, cuts, words))
    print("-" * 68 + "\n")
    print("Send this whole output back and it decides what gets fixed.\n")
    return 0


def _clip_config(cfg) -> dict:
    """The slice of config the clip publishers actually read.

    youtube_shorts was missing from here, and the effect was silent: the
    publisher read its settings out of this dict, got {}, resolved an
    empty token_path, and answered ready() False - so every clip logged
    "youtube_shorts: skipped - not configured yet" no matter how
    correctly the token had been set up. --posting-status looked right,
    --verify named the right channel, and nothing ever reached the
    channel. A slice of config is a place things go missing quietly.
    """
    return {"instagram": cfg.instagram, "facebook": cfg.facebook,
            "clips": cfg.clips, "features": cfg.features,
            "youtube_shorts": cfg.youtube_shorts,
            "zernio": cfg.zernio,
            # The Shorts publisher falls back to the VOD channel's
            # client_secrets.json - same app, different channel.
            "youtube": {"client_secrets_path": cfg.youtube.client_secrets_path,
                        "channel": cfg.youtube.channel},
            # So every posting outcome lands in logs/clips.log.
            "logs_folder": cfg.general.logs_folder}


def _find_clips(cfg, limit: int = 15) -> list:
    """Every video short enough to be a Reel, across the usual folders.

    Duration rather than filename: clips arrive named after whatever the
    streamer called them, so there is no prefix to match on.
    """
    found, seen = [], set()
    for folder in (cfg.general.watch_folder, cfg.general.uploaded_folder):
        folder = os.path.abspath(folder or "")
        if not folder or folder in seen or not os.path.isdir(folder):
            continue
        seen.add(folder)
        for name in sorted(os.listdir(folder)):
            if os.path.splitext(name)[1].lower() not in cfg.general.supported_formats:
                continue
            path = os.path.join(folder, name)
            seconds = media_duration(path)
            if seconds and seconds <= CLIP_MAX_SECONDS:
                found.append(f'"{path}"  ({seconds:.0f}s)')
            if len(found) >= limit:
                return found
    return found


def downloaded_title(video_path: str) -> str:
    """The title the platform published, from yt-dlp's info sidecar.

    yt-dlp writes `<name>.info.json` beside the video when asked, and its
    "title" field is the exact text as posted - emoji, casing, the stream
    date, all of it. Nothing else here has access to that: by the time
    the file is on disk, --restrict-filenames has flattened the title
    into `monkey_n_gamble_howl`, and no amount of reading that back
    recovers what it said.

    Returns "" when there is no sidecar, which is every video that
    arrived any other way - the caller falls through to its other
    sources exactly as before.
    """
    info = os.path.splitext(video_path)[0] + ".info.json"
    try:
        with open(info, "r", encoding="utf-8", errors="replace") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ""
    title = data.get("title") if isinstance(data, dict) else ""
    return str(title or "").strip()


def is_placeholder_title(title: str, default_title: str) -> bool:
    """True when this title names no particular stream.

    A stream whose real title could not be read gets the configured
    default, and EVERY such stream gets the same one - so matching on it
    says "this stream was already uploaded" about a completely different
    stream. Compared loosely because the generated title wraps it:
    `"Gaming Stream" 8/14/26 Stackswopo Stream`.
    """
    text = " ".join(str(title or "").lower().split())
    fallback = " ".join(str(default_title or "").lower().split())
    return bool(fallback) and (not text or text == fallback)


def get_stream_title(video_path: str, cli_title: str, cfg, allow_prompt: bool = True) -> str:
    """Work out the stream title, most-explicit source first.

    `allow_prompt=False` is used by --watch: that runs unattended in a
    background thread, where `input()` would block forever with nobody at
    the keyboard and silently wedge the upload.
    """
    if cli_title:
        return cli_title

    sidecar = os.path.splitext(video_path)[0] + ".txt"
    if os.path.exists(sidecar):
        with open(sidecar, "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            return text.splitlines()[0].strip()

    # The real title as the platform published it, saved beside the video
    # when it was downloaded. This beats reading the filename back,
    # because --restrict-filenames has already flattened the title into
    # something like monkey_n_gamble_howl - punctuation gone, spaces
    # turned to underscores, casing lost. Guessing a title back out of
    # that is how a clip ends up called "Gaming Stream".
    published = downloaded_title(video_path)
    if published:
        return published

    # Filenames often already carry a usable title (quoted, the text before
    # the date, or a yt-dlp "<channel> - <title>-<id>" name) - use it
    # instead of stopping to ask, so a --batch or --watch run doesn't need
    # to be babysat file by file.
    extracted = extract_title_from_filename(
        os.path.basename(video_path), cfg.general.filename_channel_prefixes)
    if extracted:
        return extracted

    if cfg.general.ask_for_title and allow_prompt:
        prompt = f"\nNew video detected: {os.path.basename(video_path)}\nStream title (Enter for default '{cfg.general.default_title}'): "
        typed = input(prompt).strip()
        return typed or cfg.general.default_title

    if cfg.general.ask_for_title:
        print(f"[TITLE] Could not read a title from {os.path.basename(video_path)}; "
              f"using '{cfg.general.default_title}'. Drop a .txt file next to the "
              f"video (same name, title on line 1) to set it without prompting.")
    return cfg.general.default_title



def _parse_args_helpfully(parser, argv):
    """parse_args, but suggest the real flag when one is mistyped.

    argparse answers an unknown flag with "unrecognized arguments" and
    exits, which is accurate and useless - `--watch_folder` is an obvious
    typo for `--watch`, and printing the nearest match saves a round trip.
    """
    import difflib

    known = []
    for action in parser._actions:
        known.extend(action.option_strings)

    argv = list(sys.argv[1:] if argv is None else argv)
    for arg in argv:
        if not arg.startswith("--") or arg in known:
            continue
        stem = arg.split("=", 1)[0]
        if stem in known:
            continue
        # Match on the stem too, so --watch_folder finds --watch even
        # though the whole string is not close to it.
        close = difflib.get_close_matches(stem, known, n=3, cutoff=0.5)
        close += [k for k in known
                  if k not in close and k.lstrip("-")
                  and stem.lstrip("-").startswith(k.lstrip("-"))]
        if close:
            print(f"[ERROR] No such option: {stem}")
            print(f"        Did you mean: {', '.join(dict.fromkeys(close))}")
            print("        Run `python main.py --help` for the full list.")
            raise SystemExit(2)

    return parser.parse_args(argv)


def process_file(video_path: str, cfg, cli_title: str, dup_checker: DuplicateChecker,
                  yt_logger, rb_logger, dry_run: bool, existing_youtube_videos: list = None,
                  existing_rumble_videos: list = None, allow_prompt: bool = True,
                  only_platform: str = None) -> dict:
    filename = os.path.basename(video_path)

    # Cheap checks first. Hashing reads the whole file, so doing it before
    # the extension test meant a folder of in-progress yt-dlp downloads
    # (multi-GB *.part files) got read end-to-end off the drive purely to
    # throw the result away - and those files are still being written to.
    if os.path.splitext(video_path)[1].lower() not in cfg.general.supported_formats:
        # Only worth saying out loud for files that might plausibly have
        # been meant as videos. A watch folder always contains .gitkeep,
        # sidecar .txt titles and OS junk; announcing each one every run
        # trains you to ignore [SKIP] lines, which is when a real one gets
        # missed.
        if not is_sidecar_file(filename):
            print(f"[SKIP] {filename} is not a supported video format.")
        return {"skipped": "unsupported_format"}

    # e.g. "Stream.f140.mp4" - yt-dlp's audio-only half, downloaded in full
    # but not yet muxed with the video. Real extension, real size, and it
    # stops growing, so nothing else here would catch it.
    if is_intermediate_download(video_path):
        print(f"[SKIP] {filename} is a partial download (pre-merge), not a finished video.")
        return {"skipped": "intermediate_download"}

    # Reading a multi-GB video off an external drive takes minutes, and
    # the "Processing:" banner below only appears once it is done. Without
    # this line the tool looks hung at exactly the point it is working
    # hardest.
    size_gb = os.path.getsize(video_path) / (1024 ** 3)
    if size_gb >= 0.5:
        print(f"[Check] Reading {filename} ({size_gb:.1f} GB) to see if it has "
              "been uploaded before - this takes a minute on a big file...",
              flush=True)
    file_hash = hash_file(video_path)

    # A short video is a CLIP, not a stream, and clips go somewhere else:
    # Rumble (which takes shorts) and the social accounts, never the main
    # YouTube channel - a channel of full VODs should not fill up with
    # thirty-second Twitch highlights.
    clip_cfg = cfg.clips or {}
    is_clip = False
    if clip_cfg.get("route_clips_separately", True):
        seconds = media_duration(video_path)
        # NOT clips.max_seconds - that is how long a rendered clip may
        # be, which is a different question from what counts as one.
        is_clip = bool(seconds and seconds <= float(
            clip_cfg.get("treat_as_clip_under_seconds", CLIP_MAX_SECONDS)))

    if is_clip and not only_platform:
        print(f"[Clip] {filename} is {seconds / 60:.1f} min - treating it as a "
              "clip: Rumble + social announcement, NOT the YouTube channel. "
              "(clips.route_clips_separately in config.json turns this off.)")
        only_platform = "rumble"

    # With --only, "done" means done for that platform alone; the other's
    # history is neither required nor written.
    active_platforms = (only_platform,) if only_platform else ("youtube", "rumble")

    if dup_checker.is_fully_uploaded(file_hash, platforms=active_platforms):
        where = only_platform or "both platforms"
        print(f"[SKIP] {filename} already uploaded to {where} previously (matched by content hash).")
        # Uploaded is not the same as CLIPPED. This returned here, so a
        # stream that had been through once was never looked at again -
        # and a 2.7 GB VOD sat in the watch folder being skipped every
        # single run while no clip was ever cut from it.
        _clip_already_uploaded(cfg, video_path, is_clip)
        # Then get it out of the watch folder. Leaving it meant every
        # run re-hashed 2.7 GB to reach the same answer, and the folder
        # never emptied. Retired the SAME way a freshly-uploaded video
        # is - whatever cleanup.source_video says - because "already
        # uploaded" and "just uploaded" are the same state.
        _retire_duplicate(cfg, video_path)
        return {"skipped": "duplicate"}

    if is_clip and not cli_title:
        # A clip carries its own title in the filename ("who put stacks on
        # slots"), and a batch of eleven should not stop eleven times to
        # ask for something already on disk.
        #
        # The SIDECAR comes first though. Clips this pipeline cut have a
        # .txt beside them holding the line actually spoken in the clip,
        # and reading the filename instead published "Yoo Howl - Clip 03"
        # while "Show me Q50" sat unread next to it. The filename is the
        # fallback for a clip dropped in by hand, not the first choice.
        from utils.social_promoter import clip_title as _clip_title

        sidecar = os.path.splitext(video_path)[0] + ".txt"
        spoken = ""
        if os.path.isfile(sidecar):
            try:
                with open(sidecar, "r", encoding="utf-8") as handle:
                    spoken = handle.read().strip().splitlines()[0].strip()
            except (OSError, IndexError):
                spoken = ""
        if spoken:
            stream_title = spoken
            print(f"[Clip] Title from the clip itself: {stream_title}")
        else:
            stream_title = _clip_title(video_path)
            print(f"[Clip] Title from the filename: {stream_title}")
    else:
        stream_title = get_stream_title(video_path, cli_title, cfg, allow_prompt)
    # A freshly-finished stream should be dated today; an old VOD being
    # backfilled should keep its original air date if the filename has one
    # (e.g. "'!howl' 3-20-26 ...") - otherwise every backlog upload would
    # get today's date instead of when it actually aired.
    now = extract_date_from_filename(filename) or datetime.now()
    date_str = format_date(now, cfg.general.date_style)

    # A clip gets its own shape. The archival one keeps a library of full
    # VODs sortable; on a sixty-second clip the date and the channel name
    # are scaffolding a viewer reads past, and they are what makes a feed
    # look automated.
    yt_format = (cfg.youtube.clip_title_format if is_clip
                 else cfg.youtube.title_format)
    rb_format = (cfg.rumble.clip_title_format if is_clip
                 else cfg.rumble.title_format)
    yt_title = build_title(stream_title, date_str, yt_format)
    yt_description = build_description(cfg.youtube.description_template, date_str, stream_title)
    rb_title = build_title(stream_title, date_str, rb_format)
    rb_description = build_description(cfg.rumble.description_template, date_str, stream_title)

    print(f"\n{'='*70}\nProcessing: {filename}")
    print(f"YouTube title: {yt_title}")
    print(f"Rumble title:  {rb_title}")
    print(f"{'='*70}")

    # Determined here (before the dry-run branch) so a dry run can show
    # "would skip - already exists" instead of pretending it would
    # actually upload to YouTube - otherwise the preview isn't a real
    # preview of what a --batch run against a backlog folder would do.
    # Check per-platform, not just "was this file ever seen": if a
    # previous run already succeeded on YouTube (and only died later,
    # e.g. Ctrl+C during Rumble's retry wait), re-attempting here would
    # create a real duplicate video on the channel.
    existing_yt = dup_checker.get_platform_result(file_hash, "youtube")
    existing_yt_match = None
    if not existing_yt and existing_youtube_videos:
        # Not something *this tool* uploaded before, but might already be
        # on the channel from a manual upload in the past (common for a
        # backlog folder) - matched by date in the title, since this
        # channel's title style has changed over the years but always
        # includes the date. Rumble is NOT skipped in this case - these
        # old VODs were typically only ever uploaded to YouTube manually.
        existing_yt_match = find_existing_video(existing_youtube_videos, now, stream_title)
        if existing_yt_match:
            existing_yt = existing_yt_match.url
        else:
            # Same date, different stream. Say so rather than skipping: two
            # streams on one day is normal, and a silent skip here means the
            # video simply never gets uploaded.
            same_day = find_same_date_videos(existing_youtube_videos, now)
            if same_day:
                print(f"[YouTube] {len(same_day)} video(s) already dated {date_str} "
                      f"(e.g. {same_day[0].title!r}), but none titled "
                      f"{stream_title!r} - uploading this one.")

    if existing_yt:
        note = " (would skip on a real run, still would try Rumble)" if dry_run else " (skipping, still trying Rumble)"
        print(f"[YouTube] {filename} already exists -> {existing_yt}{note}")

    # Same idea for Rumble, in the order the user specified: local history
    # by content hash (get_platform_result), local history by generated
    # title (catches re-encoded copies of the same stream), then the
    # channel's public RSS feed matched by stream date.
    existing_rb = dup_checker.get_platform_result(file_hash, "rumble")
    existing_rb_match_url = None
    # Title matching is only safe when the title IDENTIFIES the stream.
    # When the real title could not be read, every stream generates the
    # same one - "Gaming Stream" - so the second such stream matches the
    # first in local history and is skipped as already uploaded. That is
    # how a stream reached YouTube and never reached Rumble: Rumble has
    # no feed to check against, so the title is all its dedup has.
    #
    # The content hash still protects against a genuine re-upload, which
    # is the check that actually matters. Falling through here risks a
    # duplicate; refusing here loses the stream entirely, and one of
    # those is recoverable.
    generic = is_placeholder_title(stream_title, cfg.general.default_title)
    if generic:
        print(f"[Rumble] Not matching by title - \"{stream_title}\" is the "
              f"fallback name, not this stream's own, and every stream that "
              f"loses its title generates it. The content hash still "
              f"applies.")
    if not existing_rb and cfg.rumble.skip_if_exists and not generic:
        existing_rb = dup_checker.find_platform_title("rumble", rb_title)
        if not existing_rb and existing_rumble_videos:
            rb_match = find_existing_video(existing_rumble_videos, now, stream_title)
            if rb_match:
                existing_rb = existing_rb_match_url = rb_match.url
    if existing_rb:
        note = " (would skip on a real run)" if dry_run else ""
        print(f"[Rumble] Video already exists on Rumble -> {existing_rb}{note}")

    if dry_run:
        print("[DRY RUN] Would upload with the title/description above. Nothing was uploaded.")
        if cfg.general.censor_before_upload:
            yt_c = "censored" if cfg.youtube.censor_uploads else "ORIGINAL (uncensored)"
            rb_c = "censored" if cfg.rumble.censor_uploads else "ORIGINAL (uncensored)"
            print(f"[DRY RUN] Audio: YouTube would get the {yt_c} copy; Rumble would get the {rb_c} copy.")
        print("\n--- YouTube description preview ---")
        print(yt_description)
        print("\n--- Rumble description preview ---")
        print(rb_description)
        return {"dry_run": True}

    if existing_yt_match:
        # Only persisted on a real run - a dry run must not have side
        # effects on the duplicate-tracking store.
        dup_checker.record_platform_result(file_hash, filename, "youtube", existing_yt_match.url, title=yt_title)
    if existing_rb_match_url:
        dup_checker.record_platform_result(file_hash, filename, "rumble", existing_rb_match_url, title=rb_title)

    # Censoring is per-platform and computed lazily: YouTube gets the
    # censored copy (it age-restricts/demonetizes over spoken profanity),
    # Rumble gets the original audio. Doing it lazily matters a lot for
    # backlog runs - if YouTube is skipped as already-uploaded and Rumble
    # doesn't want censoring, transcription (the slow part, many minutes
    # per stream) is never run at all.
    _censored = {}
    _vertical = {}

    def vertical_path(source: str) -> str:
        """A clip re-framed 9:16, made once and shared.

        Rumble decides Shorts by aspect ratio, so a 16:9 clip lands in
        Videos next to the five-hour streams no matter how short it is.
        Instagram wants the same shape for a Reel, so one re-frame serves
        both - and it is the expensive step, so doing it twice would be
        the whole cost again.
        """
        if not is_clip or not (cfg.instagram or {}).get("vertical", True):
            return source
        if source in _vertical:
            return _vertical[source]
        try:
            from autoreel.clip_maker import make_vertical
            from autoreel.crop_strategy import resolve_crop_strategy
        except Exception:
            return source

        strategy = resolve_crop_strategy({"clips": cfg.clips},
                                         (cfg.clips or {}).get("content_kind", "gameplay"))
        os.makedirs(cfg.general.censored_folder, exist_ok=True)
        target = os.path.join(cfg.general.censored_folder,
                              f"_vertical_{os.path.basename(source)}")
        print(f"[Clip] Re-framing to 9:16 ({strategy} crop) so Rumble files it "
              "as a Short...")
        made = make_vertical(source, target, strategy)
        if not made:
            print("[Clip] Could not re-frame - uploading as-is (it will be a "
                  "regular video, not a Short).")
            made = source
        _vertical[source] = made
        return made

    def instagram_clip_path() -> str:
        """The file Instagram gets. Deliberately the ORIGINAL audio.

        Instagram is not YouTube: it does not demonetise or age-restrict
        over spoken language, so censoring a clip for it removes the
        moment and buys nothing. Set instagram.censor_uploads true to
        change that.

        Written as its own function because the obvious shortcut - reuse
        whichever re-frame already exists - would hand Instagram the
        CENSORED copy on any run where another platform asked for one.
        """
        source = video_path
        if (cfg.instagram or {}).get("censor_uploads", False):
            source = upload_path_for(True)
        return vertical_path(source)

    def upload_path_for(platform_wants_censoring: bool) -> str:
        if not (platform_wants_censoring and cfg.general.censor_before_upload):
            return vertical_path(video_path)
        if "path" not in _censored:
            print(f"[Censor] Transcribing + scanning for profanity (model={cfg.general.censor_model})...")
            censor_result = censor_video(
                video_path, cfg.general.censored_folder,
                model_name=cfg.general.censor_model,
                bleep_method=cfg.general.censor_bleep_method,
                custom_words=cfg.general.censor_custom_words,
                device=cfg.general.censor_device,
                speed=cfg.general.speed,
                padding_ms=cfg.general.censor_padding_ms,
                mute_whole_segment=cfg.general.censor_mute_whole_segment,
            )
            _censored["path"] = censor_result.output_path
            if censor_result.violation_count == -1:
                print("[Censor] Reusing a censored copy from a previous attempt.")
            elif censor_result.was_censored:
                verb = "Silenced" if cfg.general.censor_bleep_method == "silence" else "Bleeped"
                print(f"[Censor] {verb} {censor_result.violation_count} word(s): {', '.join(censor_result.censored_words)}")
                notify(
                    "Video censored before upload",
                    f"{filename}: {censor_result.violation_count} word(s) censored",
                    cfg.general.enable_desktop_notifications,
                )
            else:
                print("[Censor] No profanity/mature language detected - uploading original audio.")

            # Dead air comes out AFTER censoring, never before: trimming
            # first would move every word timing the censor pass depends
            # on and the bleeps would land on the wrong words. Only the
            # copy destined for YouTube is trimmed - the Rumble upload
            # takes the untouched source, which is the whole point of
            # the split.
            if (cfg.clips or {}).get("trim_silence"):
                _censored["path"] = _trim_dead_air(
                    _censored["path"], cfg,
                    getattr(censor_result, "words", None))

        return vertical_path(_censored["path"])

    run_started_at = time.time()
    stage_timer = StageTimer(filename,
                             enabled=bool((cfg.general.speed or {}).get('stage_timings', True)))
    results = {}
    newly_uploaded = {}

    notify("Upload starting", filename, cfg.general.enable_desktop_notifications)

    # YouTube and Rumble are independent and both spend nearly all their
    # time waiting on the network, so they run together rather than one
    # after the other. Rumble's browser path in particular spends minutes
    # logging in, filling the form, picking categories and polling for the
    # finished URL - all of which overlaps with YouTube's transfer for
    # free.
    #
    # What this does NOT do is fire an upload and walk away. For both
    # platforms the wait IS the byte transfer: YouTube's loop is the
    # resumable PUT, and Rumble's progress bar is the browser sending the
    # file. There is nothing to return early from.
    #
    # Both threads write to the same three places, so a lock guards them.
    # The duplicate store is a JSON file: two threads writing it at once
    # is how an upload record gets lost, and a lost record is a re-upload.
    upload_lock = threading.Lock()

    def record(platform: str, title: str) -> None:
        with upload_lock:
            dup_checker.record_platform_result(
                file_hash, filename, platform,
                results.get(platform, "FAILED: interrupted"), title=title)

    def progress_reporter(label: str, parallel: bool):
        """Two uploads sharing one terminal line produce garbage, so in
        parallel each reports on its own line every 10%."""
        if not parallel:
            return lambda pct: print(f"\r[{label}] Uploading... {pct}%",
                                     end="", flush=True)
        seen = {"step": -1}

        def report(pct: int) -> None:
            step = int(pct) // 10
            if step > seen["step"]:
                seen["step"] = step
                print(f"[{label}] Uploading... {pct}%", flush=True)
        return report

    # Resolved BEFORE anything starts. upload_path_for() runs the censor
    # pass on first use and caches it; asked for concurrently by two
    # threads it would transcribe the same video twice, on one GPU.
    yt_source = rb_source = ""
    if "youtube" in active_platforms and not existing_yt:
        yt_source = upload_path_for(cfg.youtube.censor_uploads)
    if "rumble" in active_platforms and not existing_rb:
        rb_source = upload_path_for(cfg.rumble.censor_uploads)

    def do_youtube(parallel: bool) -> None:
        try:
            yt = YouTubeUploader(cfg.youtube.client_secrets_path, cfg.youtube.token_path)

            def yt_on_retry(attempt, delay, exc):
                yt_logger.warning(f"{filename}: attempt {attempt} failed ({exc}); retrying in {delay}s")

            url = retry_with_backoff(
                lambda: yt.upload(
                    yt_source, yt_title, yt_description, cfg.youtube.tags,
                    chunk_mb=float(getattr(cfg.youtube, 'upload_chunk_mb', 8) or 8),
                    privacy=cfg.youtube.privacy, category_id=cfg.youtube.category_id,
                    made_for_kids=cfg.youtube.made_for_kids,
                    thumbnail_path=cfg.youtube.thumbnail_path or None,
                    playlist_id=cfg.youtube.playlist_id or None,
                    progress_callback=progress_reporter("YouTube", parallel),
                ),
                max_retries=cfg.general.max_retries, delays=cfg.general.retry_delays, on_retry=yt_on_retry,
            )
            if not parallel:
                print()
            yt_logger.info(f"{filename}: uploaded successfully -> {url}")
            notify("YouTube upload complete", url, cfg.general.enable_desktop_notifications)
            with upload_lock:
                results["youtube"] = url
                newly_uploaded["youtube"] = url
        except Exception as exc:
            if not parallel:
                print()
            print(f"[YouTube] UPLOAD FAILED: {exc}")
            print(f"          Full details: {os.path.join(cfg.general.logs_folder, 'youtube.log')}")
            yt_logger.error(f"{filename}: FAILED: {exc}")
            notify("YouTube upload FAILED", f"{filename}: {exc}", cfg.general.enable_desktop_notifications)
            with upload_lock:
                results["youtube"] = f"FAILED: {exc}"
        finally:
            # Runs even on an uncaught KeyboardInterrupt (Ctrl+C), which is
            # exactly what we need: whatever happened gets persisted
            # immediately, so a Ctrl+C here can't cause a later re-upload -
            # but the interrupt still propagates and actually stops the
            # script, instead of being silently swallowed.
            record("youtube", yt_title)

    def do_rumble(parallel: bool) -> None:
        try:
            rb = RumbleUploader(
                cfg.rumble.username, cfg.rumble.password, cfg.rumble.login_url, cfg.rumble.upload_url,
                cdp_url=cfg.rumble.cdp_url,
                primary_category=cfg.rumble.primary_category,
                secondary_category=cfg.rumble.secondary_category,
            )

            def rb_on_retry(attempt, delay, exc):
                rb_logger.warning(f"{filename}: attempt {attempt} failed ({exc}); retrying in {delay}s")

            url = retry_with_backoff(
                lambda: rb.upload(
                    rb_source, rb_title, rb_description, cfg.rumble.tags,
                    privacy=cfg.rumble.privacy, thumbnail_path=cfg.rumble.thumbnail_path or None,
                    progress_callback=progress_reporter("Rumble", parallel),
                ),
                max_retries=cfg.general.max_retries, delays=cfg.general.retry_delays, on_retry=rb_on_retry,
            )
            if not parallel:
                print()
            # Rumble sometimes finishes without ever showing a link, and
            # this recorded that as a success on the assumption the video
            # had landed anyway. It had not: a full VOD was marked
            # uploaded, the dedup store believed it, every retry was
            # skipped, and the stream simply never appeared. Ask the
            # channel instead of assuming.
            url = _confirm_on_rumble(url, rb_title, cfg)

            if _is_link(url):
                rb_logger.info(f"{filename}: uploaded successfully -> {url}")
                notify("Rumble upload complete", url,
                       cfg.general.enable_desktop_notifications)
            else:
                rb_logger.warning(f"{filename}: {url}")
            with upload_lock:
                results["rumble"] = url
                newly_uploaded["rumble"] = url
        except Exception as exc:
            if not parallel:
                print()
            # Printed, not just logged. This used to go to rumble.log and a
            # desktop toast only, so a failed Rumble upload looked exactly
            # like a successful one from the terminal: no output at all.
            print(f"[Rumble] UPLOAD FAILED: {exc}")
            if cfg.rumble.cdp_url:
                print(f"         Rumble uploads through Chrome at {cfg.rumble.cdp_url}. "
                      "If that is not running, start it with "
                      "--remote-debugging-port=9222 and log into rumble.com there.")
            print(f"         Full details: {os.path.join(cfg.general.logs_folder, 'rumble.log')}")
            rb_logger.error(f"{filename}: FAILED: {exc}")
            notify("Rumble upload FAILED", f"{filename}: {exc}", cfg.general.enable_desktop_notifications)
            with upload_lock:
                results["rumble"] = f"FAILED: {exc}"
        finally:
            record("rumble", rb_title)

    # --- Dispatch ---
    jobs = []
    if "youtube" not in active_platforms:
        print("[YouTube] Skipped - --only rumble.")
    elif existing_yt:
        results["youtube"] = existing_yt  # already announced above
    else:
        jobs.append(("youtube", do_youtube))

    if "rumble" not in active_platforms:
        print("[Rumble] Skipped - --only youtube.")
    elif existing_rb:
        print(f"[Rumble] Already on the channel - skipping: {existing_rb}")
        results["rumble"] = existing_rb
    else:
        jobs.append(("rumble", do_rumble))

    parallel = len(jobs) > 1 and bool(
        (cfg.general.speed or {}).get("parallel_uploads", True))
    if parallel:
        print(f"[Upload] YouTube and Rumble together - the slower of the two "
              f"is the wait, not the sum.")
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = [pool.submit(job, True) for _, job in jobs]
            for future in futures:
                # Re-raised here rather than swallowed; each job already
                # catches its own failures, so anything reaching this is a
                # bug rather than a failed upload.
                future.result()
    else:
        for _, job in jobs:
            job(False)

    fully_uploaded = dup_checker.is_fully_uploaded(file_hash, platforms=active_platforms)

    def retire_source() -> None:
        """Move or delete the uploaded video - AFTER the clips are cut.

        This used to run here, before clipping, and with
        cleanup.source_video set to 'delete' that meant the VOD was gone
        by the time anything tried to clip it. A stream is the source of
        the next day's clips; deleting it the moment it uploads throws
        that away, and there is no getting it back.
        """
        if not fully_uploaded:
            print(f"[INFO] {filename} left in place (not every platform "
                  "succeeded yet) - rerun --file or --batch on it later to "
                  "retry just what's still missing.")
            return
        action = resolve_source_action(cfg)
        if action == SOURCE_DELETE:
            # Only reachable when BOTH platforms already succeeded - the
            # video is published, and the user has opted into losing the
            # local copy.
            try:
                size_mb = os.path.getsize(video_path) / (1024 ** 2)
                os.remove(video_path)
                print(f"[Cleanup] Deleted source video ({size_mb:.0f} MB) - "
                      f"cleanup.source_video is 'delete'.")
            except OSError as exc:
                print(f"[WARN] Could not delete {filename}: {exc}")
        elif action == SOURCE_KEEP:
            print(f"[INFO] {filename} left in place (cleanup.source_video is 'keep').")
        else:
            dest = os.path.join(cfg.general.uploaded_folder, filename)
            os.makedirs(cfg.general.uploaded_folder, exist_ok=True)
            try:
                shutil.move(video_path, dest)
            except Exception as exc:
                print(f"[WARN] Could not move {filename} to uploaded/: {exc}")

    # Post-upload extras - strictly best-effort, only for uploads that
    # actually happened THIS run (never for pre-existing skips), and never
    # in a dry run. A failure here must not mark the upload as failed.
    stage_timer.mark("upload")

    if newly_uploaded:
        # A CLIP goes to Instagram and Facebook as a Reel - the bytes are
        # uploaded, so nothing needs hosting. Through the queue rather
        # than directly, because ten clips arrive within minutes of each
        # other and the platforms are spaced much wider than that: the
        # ones that cannot go now are kept and posted when their wait is
        # up, instead of being dropped the way they were.
        clip_reels = {}
        if is_clip and cfg.posting:
            try:
                from utils.clip_queue import CLIP_PLATFORMS, offer

                clip_reels = offer(
                    cfg.posting, _clip_config(cfg), instagram_clip_path(),
                    fallback_caption=yt_title, dry_run=dry_run)
            except Exception as exc:
                print(f"[Clips] WARNING: could not offer the clip: {exc}")

        try:
            from utils.social_promoter import announce_upload
            # cfg.posting carries the guarded public platforms (Facebook,
            # Instagram, X); without it this announces to Discord/Reddit
            # only, exactly as before.
            # newly_uploaded decides WHETHER to announce; results carries
            # every URL this video has, so the post names both platforms
            # even when one of them succeeded on an earlier run.
            announce_upload(cfg.features.get("social_promoter", {}), yt_title,
                            newly_uploaded, posting=cfg.posting,
                            config={"features": cfg.features,
                                    # Already 9:16 if a re-frame happened;
                                    # cropping it again would be a no-op
                                    # that still costs a full re-encode.
                                    "instagram": ({**cfg.instagram,
                                                   "vertical": False}
                                                  if _vertical else cfg.instagram),
                                    "clips": cfg.clips},
                            all_uploads=results,
                            # The Reel platforms were handled above, by
                            # the queue. Announcing to them here as well
                            # would post the same clip twice - once as a
                            # Reel and once as a link to the Rumble page.
                            skip_platforms=tuple(clip_reels))
        except Exception as exc:
            print(f"[Social] WARNING: announce failed: {exc}")
        # A finished STREAM is the source of the next day of clips. The
        # transcript the censor pass already produced is what scores the
        # highlights, so this is nearly free at this point - and doing it
        # here rather than by hand is the difference between having clips
        # and meaning to make some.
        if not is_clip and (cfg.clips or {}).get("auto_from_streams", False):
            try:
                from utils.clip_runner import make_clips, print_run

                # The source is still where it was: retire_source() now
                # runs after this. The fallback stays for a re-run over a
                # video that was already moved to uploaded/ on an earlier
                # pass.
                source = video_path
                if not os.path.isfile(source):
                    moved = _suggest_paths(cfg, os.path.basename(video_path))
                    if moved:
                        source = moved[0]
                run = make_clips(cfg, source, stream_title,
                                 count=(cfg.clips or {}).get("count"))
                print_run(run)
                delivered = _deliver_clips(run, cfg)
                if delivered:
                    print(f"[Clips] {delivered} clip(s) moved into "
                          f"{cfg.general.watch_folder} - they will be posted "
                          "one at a time, on the spacing set for each "
                          "platform.")
            except Exception as exc:
                # Clips are a bonus on top of a successful upload; failing
                # to make them must not make the upload look failed.
                print(f"[Clips] WARNING: could not make clips: {exc}")

        try:
            optimizer_cfg = cfg.features.get("content_optimizer", {})
            if optimizer_cfg.get("enabled", True):
                from utils.censor import transcript_cache_path
                from utils.content_optimizer import generate_report
                base = os.path.splitext(filename)[0]
                report = generate_report(
                    base, transcript_cache_path(cfg.general.censored_folder, base),
                    stream_title, date_str, cfg.general.uploaded_folder, optimizer_cfg,
                )
                if report:
                    print(f"[Optimize] SEO/chapters report written -> {report}")
        except Exception as exc:
            print(f"[Optimize] WARNING: report failed: {exc}")

    if newly_uploaded:
        stage_timer.mark("metadata/optimizer")

    # Only now is the VOD finished with. It had two jobs - the upload and
    # the clips - and this used to run between them.
    retire_source()

    # Disk cleanup LAST: the optimizer above reads the cached transcript,
    # so removing it any earlier would break the report.
    try:
        report = cleanup_after_upload(
            cfg, video_path, _censored.get("path"),
            results=results, since_ts=run_started_at,
            active_platforms=active_platforms,
            # Clips are scored from the cached transcript. They are cut
            # above now rather than after this, but the cache is still
            # kept when clipping is on: a re-run over the same stream
            # would otherwise pay for a second transcription.
            keep_transcript=bool(
                not is_clip and (cfg.clips or {}).get("auto_from_streams")))
        freed = report.freed_mb
        keep = int((cfg.general.cleanup or {}).get("keep_uploaded_videos", 0) or 0)
        if keep > 0:
            freed += prune_uploaded_folder(cfg, keep)
        if freed >= 1:
            print(f"[Cleanup] Freed {freed:.0f} MB of working files.")
        for what, reason in report.kept:
            print(f"[Cleanup] Kept {what}: {reason}.")
    except Exception as exc:
        print(f"[Cleanup] WARNING: cleanup failed: {exc}")

    stage_timer.mark("cleanup")
    if stage_timer.enabled:
        print(f"[Timing] {stage_timer.summary()}")
        print(f"[Timing] {filename}: total wall time "
              f"{time.time() - run_started_at:.1f}s")

    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Auto-upload streams to YouTube + Rumble.")
    # Optional value, same as --batch. Passing the folder on the command
    # line means re-extracting the ZIP (which overwrites config.json, and
    # therefore any watch_folder edit) can't silently point this at the
    # wrong place.
    parser.add_argument("--watch", nargs="?", const="", metavar="FOLDER",
                        help="Watch a folder for new videos and upload them "
                             "(defaults to general.watch_folder from config.json).")
    parser.add_argument("--dry-run", action="store_true", help="Preview titles/descriptions; upload nothing.")
    parser.add_argument("--file", help="Upload one specific video file now.")
    parser.add_argument("--title", help="Stream title to use with --file (skips the interactive prompt).")
    # Optional value: bare `--batch` processes the configured watch_folder,
    # which is the common case (drop files in, run it once). Pass a path to
    # point it somewhere else for a one-off, e.g. --batch "D:\videos stizz".
    parser.add_argument("--batch", nargs="?", const="",
                        help="Process every supported video already in a folder "
                             "(defaults to general.watch_folder from config.json).")
    parser.add_argument("--test-config", action="store_true", help="Validate config.json/.env, then exit.")
    parser.add_argument("--only", choices=("youtube", "rumble"), metavar="PLATFORM",
                        help="Upload to just one platform (youtube|rumble). The "
                             "other is skipped entirely, and 'done' means done "
                             "for the selected platform only.")
    parser.add_argument("--forget", metavar="FILE_OR_TITLE",
                        help="Erase a video's upload history so it can be "
                             "retried. Takes a path, or just words from the "
                             "stream title - the tool moves videos between "
                             "folders itself, so it looks the file up for you "
                             "(use --forget-platform to target one platform).")
    parser.add_argument("--forget-platform", choices=("youtube", "rumble"),
                        help="With --forget, only clear this platform.")
    parser.add_argument("--health", action="store_true", help="Run disk/CPU/network health checks + temp cleanup, then exit.")
    parser.add_argument("--clips", metavar="FILE",
                        help="Render vertical clips with burned-in captions from "
                             "an already-uploaded video, ready to post by hand.")
    parser.add_argument("--clip-report", action="store_true",
                        help="What happened to every clip: cut, posted, "
                             "waiting, or failed and why. Reads "
                             "logs/clips.log.")
    parser.add_argument("--retry-clips", nargs="?", const="all",
                        metavar="PLATFORM",
                        help="Clips that gave up while a token was broken. "
                             "Shows what it would put back in the queue and "
                             "changes nothing; add --now to actually do it. "
                             "Optionally name one platform.")
    parser.add_argument("--retry-age", type=float, default=None,
                        metavar="DAYS",
                        help="With --retry-clips: skip clips older than this. "
                             "Defaults to the same limit the queue itself "
                             "uses (36 hours) - past that the next drain "
                             "drops them again whatever this says.")
    parser.add_argument("--setup-zernio", action="store_true",
                        help="List the accounts your Zernio key can post to "
                             "and write the X one into config.json. Needs "
                             "ZERNIO_API_KEY in .env. Reads only; posts "
                             "nothing.")
    parser.add_argument("--backfill", metavar="PLATFORM",
                        help="Queue clips that were cut BEFORE this platform "
                             "was switched on. Enabling a platform does not "
                             "reach back on its own, so clips already posted "
                             "elsewhere never get offered to it. Shows what "
                             "it would do; add --now to do it.")
    parser.add_argument("--learn", action="store_true",
                        help="Look up how many views the posted clips got, "
                             "and say what the numbers suggest about which "
                             "clips work. Reads only; posts nothing. Says "
                             "nothing at all until there are enough measured "
                             "clips to mean something.")
    parser.add_argument("--keep-source", action="store_true",
                        help="Never move or delete the source video, whatever "
                             "cleanup.source_video says. For uploading out of "
                             "a library folder you want left exactly as it is.")
    parser.add_argument("--clips-from", metavar="FOLDER_OR_URL",
                        help="Cut clips from every video in a folder of old "
                             "VODs, or from your own channel's recent uploads "
                             "if given a URL. A folder is only ever READ - "
                             "nothing in it is moved, renamed or deleted.")
    parser.add_argument("--limit", type=int, default=None,
                        help="With a --clips-from URL: how many recent videos "
                             "to take (default 3). Each is a download plus a "
                             "full transcription, so start small.")
    parser.add_argument("--clip-count", type=int, default=None,
                        help="How many clips to make with --clips (default 3).")
    parser.add_argument("--mode", metavar="NAME", default=None,
                        help="Routing for this run, overriding config.json. "
                             "'full_rumble_clean_youtube' sends the UNCENSORED "
                             "full VOD to Rumble and the CENSORED copy to "
                             "YouTube, with clips going to Shorts. Omit to "
                             "keep the behaviour already configured.")
    parser.add_argument("--trim-silence", dest="trim_silence",
                        action="store_true", default=None,
                        help="Cut dead air out of the censored copy before it "
                             "uploads. Only removes stretches with no speech "
                             "AND quiet audio.")
    parser.add_argument("--no-trim-silence", dest="trim_silence",
                        action="store_false",
                        help="Keep the full length even if the mode or config "
                             "asks for a trim.")
    parser.add_argument("--setup-shorts", action="store_true",
                        help="Sign in to the YouTube channel that Shorts go "
                             "to. A YouTube token is bound to the CHANNEL "
                             "picked on the consent screen, not the account - "
                             "so pick the Shorts channel here, not the VOD "
                             "one, or Shorts will land on the wrong channel.")
    parser.add_argument("--tidy-vods", action="store_true",
                        help="After clipping, delete the VODs this run "
                             "DOWNLOADED. Only ever applies to a --clips-from "
                             "URL: a folder you pointed at is your library and "
                             "is never touched. For the daily task, where "
                             "three VODs a day at 3-5 GB each fills a drive in "
                             "a week. The archive still remembers them, so "
                             "nothing is fetched twice.")
    parser.add_argument("--profile", metavar="KIND", default=None,
                        choices=("monkey", "gta", "whole"),
                        help="Framing for THIS run, ignoring config.json: "
                             "monkey = two people on camera beside a browser, "
                             "keep that rectangle; gta = gameplay, crop follows "
                             "the action; "
                             "whole = keep the entire frame on a blurred "
                             "background. A folder of mixed VODs needs one run "
                             "per kind - a single profile crops the other kind "
                             "wrong, and that is invisible until the clips are "
                             "already posted.")
    parser.add_argument("--gpu-check", action="store_true",
                        help="Report whether the censor pass will use the GPU, and "
                             "load the configured model to prove it. No upload.")
    parser.add_argument("--posting-status", action="store_true",
                        help="Show what social posting would do right now: kill switch, "
                             "per-platform caps, and which credentials are in .env. Posts nothing.")
    parser.add_argument("--verify", action="store_true",
                        help="With --posting-status, also ask each API who your token "
                             "belongs to. Read-only - creates and publishes nothing.")
    parser.add_argument("--preview-crop", metavar="FILE",
                        help="Write before/after stills showing exactly what "
                             "the clip framing will keep, so the rectangle can "
                             "be aimed in seconds instead of after ten renders.")
    parser.add_argument("--check-sync", metavar="FILE", nargs="?", const="",
                        help="Measure whether captions will line up with the "
                             "audio in clips cut from this video, and say "
                             "which of the possible causes it is. Cuts two "
                             "short test clips and compares where the sound "
                             "actually lands against where the transcript "
                             "says it should.")
    parser.add_argument("--recaption", action="store_true",
                        help="Rewrite the caption on every clip still "
                             "waiting to post, using the current title "
                             "wording and tags. The queue used to freeze a "
                             "caption when the clip was queued, so a backlog "
                             "kept publishing text written before the last "
                             "fix. Shows before and after for each one.")
    parser.add_argument("--check-llm", action="store_true",
                        help="Ask the configured model provider one question, "
                             "to prove the key in .env actually works. A wrong "
                             "key looks exactly like a working one until the "
                             "clips come out picked by the fallback scorer.")
    parser.add_argument("--post-queue", action="store_true",
                        help="Post whatever clips are waiting on a platform's "
                             "spacing and are now due, then stop. --watch does "
                             "this on a timer; this is for checking it by hand.")
    parser.add_argument("--post-reel", metavar="FILE",
                        help="Publish one video to Instagram as a Reel, now. "
                             "Uploads the file directly - no hosting needed. "
                             "Use it to prove Instagram works before trusting "
                             "it to the pipeline.")
    parser.add_argument("--caption", default="",
                        help="Caption for --post-reel.")
    parser.add_argument("--now", action="store_true",
                        help="With --post-reel: skip the minimum gap between "
                             "posts and publish immediately. For testing one "
                             "clip by hand. The daily cap, the kill switch and "
                             "the circuit breaker all still apply.")
    parser.add_argument("--find-clips", action="store_true",
                        help="List clips of you across X, TikTok and Facebook - "
                             "who posted, when, how many views. Downloads "
                             "nothing and posts nothing; you decide per clip.")
    parser.add_argument("--find-limit", type=int, default=20,
                        help="Most results per source for --find-clips.")
    parser.add_argument("--browser", default="", metavar="NAME",
                        help="With --find-clips: read cookies from this "
                             "browser (chrome, edge, firefox) so signed-in "
                             "pages can be listed. Still lists only.")
    parser.add_argument("--cookies", default="", metavar="FILE",
                        help="With --find-clips: a cookies.txt exported from "
                             "your browser. Use when --browser cannot read "
                             "them (Chrome 127+ locks its own store).")
    parser.add_argument("--study-instagram", action="store_true",
                        help="Read your own recent Instagram captions and "
                             "print a caption_template matching how you "
                             "actually post. Reads only; changes nothing.")
    parser.add_argument("--set-env", nargs="+", metavar="KEY=VALUE",
                        help="Write credentials into .env without opening an "
                             "editor, e.g. --set-env TWITTER_ACCESS_TOKEN=abc "
                             "TWITTER_ACCESS_SECRET=def. Backs .env up first "
                             "and leaves every other line alone.")
    parser.add_argument("--setup-meta", action="store_true",
                        help="Fill in FB_PAGE_TOKEN/FB_PAGE_ID/IG_* in .env from a "
                             "Meta token you already have. Reads from Graph, writes "
                             "to .env, posts nothing.")
    parser.add_argument("--meta-token", metavar="TOKEN",
                        help="Use this Meta token for --setup-meta instead of "
                             "looking for one in .env.")
    parser.add_argument("--meta-page", metavar="NAME",
                        help="Which Page to use, when the token can see several.")
    parser.add_argument("--reset-failures", nargs="?", const="all", metavar="PLATFORM",
                        help="Clear a tripped circuit breaker (all platforms, or one "
                             "named). Use after fixing whatever was failing.")
    announce = parser.add_mutually_exclusive_group()
    announce.add_argument("--announce-all", action="store_true",
                          help="Announce this upload everywhere at once - Discord, "
                               "Facebook, Reddit - without editing config. Says which "
                               "platforms stay manual and why.")
    announce.add_argument("--no-announce", action="store_true",
                          help="Announce nowhere this run: no Discord, no Reddit, no "
                               "Facebook/Instagram/X. Config is not changed.")
    args = _parse_args_helpfully(parser, argv)

    # Printed on EVERY run, not just --test-config: a stale extract running
    # old code has silently caused several confusing "the fix did nothing"
    # sessions, and the build stamp settles it in one line.
    print(f"AutoBleep auto-uploader | Build: {BUILD}")

    config_dir = os.path.dirname(os.path.abspath(__file__))
    # config.json is NOT tracked in git - config.example.json is. Your
    # settings are yours, and a pull must never collide with a switch you
    # flipped. First run copies the example across.
    config_file = os.path.join(config_dir, "config.json")
    example_file = os.path.join(config_dir, "config.example.json")
    if not os.path.isfile(config_file) and os.path.isfile(example_file):
        shutil.copyfile(example_file, config_file)
        print(f"[Config] First run - created config.json from the example.")
        print(f"[Config] It is yours now; git will not touch it again.")
    for line in merge_new_settings(config_file, example_file):
        print(f"[Config] {line}")
    cfg = load_config(config_file, os.path.join(config_dir, ".env"))

    # Applied to the loaded config only - config.json is never rewritten, so
    # the flag governs this run and nothing after it.
    # A named mode is applied to the LOADED config only - config.json is
    # never rewritten, so a one-off run cannot silently change what every
    # later run does. Everything it touches is a value that already
    # existed; the mode just sets several of them together and says so.
    wanted_mode = args.mode if args.mode is not None else cfg.mode
    if wanted_mode:
        settings = (cfg.modes or {}).get(wanted_mode)
        if settings is None:
            print(f"[ERROR] Unknown mode '{wanted_mode}'. Known: "
                  f"{', '.join(sorted(cfg.modes or {})) or '(none configured)'}")
            return 1
        cfg = _apply_mode(cfg, wanted_mode, settings)

    if args.trim_silence is not None:
        cfg.clips = dict(cfg.clips or {})
        cfg.clips["trim_silence"] = bool(args.trim_silence)

    if args.profile:
        cfg.clips = dict(cfg.clips or {})
        cfg.clips["profile"] = args.profile
        # An explicit crop_strategy outranks a profile, so a leftover one
        # in config.json would quietly win over the flag just typed.
        cfg.clips["crop_strategy"] = ""
        print(f"[Clips] Framing for this run: {args.profile}")

    if args.announce_all or args.no_announce:
        from utils.social_promoter import (
            disable_all_announcements, enable_all_announcements)
        switch = enable_all_announcements if args.announce_all else disable_all_announcements
        promoter, posting, notes = switch(
            cfg.features.get("social_promoter", {}), cfg.posting)
        cfg.features["social_promoter"] = promoter
        cfg.posting = posting
        label = "ON everywhere it can run" if args.announce_all else "OFF everywhere"
        print(f"[Social] Announcements: {label} for this run.")
        for note in notes:
            print(f"[Social]   {note}")

    if args.post_reel:
        from publishers.instagram import InstagramPublisher

        if not os.path.isfile(args.post_reel):
            print(f"[ERROR] File not found: {args.post_reel}")
            for path in _suggest_paths(cfg, os.path.basename(args.post_reel)):
                print(f"        Did you mean: {path}")
            # A basename match is no help when the guess was the wrong
            # NAME rather than the wrong folder. Reels only take short
            # videos anyway, so listing every clip lying around turns a
            # dead end into a menu.
            candidates = _find_clips(cfg)
            if candidates:
                print("\n        Clips found (short enough to be a Reel):")
                for path in candidates:
                    print(f"          {path}")
            return 1

        seconds = media_duration(args.post_reel) or 0
        if seconds > 15 * 60:
            print(f"[Instagram] {os.path.basename(args.post_reel)} is "
                  f"{seconds / 60:.0f} min. Reels cap at 15 - upload a clip, "
                  "not a full stream.")
            return 1

        # The guard still decides, even for a hand-run post: the cap and
        # spacing exist to protect the account, and a manual trigger is
        # exactly when they get walked past.
        from publish_guard import PublishGuard
        guard = PublishGuard(cfg.posting, (cfg.posting or {}).get("state_path"))
        allowed, reason = guard.can_post("instagram",
                                         ignore_spacing=args.now)
        if not allowed:
            print(f"[Instagram] blocked: {reason}")
            return 1

        # Routed through the same function the pipeline uses, so a hand
        # test proves the real thing - caption template, 9:16 re-frame
        # and all - rather than a simpler path that happens to work.
        from utils.social_promoter import post_clip_to_instagram

        print(f"[Instagram] {os.path.basename(args.post_reel)} ({seconds:.0f}s)")
        ok = post_clip_to_instagram(
            cfg.posting, args.post_reel, args.caption,
            config={"features": cfg.features, "instagram": cfg.instagram,
                    "clips": cfg.clips},
            ignore_spacing=args.now)
        if ok:
            print("[Instagram] Published. Check the account.")
            return 0
        print("[Instagram] Failed - the reason is in the log above.")
        return 1

    if args.find_clips:
        from utils.clip_finder import run

        sources = (cfg.clips or {}).get("find_sources") or []
        if not sources:
            print("[Find] No sources. Add clips.find_sources to config.json - "
                  "search URLs or profile URLs, one per entry.")
            return 1
        report = os.path.join(cfg.general.logs_folder, "clips_found.txt")
        run(sources, limit=args.find_limit, report_path=report,
            browser=args.browser, cookies_file=args.cookies)
        print("\n[Find] Nothing was downloaded and nothing was posted. These "
              "are other people's cuts and captions - ask before reposting, "
              "or credit the poster.")
        return 0

    if args.study_instagram:
        from utils.meta_setup import (MetaError, recent_captions,
                                      study_captions, suggest_template)

        token = os.environ.get("IG_PAGE_TOKEN", "")
        account = os.environ.get("IG_BUSINESS_ACCOUNT_ID", "")
        if not token or not account:
            print("[Instagram] IG_PAGE_TOKEN and IG_BUSINESS_ACCOUNT_ID must "
                  "be set. Run: python main.py --setup-meta")
            return 1
        try:
            captions = recent_captions(account, token)
        except MetaError as exc:
            print(f"[Instagram] {exc}")
            return 1
        if not captions:
            print("[Instagram] No captioned posts came back to learn from.")
            return 1

        study = study_captions(captions)
        print(f"[Instagram] Read {study['sampled']} of your own captions.")
        for label, items in (("Hashtags", study["hashtags"]),
                             ("Emoji run", study["emoji"])):
            for value, count in items:
                print(f"[Instagram]   {label}: {value}  ({count}x)")
        for (text, url), count in study["links"]:
            print(f"[Instagram]   Link: {text or '(no label)'} -> {url}  ({count}x)")

        print("\n--- caption_template ---")
        print(suggest_template(study))
        print("------------------------")
        print("\nPaste that into config.json under \"instagram\" -> "
              "\"caption_template\" if you want it. {title} is filled in "
              "with the clip name.")
        return 0

    if args.set_env:
        from utils.meta_setup import update_env

        values, bad = {}, []
        for pair in args.set_env:
            key, sep, value = pair.partition("=")
            key = key.strip()
            if not sep or not key:
                bad.append(pair)
                continue
            # Quotes survive a copy/paste from a credentials page and
            # would be stored as part of the secret.
            values[key] = value.strip().strip('"').strip("'")
        if bad:
            print("[Env] Not in KEY=VALUE form, ignored: " + ", ".join(bad))
        if not values:
            print("[Env] Nothing to write.")
            return 1

        env_path = os.path.join(config_dir, ".env")
        backup = update_env(env_path, values)
        for key, value in values.items():
            # Never the secret itself - this scrolls back in a terminal
            # and ends up in screenshots.
            shown = f"{value[:4]}...{value[-4:]}" if len(value) > 12 else "(set)"
            print(f"[Env] {key} = {shown}")
        if backup:
            print(f"[Env] Previous .env saved as {os.path.basename(backup)}")
        print("[Env] Check it with: python main.py --posting-status --verify")
        print("[Env] If a platform was failing, clear its breaker: "
              "python main.py --reset-failures")
        return 0

    if args.setup_meta:
        from utils.meta_setup import MetaError, WRITES, setup

        env_path = os.path.join(config_dir, ".env")
        try:
            result = setup(env_path, args.meta_token or "", args.meta_page or "")
        except MetaError as exc:
            print(f"[Meta] {exc}")
            return 1
        print(f"[Meta] Token found via {result['source']}.")
        print(f"[Meta] Page: {result['page_name']} ({result['values']['FB_PAGE_ID']})")
        for key in WRITES:
            value = result["values"].get(key)
            if value:
                # Never the token itself. A .env is pasted into chat logs
                # and screenshots more often than anyone means to.
                shown = f"{value[:6]}...{value[-4:]}" if "TOKEN" in key else value
                print(f"[Meta]   {key} = {shown}")
            else:
                print(f"[Meta]   {key} = (not set)")
        for warning in result["warnings"]:
            print(f"[Meta] NOTE: {warning}")
        if result["backup"]:
            print(f"[Meta] Previous .env saved as {os.path.basename(result['backup'])}")
        if result.get("exchanged"):
            print("[Meta] Traded the short-lived token for a long-lived one - "
                  "this is the step that stops Facebook expiring overnight.")
        expiry = result.get("page_token_expiry", "")
        if expiry:
            print(f"[Meta] Page token: {expiry}.")
            if "never" not in expiry:
                print("[Meta] It will expire. To make it permanent, set the "
                      "app credentials once and run this again:")
                print("[Meta]   python main.py --set-env FB_APP_ID=... "
                      "FB_APP_SECRET=...")
                print("[Meta]   (developers.facebook.com -> your app -> "
                      "Settings -> Basic)")
        print("[Meta] Done. Check it with: python main.py --posting-status --verify")
        return 0

    if args.reset_failures:
        from publish_guard import PublishGuard
        guard = PublishGuard(cfg.posting, (cfg.posting or {}).get("state_path"))
        target = None if args.reset_failures == "all" else args.reset_failures
        before = {p: guard.consecutive_failures(p)
                  for p in (cfg.posting.get("platforms") or {})}
        guard.reset_failures(target)
        tripped = {p: n for p, n in before.items() if n}
        if tripped:
            for platform, count in tripped.items():
                if target in (None, platform):
                    print(f"[Posting] Cleared {platform} ({count} consecutive failures).")
        else:
            print("[Posting] No circuit breaker was tripped - nothing to clear.")
        return 0

    if args.forget:
        matches = _find_video(cfg, args.forget)
        if not matches:
            print(f"[ERROR] No video found for: {args.forget}")
            print("        Give a path, or words from the stream title.")
            return 1
        # The same video in watch_folder AND uploaded/ is not ambiguity -
        # it is this tool's own filing. It moves a video between those two
        # folders, and upload history is keyed on the filename, so both
        # copies clear the very same record. Refusing there sent the user
        # to type out a 90-character path for no decision at all.
        names = {os.path.basename(p) for p in matches}
        if len(names) > 1:
            print(f"[ERROR] {len(names)} different videos match: {args.forget}")
            for path in matches:
                print(f"        {path}")
            print("        Add more words from the title, or pass the path.")
            return 1
        if len(matches) > 1:
            print(f"Matched the same video in {len(matches)} folders - "
                  f"they share one upload record.")
        target_file = matches[0]
        if target_file != args.forget:
            print(f"Matched: {target_file}")
        checker = DuplicateChecker(cfg.general.duplicate_store_path)

        # Filename lookup first: hashing a multi-GB video off an external
        # drive takes minutes, and the store already records the filename.
        targets = checker.find_hashes_by_filename(target_file)
        if not targets:
            size_gb = os.path.getsize(target_file) / (1024 ** 3)
            print(f"No record under that filename; identifying by content "
                  f"instead ({size_gb:.1f} GB to read, this can take a minute)...")
            targets = [hash_file(target_file)]

        scope = args.forget_platform or "both platforms"
        if any(checker.forget(t, args.forget_platform) for t in targets):
            print(f"Forgot {os.path.basename(target_file)} ({scope}).")
            print(f"Upload it again with:")
            only = f" --only {args.forget_platform}" if args.forget_platform else ""
            print(f'    python auto_uploader/main.py --file "{target_file}"'
                  f'{only} --keep-source')
            return 0
        print(f"No upload history recorded for {os.path.basename(target_file)} "
              f"({scope}) - nothing to clear. You can run --file on it directly.")
        return 0

    if args.setup_zernio:
        from publishers.zernio import ZernioError, ZernioPublisher

        publisher = ZernioPublisher({"zernio": cfg.zernio})
        if not publisher.token():
            print("[Zernio] No API key. Add it to .env first:")
            print("         python main.py --set-env ZERNIO_API_KEY=sk_...")
            return 1
        try:
            accounts = publisher.accounts()
        except ZernioError as exc:
            print(f"[Zernio] Could not read your accounts: {exc}")
            return 1
        if not accounts:
            print("[Zernio] The key works, but no social accounts are "
                  "connected yet.")
            print("         Connect X at zernio.com, then run this again.")
            return 0

        from publishers.zernio import DESTINATIONS, NOT_AUTOMATED

        wanted = set(DESTINATIONS.values())
        print(f"[Zernio] {len(accounts)} account(s) on this key:")
        found: dict = {}
        for account in accounts:
            platform = str(account.get("platform", "?"))
            handle = str(account.get("username")
                         or account.get("displayName") or "?")
            if platform in NOT_AUTOMATED:
                note = "  (left to you - see the config comment)"
            elif platform in wanted and platform not in found:
                found[platform] = str(account.get("_id")
                                      or account.get("id") or "")
                note = "  <-- will post clips"
            else:
                note = ""
            print(f"           {platform:<12} {handle}{note}")

        if not found:
            print("[Zernio] None of these is a destination this posts to "
                  f"({', '.join(sorted(wanted))}). Connect one at zernio.com.")
            return 1

        config_file = os.path.join(config_dir, "config.json")
        try:
            with open(config_file, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            block = raw.setdefault("zernio", {})
            accounts_block = block.setdefault("accounts", {})
            for platform, account_id in found.items():
                accounts_block.setdefault(platform, {})["account_id"] = account_id
            with open(config_file, "w", encoding="utf-8") as handle:
                json.dump(raw, handle, indent=2, ensure_ascii=False)
        except (OSError, ValueError) as exc:
            print(f"[Zernio] Found the accounts but could not save them: {exc}")
            for platform, account_id in found.items():
                print(f"         zernio.accounts.{platform}.account_id = "
                      f"{account_id}")
            return 1

        print(f"[Zernio] Saved {len(found)} account id(s) to config.json.")
        for destination, platform in sorted(DESTINATIONS.items()):
            if platform not in found:
                continue
            settings = (cfg.posting.get("platforms") or {}).get(destination, {})
            print(f"[Zernio]   posting.platforms.{destination}.enabled = true"
                  f"   ({settings.get('daily_cap', '?')}/day, every "
                  f"{settings.get('min_minutes_between', '?')} min)")
        return 0

    if args.backfill:
        from job_queue import JobQueue
        from utils.clip_queue import CLIP_PLATFORMS, MAX_DEFERRED_AGE_S
        from utils.clip_queue import caption_for

        platform = str(args.backfill).strip().lower()
        if platform not in CLIP_PLATFORMS:
            print(f"[Backfill] Not a clip platform: {platform}")
            print(f"           Try one of: {', '.join(CLIP_PLATFORMS)}")
            return 1

        queue = JobQueue(path=cfg.posting.get("queue_path"))
        jobs = queue.list_jobs()
        have = {j.clip_path for j in jobs if j.platform == platform}
        # Every clip the queue knows about, whichever platform put it
        # there. That set IS the record of what has been cut.
        known = {}
        for job in jobs:
            known.setdefault(job.clip_path, job)

        cutoff = time.time() - MAX_DEFERRED_AGE_S
        missing, gone, stale = [], [], []
        for path, job in known.items():
            if path in have:
                continue
            if not os.path.isfile(path):
                gone.append(path)
            elif job.created_at and job.created_at < cutoff:
                stale.append(path)
            else:
                missing.append(path)

        print(f"[Backfill] {len(known)} clip(s) known, "
              f"{len(have)} already offered to {platform}.")
        if gone:
            print(f"[Backfill] {len(gone)} skipped - the clip file is gone.")
        if stale:
            print(f"[Backfill] {len(stale)} skipped - past the "
                  f"{MAX_DEFERRED_AGE_S / 3600:.0f}h the queue will hold a clip.")
        if not missing:
            print(f"[Backfill] Nothing to add for {platform}.")
            return 0

        if not args.now:
            for path in missing[:10]:
                print(f"             {os.path.basename(path)}")
            if len(missing) > 10:
                print(f"             ... and {len(missing) - 10} more")
            print(f"[Backfill] Would queue {len(missing)} for {platform}. "
                  f"Nothing changed - add --now to do it.")
            return 0

        clip_cfg = _clip_config(cfg)
        for path in missing:
            queue.enqueue(platform, path,
                          caption_for(platform, path, "", clip_cfg))
        print(f"[Backfill] Queued {len(missing)} for {platform}.")
        print(f"[Backfill] The daily cap and spacing still apply, so these go "
              f"out over days. --watch drains them.")
        return 0

    if args.retry_clips:
        from job_queue import FAILED, JobQueue

        queue = JobQueue(path=cfg.posting.get("queue_path"))
        wanted = str(args.retry_clips).lower()
        jobs = [j for j in queue.list_jobs(FAILED)
                if wanted in ("all", j.platform.lower())]
        if not jobs:
            print("[Retry] Nothing has given up." if wanted == "all"
                  else f"[Retry] Nothing has given up on {wanted}.")
            return 0

        # The queue drops anything past MAX_DEFERRED_AGE_S on the next
        # drain. Requeuing something older than that is a promise the
        # very next run breaks - it would report 32 revived and post
        # none of them.
        from utils.clip_queue import MAX_DEFERRED_AGE_S

        limit_days = (MAX_DEFERRED_AGE_S / 86400.0 if args.retry_age is None
                      else args.retry_age)
        if args.retry_age is not None and args.retry_age > MAX_DEFERRED_AGE_S / 86400.0:
            print(f"[Retry] NOTE: the queue drops clips over "
                  f"{MAX_DEFERRED_AGE_S / 3600:.0f}h old on the next drain, "
                  f"so anything revived past that will go straight back to "
                  f"given-up.")
        eligible, gone, stale = JobQueue.sort_retryable(
            jobs, limit_days * 86400)

        print(f"[Retry] {len(jobs)} clip(s) gave up. Why they stopped:")
        reasons: dict = {}
        for job in jobs:
            key = (job.last_error or "no reason recorded")[:70]
            reasons[key] = reasons.get(key, 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"         {count:>3}  {reason}")

        if gone:
            print(f"[Retry] {len(gone)} skipped - the clip file is gone.")
        if stale:
            print(f"[Retry] {len(stale)} skipped - older than "
                  f"{limit_days * 24:.0f}h, which is the point the queue "
                  f"drops them anyway. Cut fresh clips instead: "
                  f"--clips FILE.")
        if not eligible:
            print("[Retry] Nothing left to put back.")
            return 0

        if not args.now:
            print(f"[Retry] Would requeue {len(eligible)}. "
                  f"Nothing changed - add --now to do it.")
            return 0

        for job in eligible:
            queue.retry(job.id)
        print(f"[Retry] Requeued {len(eligible)}.")
        print("[Retry] The daily caps and spacing still apply, so these go "
              "out over days, not all at once. --watch drains them, or run "
              "--post-queue.")
        return 0

    if args.learn:
        from autoreel.memory import Ledger, harvest, learn, ledger_path

        path = ledger_path()
        ledger = Ledger(path)
        total = len(ledger.records())
        if not total:
            print("[Learn] Nothing remembered yet. Clips cut from now on are "
                  "recorded automatically; this reads that record.")
            return 0

        pending = len(ledger.unchecked())
        print(f"[Learn] {total} clip(s) remembered, {pending} not yet counted.")
        if pending:
            print("[Learn] Asking each platform how the posted ones did...")
            filled = harvest(ledger, say=print)
            print(f"[Learn] Counted {filled}." if filled
                  else "[Learn] Could not read any view counts this time.")

        print()
        print(learn(ledger).summary())
        print()
        print(f"[Learn] Memory: {path}")
        return 0

    if args.health:
        ok = run_health_check(cfg, cfg.features.get("self_healing", {}))
        return 0 if ok else 1

    if args.gpu_check:
        import time as _t

        from autoreel.transcription import Transcriber, detect_device

        device, label = detect_device()
        print(f"\nDetected      : {device.upper()} ({label})")
        print(f"Configured    : censor_device={cfg.general.censor_device or 'auto'}, "
              f"model={cfg.general.censor_model}")

        wanted = cfg.general.censor_device or device
        if wanted == "cuda" and device != "cuda":
            print("\n[WARN] config asks for CUDA but no GPU was detected. Either "
                  "torch is not installed (pip install torch) or the driver is "
                  "not visible. The censor pass will fall back to CPU.")

        print(f"\nLoading {cfg.general.censor_model} on {wanted}... "
              "(first run downloads the model)")
        started = _t.time()
        transcriber = Transcriber(model_name=cfg.general.censor_model,
                                  device=cfg.general.censor_device)
        try:
            transcriber._load()
        except Exception as exc:
            print(f"[FAIL] Could not load the model: {exc}")
            return 1
        print(f"[OK] Loaded on {transcriber._resolved_device.upper()} "
              f"in {_t.time() - started:.0f}s using "
              f"{transcriber._resolved_compute or 'default'} precision.")
        if transcriber._resolved_device != "cuda" and wanted == "cuda":
            print("     It fell back to CPU - censoring will work but be slow.")
            print("     Fix with: pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
        transcriber.release()
        return 0

    if args.clips:
        from utils.clip_runner import make_clips, print_run

        if not os.path.isfile(args.clips):
            print(f"[ERROR] File not found: {args.clips}")
            for path in _suggest_paths(cfg, os.path.basename(args.clips)):
                print(f"        Did you mean: {path}")
            return 1
        title = get_stream_title(args.clips, args.title, cfg, allow_prompt=False)
        # transcribe_if_needed, because --clips is now also how ONE video
        # gets its own framing: a folder of mixed VODs needs a run per
        # kind, and each of those videos is a fresh download with no
        # transcript beside it yet. Without this it failed on a raw VOD
        # while --clips-from over the same file worked, which reads as
        # the file being broken.
        run = make_clips(cfg, args.clips, title, count=args.clip_count,
                         transcribe_if_needed=True)
        print_run(run)
        _deliver_clips(run, cfg)
        return 0

    if args.setup_shorts:
        from publishers.youtube_shorts import YouTubeShortsPublisher

        publisher = YouTubeShortsPublisher({"youtube": cfg.youtube.__dict__
                                            if hasattr(cfg.youtube, "__dict__")
                                            else dict(cfg.youtube or {}),
                                            "youtube_shorts": cfg.youtube_shorts})
        secrets = publisher.client_secrets_path()
        token = publisher.token_path()
        if not secrets or not os.path.isfile(secrets):
            print(f"[Shorts] client_secrets.json not found at "
                  f"{secrets or '(not set)'}")
            print("         It is the same file the VOD uploader uses.")
            return 1
        if os.path.isfile(token):
            print(f"[Shorts] Already signed in ({token}).")
            print("         Delete that file and re-run to switch channel.")
            return 0

        print(f"[Shorts] A browser will open. Sign in and pick "
              f"{cfg.youtube_shorts.get('channel', 'the Shorts channel')}.")
        print("[Shorts] The token remembers whichever channel you choose, so "
              "picking the VOD channel here sends Shorts there instead.")
        # NOT `from ... import YouTubeUploader` - that binds the name as a
        # local for the WHOLE of main(), so the module-level import at the
        # top stops resolving and the YouTube dedup check hundreds of lines
        # above this dies with UnboundLocalError. It did exactly that, and
        # took the whole --batch run down with it.
        try:
            YouTubeUploader(secrets, token)._client()
        except Exception as exc:
            print(f"[Shorts] Sign-in failed: {exc}")
            return 1
        print(f"[Shorts] Signed in. Token saved to {token}")
        print("[Shorts] Uploads are PRIVATE until you set "
              "youtube_shorts.privacy to \"public\" in config.json.")
        print("[Shorts] Turn posting on with posting.platforms."
              "youtube_shorts.enabled = true")
        return 0

    if args.clip_report:
        from utils.clip_log import report

        print()
        print(report(cfg.general.logs_folder))
        return 0

    if args.clips_from:
        from utils.channel_vods import DEFAULT_LIMIT, fetch_channel, is_url
        from utils.clip_runner import make_clips, print_run

        if is_url(args.clips_from):
            # Your own channel's uploads, downloaded once each and kept.
            # The archive beside them is what makes this safe to re-run.
            folder = os.path.join(cfg.project_root, "downloaded_vods")
            limit = args.limit or DEFAULT_LIMIT
            print(f"[VODs] Fetching up to {limit} recent video(s) from "
                  f"{args.clips_from}")
            print(f"[VODs] Into {folder} - already-taken videos are skipped.")
            source_channel = args.clips_from
            grabbed, problem = fetch_channel(args.clips_from, folder,
                                             cfg.general.supported_formats,
                                             limit,
                                             browser=args.browser or "")
            if problem:
                print(f"[VODs] {problem}")
                return 1
            if not grabbed:
                print("[VODs] Nothing new - every recent video has been "
                      "taken before. Raise --limit to reach further back.")
                return 0
            print(f"[VODs] {len(grabbed)} new video(s) downloaded.")
        else:
            source_channel = ""
            folder = os.path.abspath(os.path.expanduser(args.clips_from))
        if not os.path.isdir(folder):
            print(f"[ERROR] --clips-from folder does not exist: {folder}")
            return 1

        try:
            names = sorted(os.listdir(folder))
        except OSError as exc:
            print(f"[ERROR] Could not read {folder}: {exc}")
            return 1

        videos = [
            os.path.join(folder, name) for name in names
            if os.path.splitext(name)[1].lower() in cfg.general.supported_formats
            and os.path.isfile(os.path.join(folder, name))
            # .part / .ytdl leftovers from an interrupted download are not
            # videos, and a half-file transcribes to nonsense.
            and not is_intermediate_download(os.path.join(folder, name))
        ]
        if not videos:
            print(f"[Clips] No finished videos in {folder}.")
            return 0

        wanted = args.clip_count or (cfg.clips or {}).get("count", 10)
        print(f"[Clips] {len(videos)} video(s) in {folder}")
        print(f"[Clips] Reading only - nothing in that folder is moved, "
              f"renamed or deleted.")
        total = 0
        clipped = []
        for index, path in enumerate(videos, start=1):
            name = os.path.basename(path)
            print(f"\n[Clips] ({index}/{len(videos)}) {name}")
            title = get_stream_title(path, "", cfg, allow_prompt=False)
            try:
                run = make_clips(cfg, path, title, count=wanted,
                                 notify=False, transcribe_if_needed=True)
            except Exception as exc:
                print(f"[Clips] skipped {name}: {exc}")
                continue
            print_run(run)
            delivered = _deliver_clips(run, cfg)
            total += delivered
            if delivered:
                clipped.append(path)
        print(f"\n[Clips] {total} clip(s) delivered to "
              f"{cfg.general.watch_folder}.")

        if args.tidy_vods and clipped:
            print(tidy_downloaded_vods(clipped, folder, cfg.project_root))
        print("[Clips] Start the uploader to post them on each platform's "
              "spacing:  python main.py --watch")
        return 0

    if args.posting_status:
        from publish_guard import PublishGuard
        from utils.clip_queue import summary
        from utils.posting_status import report

        guard = PublishGuard(cfg.posting, cfg.posting.get("state_path"))
        account = (cfg.features.get("social_promoter", {}) or {}).get(
            "reddit_account", "")
        # youtube_shorts and youtube come along so --verify can ask which
        # CHANNEL the Shorts token belongs to. That is the one setup
        # mistake nothing else catches.
        report({"posting": cfg.posting,
                "youtube_shorts": cfg.youtube_shorts,
            "zernio": cfg.zernio,
                "youtube": {"channel": cfg.youtube.channel,
                            "client_secrets_path": cfg.youtube.client_secrets_path}},
               guard, account, live=args.verify)
        print(f"\n  {summary(cfg.posting)}")
        return 0

    if args.check_sync is not None:
        # Naming the file is the step most likely to go wrong - the VODs
        # have long names with spaces and the folder differs per machine.
        # With no name, take the newest one there is.
        source = args.check_sync
        if not source:
            source = _newest_video(cfg)
            if not source:
                print("[Sync] No videos found. Give it a path:\n"
                      '         python main.py --check-sync "downloaded_vods'
                      '\\<the real filename>.mp4"')
                return 1
            print(f"[Sync] Newest video: {source}")
        elif not os.path.isfile(source):
            # Accepts part of a title as well as a path, same as the
            # other commands that take a video.
            found = _find_video(cfg, source)
            if not found:
                print(f"[Sync] File not found: {source}")
                return 1
            source = found[0]
            print(f"[Sync] Using {source}")
        return _check_sync(cfg, source)

    if args.recaption:
        from utils.clip_queue import recaption

        changed = recaption(cfg.posting, _clip_config(cfg))
        if not changed:
            print("[Clips] Nothing waiting needed rewording - every queued "
                  "clip already reads the way it would be written today.")
            return 0
        print(f"[Clips] Reworded {len(changed)} queued clip(s):\n")
        for platform, clip, before, after in changed:
            print(f"  {platform}  {os.path.basename(clip)}")
            print(f"    was: {before.splitlines()[0] if before else '(nothing)'}")
            print(f"    now: {after.splitlines()[0]}")
            extra = len(after.splitlines()) - 1
            if extra > 0:
                print(f"         (+{extra} more line(s), tags included)")
            print()
        return 0

    if args.preview_crop:
        from autoreel.crop_preview import describe, preview
        from autoreel.crop_strategy import resolve_crop_strategy, resolve_region

        source = args.preview_crop
        if not os.path.isfile(source):
            found = _suggest_paths(cfg, os.path.basename(source))
            if not found:
                print(f"[Crop] File not found: {source}")
                return 1
            source = found[0]

        clips = cfg.clips or {}
        strategy = resolve_crop_strategy({"clips": clips},
                                         clips.get("content_kind", "gameplay"))
        region = resolve_region({"clips": clips})
        out_dir = os.path.join(cfg.general.logs_folder, "crop_preview")
        print(f"[Crop] profile={clips.get('profile', 'monkey')} "
              f"strategy={strategy}")
        if strategy == "region":
            print(f"[Crop] keeping {describe(region)}")
        if strategy == "stack":
            from autoreel.crop_strategy import resolve_stack

            halves = resolve_stack({"clips": clips})
            print(f"[Crop] top pane:    {describe(halves['top'])}")
            print(f"[Crop] bottom pane: {describe(halves['bottom'])}")
        written = preview(source, out_dir, region, strategy=strategy)
        if not written:
            print("[Crop] Could not read that video (is ffmpeg on PATH?)")
            return 1
        print(f"[Crop] {len(written)} still(s) -> {out_dir}")
        if strategy == "stack":
            # The preview renders a single crop, so *_result is not the
            # stacked layout. Say so rather than letting a misleading
            # picture be trusted - the *_source stills are the useful
            # part here: they show the frame the two panes come out of.
            print("[Crop] Open a *_source.jpg and find the two people. The "
                  "panes are set in config.json under "
                  "clips.profiles.monkey.stack, as fractions of the frame:")
            print("[Crop]   x/y = top-left corner, 0.0 to 1.0 across and "
                  "down; width/height = how much of the frame the pane is.")
            print("[Crop]   'top' is whichever person you want on top of "
                  "the finished clip.")
        else:
            print("[Crop] *_source.jpg shows the red box on the original; "
                  "*_result.jpg is what the clip will look like.")
            print("[Crop] Adjust clips.crop_region in config.json "
                  "(fractions of the frame) and run this again.")
        return 0

    if args.check_llm:
        from autoreel.llm_highlights import check

        clips = cfg.clips or {}
        ok, detail = check(str(clips.get("llm_provider", "")),
                           str(clips.get("llm_model", "")))
        print(f"[LLM] {detail}")
        if not ok:
            print("[LLM] Clips will still be cut - the scorer picks them "
                  "instead. It is the model pass that adds the judgement.")
            # Which models the key CAN reach is the one thing that turns a
            # 404 on a model name into something actionable.
            from autoreel.llm_highlights import available, list_models, usable_models

            provider, key = available(str(clips.get("llm_provider", "")))
            reachable = usable_models(list_models(provider, key)) if key else []
            if reachable:
                print("[LLM] This key CAN reach: "
                      + ", ".join(reachable[:6]))
                print("[LLM] Pin one with clips.llm_model in config.json if "
                      "the automatic choice is wrong.")
            elif key:
                print("[LLM] The key could not list any models either - it is "
                      "the credential rather than the model name. Get one at "
                      "aistudio.google.com/apikey.")
        return 0 if ok else 1

    if args.post_queue:
        from utils.clip_queue import drain, summary

        print(f"[Clips] {summary(cfg.posting)}")
        posted = drain(cfg.posting, _clip_config(cfg),
                       dry_run=args.dry_run or cfg.general.dry_run_mode)
        if not posted:
            print("[Clips] Nothing was due to post right now.")
        else:
            for platform, count in sorted(posted.items()):
                print(f"[Clips] {platform}: posted {count}.")
        return 0

    if args.test_config:
        _report_clip_brain(cfg)
        print(f"  Censor before upload : {cfg.general.censor_before_upload} "
              f"(method: {cfg.general.censor_bleep_method})")
        print(f"  YouTube censored     : {cfg.youtube.censor_uploads}")
        print(f"  Rumble  censored     : {cfg.rumble.censor_uploads}")
        print(f"  Rumble  categories   : {cfg.rumble.primary_category} / {cfg.rumble.secondary_category}")
        print(f"  Watch folder         : {os.path.abspath(cfg.general.watch_folder)}")
        problems = validate_config(cfg)
        if problems:
            print("Config problems found:")
            for p in problems:
                print(f"  - {p}")
            return 1
        print("Config looks good. Folders created/verified. (This does not test real YouTube/Rumble login.)")
        return 0

    validate_config(cfg)  # ensures folders exist even outside --test-config
    dry_run = args.dry_run or cfg.general.dry_run_mode

    if args.keep_source:
        # Applied to the loaded config rather than threaded through every
        # call: "leave the originals alone" is a property of the whole
        # run, not of one file in it.
        cfg.general.cleanup = dict(cfg.general.cleanup or {})
        cfg.general.cleanup["source_video"] = "keep"
        print("[Cleanup] --keep-source: originals stay exactly where they are.")

    # None = flag not passed at all; "" = bare `--batch` (use the config's
    # watch folder); anything else = an explicit folder for this run only.
    # None = flag absent; "" = bare --watch (use config); else an explicit folder.
    watch_folder = cfg.general.watch_folder
    if args.watch is not None and args.watch:
        watch_folder = os.path.abspath(os.path.expanduser(args.watch))
        if not os.path.isdir(watch_folder):
            print(f"[ERROR] --watch folder does not exist: {watch_folder}")
            return 1

    # Checked here, before the channel fetches below: without it a typo'd
    # or moved path surfaced as a raw FileNotFoundError traceback out of
    # hash_file() - and only after a pointless round-trip to YouTube.
    if args.file and not os.path.isfile(args.file):
        print(f"[ERROR] File not found: {args.file}")
        hints = _suggest_paths(cfg, os.path.basename(args.file))
        if hints:
            print("        Did you mean:")
            for path in hints:
                print(f"          {path}")
        return 1

    batch_folder = None
    if args.batch is not None:
        batch_folder = os.path.abspath(args.batch or cfg.general.watch_folder)
        if not os.path.isdir(batch_folder):
            print(f"[ERROR] --batch folder does not exist: {batch_folder}")
            return 1
        print(f"Batch folder: {batch_folder}")
        if cfg.posting:
            try:
                from utils.clip_queue import drain

                drain(cfg.posting, _clip_config(cfg), dry_run=dry_run,
                      quiet=True)
            except Exception as exc:
                print(f"[Clips] WARNING: could not post the queue: {exc}")

    # Checked before the logging and the channel fetches below, so an
    # incomplete command answers instantly instead of spending two
    # network round trips to tell the user a word is missing.
    if not args.file and args.batch is None and args.watch is None and not dry_run:
        return _no_target(parser, args)

    # Before anything can post, so a failure explains itself the first
    # time rather than three uploads later as a tripped breaker.
    setup_publisher_logging(cfg.general.logs_folder)
    yt_logger = setup_logger("youtube", cfg.general.logs_folder)
    rb_logger = setup_logger("rumble", cfg.general.logs_folder)
    dup_checker = DuplicateChecker(cfg.general.duplicate_store_path)

    existing_youtube_videos = []
    existing_videos_fetch_failed = False
    # Runs for dry runs too: the whole point of previewing a backlog batch
    # is seeing which files would be skipped as already-on-YouTube, which
    # is impossible without this. Fetched once per run (not once per
    # file) - cheap (a couple of quota units regardless of channel size).
    try:
        yt_for_check = YouTubeUploader(cfg.youtube.client_secrets_path, cfg.youtube.token_path)
        existing_youtube_videos = fetch_existing_videos(yt_for_check.get_service())
        print(f"[YouTube] Found {len(existing_youtube_videos)} existing video(s) on the channel for dedup checks.")
    except Exception as exc:
        existing_videos_fetch_failed = True
        print(f"[WARN] Could not fetch existing YouTube videos ({exc}); dedup-by-date check will be skipped.")

    # --batch is the case where uploading everything blind is actually
    # dangerous (a folder full of old VODs, many likely already on
    # YouTube) - refuse to run it without the safety net rather than
    # silently uploading duplicates of everything. --file/--watch proceed
    # regardless, since duplicating one specific/freshly-recorded video is
    # much lower-stakes than blind-uploading an entire backlog folder.
    # Only blocks real runs - a dry run uploads nothing, so it's safe (and
    # useful) to let it proceed and preview titles/dates even when the
    # dedup check is unavailable.
    existing_rumble_videos = []
    if not dry_run and cfg.rumble.skip_if_exists:
        try:
            existing_rumble_videos = fetch_rumble_videos(cfg.rumble.rss_url, cfg.rumble.cdp_url)
            print(f"[Rumble] Found {len(existing_rumble_videos)} existing video(s) via RSS for dedup checks.")
        except Exception as exc:
            # Not a warning: the local hash/title history is the primary
            # defence and it is working. The feed only ever added cover
            # for videos put on Rumble by hand, so losing it degrades
            # nothing that was already protected.
            #
            # It says "no feed exists" rather than "the fetch failed"
            # because Rumble does not publish RSS at all - not
            # <page>/index.xml, not <page>/rss. The configured address
            # returns an ordinary web page with HTTP 200, which reads as
            # a Cloudflare challenge from the outside. Blaming Cloudflare
            # sent a whole evening chasing an impersonation problem that
            # did not exist, and telling you to open Chrome on a
            # debugging port cannot help fetch something that is not
            # there. Said once per run.
            _say_once("rumble-feed",
                      f"[Rumble] No channel feed to check - Rumble does not "
                      f"publish RSS, so the configured rss_url returns a web "
                      f"page. ({exc})\n"
                      f"         Dedup falls back to local upload history, "
                      f"which already covers everything this tool uploaded. "
                      f"The only gap is a video you put on Rumble by hand.")

    if batch_folder and existing_videos_fetch_failed and not dry_run:
        print(
            "[ABORTED] Refusing to run --batch without the existing-video dedup check working "
            "(it would risk re-uploading videos already on the channel). Fix the YouTube auth "
            "issue above, then try again. (--file and --watch aren't blocked by this.)"
        )
        return 1

    if args.file:
        process_file(args.file, cfg, args.title, dup_checker, yt_logger, rb_logger, dry_run,
                     existing_youtube_videos, existing_rumble_videos,
                     only_platform=args.only)
        return 0

    if batch_folder:
        # Count the videos BEFORE processing. A batch that finds nothing
        # used to look exactly like one that worked - it printed skip
        # lines for the non-videos and then simply ended, with no way to
        # tell "uploaded everything" from "there was nothing here".
        candidates = [
            f for f in sorted(os.listdir(batch_folder))
            if os.path.isfile(os.path.join(batch_folder, f))
            and os.path.splitext(f)[1].lower() in cfg.general.supported_formats
            and not is_intermediate_download(f)
        ]
        if not candidates:
            print(f"\n[Batch] No videos found in {batch_folder}")
            print(f"        Looked for: {', '.join(cfg.general.supported_formats)}")
            print(f"        Watch folder is {cfg.general.watch_folder} - try "
                  "`--batch` with no folder to use that instead.")
            return 0

        print(f"\n[Batch] {len(candidates)} video(s) to process:")
        for name in candidates:
            print(f"          - {name}")

        outcomes = []
        for fname in sorted(os.listdir(batch_folder)):
            path = os.path.join(batch_folder, fname)
            if os.path.isfile(path):
                result = process_file(path, cfg, None, dup_checker, yt_logger, rb_logger,
                                      dry_run, existing_youtube_videos,
                                      existing_rumble_videos, only_platform=args.only)
                if fname in candidates:
                    outcomes.append((fname, (result or {}).get("skipped")))

        uploaded = [n for n, skipped in outcomes if not skipped]
        skipped = [(n, why) for n, why in outcomes if why]
        print(f"\n[Batch] Done: {len(uploaded)} processed, {len(skipped)} skipped.")
        for name, why in skipped:
            print(f"          skipped {name} ({why})")
        return 0

    if args.watch is not None or dry_run:
        print(f"Watching {watch_folder} for new videos... (Ctrl+C to stop)")
        if dry_run:
            print("[DRY RUN MODE] Nothing will actually be uploaded.")

        # --watch only reacts to files ARRIVING. Anything already sitting
        # in the folder would otherwise wait here forever with no hint
        # that it's being ignored.
        try:
            already = [
                f for f in sorted(os.listdir(watch_folder))
                if os.path.isfile(os.path.join(watch_folder, f))
                and os.path.splitext(f)[1].lower() in cfg.general.supported_formats
                and not is_intermediate_download(f)
            ]
        except OSError:
            already = []
        if already:
            print(f"\n[NOTE] {len(already)} video(s) are ALREADY in this folder. "
                  f"--watch only picks up files that arrive from now on.")
            for f in already[:5]:
                print(f"         - {f}")
            if len(already) > 5:
                print(f"         ... and {len(already) - 5} more")
            hint = ("--batch" if watch_folder == os.path.abspath(cfg.general.watch_folder)
                    else f'--batch "{watch_folder}"')
            print(f"       Run `python main.py {hint}` (in another window, or "
                  "stop this first) to upload those.\n")

        def on_ready(path):
            # allow_prompt=False: this runs in the watcher's background
            # thread with nobody at the keyboard.
            try:
                process_file(path, cfg, None, dup_checker, yt_logger, rb_logger,
                             dry_run, existing_youtube_videos,
                             existing_rumble_videos,
                             allow_prompt=False, only_platform=args.only)
            finally:
                # Said after EVERY file, including one that failed. A run
                # that ends on a stack trace or on a timing table looks
                # like a run that stopped, and there was no way to tell
                # from the output that the watcher was still there.
                print(f"\n[Watch] Done with {os.path.basename(path)}. "
                      f"Watching {watch_folder} for the next one... "
                      f"(Ctrl+C to stop)\n")

        watcher = FolderWatcher(
            watch_folder, cfg.general.supported_formats,
            cfg.general.stability_check_seconds, on_ready,
        )
        watcher.start()
        try:
            # Clips deferred by a platform's spacing wait here, not in
            # the bin. Checked on a timer rather than only when a new
            # video arrives, because the whole point is that the wait
            # expires long after the last file did.
            next_drain = 0.0
            next_autoclip = time.time() + 15
            while True:
                time.sleep(1)
                if cfg.posting and time.time() >= next_drain:
                    next_drain = time.time() + CLIP_DRAIN_SECONDS
                    try:
                        from utils.clip_queue import drain

                        drain(cfg.posting, _clip_config(cfg),
                              dry_run=dry_run, quiet=True)
                    except Exception as exc:
                        print(f"[Clips] WARNING: could not post the queue: {exc}")

                if time.time() >= next_autoclip:
                    next_autoclip = time.time() + AUTOCLIP_SECONDS
                    try:
                        _autoclip_one(cfg)
                    except Exception as exc:
                        print(f"[Clips] WARNING: auto-clip failed: {exc}")
        except KeyboardInterrupt:
            print("\nStopping...")
            watcher.stop()
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
