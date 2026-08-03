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
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.store_path):
            with open(self.store_path, "r", encoding="utf-8") as f:
                self._seen = json.load(f)
        else:
            self._seen = {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.store_path) or ".", exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(self._seen, f, indent=2)

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

    def is_fully_uploaded(self, file_hash: str, platforms: tuple = ("youtube", "rumble")) -> bool:
        """True only if every platform in `platforms` already has a
        successful (non-FAILED) result recorded for this file content."""
        record = self._seen.get(file_hash)
        if not record:
            return False
        results = record.get("results", {})
        return all(_is_success(results.get(p)) for p in platforms)
