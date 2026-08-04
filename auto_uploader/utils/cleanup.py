"""
Post-upload disk cleanup.

THE DELETE CONTRACT
-------------------
An artifact may be deleted only when no future retry can need it. Retries
are per-platform: a run where YouTube succeeded and Rumble failed will
come back and retry Rumble alone, so "did everything succeed?" is the
wrong question. The right one is "which platforms are still pending, and
what do they need?".

  source video        Needed by a retry of ANY platform.
                      -> cleanup NEVER deletes it. Removing the source is
                         handled separately in main.py, is opt-in, and only
                         happens once every platform has succeeded.

  censored re-encode  Needed only to retry a platform with
                      censor_uploads=true. If YouTube (the only censoring
                      platform by default) already succeeded, a pending
                      Rumble retry uploads the ORIGINAL and will never
                      touch it.
                      -> delete when no pending platform wants censoring,
                         even if another platform failed.

  transcript cache    Its only purposes are (a) the optimizer report,
                      which has already run by this point, and (b) making
                      a re-censor cheap. Same condition as the re-encode.

  extracted audio     Temporary. censor_video already removes these in its
                      own finally; cleanup is a safety net for a run that
                      was killed mid-pass.

  Rumble page dumps   Diagnostics for a FAILED Rumble run. These are
                      global files, not per-video, so deleting "all dumps"
                      after one video succeeds would destroy the evidence
                      for a different video that is still broken.
                      -> delete only dumps written during THIS file's own
                         Rumble attempt, and only if that attempt succeeded.

  logs                Kilobytes, and the only record of why an upload
                      failed. -> trimmed to a bound, never deleted.

Every deletion targets a path this pipeline generated under a name it
controls. Nothing here globs on user-supplied text without escaping it,
and nothing here touches watch_folder or uploaded/.
"""

import glob
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional

from utils.censor import transcript_cache_path, words_cache_path

# What `cleanup.source_video` may be set to.
SOURCE_MOVE = "move"      # default: keep it, in uploaded/
SOURCE_DELETE = "delete"  # opt-in: free the space; the local copy is gone
SOURCE_KEEP = "keep"      # leave it exactly where it was

ALL_PLATFORMS = ("youtube", "rumble")

# Trim to this fraction of the limit, so a file that was just trimmed is
# comfortably under the threshold and a second run is a no-op.
_TRIM_TARGET = 0.9
_TRIM_MARKER = b"[log trimmed - older entries dropped, most recent kept]\n"


@dataclass
class CleanupReport:
    """What cleanup did, and what it deliberately left alone."""
    freed_mb: float = 0.0
    removed: list = field(default_factory=list)
    kept: list = field(default_factory=list)   # (path-ish, reason)

    def keep(self, what: str, reason: str) -> None:
        self.kept.append((what, reason))


def _size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1024 ** 2)
    except OSError:
        return 0.0


def _remove(path: Optional[str], report: CleanupReport,
            protected: Iterable[str] = ()) -> None:
    """Delete `path`, recording it. Silent no-op if it's already gone.

    `protected` is a belt-and-braces guard: a real path in that set is
    never removed, whatever the caller asked for.
    """
    if not path:
        return
    try:
        real = os.path.realpath(path)
    except OSError:
        return
    for guard in protected:
        if guard and os.path.realpath(guard) == real:
            return
    if not os.path.isfile(path):
        return
    freed = _size_mb(path)
    try:
        os.remove(path)
    except OSError:
        return
    report.freed_mb += freed
    report.removed.append(path)


def _settings(cfg) -> dict:
    return getattr(cfg.general, "cleanup", None) or {}


def censoring_platforms(cfg) -> set:
    """Platforms configured to upload the censored copy."""
    out = set()
    if getattr(cfg.youtube, "censor_uploads", False):
        out.add("youtube")
    if getattr(cfg.rumble, "censor_uploads", False):
        out.add("rumble")
    return out


