"""
Optional second opinion on which windows are worth clipping.

WHY THIS EXISTS
---------------
`highlights.py` scores what a transcript LOOKS like - reaction words,
shouting, speech density, where the peak sits. That is a real signal and
it is free, but it cannot read. It does not know that the funny part was
the reply rather than the shout, or that the twenty seconds before were
setup that the clip needs to make sense.

Both open-source generators worth comparing against - AI-Youtube-Shorts-
Generator and OpenShorts - reached the same conclusion and solved it the
same way: hand the transcript to a language model and ask it which
moments a person would clip. That is the one idea in either project that
this pipeline did not already have, and it is the one that decides whether
a clip makes sense.

What is NOT taken from them: the rest. One routes every video through a
paid credit API; the other is a Docker stack with Postgres, S3, a React
dashboard and four vendor keys. Neither is an improvement on a folder and
a GPU that already work.

HOW IT FAILS
------------
Silently, into the local scorer. No key, no network, a bad response, a
timeout - all of them return None and the caller uses the scores it
already had. A clip pipeline that stops working because a model provider
is down is worse than one that occasionally picks a duller clip.

Nothing here is a dependency: it speaks HTTP with urllib, so `pip install`
gains nothing new.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Optional

GEMINI = "gemini"
OPENAI = "openai"

# Last resort only. Model names are retired faster than a pinned default
# can be maintained - the first key tried against this hit "gemini-2.5-flash
# is no longer available to new users" - so the real answer is to ASK the
# provider what it has and take the best of it. See resolve_model().
DEFAULT_MODELS = {
    GEMINI: "gemini-flash-latest",
    OPENAI: "gpt-4o-mini",
}

# Model families that cannot do this job, whatever they are called.
_NOT_TEXT = ("embedding", "aqa", "imagen", "veo", "image", "tts", "audio",
             "vision", "live", "realtime", "whisper", "dall-e", "moderation")

_KEY_NAMES = {
    GEMINI: ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    OPENAI: ("OPENAI_API_KEY",),
}

_TIMEOUT = 90

# Each candidate's transcript, trimmed. The whole point is the model
# reading what was said; a few hundred characters is a clip's worth of
# speech, and sending more of sixty candidates only costs latency.
_MAX_TEXT_CHARS = 700

# How many candidates to offer per clip wanted. Enough that the model has
# a real choice, few enough that the prompt stays small.
CANDIDATE_MULTIPLIER = 4
MAX_CANDIDATES = 60

# How many candidates get FRAMES attached. Every image is tokens and
# upload time, and the text pass has already sorted the list - so the
# ones near the bottom are not worth looking at. Twenty-four covers a
# twenty-clip run with room to reject.
VISION_MAX_CANDIDATES = 24


SYSTEM_PROMPT = """\
You pick the moments worth cutting out of a live stream.

The streamer is loud, funny and swears a lot; the audience is there for
reactions and for the back-and-forth with whoever else is on the call.
You are choosing for Reels and Shorts, where a viewer decides in two
seconds whether to keep watching.

Pick the candidates where SOMETHING HAPPENS - an argument, a punchline, a
reaction, someone getting caught out, a story landing. Reject the ones
that are only loud, only filler, or only make sense to somebody who
watched the whole stream. If a candidate needs context it does not
contain, it is not a clip.

For each one you pick, write a TITLE:
- what actually happens in it, in the streamer's own words where possible
- no hashtags, no emoji, no "you won't believe", no ALL CAPS
- under 70 characters, and a real phrase rather than a label
- never "Funny Moment", "Epic Fail", "Clip 3" or anything that would fit
  any other clip equally well

Reply with JSON only:
{"clips": [{"index": <candidate number>, "score": <0-100>, "title": "..."}]}

