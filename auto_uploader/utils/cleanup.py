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
import re
import time
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


def platforms_needing_retry(results: Optional[dict],
                            active_platforms: Iterable[str] = ALL_PLATFORMS) -> set:
    """Platforms that did NOT finish successfully and will be retried.

    A missing key counts as pending: the run may have been interrupted
    before that platform was even attempted.
    """
    if results is None:
        return set()
    pending = set()
    for platform in (active_platforms or ALL_PLATFORMS):
        outcome = results.get(platform)
        if not outcome or str(outcome).startswith("FAILED:"):
            pending.add(platform)
    return pending


def censored_copy_is_safe_to_delete(cfg, results: Optional[dict],
                                    active_platforms: Iterable[str] = ALL_PLATFORMS) -> bool:
    """True when no pending retry needs the censored re-encode.

    This is deliberately NOT "everything succeeded". If YouTube is the only
    platform that censors and it already succeeded, a pending Rumble retry
    uploads the original - keeping a multi-GB re-encode for it would be
    pure waste.
    """
    if results is None:
        return True
    return not (platforms_needing_retry(results, active_platforms)
                & censoring_platforms(cfg))


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


# ── The notes beside a video ─────────────────────────────────────────────
#
# Every clip carries its caption, its spoken line and its tags in small
# .txt files named after it. They are read at POST time, once per
# platform, so they have to outlive the upload to the first one.
#
# Nothing ever deleted them. The video went to uploaded/ or was removed,
# and the notes stayed in the watch folder for good:
#
#     Scammer Wop Back To Pay My Dues Sick Again Stackswopo St....txt   x6
#
# Two rules, because there are two ways one is finished with:
#
#  * the ordinary one - every platform posted, so nothing can still need
#    it, and it goes at the same moment as the rest of the working files;
#  * the leftover one - a note whose video no longer exists anywhere.
#    That is only swept after the queue's own give-up age, because until
#    then a post could still be waiting to use it.

SIDECAR_SUFFIXES = ("_subject.txt", "_caption.txt", "_line.txt", ".txt")

# Past this, the queue has abandoned any job that could still want one -
# the same reasoning as VERTICAL_MIN_AGE_S, and the same number.
ORPHAN_SIDECAR_MIN_AGE_S = 36 * 3600

# A sidecar is one line. Anything substantial that happens to be a .txt in
# these folders is somebody's own file and is left alone.
ORPHAN_SIDECAR_MAX_BYTES = 64 * 1024


def sidecar_paths(video_path: str) -> list:
    """Every note file that belongs to this video.

    Includes the ones named after the clip when the path is a "_vertical_"
    re-frame, because copy_sidecars puts a copy beside each.
    """
    if not video_path:
        return []
    stem = os.path.splitext(video_path)[0]
    stems = {stem}
    plain = os.path.join(
        os.path.dirname(stem),
        re.sub(r"^_?vertical[_\s]+", "", os.path.basename(stem), flags=re.I))
    stems.add(plain)
    return [base + suffix for base in sorted(stems)
            for suffix in SIDECAR_SUFFIXES]


def _sidecar_stem(name: str) -> str:
    """The video basename a note belongs to, or "" if it is not a note."""
    lowered = name.lower()
    if not lowered.endswith(".txt"):
        return ""
    for suffix in SIDECAR_SUFFIXES:
        if lowered.endswith(suffix):
            base = name[:len(name) - len(suffix)]
            break
    else:                                       # pragma: no cover
        return ""
    return re.sub(r"^_?vertical[_\s]+", "", base, flags=re.I)


def _folders_to_sweep(cfg) -> list:
    seen, out = set(), []
    for folder in (getattr(cfg.general, "watch_folder", ""),
                   getattr(cfg.general, "censored_folder", ""),
                   getattr(cfg.general, "uploaded_folder", "")):
        if folder and folder not in seen and os.path.isdir(folder):
            seen.add(folder)
            out.append(folder)
    return out


