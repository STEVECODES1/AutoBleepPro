"""
Duplicate-upload detection: filename + sha256 content hash, tracked in a
small local JSON store next to this project (not moviepy/API-dependent so
it's trivially testable).

Tracking is per-platform and persisted immediately after each platform's
result is known - NOT batched until both platforms are attempted. That
matters: if the process is interrupted (Ctrl+C, crash) after YouTube
succeeds but before Rumble finishes, a later re-run must remember that
YouTube already succeeded and only retry Rumble, instead of re-uploading
to YouTube and creating a real duplicate video.
"""

import hashlib
import json
import time
import threading
import os
from dataclasses import dataclass, field
from typing import Optional


def hash_file(path: str, chunk_size: int = 8 * 1024 * 1024) -> str:
    """sha256 of a file's contents, streamed so multi-GB videos don't get
    loaded into memory at once."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _is_success(result: Optional[str]) -> bool:
    return bool(result) and not result.startswith("FAILED:")


@dataclass
class DuplicateChecker:
    store_path: str
    _seen: dict = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        # YouTube and Rumble record their results from two threads. The
        # caller holds a lock today, but this store is the one file whose
        # corruption means re-uploading a whole stream, so it does not
        # rely on every future caller remembering to.
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        """Read the store. A file we cannot read is kept, not discarded.

        An empty store means every video ever uploaded looks new, so a
        corrupt file must never quietly become one - that is a re-upload
        of the entire back catalogue. The unreadable file is moved aside
        instead, so the evidence survives and the next write starts clean.
        """
        with self._lock:
            if not os.path.exists(self.store_path):
                self._seen = {}
                return
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
            except (OSError, ValueError) as exc:
                spoiled = f"{self.store_path}.corrupt-{int(time.time())}"
                try:
                    os.replace(self.store_path, spoiled)
                except OSError:
                    spoiled = "(could not be moved aside)"
                print(f"[Dedup] WARNING: {self.store_path} could not be read "
                      f"({exc}). Moved to {spoiled}. Everything already "
                      "uploaded will look new until it is restored - check "
                      "before running --batch.")
                self._seen = {}
                return
            self._seen = loaded if isinstance(loaded, dict) else {}

    def _save(self) -> None:
        """Write atomically: temp file, then replace.

        A plain open(path, "w") truncates the real file first, so a crash
        or a full disk mid-write leaves a half-written store - and a store
        that will not parse is a store that re-uploads everything.
        """
        with self._lock:
            os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
            tmp = f"{self.store_path}.{os.getpid()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._seen, f, indent=2)
                os.replace(tmp, self.store_path)
            except OSError:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise

    def get_platform_result(self, file_hash: str, platform: str) -> Optional[str]:
        """The recorded URL for `platform` if this exact file content
        already succeeded there, else None. A prior FAILED result doesn't
        count - that platform is still eligible for a fresh attempt."""
        record = self._seen.get(file_hash)
        if not record:
            return None
        result = record.get("results", {}).get(platform)
        return result if _is_success(result) else None

    def record_platform_result(self, file_hash: str, filename: str, platform: str, result: str,
                               title: str = None) -> None:
        """Persist one platform's result immediately (not batched with the
        other platform), so a partially-completed run is resumable. `title`
        (the generated upload title) enables title-based dedup for
        re-encoded copies of the same stream, whose hashes differ."""
        record = self._seen.setdefault(file_hash, {"filename": filename, "results": {}})
        record["filename"] = filename
        record["results"][platform] = result
        if title:
            record.setdefault("titles", {})[platform] = title
        self._save()

    def find_platform_title(self, platform: str, title: str):
        """URL of a previous SUCCESSFUL upload to `platform` with this exact
        generated title, or None. Catches the same stream arriving as a
        different file (re-encode/remux), which hash matching can't."""
        for record in self._seen.values():
            if record.get("titles", {}).get(platform) == title and _is_success(
                record.get("results", {}).get(platform)
            ):
                return record["results"][platform]
        return None

    def find_hashes_by_filename(self, filename: str) -> list:
        """Recorded hashes whose stored filename matches `filename`.

        Lets callers look a record up without re-reading (and hashing) the
        video itself, which on an external drive is minutes of I/O for a
        multi-GB file.
        """
        target = os.path.basename(filename or "").lower()
        if not target:
            return []
        return [h for h, rec in self._seen.items()
                if os.path.basename(rec.get("filename", "")).lower() == target]

    def forget(self, file_hash: str, platform: str = None) -> bool:
        """Drop a recorded result so the file can be retried.

        Needed when a result was recorded wrongly - e.g. a duplicate check
        matched the wrong video and marked an upload "done" that never
        happened. Returns True if anything was removed.
        """
        record = self._seen.get(file_hash)
        if not record:
            return False
        if platform is None:
            del self._seen[file_hash]
        else:
            if platform not in record.get("results", {}):
                return False
            record["results"].pop(platform, None)
            record.get("titles", {}).pop(platform, None)
        self._save()
        return True

    def is_fully_uploaded(self, file_hash: str, platforms: tuple = ("youtube", "rumble")) -> bool:
        """True only if every platform in `platforms` already has a
        successful (non-FAILED) result recorded for this file content."""
        record = self._seen.get(file_hash)
        if not record:
            return False
        results = record.get("results", {})
        return all(_is_success(results.get(p)) for p in platforms)
