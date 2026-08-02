"""
Duplicate-upload detection: filename + sha256 content hash, tracked in a
small local JSON store next to this project (not moviepy/API-dependent so
it's trivially testable).
"""

import hashlib
import json
import os
from dataclasses import dataclass, field


def hash_file(path: str, chunk_size: int = 8 * 1024 * 1024) -> str:
    """sha256 of a file's contents, streamed so multi-GB videos don't get
    loaded into memory at once."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


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

    def is_duplicate(self, path: str) -> bool:
        """True if this exact file content (by hash) has already been
        uploaded, regardless of filename or which folder it's in now."""
        return hash_file(path) in self._seen

    def mark_uploaded(self, path: str, results: dict) -> None:
        """Record a file as uploaded. `results` is e.g.
        {"youtube": "https://...", "rumble": "https://..."} - stored for
        reference so upload history is inspectable in the JSON file."""
        file_hash = hash_file(path)
        self._seen[file_hash] = {
            "filename": os.path.basename(path),
            "results": results,
        }
        self._save()
