"""Put the full-stream link into Shorts that went up before it existed.

Since 10-31 clips are cut and posted right after the censor pass, while the
full stream is still uploading - so the first Shorts of a stream go up with
no "Full video:" line (links are read at post time). Once the full stream's
links are noted (utils/stream_links.remember), this goes back over the
Shorts posted in the last few days (their URLs are in clip_memory.json),
and adds the same lines youtube_shorts.description_for would have written:

  ▶ Full video: https://www.youtube.com/watch?v=...
  ▶ Uncut & uncensored on Rumble: https://rumble.com/v...

Title, tags, category and language stay as they are. One read (1 unit)
and one update (50 units) per Short; each Short is done once (logs/
short_links.json). Never raises.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Iterable

FULL = "▶ Full video: "
UNCUT = "▶ Uncut & uncensored on Rumble: "
SNIPPET_FIELDS = ("title", "description", "tags", "categoryId",
                  "defaultLanguage", "defaultAudioLanguage")
MAX_AGE_DAYS = 4
MAX_PER_RUN = 25
_ID = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")


def video_id(url: str) -> str:
    m = _ID.search(url or "")
    return m.group(1) if m else ""


def with_links(description: str, full: str, uncut: str = "") -> str:
    """The description with the link lines under its first paragraph,
    or unchanged when the full link is already there."""
    body = description or ""
    if not full or full in body:
        return body
    lines = [FULL + full]
    if uncut and uncut.startswith("https://rumble.com/v") and uncut not in body:
        lines.append(UNCUT + uncut)
    head, sep, rest = body.partition("\n\n")
    return f"{head}\n\n" + "\n".join(lines) + (f"\n\n{rest}" if sep else "")


def _ledger_path(logs_folder: str) -> str:
    return os.path.join(logs_folder or "logs", "short_links.json")


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass


def candidates(records, done: dict, folders: Iterable[str] = (),
               now: float = 0.0, link_for_clip=None):
    """[(video_id, full, uncut)] for Shorts that can now get their link."""
    if link_for_clip is None:
        from utils.stream_links import link_for_clip
    now = now or time.time()
    out, seen = [], set(done)
    for r in records:
        url = (getattr(r, "posted", {}) or {}).get("youtube_shorts", "")
        vid = video_id(url)
        if not vid or vid in seen:
            continue
        if now - float(getattr(r, "created", 0) or 0) > MAX_AGE_DAYS * 86400:
            continue
        path = getattr(r, "path", "") or ""
        try:
            full = link_for_clip(path, "youtube", folders)
            uncut = link_for_clip(path, "rumble", folders)
        except Exception:
            continue
        if full and ("youtube.com" in full or "youtu.be" in full):
            out.append((vid, full, uncut))
            seen.add(vid)
    return out


def _service(cfg):
    try:
        with open(os.path.join(cfg.project_root, "config.json"), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, AttributeError):
        return None, ""
    from publishers.youtube_shorts import YouTubeShortsPublisher
    from utils.youtube_uploader import YouTubeUploader

    pub = YouTubeShortsPublisher(raw)
    token = pub.token_path()
    if not token or not os.path.isfile(token):
        return None, ""
    secrets = pub.client_secrets_path()
    return YouTubeUploader(secrets, token).get_service(), secrets


def run(cfg, folders: Iterable[str] = (), service=None, records=None,
        say=print) -> int:
    """Add the links where they are missing. How many Shorts changed."""
    if os.environ.get("PYTEST_CURRENT_TEST") and service is None:
        return 0
    path = _ledger_path(getattr(cfg.general, "logs_folder", ""))
    done = _load(path)
    try:
        if records is None:
            from autoreel.memory import Ledger, ledger_path

            records = Ledger(ledger_path(cfg)).records()
        todo = candidates(records, done, folders)[:MAX_PER_RUN]
    except Exception as exc:
        say(f"[Shorts] Link backfill: could not read the clips ({exc}).")
        return 0
    if not todo:
        return 0
    secrets = ""
    if service is None:
        try:
            service, secrets = _service(cfg)
        except Exception as exc:
            say(f"[Shorts] Link backfill: not signed in ({exc}).")
            return 0
        if service is None:
            return 0
    changed = 0
    try:
        from utils import quota
        project = quota.project_of(secrets) if secrets else quota.MAIN
    except Exception:
        quota, project = None, ""
    for vid, full, uncut in todo:
        try:
            items = service.videos().list(part="snippet", id=vid).execute().get("items") or []
            if not items:
                done[vid] = {"at": time.time(), "gone": True}
                continue
            snippet = items[0].get("snippet") or {}
            old = snippet.get("description", "") or ""
            new = with_links(old, full, uncut)
            if new != old:
                body = {k: snippet[k] for k in SNIPPET_FIELDS if k in snippet}
                body["description"] = new
                service.videos().update(part="snippet",
                                        body={"id": vid, "snippet": body}).execute()
                changed += 1
                if quota is not None:
                    quota.record(51, f"short link {vid}", project=project)
            done[vid] = {"at": time.time(), "full": full}
        except Exception as exc:
            say(f"[Shorts] Link backfill {vid}: {str(exc)[:120]}")
    _save(path, done)
    if changed:
        say(f"[Shorts] Added the full-stream link to {changed} Short(s) "
            f"posted before the stream was up.")
    return changed
