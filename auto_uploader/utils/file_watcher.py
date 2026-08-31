"""
Watches a folder for new video files using watchdog, waiting for each file
to stop growing (i.e. finish being copied/written) before handing it off -
otherwise a half-copied file would get "uploaded" mid-write.
"""

import os
import queue
import re
import threading
import time
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Downloaders leave finished-but-not-final files lying around that already
# have a real video extension:
#
#   "Stream.f140.mp4"  yt-dlp's audio-only stream, before muxing
#   "Stream.f299.mp4"  yt-dlp's video-only stream, before muxing
#   "Stream.temp.mp4"  ffmpeg's mux target
#
# The .part/.ytdl files are already ignored by the extension filter, but
# these are not: yt-dlp downloads each stream fully, *then* merges, so
# there's a window where an audio-only .mp4 sits there complete and
# unchanging. Without this it would look "stable" and get uploaded.
_INTERMEDIATE_STEM = re.compile(
    r"\.(f\d{1,4}|temp|tmp|part|download|ytdl)$", re.IGNORECASE)


def is_intermediate_download(path: str) -> bool:
    """True for a downloader's in-progress / pre-merge artefact."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return bool(_INTERMEDIATE_STEM.search(stem))


# Extensions that are plausibly a video someone MEANT to upload, but that
# this config does not list as supported. These are the only ones worth a
# [SKIP] line, because they are the only ones where the answer might be
# "add it to supported_formats" rather than "that is not a video".
#
# Listing what to WARN about, rather than what to ignore, is the right way
# round: pointing --batch at a folder that also holds code produced a
# [SKIP] line for every .py and .bat in it, which buries the skips that
# actually mean something. Anything not named here is skipped silently.
_MAYBE_VIDEO_EXTENSIONS = frozenset({
    ".webm", ".m4v", ".mpg", ".mpeg", ".m2ts", ".mts", ".vob", ".ogv",
    ".3gp", ".rm", ".rmvb", ".asf", ".divx", ".f4v", ".mxf", ".dv",
})


def looks_like_a_video_attempt(path: str) -> bool:
    """True if skipping this file is worth mentioning."""
    return os.path.splitext(os.path.basename(path))[1].lower() in _MAYBE_VIDEO_EXTENSIONS


def is_sidecar_file(path: str) -> bool:
    """True for a file whose skip is not worth reporting.

    Kept as the inverse of the above so existing callers read the same
    way: everything that is not a plausible video attempt is a sidecar as
    far as the log is concerned.
    """
    return not looks_like_a_video_attempt(path)


class _NewVideoHandler(FileSystemEventHandler):
    def __init__(self, supported_formats: tuple, stability_seconds: int, on_ready: Callable[[str], None]):
        self.supported_formats = supported_formats
        self.stability_seconds = stability_seconds
        self.on_ready = on_ready
        self._pending: set = set()
        self._lock = threading.Lock()

    def on_created(self, event):
        if event.is_directory:
            return
        self._maybe_watch(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        self._maybe_watch(event.dest_path)

    def _maybe_watch(self, path: str) -> None:
        if os.path.splitext(path)[1].lower() not in self.supported_formats:
            return
        if is_intermediate_download(path):
            return
        with self._lock:
            if path in self._pending:
                return
            self._pending.add(path)
        threading.Thread(target=self._wait_for_stable, args=(path,), daemon=True).start()

    def _wait_for_stable(self, path: str) -> None:
        """Poll file size until it hasn't changed for `stability_seconds`,
        which is a simple, dependency-free way to detect 'done writing'
        without needing OS-specific file-lock APIs."""
        try:
            last_size = -1
            stable_since = None
            # A file being written very slowly - a network drive, a stalled
            # download - would otherwise hold this thread for days, and the
            # path stays in _pending so it can never be re-queued either.
            deadline = time.time() + MAX_STABILITY_WAIT_S
            while True:
                try:
                    size = os.path.getsize(path)
                except FileNotFoundError:
                    return  # file disappeared (renamed/deleted) before it stabilized
                except OSError:
                    # The DRIVE dropped out mid-wait, not the file. On an
                    # external disk under load that is a real, repeated
                    # event ([WinError 3] 'D:\\', PermissionError) and it
                    # comes back a moment later. Only FileNotFoundError
                    # was caught here, so anything else killed this
                    # thread outright - and because the discard below
                    # never ran, the path stayed in _pending forever and
                    # that video could never be queued again for the life
                    # of the process. Treat it as "size unknown, keep
                    # waiting"; `deadline` still bounds the whole wait.
                    size = last_size
                now = time.time()
                if size != last_size:
                    last_size = size
                    stable_since = now
                elif stable_since and (now - stable_since) >= self.stability_seconds:
                    break
                if now >= deadline:
                    print(f"[Watch] {os.path.basename(path)} is still growing "
                          f"after {MAX_STABILITY_WAIT_S / 60:.0f} min - giving up "
                          "on it for now. It will be picked up by --batch once "
                          "it has finished.")
                    return
                time.sleep(1)
        finally:
            # On EVERY exit - stabilised, vanished, timed out, or crashed
            # on something nobody predicted. A path left in _pending is a
            # video _maybe_watch() will refuse to queue ever again.
            with self._lock:
                self._pending.discard(path)

        self.on_ready(path)


# How long a file may keep growing before the watcher stops waiting on
# it. Long enough for a slow copy of a multi-GB stream, short enough that
# a stalled one does not block the folder forever.
MAX_STABILITY_WAIT_S = 30 * 60


class FolderWatcher:
    """Watch a folder and process new videos ONE AT A TIME.

    The serialisation is the important part. Each file gets its own
    stability-watching thread, and those used to call straight through to
    the handler - so eleven Twitch clips arriving together started eleven
    simultaneous censor passes. Each of those loads its own Whisper model,
    and eleven copies of large-v3 do not fit on a consumer GPU: CUDA
    returned "out of memory", every one of them silently fell back to the
    CPU, and the machine crawled.

    Videos now queue and a single worker drains them, so the second file
    starts when the first has finished. On a GPU that is also FASTER
    overall - one pass at full speed beats eleven fighting for VRAM.
    """

    def __init__(self, folder: str, supported_formats: tuple, stability_seconds: int, on_ready: Callable[[str], None]):
        os.makedirs(folder, exist_ok=True)
        self.folder = folder
        self._on_ready = on_ready
        self._queue: queue.Queue = queue.Queue()
        self._stopping = threading.Event()
        self._handler = _NewVideoHandler(supported_formats, stability_seconds,
                                         self._enqueue)
        self._observer = Observer()
        self._observer.schedule(self._handler, folder, recursive=False)
        self._paused = threading.Event()
        self._worker = None

    def consider(self, path: str) -> None:
        """Offer a file that was ALREADY here when the watch started.

        Through the same stability wait as a file that arrives, which is
        the whole point. The startup sweep used to call the processor
        directly, so a video still being written - a 7 GB download in
        progress, a copy onto the drive - was picked up half finished. A
        four-hour stream went up as fifty-four minutes that way, and
        nothing in the log said the file had been truncated, because from
        the uploader's side it had not been: that was genuinely all there
        was at the moment it looked.
        """
        self._handler._maybe_watch(path)

    def _enqueue(self, path: str) -> None:
        depth = self._queue.qsize()
        if depth:
            print(f"[Queue] {os.path.basename(path)} is next - {depth} "
                  "already waiting. Videos are processed one at a time so "
                  "the GPU is not split between them.")
        self._queue.put(path)

    def _drain(self) -> None:
        while not self._stopping.is_set():
            try:
                path = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self._on_ready(path)
            except Exception as exc:
                # One bad video must not end the watch. Whatever went
                # wrong was already reported by the handler; this only
                # keeps the queue moving.
                print(f"[Queue] {os.path.basename(path)} failed: {exc}")
            finally:
                self._queue.task_done()

    def start(self) -> None:
        self._stopping.clear()
        self._worker = threading.Thread(target=self._drain, name="upload-worker",
                                        daemon=True)
        self._worker.start()
        self._observer.start()

    def pause(self) -> None:
        self._observer.stop()
        self._observer.join()

    def resume(self) -> None:
        self._observer = Observer()
        self._observer.schedule(self._handler, self.folder, recursive=False)
        self._observer.start()

    def stop(self) -> None:
        self._stopping.set()
        self._observer.stop()
        self._observer.join()
        if self._worker is not None:
            self._worker.join(timeout=5)
