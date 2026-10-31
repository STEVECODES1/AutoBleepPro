"""A story hook in front of the wopovod full-stream title (owner, 10-31).

  before: "Stackswopo - TOPIC BOUL - 10-30-26 (FULL STREAM)"
  after:  "Stackswopo Gets EXPOSED By His Ex | TOPIC BOUL - 10-30-26 (FULL STREAM)"

The stream name and date stay (search, links, the owner's own naming); the
hook in front is what makes someone click - the Stackswopo videos that get
views are named for what HAPPENS, not for the stream.

One model request per stream, right after the censor pass (the transcript
is there by then), before the upload. The hook is saved per file content
in logs/title_hooks.json, so a retry or a re-run gets the SAME title and
the duplicate checks (which match on title) keep working. Anything wrong -
no model answers, too long, a kid/age word, a word YouTube would read
badly - and the plain title is used, as before.
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Callable, Optional

_LOCK = threading.Lock()
MAX_TITLE = 100
MAX_HOOK = 60
_LEAD = re.compile(r"^\s*stackswopo\s*[-|:]\s*", re.I)
_NAMES = re.compile(r"^(stackswopo|stacks|johnny cox)\b", re.I)

PROMPT = """You name YouTube videos for a streamer called Stackswopo (his GTA RP character is Johnny Cox).
Below is the transcript of one whole livestream ("[seconds] what was said").
Stream name: {name}

Write ONE short title hook about the single most dramatic or funny thing that
really happens in it - the way a viewer would tell a friend. Third person,
3 to 9 words, starting with "Stackswopo" (or "Johnny Cox" if it happens in GTA RP).
Good: "Stackswopo Gets EXPOSED By His Ex", "Johnny Cox Gets Robbed In GTA RP",
"Stackswopo Loses It After Losing 20 Games".
It must be true to the transcript. No children or ages, no slurs, no sexual
words, no animal words about people, no emojis, no hashtags, no quotes.

Reply with JSON only: {{"hook": "..."}}

TRANSCRIPT:
{transcript}"""


def _ledger_path(cfg) -> str:
    folder = getattr(getattr(cfg, "general", None), "logs_folder", "") or "logs"
    return os.path.join(folder, "title_hooks.json")


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: str, key: str, entry: dict) -> None:
    try:
        with _LOCK:
            data = _load(path)
            data[key] = entry
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, path)
    except OSError:
        pass


def clean_hook(raw: str) -> str:
    """The hook as it may be used, or "" when it may not."""
    hook = " ".join(str(raw or "").replace('"', "").replace("|", " ").split())
    hook = hook.strip(" -:.!,")
    if not hook or len(hook) > MAX_HOOK or len(hook.split()) < 3 or len(hook.split()) > 10:
        return ""
    if "#" in hook or re.search(r"[^\w\s'’,.!?&$-]", hook):
        return ""
    if not _NAMES.match(hook):
        hook = "Stackswopo " + hook
    try:
        from autoreel.safe_text import child_risk

        if child_risk(hook):
            return ""
    except Exception:
        return ""          # a guard that cannot run is a no
    try:
        from utils.text_guard import youtube_title_risk

        if youtube_title_risk(hook):
            return ""
    except Exception:
        return ""
    return hook


def hooked(base_title: str, hook: str, limit: int = MAX_TITLE) -> str:
    """'<hook> | <base without its leading "Stackswopo - ">', or base."""
    hook = clean_hook(hook)
    if not hook or not base_title:
        return base_title
    rest = _LEAD.sub("", base_title, count=1).strip()
    out = f"{hook} | {rest}"
    return out if len(out) <= limit else base_title


def ask(segments, stream_name: str, askers=None) -> str:
    """One hook from the first model that gives a usable one, or ""."""
    if askers is None:
        if os.environ.get("PYTEST_CURRENT_TEST"):
            return ""
        from utils.stories import _askers

        askers = _askers()
    from utils.stories import condensed

    text = condensed(segments, every=30.0, limit=40000)
    if len(text) < 200:
        return ""
    prompt = PROMPT.format(name=stream_name or "(none)", transcript=text)
    for name, call in askers:
        try:
            reply = call(prompt) or ""
            i = reply.find("{")
            obj, _ = json.JSONDecoder().raw_decode(reply[i:]) if i >= 0 else ({}, 0)
            hook = clean_hook(obj.get("hook", "")) if isinstance(obj, dict) else ""
        except Exception as exc:
            print(f"[Title] {name}: no hook ({str(exc)[:80]})")
            continue
        if hook:
            return hook
    return ""


def for_stream(cfg, key: str, source: str, base_title: str, stream_name: str,
               clean: Optional[Callable[[str], str]] = None,
               segments=None, askers=None) -> str:
    """The YouTube title for this full stream - hooked when it can be.

    `key` is the file's content hash: the first answer is kept for good,
    so every later run of the same file asks nothing and gets the same
    title back."""
    if not key or not base_title:
        return base_title
    path = _ledger_path(cfg)
    saved = _load(path).get(key)
    if isinstance(saved, dict) and "hook" in saved:
        return hooked(base_title, saved.get("hook", "")) if saved.get("hook") else base_title
    if segments is None:
        try:
            from utils.recap import transcript_of

            segments = transcript_of(cfg, source)
        except Exception:
            segments = []
    hook = ask(segments or [], stream_name, askers=askers)
    if hook and clean is not None:
        try:
            if clean(hook) != hook:      # the YouTube title cleaner had to change it
                hook = ""
        except Exception:
            hook = ""
    title = hooked(base_title, hook) if hook else base_title
    if title == base_title:
        hook = ""
    if hook or segments:
        # A "no hook" is kept too once the transcript was really read, so a
        # retry does not change the title under the duplicate checks.
        _save(path, key, {"hook": hook, "title": title, "base": base_title})
    print(f"[Title] {'Hook: ' + title if hook else 'No hook - keeping the plain title.'}")
    return title