Order does not matter. Return fewer than asked rather than padding with
candidates you would not actually post.\
"""


def api_key(provider: str) -> str:
    for name in _KEY_NAMES.get(provider, ()):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def available(preferred: str = "") -> tuple:
    """(provider, key) for whichever is configured, or ("", "").

    Gemini first when neither is named: its free tier covers this
    workload, so the default costs nothing to have switched on.
    """
    order = [preferred] if preferred in (GEMINI, OPENAI) else [GEMINI, OPENAI]
    for provider in order:
        key = api_key(provider)
        if key:
            return provider, key
    return "", ""


# ── Which model to use ───────────────────────────────────────────────────

def _model_rank(name: str) -> tuple:
    """Sort key for a model name, best first (use with reverse=True).

    Ordered on what this job wants: a current, general-purpose, fast
    model. Flash-class first because the work is reading a few thousand
    words and returning a short list - a reasoning-heavy model would cost
    more and take longer to reach the same answer.
    """
    name = name.lower().rsplit("/", 1)[-1]
    version = 0.0
    match = re.search(r"(\d+(?:\.\d+)?)", name)
    if match:
        try:
            version = float(match.group(1))
        except ValueError:
            version = 0.0
    family = 2 if ("flash" in name and "lite" not in name) else \
        1 if ("flash" in name or "mini" in name) else 0
    # A stable name outranks a dated snapshot of the same thing, and
    # anything outranks a preview that can disappear mid-week.
    stable = 0 if any(tag in name for tag in ("preview", "exp", "beta")) else 1
    return (stable, family, version)


def usable_models(names: list) -> list:
    """The named models that could do this, best first."""
    keep = [n for n in names
            if n and not any(bad in n.lower() for bad in _NOT_TEXT)]
    return sorted(keep, key=_model_rank, reverse=True)


def list_models(provider: str, key: str) -> list:
    """What this key can actually reach. Empty on any failure."""
    if provider != GEMINI:
        # OpenAI's list is large and mostly irrelevant here, and its
        # small-model names have been stable for years.
        return []
    url = ("https://generativelanguage.googleapis.com/v1beta/models"
           f"?key={key}&pageSize=200")
    request = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    names = []
    for entry in data.get("models") or []:
        if not isinstance(entry, dict):
            continue
        methods = entry.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        name = str(entry.get("name") or "").rsplit("/", 1)[-1]
        if name:
            names.append(name)
    return names


def resolve_model(provider: str, key: str, configured: str = "") -> str:
    """The model to call: whatever was configured, else the best on offer.

    Pinning a name in code was the bug. Providers retire models on their
    own schedule and a pinned default fails with a 404 that reads like a
    broken key - which is exactly how this was found. Asking costs one
    request and survives the next retirement without an edit.
    """
    if configured:
        return configured
    available_names = usable_models(list_models(provider, key))
    return available_names[0] if available_names else DEFAULT_MODELS[provider]


def _timestamp(seconds: float) -> str:
    seconds = int(max(0.0, seconds))
    return f"{seconds // 60}m{seconds % 60:02d}s"


VISION_NOTE = """\

You can SEE two frames from each candidate - one early, one near the end.
Use them. On this channel the funniest moments are visual: a face
reaction, someone walking up behind, a fight starting. The transcript
misses all of it, and a candidate whose words are dull but whose frames
show something happening is exactly the one worth picking.

Say what you can see in the title where it helps. Do not describe the
frames back to me.\
"""


def build_vision_contents(candidates: list, count: int, source_path: str,
                          grab=None) -> list:
    """Gemini `contents` parts: the prompt, then text+frames per candidate.

    Falls back to text-only parts for any candidate whose frames could
    not be read, so one unreadable stretch does not cost the whole pass.
    """
    from . import vision_frames

    grab = grab or vision_frames.frames_for
    parts = [{"text": f"Pick the {count} best of these {len(candidates)} "
                      f"candidates.\n"}]
    for number, highlight in enumerate(candidates, start=1):
        text = " ".join((highlight.text or "").split())[:_MAX_TEXT_CHARS]
        parts.append({"text": (
            f"\n[{number}] at {_timestamp(highlight.start)}, "
            f"{highlight.end - highlight.start:.0f}s\n{text}\n")})
        try:
            frames = grab(source_path, highlight.start, highlight.end)
        except Exception:
            frames = []
        for jpeg in frames:
            parts.append(vision_frames.as_inline_data(jpeg))
    return parts


def build_prompt(candidates: list, count: int) -> str:
    """The candidate list, as the model sees it."""
    lines = [f"Pick the {count} best of these {len(candidates)} candidates.",
             ""]
    for number, highlight in enumerate(candidates, start=1):
        text = " ".join((highlight.text or "").split())[:_MAX_TEXT_CHARS]
        lines.append(
            f"[{number}] at {_timestamp(highlight.start)}, "
            f"{highlight.end - highlight.start:.0f}s\n{text}\n")
    return "\n".join(lines)


# ── Talking to a provider ────────────────────────────────────────────────

def _post(url: str, payload: dict, headers: dict) -> Optional[dict]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError):
        return None


def _post_detailed(url: str, payload: dict, headers: dict) -> tuple:
    """(data, error). Same call as _post, but says what went wrong.

    The normal path does not want the reason - it falls back silently and
    the reason would just be noise on every clip run. `--check-llm` wants
    nothing else, because "it did not work" is the one answer that helps
    nobody when a key has just been pasted in.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace")), ""
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
            detail = str(payload.get("error", {}).get("message", "")).strip()
        except Exception:
            detail = ""
        return None, f"HTTP {exc.code}: {detail or exc.reason}"
    except urllib.error.URLError as exc:
        return None, f"could not reach the API: {exc.reason}"
    except (OSError, ValueError, TimeoutError) as exc:
        return None, str(exc)