def platforms_needing_retry(results: Optional[dict]) -> set:
    """Platforms that did NOT finish successfully and will be retried.

    A missing key counts as pending: the run may have been interrupted
    before that platform was even attempted.
    """
    if results is None:
        return set()
    pending = set()
    for platform in ALL_PLATFORMS:
        outcome = results.get(platform)
        if not outcome or str(outcome).startswith("FAILED:"):
            pending.add(platform)
    return pending


def censored_copy_is_safe_to_delete(cfg, results: Optional[dict]) -> bool:
    """True when no pending retry needs the censored re-encode.

    This is deliberately NOT "everything succeeded". If YouTube is the only
    platform that censors and it already succeeded, a pending Rumble retry
    uploads the original - keeping a multi-GB re-encode for it would be
    pure waste.
    """
    if results is None:
        return True
    return not (platforms_needing_retry(results) & censoring_platforms(cfg))


def trim_log(path: str, max_mb: float) -> float:
    """Bound a log file's size, keeping the most recent entries.

    Trimmed rather than deleted: the logs are how a failed upload gets
    diagnosed, they're kilobytes, and deleting them frees nothing while
    losing the only record of what went wrong.

    Idempotent - it trims to ~90% of the limit, so a file that was just
    trimmed is under the threshold and running again does nothing.
    """
    if max_mb <= 0 or not os.path.isfile(path):
        return 0.0
    max_bytes = int(max_mb * 1024 * 1024)
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0.0
    if size <= max_bytes:
        return 0.0

    keep = max(1, int(max_bytes * _TRIM_TARGET))
    try:
        with open(path, "rb") as f:
            f.seek(-min(keep, size), os.SEEK_END)
            tail = f.read()
        # Drop the leading partial line so every line in the file is whole.
        newline = tail.find(b"\n")
        if newline != -1:
            tail = tail[newline + 1:]
        with open(path, "wb") as f:
            f.write(_TRIM_MARKER)
            f.write(tail)
    except OSError:
        return 0.0
    return (size - os.path.getsize(path)) / (1024 ** 2)


def _censored_copies(cfg, basename: str) -> list:
    """Every censored re-encode belonging to `basename`.

    Anchored on the '_CENSORED_' infix that censor_video adds, so 'clip'
    cannot match 'clip2_CENSORED_...'. The basename is glob-escaped -
    yt-dlp names like 'Title [dQw4w9WgXcQ]' contain '[' and ']', which are
    character-class syntax and would otherwise match the wrong files.
    """
    pattern = os.path.join(cfg.general.censored_folder,
                           glob.escape(basename) + "_CENSORED_*.mp4")
    return sorted(glob.glob(pattern))


def _temp_audio_paths(cfg, basename: str) -> list:
    """Exact temp-WAV names censor_video uses - no globbing on user text."""
    work = cfg.general.censored_folder
    return [os.path.join(work, f"_{basename}_audio.wav"),
            os.path.join(work, f"_{basename}_audio_clean.wav")]


def _page_dumps_since(cfg, since_ts: Optional[float]) -> list:
    """Rumble dumps written at/after `since_ts`.

    Page dumps are global, so a time window is what makes "this file's
    dumps" meaningful. With no window, nothing is eligible.
    """
    if since_ts is None:
        return []
    found = []
    for path in glob.glob(os.path.join(cfg.general.logs_folder,
                                       "rumble_page_dump_*.html")):
        try:
            if os.path.getmtime(path) >= since_ts:
                found.append(path)
        except OSError:
            continue
    return sorted(found)