def prune_orphan_sidecars(cfg, min_age_s: float = ORPHAN_SIDECAR_MIN_AGE_S) -> int:
    """Delete notes whose video is gone from every folder we know about.

    Returns HOW MANY, not megabytes: a sidecar is one line, so a hundred
    of them free nothing measurable and would be reported as "0 MB" -
    which reads as having done nothing at all.
    """
    settings = _settings(cfg)
    if not settings.get("enabled", True) or not settings.get("sidecars", True):
        return 0

    folders = _folders_to_sweep(cfg)
    if not folders:
        return 0

    formats = tuple(getattr(cfg.general, "supported_formats", ()) or ())
    videos = set()
    notes = []
    for folder in folders:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            extension = os.path.splitext(name)[1].lower()
            if extension in formats:
                stem = re.sub(r"^_?vertical[_\s]+", "",
                              os.path.splitext(name)[0], flags=re.I)
                videos.add(stem.lower())
            elif extension == ".txt":
                notes.append((folder, name))

    now = time.time()
    report = CleanupReport()
    for folder, name in notes:
        stem = _sidecar_stem(name)
        if not stem or stem.lower() in videos:
            continue
        path = os.path.join(folder, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if stat.st_size > ORPHAN_SIDECAR_MAX_BYTES:
            continue        # somebody's own file, not one of ours
        if now - stat.st_mtime < min_age_s:
            continue        # a post could still be queued for it
        _remove(path, report)
    return len(report.removed)


def cleanup_after_upload(
    cfg,
    video_path: str,
    censored_path: Optional[str] = None,
    results: Optional[dict] = None,
    since_ts: Optional[float] = None,
    active_platforms: Iterable[str] = ALL_PLATFORMS,
    keep_transcript: bool = False,
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
    active_platforms = tuple(active_platforms or ALL_PLATFORMS)
    pending = platforms_needing_retry(results, active_platforms)

    # 1. Censored re-encode - the gigabytes.
    if not settings.get("censored_copy", True):
        report.keep("censored copy", "cleanup.censored_copy is false")
    elif not censored_copy_is_safe_to_delete(cfg, results, active_platforms):
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
    if keep_transcript:
        # Clips are scored FROM this transcript. Deleting it here left
        # the clip pass with "no cached transcript for this video" on
        # every stream - and re-transcribing four hours to cut ten clips
        # is not a trade worth making for a few KB of JSON.
        report.keep("transcript cache", "clips are still to be cut from it")
    elif not settings.get("transcript_cache", True):
        report.keep("transcript cache", "cleanup.transcript_cache is false")
    elif not censored_copy_is_safe_to_delete(cfg, results, active_platforms):
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

    # 5. The notes beside the video - caption, spoken line, tags.
    #
    #    They are read at POST time, once per platform, so they cannot go
    #    while any platform is still waiting. Once none is, nothing can
    #    ever read them again and they were being left in the watch folder
    #    forever, long after the video itself had moved out.
    if not settings.get("sidecars", True):
        report.keep("caption notes", "cleanup.sidecars is false")
    elif pending:
        report.keep("caption notes",
                    f"still needed to post {', '.join(sorted(pending))}")
    else:
        for path in sidecar_paths(video_path):
            _remove(path, report, protected)
        if censored_path:
            for path in sidecar_paths(censored_path):
                _remove(path, report, protected)

    # 6. Logs: bounded, never deleted.
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


# A re-frame is only scratch once nothing is still waiting to post it.
# The queue's own ceiling: past this a job is abandoned as too old to be
# worth posting, so nothing can still be pointing at the file.
VERTICAL_MIN_AGE_S = 36 * 3600


def _queued_clip_paths(cfg) -> set:
    """Every clip path a job is still waiting on. Empty set on any doubt.

    Doubt matters here. A queue that cannot be read must read as "every
    file is spoken for", never as "nothing is" - deleting a clip out from
    under a pending post loses the post silently, which is the exact
    class of failure this project keeps finding days late.
    """
    path = (getattr(cfg, "posting", {}) or {}).get(
        "queue_path") or "./clip_jobs.json"

    # Parsed here FIRST, on purpose. JobQueue swallows an unreadable file
    # and comes back empty, which is indistinguishable from "nothing is
    # queued" - and this function's whole job is to tell those two apart.
    # A test caught it deleting files against a corrupt queue.
    if os.path.exists(path):
        try:
            import json

            with open(path, "r", encoding="utf-8") as handle:
                json.load(handle)
        except (OSError, ValueError):
            return None

    try:
        from job_queue import ACTIVE_STATES, JobQueue

        queue = JobQueue(path=path)
        return {os.path.realpath(job.clip_path)
                for job in queue.list_jobs(ACTIVE_STATES)}
    except Exception:
        return None


def prune_vertical_copies(cfg, min_age_s: float = VERTICAL_MIN_AGE_S) -> float:
    """Delete 9:16 re-frames nothing is waiting to post. Returns MB freed.

    vertical_path writes "_vertical_<clip>.mp4" into censored/ so that
    Rumble and Instagram share one encode instead of paying for it twice.
    NOTHING has ever deleted them. Each is a full re-encode of a clip, so
    a machine that clips daily grows a folder of them forever - and this
    project runs off an external drive, where a full disk stops the
    RECORDER, not just the posting.

    Two conditions, both required, because the queue stores the re-framed
    path and not the original:

      * no active job is waiting on this file, and
      * it is older than the queue's own give-up age, so a job that is
        about to be created cannot be raced.
    """
    folder = getattr(cfg.general, "censored_folder", "")
    if not folder or not os.path.isdir(folder):
        return 0.0

    spoken_for = _queued_clip_paths(cfg)
    if spoken_for is None:
        # The queue could not be read. Delete nothing.
        return 0.0

    now = time.time()
    report = CleanupReport()
    for name in os.listdir(folder):
        if not name.lower().startswith("_vertical_"):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if os.path.realpath(path) in spoken_for:
            report.keep(name, "a queued post is still waiting on it")
            continue
        try:
            if now - os.path.getmtime(path) < min_age_s:
                report.keep(name, "too recent to be sure nothing wants it")
                continue
        except OSError:
            continue
        _remove(path, report)
    return report.freed_mb
