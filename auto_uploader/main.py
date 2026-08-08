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

import argparse
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

# Bump when shipping user-visible changes, so --test-config can prove
# which build is actually running (stale extracts have silently caused
# several confusing "the fix did nothing" runs).
BUILD = "2026-08-09.2 instagram keeps the original audio"

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
            shutil.move(source, os.path.join(cfg.general.watch_folder,
                                             os.path.basename(source)))
            moved += 1
        except OSError as exc:
            print(f"[Clips] could not deliver {os.path.basename(source)}: {exc}")
    return moved


def _find_clips(cfg, limit: int = 15) -> list:
    """Every video short enough to be a Reel, across the usual folders.

    Duration rather than filename: clips arrive named after whatever the
    streamer called them, so there is no prefix to match on.
    """
    from utils.ffmpeg_tools import media_duration

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
        return {"skipped": "duplicate"}

    if is_clip and not cli_title:
        # A clip carries its own title in the filename ("who put stacks on
        # slots"), and a batch of eleven should not stop eleven times to
        # ask for something already on disk.
        from utils.social_promoter import clip_title as _clip_title
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

    yt_title = build_title(stream_title, date_str, cfg.youtube.title_format)
    yt_description = build_description(cfg.youtube.description_template, date_str, stream_title)
    rb_title = build_title(stream_title, date_str, cfg.rumble.title_format)
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
    if not existing_rb and cfg.rumble.skip_if_exists:
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
        return vertical_path(_censored["path"])

    run_started_at = time.time()
    stage_timer = StageTimer(filename,
                             enabled=bool((cfg.general.speed or {}).get('stage_timings', True)))
    results = {}
    newly_uploaded = {}

    notify("Upload starting", filename, cfg.general.enable_desktop_notifications)

    # --- YouTube ---
    # existing_yt was already determined (and, if it came from
    # find_existing_video, already persisted) above the dry-run check.
    if "youtube" not in active_platforms:
        print("[YouTube] Skipped - --only rumble.")
    elif existing_yt:
        results["youtube"] = existing_yt  # already announced above
    else:
        try:
            yt = YouTubeUploader(cfg.youtube.client_secrets_path, cfg.youtube.token_path)

            def yt_progress(pct):
                print(f"\r[YouTube] Uploading... {pct}%", end="", flush=True)

            def yt_on_retry(attempt, delay, exc):
                yt_logger.warning(f"{filename}: attempt {attempt} failed ({exc}); retrying in {delay}s")

            url = retry_with_backoff(
                lambda: yt.upload(
                    upload_path_for(cfg.youtube.censor_uploads), yt_title, yt_description, cfg.youtube.tags,
                    chunk_mb=float(getattr(cfg.youtube, 'upload_chunk_mb', 8) or 8),
                    privacy=cfg.youtube.privacy, category_id=cfg.youtube.category_id,
                    made_for_kids=cfg.youtube.made_for_kids,
                    thumbnail_path=cfg.youtube.thumbnail_path or None,
                    playlist_id=cfg.youtube.playlist_id or None,
                    progress_callback=yt_progress,
                ),
                max_retries=cfg.general.max_retries, delays=cfg.general.retry_delays, on_retry=yt_on_retry,
            )
            print()
            yt_logger.info(f"{filename}: uploaded successfully -> {url}")
            notify("YouTube upload complete", url, cfg.general.enable_desktop_notifications)
            results["youtube"] = url
            newly_uploaded["youtube"] = url
        except Exception as exc:
            print()
            print(f"[YouTube] UPLOAD FAILED: {exc}")
            print(f"          Full details: {os.path.join(cfg.general.logs_folder, 'youtube.log')}")
            yt_logger.error(f"{filename}: FAILED: {exc}")
            notify("YouTube upload FAILED", f"{filename}: {exc}", cfg.general.enable_desktop_notifications)
            results["youtube"] = f"FAILED: {exc}"
        finally:
            # Runs even on an uncaught KeyboardInterrupt (Ctrl+C), which is
            # exactly what we need: whatever happened gets persisted
            # immediately, so a Ctrl+C here can't cause a later re-upload -
            # but the interrupt still propagates and actually stops the
            # script, instead of being silently swallowed.
            dup_checker.record_platform_result(file_hash, filename, "youtube", results.get("youtube", "FAILED: interrupted"), title=yt_title)

    # --- Rumble ---
    # existing_rb was already determined (hash -> stored title -> RSS feed)
    # above the dry-run check, and announced there.
    if "rumble" not in active_platforms:
        print("[Rumble] Skipped - --only youtube.")
    elif existing_rb:
        print(f"[Rumble] Already on the channel - skipping: {existing_rb}")
        results["rumble"] = existing_rb
    else:
        try:
            rb = RumbleUploader(
                cfg.rumble.username, cfg.rumble.password, cfg.rumble.login_url, cfg.rumble.upload_url,
                cdp_url=cfg.rumble.cdp_url,
                primary_category=cfg.rumble.primary_category,
                secondary_category=cfg.rumble.secondary_category,
            )

            def rb_progress(pct):
                print(f"\r[Rumble] Uploading... {pct}%", end="", flush=True)

            def rb_on_retry(attempt, delay, exc):
                rb_logger.warning(f"{filename}: attempt {attempt} failed ({exc}); retrying in {delay}s")

            url = retry_with_backoff(
                lambda: rb.upload(
                    upload_path_for(cfg.rumble.censor_uploads), rb_title, rb_description, cfg.rumble.tags,
                    privacy=cfg.rumble.privacy, thumbnail_path=cfg.rumble.thumbnail_path or None,
                    progress_callback=rb_progress,
                ),
                max_retries=cfg.general.max_retries, delays=cfg.general.retry_delays, on_retry=rb_on_retry,
            )
            print()
            rb_logger.info(f"{filename}: uploaded successfully -> {url}")
            notify("Rumble upload complete", url, cfg.general.enable_desktop_notifications)
            results["rumble"] = url
            newly_uploaded["rumble"] = url
        except Exception as exc:
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
            results["rumble"] = f"FAILED: {exc}"
        finally:
            dup_checker.record_platform_result(file_hash, filename, "rumble", results.get("rumble", "FAILED: interrupted"), title=rb_title)

    fully_uploaded = dup_checker.is_fully_uploaded(file_hash, platforms=active_platforms)
    if fully_uploaded:
        action = resolve_source_action(cfg)
        if action == SOURCE_DELETE:
            # Only reachable when BOTH platforms already succeeded - the
            # video is published, and the user has opted into losing the
            # local copy.
            size_mb = os.path.getsize(video_path) / (1024 ** 2)
            try:
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
    else:
        print(f"[INFO] {filename} left in place (not every platform succeeded yet) - "
              f"rerun --file or --batch on it later to retry just what's still missing.")

    # Post-upload extras - strictly best-effort, only for uploads that
    # actually happened THIS run (never for pre-existing skips), and never
    # in a dry run. A failure here must not mark the upload as failed.
    stage_timer.mark("upload")

    if newly_uploaded:
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
                            # Only a CLIP goes to Instagram as a Reel. A
                            # five-hour stream is neither wanted there nor
                            # accepted - Reels cap at 15 minutes.
                            # The re-framed copy if one was made, so
                            # Instagram does not pay for a second crop of
                            # the same clip.
                            clip_path=instagram_clip_path() if is_clip else "")
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

                run = make_clips(cfg, video_path, stream_title,
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

    # Disk cleanup LAST: the optimizer above reads the cached transcript,
    # so removing it any earlier would break the report.
    try:
        report = cleanup_after_upload(
            cfg, video_path, _censored.get("path"),
            results=results, since_ts=run_started_at,
            active_platforms=active_platforms)
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
    parser.add_argument("--forget", metavar="FILE",
                        help="Erase this file's upload history so it can be "
                             "retried (use --forget-platform to target one).")
    parser.add_argument("--forget-platform", choices=("youtube", "rumble"),
                        help="With --forget, only clear this platform.")
    parser.add_argument("--health", action="store_true", help="Run disk/CPU/network health checks + temp cleanup, then exit.")
    parser.add_argument("--clips", metavar="FILE",
                        help="Render vertical clips with burned-in captions from "
                             "an already-uploaded video, ready to post by hand.")
    parser.add_argument("--clip-count", type=int, default=None,
                        help="How many clips to make with --clips (default 3).")
    parser.add_argument("--gpu-check", action="store_true",
                        help="Report whether the censor pass will use the GPU, and "
                             "load the configured model to prove it. No upload.")
    parser.add_argument("--posting-status", action="store_true",
                        help="Show what social posting would do right now: kill switch, "
                             "per-platform caps, and which credentials are in .env. Posts nothing.")
    parser.add_argument("--verify", action="store_true",
                        help="With --posting-status, also ask each API who your token "
                             "belongs to. Read-only - creates and publishes nothing.")
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
    cfg = load_config(os.path.join(config_dir, "config.json"), os.path.join(config_dir, ".env"))

    # Applied to the loaded config only - config.json is never rewritten, so
    # the flag governs this run and nothing after it.
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
        from utils.ffmpeg_tools import media_duration

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
        if not os.path.isfile(args.forget):
            print(f"[ERROR] File not found: {args.forget}")
            for path in _suggest_paths(cfg, os.path.basename(args.forget)):
                print(f"        Did you mean: {path}")
            return 1
        checker = DuplicateChecker(cfg.general.duplicate_store_path)

        # Filename lookup first: hashing a multi-GB video off an external
        # drive takes minutes, and the store already records the filename.
        targets = checker.find_hashes_by_filename(args.forget)
        if not targets:
            size_gb = os.path.getsize(args.forget) / (1024 ** 3)
            print(f"No record under that filename; identifying by content "
                  f"instead ({size_gb:.1f} GB to read, this can take a minute)...")
            targets = [hash_file(args.forget)]

        scope = args.forget_platform or "both platforms"
        if any(checker.forget(t, args.forget_platform) for t in targets):
            print(f"Forgot {os.path.basename(args.forget)} ({scope}). "
                  f"Run --file on it to upload again.")
            return 0
        print(f"No upload history recorded for {os.path.basename(args.forget)} "
              f"({scope}) - nothing to clear. You can run --file on it directly.")
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
        print_run(make_clips(cfg, args.clips, title, count=args.clip_count))
        return 0

    if args.posting_status:
        from publish_guard import PublishGuard
        from utils.posting_status import report

        guard = PublishGuard(cfg.posting, cfg.posting.get("state_path"))
        account = (cfg.features.get("social_promoter", {}) or {}).get(
            "reddit_account", "")
        report({"posting": cfg.posting}, guard, account, live=args.verify)
        return 0

    if args.test_config:
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
            # defence and it is working. The feed only adds cover for
            # videos put on Rumble outside this tool, so losing it to a
            # Cloudflare challenge degrades nothing that was already
            # protected. Said once, plainly, with the way to restore it.
            print(f"[Rumble] Channel feed unavailable ({exc}).")
            print("         Dedup falls back to local upload history, which already "
                  "covers everything this tool uploaded.")
            if cfg.rumble.cdp_url:
                print(f"         To use the feed too, leave Chrome open with "
                      f"--remote-debugging-port on {cfg.rumble.cdp_url}.")

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
            process_file(path, cfg, None, dup_checker, yt_logger, rb_logger, dry_run,
                         existing_youtube_videos, existing_rumble_videos,
                         allow_prompt=False, only_platform=args.only)

        watcher = FolderWatcher(
            watch_folder, cfg.general.supported_formats,
            cfg.general.stability_check_seconds, on_ready,
        )
        watcher.start()
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nStopping...")
            watcher.stop()
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