def check(provider: str = "", model: str = "") -> tuple:
    """(ok, detail) for the configured key. One tiny real request.

    A key that is present but wrong looks exactly like a key that works,
    right up until the clips come out chosen by the fallback scorer and
    nobody knows why. This asks.
    """
    provider, key = available(provider)
    if not provider:
        return False, ("no GEMINI_API_KEY or OPENAI_API_KEY in .env")
    model = resolve_model(provider, key, model)

    if provider == GEMINI:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        data, error = _post_detailed(
            url, {"contents": [{"parts": [{"text": "Reply with: ok"}]}]}, {})
    else:
        data, error = _post_detailed(
            "https://api.openai.com/v1/chat/completions",
            {"model": model,
             "messages": [{"role": "user", "content": "Reply with: ok"}],
             "max_tokens": 5},
            {"Authorization": f"Bearer {key}"})

    if error:
        return False, f"{provider} ({model}) rejected the key - {error}"
    if not isinstance(data, dict):
        return False, f"{provider} returned nothing usable"
    return True, f"{provider} ({model}) answered - the key works"


def _ask_gemini(key: str, model: str, prompt: str) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": 0.4},
    }
    data = _post(url, payload, {})
    if not isinstance(data, dict):
        return ""
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return ""


def _ask_gemini_vision(key: str, model: str, parts: list) -> str:
    """Same call as _ask_gemini, with images among the parts."""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT + VISION_NOTE}]},
        "contents": [{"parts": parts}],
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": 0.4},
    }
    data = _post(url, payload, {})
    if not isinstance(data, dict):
        return ""
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return ""


def _ask_openai(key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
    }
    data = _post("https://api.openai.com/v1/chat/completions", payload,
                 {"Authorization": f"Bearer {key}"})
    if not isinstance(data, dict):
        return ""
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""


def parse_reply(raw: str, candidate_count: int) -> list:
    """[(index, score, title)] from the model's JSON. Junk is dropped.

    Tolerant on purpose: a model that wraps its JSON in a code fence, or
    returns a bare list instead of the documented object, has still done
    the job asked of it and should not cost a whole stream's clips.
    """
    if not raw:
        return []
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except ValueError:
        return []

    entries = data.get("clips") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []

    picked, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        if not 1 <= index <= candidate_count or index in seen:
            continue
        seen.add(index)
        try:
            score = float(entry.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        title = " ".join(str(entry.get("title") or "").split())
        picked.append((index, score, title))
    return picked


def rank(candidates: list, count: int, provider: str = "",
         model: str = "", ask=None, source_path: str = "") -> Optional[list]:
    """The candidates a model would actually post, or None.

    None means "no opinion" - no key, no network, nothing usable came
    back - and the caller keeps its own ranking. It never means "none of
    these are any good".
    """
    if not candidates or count <= 0:
        return None

    provider, key = available(provider)
    if not provider:
        return None
    model = resolve_model(provider, key, model)

    shortlist = candidates[:MAX_CANDIDATES]

    # Vision is Gemini-only here and only when there is a file to sample.
    # It is tried FIRST and falls back to the words on any failure: a
    # model that cannot see is the behaviour this had all along, and it
    # is much better than no clips.
    raw = ""
    if source_path and provider == GEMINI and ask is None:
        looking = shortlist[:VISION_MAX_CANDIDATES]
        try:
            parts = build_vision_contents(looking, count, source_path)
            raw = _ask_gemini_vision(key, model, parts)
        except Exception:
            raw = ""
        if raw:
            shortlist = looking
            print(f"[Clips] A model watched {len(looking)} candidates.")

    if not raw:
        prompt = build_prompt(shortlist, count)
        ask = ask or (_ask_gemini if provider == GEMINI else _ask_openai)
        try:
            raw = ask(key, model, prompt)
        except Exception:
            return None

    picked = parse_reply(raw, len(shortlist))
    if not picked:
        return None

    picked.sort(key=lambda item: item[1], reverse=True)
    chosen = []
    for index, score, title in picked[:count]:
        highlight = shortlist[index - 1]
        if title:
            # The model read the clip; its title beats the best sentence
            # picked out of it by length and punctuation alone.
            highlight.hook = title
        highlight.score = score or highlight.score
        chosen.append(highlight)
    chosen.sort(key=lambda h: h.start)
    return chosen
