"""What was posted where, written the moment it happens (11-04) - so every
post can be shown with its link (tools/post_links.py turns ids into links).

One JSON line per post in logs/post_refs.jsonl:
  {"at", "clip", "platform", "ref", "title"}
`ref` is a URL (Shorts), "buffer post <id>" (X/TikTok via Buffer), or a
bare Meta id (Instagram media id, Facebook video id). Never raises.
"""

import json
import os
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(_ROOT, "logs", "post_refs.jsonl")


def record(clip_path: str, platform: str, ref, title: str = "") -> None:
    try:
        from utils.clip_queue import clip_key

        ref = str(ref or "").strip()
        if not ref or ref in ("True", "dry-run"):
            return
        line = {"at": time.time(), "clip": clip_key(clip_path), "platform": platform,
                "ref": ref, "title": (title or "").strip().splitlines()[0][:150]
                if (title or "").strip() else ""}
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        with open(PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


def recent(hours: float = 48) -> list:
    cutoff = time.time() - hours * 3600
    out = []
    try:
        with open(PATH, encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("at", 0) >= cutoff:
                    out.append(item)
    except OSError:
        pass
    return out