def cleanup_after_upload(
    cfg,
    video_path: str,
    censored_path: Optional[str] = None,
    results: Optional[dict] = None,
    since_ts: Optional[float] = None,
) -> CleanupReport:
    """Free this video's working files, per the contract at the top.

    `results` maps platform -> URL or "FAILED: ...". Pass it: without it,
    cleanup assumes nothing is pending and behaves as it would after a
    fully successful run.
    """
    report = CleanupReport()
    settings = _settings(cfg)
    if not settings.get("enabled", True):
        report.keep("all", "cleanup.enabled is false")
        return report

    basename = os.path.splitext(os.path.basename(video_path))[0]
    # The source video is never a cleanup target, and neither is whatever
    # the censored path resolves to when censoring was skipped (it's then
    # the source itself).
    protected = {video_path}
    pending = platforms_needing_retry(results)

    # 1. Censored re-encode - the gigabytes.
    if not settings.get("censored_copy", True):
        report.keep("censored copy", "cleanup.censored_copy is false")
    elif not censored_copy_is_safe_to_delete(cfg, results):
        blocked = sorted(pending & censoring_platforms(cfg))
        report.keep("censored copy",
                    f"still needed to retry {', '.join(blocked)}")
    else:
        targets = set(_censored_copies(cfg, basename))
        if censored_path:
            targets.add(censored_path)
        for path in sorted(targets):
            _remove(path, report, protected)

    # 2. Transcript cache - same condition; it exists to make a re-censor
    #    cheap, and the optimizer report has already been written by now.
    if not settings.get("transcript_cache", True):
        report.keep("transcript cache", "cleanup.transcript_cache is false")
    elif not censored_copy_is_safe_to_delete(cfg, results):
        report.keep("transcript cache", "still needed to retry censoring")
    else:
        _remove(transcript_cache_path(cfg.general.censored_folder, basename),
                report, protected)
        _remove(words_cache_path(cfg.general.censored_folder, basename),
                report, protected)

    # 3. Extracted audio. censor_video removes these itself; this only
    #    catches a pass that was killed before its finally ran.
    for path in _temp_audio_paths(cfg, basename):
        _remove(path, report, protected)

    # 4. Rumble page dumps, only from this file's own attempt.
    if not settings.get("page_dumps", True):
        report.keep("page dumps", "cleanup.page_dumps is false")
    elif "rumble" in pending:
        report.keep("page dumps", "Rumble still pending - dumps explain why")
    elif since_ts is None:
        report.keep("page dumps", "no run window given; cannot tell which are ours")
    else:
        for path in _page_dumps_since(cfg, since_ts):
            _remove(path, report, protected)

    # 5. Logs: bounded, never deleted.
    max_mb = float(settings.get("trim_logs_mb", 5) or 0)
    for name in ("youtube.log", "rumble.log"):
        report.freed_mb += trim_log(os.path.join(cfg.general.logs_folder, name), max_mb)

    return report


def resolve_source_action(cfg) -> str:
    """How to treat the source video once EVERY platform has succeeded."""
    action = str(_settings(cfg).get("source_video", SOURCE_MOVE)).strip().lower()
    return action if action in (SOURCE_MOVE, SOURCE_DELETE, SOURCE_KEEP) else SOURCE_MOVE


def prune_uploaded_folder(cfg, keep_newest: int) -> float:
    """Delete all but the `keep_newest` most recent videos in uploaded/.

    Opt-in via `cleanup.keep_uploaded_videos`; callers must not invoke this
    with 0, which would empty the folder. uploaded/ is where every source
    video lands after a successful upload, so on a machine that processes
    streams regularly it grows by a full VOD each time and is usually the
    real reason a disk filled up.
    """
    if not keep_newest or keep_newest < 1:
        return 0.0
    folder = cfg.general.uploaded_folder
    if not os.path.isdir(folder):
        return 0.0

    videos = [
        os.path.join(folder, name) for name in os.listdir(folder)
        if os.path.splitext(name)[1].lower() in cfg.general.supported_formats
    ]
    videos = [v for v in videos if os.path.isfile(v)]
    videos.sort(key=lambda p: (os.path.getmtime(p), p), reverse=True)

    report = CleanupReport()
    for path in videos[keep_newest:]:
        _remove(path, report)
    return report.freed_mb
