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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime

# Bump when shipping user-visible changes, so --test-config can prove
# which build is actually running (stale extracts have silently caused
# several confusing "the fix did nothing" runs).
BUILD = "2026-08-03.12 watch-takes-a-folder"

from utils.censor import censor_video
from utils.config import load_config, validate_config
from utils.duplicate_checker import DuplicateChecker, hash_file
from utils.file_watcher import FolderWatcher, is_intermediate_download
from utils.logging_setup import setup_logger
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
from utils.youtube_checker import fetch_existing_videos, find_existing_video
from utils.youtube_uploader import YouTubeUploader


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


def process_file(video_path: str, cfg, cli_title: str, dup_checker: DuplicateChecker,
                  yt_logger, rb_logger, dry_run: bool, existing_youtube_videos: list = None,
                  existing_rumble_videos: list = None, allow_prompt: bool = True) -> dict:
    filename = os.path.basename(video_path)

    # Cheap checks first. Hashing reads the whole file, so doing it before
    # the extension test meant a folder of in-progress yt-dlp downloads
    # (multi-GB *.part files) got read end-to-end off the drive purely to
    # throw the result away - and those files are still being written to.
    if os.path.splitext(video_path)[1].lower() not in cfg.general.supported_formats:
        print(f"[SKIP] {filename} is not a supported video format.")
        return {"skipped": "unsupported_format"}

    # e.g. "Stream.f140.mp4" - yt-dlp's audio-only half, downloaded in full
    # but not yet muxed with the video. Real extension, real size, and it
    # stops growing, so nothing else here would catch it.
    if is_intermediate_download(video_path):
        print(f"[SKIP] {filename} is a partial download (pre-merge), not a finished video.")
        return {"skipped": "intermediate_download"}

    file_hash = hash_file(video_path)

    if dup_checker.is_fully_uploaded(file_hash):
        print(f"[SKIP] {filename} already uploaded to both platforms previously (matched by content hash).")
        return {"skipped": "duplicate"}

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
        existing_yt_match = find_existing_video(existing_youtube_videos, now)
        if existing_yt_match:
            existing_yt = existing_yt_match.url

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
            rb_match = find_existing_video(existing_rumble_videos, now)
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

    def upload_path_for(platform_wants_censoring: bool) -> str:
        if not (platform_wants_censoring and cfg.general.censor_before_upload):
            return video_path
        if "path" not in _censored:
            print(f"[Censor] Transcribing + scanning for profanity (model={cfg.general.censor_model})...")
            censor_result = censor_video(
                video_path, cfg.general.censored_folder,
                model_name=cfg.general.censor_model,
                bleep_method=cfg.general.censor_bleep_method,
                custom_words=cfg.general.censor_custom_words,
                device=cfg.general.censor_device,
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
        return _censored["path"]

    results = {}
    newly_uploaded = {}

    notify("Upload starting", filename, cfg.general.enable_desktop_notifications)

    # --- YouTube ---
    # existing_yt was already determined (and, if it came from
    # find_existing_video, already persisted) above the dry-run check.
    if existing_yt:
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
    if existing_rb:
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
            rb_logger.error(f"{filename}: FAILED: {exc}")
            notify("Rumble upload FAILED", f"{filename}: {exc}", cfg.general.enable_desktop_notifications)
            results["rumble"] = f"FAILED: {exc}"
        finally:
            dup_checker.record_platform_result(file_hash, filename, "rumble", results.get("rumble", "FAILED: interrupted"), title=rb_title)

    if dup_checker.is_fully_uploaded(file_hash):
        dest = os.path.join(cfg.general.uploaded_folder, filename)
        os.makedirs(cfg.general.uploaded_folder, exist_ok=True)
        try:
            shutil.move(video_path, dest)
        except Exception as exc:
            print(f"[WARN] Could not move {filename} to uploaded/: {exc}")
        censored_copy = _censored.get("path")
        if censored_copy and censored_copy != video_path and os.path.exists(censored_copy):
            os.remove(censored_copy)  # temp censored copy, no longer needed once fully uploaded
    else:
        print(f"[INFO] {filename} left in place (not every platform succeeded yet) - "
              f"rerun --file or --batch on it later to retry just what's still missing.")

    # Post-upload extras - strictly best-effort, only for uploads that
    # actually happened THIS run (never for pre-existing skips), and never
    # in a dry run. A failure here must not mark the upload as failed.
    if newly_uploaded:
        try:
            from utils.social_promoter import announce_upload
            announce_upload(cfg.features.get("social_promoter", {}), yt_title, newly_uploaded)
        except Exception as exc:
            print(f"[Social] WARNING: announce failed: {exc}")
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
    parser.add_argument("--health", action="store_true", help="Run disk/CPU/network health checks + temp cleanup, then exit.")
    args = parser.parse_args(argv)

    # Printed on EVERY run, not just --test-config: a stale extract running
    # old code has silently caused several confusing "the fix did nothing"
    # sessions, and the build stamp settles it in one line.
    print(f"AutoBleep auto-uploader | Build: {BUILD}")

    config_dir = os.path.dirname(os.path.abspath(__file__))
    cfg = load_config(os.path.join(config_dir, "config.json"), os.path.join(config_dir, ".env"))

    if args.health:
        ok = run_health_check(cfg, cfg.features.get("self_healing", {}))
        return 0 if ok else 1

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

    batch_folder = None
    if args.batch is not None:
        batch_folder = os.path.abspath(args.batch or cfg.general.watch_folder)
        if not os.path.isdir(batch_folder):
            print(f"[ERROR] --batch folder does not exist: {batch_folder}")
            return 1
        print(f"Batch folder: {batch_folder}")

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
            # Non-fatal, unlike the YouTube fetch for --batch: the local
            # hash/title history still prevents this tool re-uploading its
            # own work; the RSS only adds protection for videos uploaded
            # manually outside the tool.
            print(f"[WARN] Rumble RSS dedup check unavailable ({exc}); relying on local history only.")

    if batch_folder and existing_videos_fetch_failed and not dry_run:
        print(
            "[ABORTED] Refusing to run --batch without the existing-video dedup check working "
            "(it would risk re-uploading videos already on the channel). Fix the YouTube auth "
            "issue above, then try again. (--file and --watch aren't blocked by this.)"
        )
        return 1

    if args.file:
        process_file(args.file, cfg, args.title, dup_checker, yt_logger, rb_logger, dry_run,
                     existing_youtube_videos, existing_rumble_videos)
        return 0

    if batch_folder:
        for fname in sorted(os.listdir(batch_folder)):
            path = os.path.join(batch_folder, fname)
            if os.path.isfile(path):
                process_file(path, cfg, None, dup_checker, yt_logger, rb_logger, dry_run,
                             existing_youtube_videos, existing_rumble_videos)
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
                         allow_prompt=False)

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
