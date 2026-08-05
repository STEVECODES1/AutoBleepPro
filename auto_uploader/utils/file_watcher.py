"""
Watches a folder for new video files using watchdog, waiting for each file
to stop growing (i.e. finish being copied/written) before handing it off -
otherwise a half-copied file would get "uploaded" mid-write.
"""

import os
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


# Files that live in a watch folder by design and were never meant to be
# videos: git's folder placeholder, the .txt title sidecars this tool
# reads, its own caches, and the junk Windows/macOS scatter around. They
# are skipped like any other non-video, just silently - a [SKIP] line per
# run for each of these is noise that hides the skips that matter.
_SIDECAR_NAMES = frozenset({
    ".gitkeep", ".gitignore", "thumbs.db", "desktop.ini", ".ds_store",
})
_SIDECAR_EXTENSIONS = frozenset({
    ".txt", ".json", ".md", ".log", ".srt", ".vtt", ".jpg", ".jpeg",
    ".png", ".webp", ".ini", ".db", ".lnk", ".url", ".part", ".ytdl",
})


def is_sidecar_file(path: str) -> bool:
    """True for a companion/system file that is not a failed video."""
    name = os.path.basename(path).lower()
    if name in _SIDECAR_NAMES or name.startswith("."):
        return True
    return os.path.splitext(name)[1] in _SIDECAR_EXTENSIONS


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
        last_size = -1
        stable_since = None
        while True:
            try:
                size = os.path.getsize(path)
            except FileNotFoundError:
                return  # file disappeared (renamed/deleted) before it stabilized
            now = time.time()
            if size != last_size:
                last_size = size
                stable_since = now
            elif stable_since and (now - stable_since) >= self.stability_seconds:
                break
            time.sleep(1)

        with self._lock:
            self._pending.discard(path)
        self.on_ready(path)


class FolderWatcher:
    def __init__(self, folder: str, supported_formats: tuple, stability_seconds: int, on_ready: Callable[[str], None]):
        os.makedirs(folder, exist_ok=True)
        self.folder = folder
        self._handler = _NewVideoHandler(supported_formats, stability_seconds, on_ready)
        self._observer = Observer()
        self._observer.schedule(self._handler, folder, recursive=False)
        self._paused = threading.Event()

    def start(self) -> None:
        self._observer.start()

    def pause(self) -> None:
        self._observer.stop()
        self._observer.join()

    def resume(self) -> None:
        self._observer = Observer()
        self._observer.schedule(self._handler, self.folder, recursive=False)
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()
