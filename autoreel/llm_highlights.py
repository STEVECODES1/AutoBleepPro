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
import time
import urllib.error
import urllib.request
from typing import Optional

GEMINI = "gemini"
OPENAI = "openai"
ANTHROPIC = "anthropic"

# Last resort only. Model names are retired faster than a pinned default
# can be maintained - the first key tried against this hit "gemini-2.5-flash
# is no longer available to new users" - so the real answer is to ASK the
# provider what it has and take the best of it. See resolve_model().
DEFAULT_MODELS = {
    GEMINI: "gemini-flash-latest",
    OPENAI: "gpt-4o-mini",
    ANTHROPIC: "claude-sonnet-5",
}

# Model families that cannot do this job, whatever they are called.
_NOT_TEXT = ("embedding", "aqa", "imagen", "veo", "image", "tts", "audio",
             "vision", "live", "realtime", "whisper", "dall-e", "moderation")

_KEY_NAMES = {
    GEMINI: ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    OPENAI: ("OPENAI_API_KEY",),
    ANTHROPIC: ("ANTHROPIC_API_KEY",),
}

# Tried in this order. Gemini first because it is the only one here that
# can be shown the FRAMES, which is the whole reason the picks got good;
# the others are text-only and are the backstop for the days it will not
# answer.
PROVIDER_ORDER = (GEMINI, OPENAI, ANTHROPIC)

_TIMEOUT = 90

# The vision pass is a different size of request: dozens of JPEGs in one
# body, which the model has to receive AND look at before it answers a
# word. On a real run it timed out at

#   The vision pass failed (The read operation timed out (48 images, 0.6 MB))

# and the whole point of the pass - picking clips on what is on screen
# rather than on the transcript alone - was lost to a clock set for a
# text request.
_VISION_TIMEOUT = 300

# One wait, not a retry loop: a busy model clears in seconds and a
# pipeline that hammers a rate limit gets a longer one.
_BUSY_RETRY_SECONDS = 20

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

# Total images in one request. Two per candidate times twenty-four is
# forty-eight, and a request that grows past this starts being refused
# for its size rather than its content - which arrives as an empty reply
# and looks exactly like the model having no opinion.
VISION_MAX_IMAGES = 48


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
- NEVER reproduce a slur or a masked word. Some transcripts arrive with
  words starred out; those are censored on purpose. Describe the moment
  instead - "the instant he finds out her age" - and never copy the
  stars into the title either
- no hashtags, no emoji, no "you won't believe", no ALL CAPS
- under 70 characters, and a real phrase rather than a label
- never "Funny Moment", "Epic Fail", "Clip 3" or anything that would fit
  any other clip equally well

Reply with JSON only:
{"clips": [{"index": <candidate number>, "score": <0-100>, "title": "..."}]}

Order does not matter.

RETURN FEWER THAN ASKED. This matters more than any other instruction
here. You are given far more candidates than there are good moments in a
stream, and a list padded to the number requested is worse than a short
list - every weak clip posted costs the channel more than a missing one
would. If only six of forty are worth posting, return six. Score
anything you are unsure about below 50 and leave it out.\
"""


# Appended when the model refused to write titles. It still ranks - and
# ranking is the half the local scorer cannot do.
NUMBERS_ONLY = """

IMPORTANT, THIS TIME ONLY: do NOT write titles. Reply with the index and
the score for each clip you pick and nothing else:
{"clips": [{"index": <candidate number>, "score": <0-100>}]}
"""


def api_key(provider: str) -> str:
    for name in _KEY_NAMES.get(provider, ()):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def all_available(preferred: str = "") -> list:
    """[(provider, key)] for every one configured, best first.

    Every one, not the first one. A single provider means a single point
    of failure, and the failure is silent: the run falls back to a local
    scorer that cannot tell whether anything was funny and cuts a full
    set of guesses. A second key turns that from a bad day into a
    slightly slower one.
    """
    order = list(PROVIDER_ORDER)
    if preferred in order:
        order = [preferred] + [p for p in order if p != preferred]
    return [(p, api_key(p)) for p in order if api_key(p)]


def available(preferred: str = "") -> tuple:
    """(provider, key) for whichever is configured, or ("", "").

    Gemini first when neither is named: its free tier covers this
    workload, so the default costs nothing to have switched on.
    """
    found = all_available(preferred)
    return found[0] if found else ("", "")


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
    parts = [{"text": f"Pick AT MOST {count} of these {len(candidates)} "
                      f"candidates - fewer if fewer are good.\n"}]
    for number, highlight in enumerate(candidates, start=1):
        text = for_the_model(highlight.text)[:_MAX_TEXT_CHARS]
        parts.append({"text": (
            f"\n[{number}] at {_timestamp(highlight.start)}, "
            f"{highlight.end - highlight.start:.0f}s\n{text}\n")})
        try:
            frames = grab(source_path, highlight.start, highlight.end)
        except Exception:
            frames = []
        for jpeg in frames:
            if sum(1 for part in parts if "inline_data" in part) >= VISION_MAX_IMAGES:
                break
            parts.append(vision_frames.as_inline_data(jpeg))
    return parts


def learned_lines() -> list:
    """What past clips actually did, as sentences for the model.

    Sentences rather than weights: a wrong lesson shows up here as a
    strange instruction a person can read and delete, not as a number
    nobody can see. Returns [] until the ledger has enough measured
    clips to say anything - which is most of the time, and correct.
    """
    try:
        from autoreel.memory import Ledger, learn, ledger_path

        return learn(Ledger(ledger_path())).prompt_lines()
    except Exception:
        return []


def for_the_model(text: str) -> str:
    """A candidate's transcript with the words the model will not echo removed.

    Gemini refused these clips at the OUTPUT: "the model output could not
    be generated. This output contains sensitive words". Not the prompt -
    it read the transcript fine. It was asked to title each clip "in the
    streamer's own words", the streamer's own words include slurs, and it
    will not write one.

    safetySettings cannot reach that; output policy is not configurable.
    So the slur never goes in, and there is nothing to echo. The moment
    is still described - the surrounding sentence is untouched - and the
    model picks and names it from what is left.

    Masking was not enough on its own: "f***" is still a sensitive word
    to the filter and the refusal came back unchanged, so a flagged word
    is REMOVED here rather than starred. What is left is the sentence
    around it, which is all the model needs to tell one moment from
    another.

    No loss anywhere downstream either: every platform that gets a title
    from this already has those words stripped or masked before posting.
    """
    from .safe_text import DROP_ENTIRELY, clean, _checker, _flagged

    checker = _checker()
    kept = []
    for word in " ".join((text or "").split()).split():
        bare = word.lower().strip(".,!?;:\"'")
        if bare in DROP_ENTIRELY or _flagged(word, checker):
            continue
        kept.append(word)
    # clean() as a backstop for anything the word split missed.
    return clean(" ".join(kept))


def build_prompt(candidates: list, count: int, lessons: Optional[list] = None) -> str:
    """The candidate list, as the model sees it."""
    lines = [f"Pick AT MOST {count} of these {len(candidates)} candidates - "
             f"fewer if fewer are good.", ""]
    lessons = learned_lines() if lessons is None else lessons
    if lessons:
        lines += lessons + [""]
    for number, highlight in enumerate(candidates, start=1):
        text = for_the_model(highlight.text)[:_MAX_TEXT_CHARS]
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


def _post_detailed(url: str, payload: dict, headers: dict,
                   timeout: Optional[int] = None) -> tuple:
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
        with urllib.request.urlopen(
                request, timeout=timeout or _TIMEOUT) as response:
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


# What this asks the model to do is READ a transcript of the channel's
# own stream and say which minutes are worth clipping. The transcript is
# a loud, sweary Monkey-app call, and on the default thresholds Gemini
# declines to answer at all - the request comes back with no candidate
# and the caller sees "the model returned nothing usable", which is how
# a working API key produced scorer-picked titles for weeks.
#
# BLOCK_ONLY_HIGH rather than off: the severe end still blocks, and what
# gets through is the ordinary swearing this channel is made of. Nothing
# here generates anything - the model classifies footage the account
# owner recorded, and the reply is a list of indexes and titles.
_SAFETY = [{"category": name, "threshold": "BLOCK_ONLY_HIGH"} for name in (
    "HARM_CATEGORY_HARASSMENT",
    "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "HARM_CATEGORY_DANGEROUS_CONTENT",
)]


# The last reason a provider gave for not answering, so the caller can
# tell "no key" from "it read this and said no". Those need opposite
# advice and got the same line for weeks.
_LAST_REFUSAL = {"why": ""}


def last_refusal() -> str:
    """Why the provider declined, or "" if it was not a refusal."""
    return _LAST_REFUSAL["why"]


def _remember_refusal(why: str) -> None:
    _LAST_REFUSAL["why"] = why


def _refusal(data: dict) -> str:
    """Why Gemini gave no answer, in its own words. "" if it did answer."""
    if not isinstance(data, dict):
        return "no reply"
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        return f"the prompt was blocked ({feedback['blockReason']})"
    for candidate in data.get("candidates") or []:
        reason = (candidate or {}).get("finishReason", "")
        if reason and reason not in ("STOP", "MAX_TOKENS"):
            return f"the answer was stopped ({reason})"
    if not data.get("candidates"):
        return "no candidates in the reply"
    return ""


def _ask_gemini(key: str, model: str, prompt: str) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": _SAFETY,
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": 0.4},
    }
    data = _post(url, payload, {})
    if not isinstance(data, dict):
        return ""
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        # Said out loud. Swallowed, this is indistinguishable from a
        # model that read the clips and liked none of them.
        why = _refusal(data)
        if why:
            _remember_refusal(why)
            print(f"[Clips] The model would not answer: {why}.")
        return ""


def _ask_gemini_vision(key: str, model: str, parts: list) -> tuple:
    """(reply_text, why_not). Same call as _ask_gemini, with images.

    Returns the reason rather than swallowing it. A vision request can
    fail for reasons the text one never does - a model that takes no
    images, a body too large, a quota that counts images differently -
    and "came back empty" is not something anyone can act on.
    """
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT + VISION_NOTE}]},
        "contents": [{"parts": parts}],
        # Same reason as the text call, and more so: these are FRAMES of
        # the stream, and the default thresholds decline a Monkey call
        # outright. See _SAFETY.
        "safetySettings": _SAFETY,
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": 0.4},
    }
    images = sum(1 for part in parts if "inline_data" in part)
    megabytes = len(json.dumps(payload)) / 1e6

    # 429 here is "this model is busy", not "you are over quota" - it is
    # the free tier being shared and it clears in seconds. Falling
    # straight back to the words threw away the whole vision pass over a
    # spike that a single wait would have ridden out.
    data, problem = _post_detailed(url, payload, {}, timeout=_VISION_TIMEOUT)
    # A timeout gets the same second chance as a busy model. It used to
    # get none, so one slow response threw away the entire vision pass -
    # and what came back instead was ten clips chosen on the transcript,
    # spaced evenly across the stream, most of them about nothing.
    if problem and ("429" in str(problem) or "timed out" in str(problem).lower()):
        why_waiting = ("The model is busy" if "429" in str(problem)
                       else "That took too long")
        print(f"[Clips] {why_waiting} - waiting 20s and trying once more...")
        time.sleep(_BUSY_RETRY_SECONDS)
        data, problem = _post_detailed(url, payload, {},
                                       timeout=_VISION_TIMEOUT)

    if problem:
        return "", f"{problem} ({images} images, {megabytes:.1f} MB)"
    if not isinstance(data, dict):
        return "", f"no response ({images} images, {megabytes:.1f} MB)"
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"], ""
    except (KeyError, IndexError, TypeError):
        blocked = str(data.get("promptFeedback", "")) or str(data)[:200]
        return "", f"reply had no text: {blocked}"


def _ask_anthropic(key: str, model: str, prompt: str) -> str:
    """Claude. No JSON mode to ask for - parse_reply already copes with a
    fenced or bare reply, which is what it was written tolerant for."""
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
    }
    data = _post("https://api.anthropic.com/v1/messages", payload,
                 {"x-api-key": key, "anthropic-version": "2023-06-01"})
    if not isinstance(data, dict):
        return ""
    try:
        return data["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return ""


def asker_for(provider: str):
    """The function that talks to this provider."""
    return {GEMINI: _ask_gemini, OPENAI: _ask_openai,
            ANTHROPIC: _ask_anthropic}.get(provider, _ask_gemini)


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
    _remember_refusal("")
    if not candidates or count <= 0:
        return None

    # Every configured provider, not just the first. One provider is one
    # point of failure, and the failure is silent - the run drops to a
    # local scorer that cannot tell whether anything was funny. A second
    # key turns a bad day into a slower one. `ask` being injected means a
    # caller is driving this directly, so that stays single-provider.
    configured = [] if ask is not None else all_available(provider)
    if not configured:
        # available() is the single-provider answer and stays
        # authoritative: a caller driving this directly, or a test, names
        # one provider through it and must not be overridden by whatever
        # else happens to be in the environment.
        one, key = available(provider)
        configured = [(one, key)] if one else []
    if not configured:
        return None

    shortlist = candidates[:MAX_CANDIDATES]
    picked: list = []
    for attempt, (provider, key) in enumerate(configured):
        if attempt:
            print(f"[Clips] Asking {provider} instead.")
        picked, looked_at = _ask_one_provider(
            provider, key, model, shortlist, count, source_path, ask)
        if picked:
            return _chosen_from(picked, looked_at, count)
    return None


def _ask_one_provider(provider, key, model, shortlist, count, source_path,
                      ask) -> tuple:
    """((index, score, title) list, the shortlist those indices refer to).

    The second half matters: the vision pass narrows the shortlist to the
    candidates it could attach frames to, and the model's indices are
    into THAT list. Returning only the picks would have them read against
    the wrong candidates - a clip chosen at 40 minutes rendered from 12.
    """
    model = resolve_model(provider, key, model)

    # Vision is Gemini-only here and only when there is a file to sample.
    # It is tried FIRST and falls back to the words on any failure: a
    # model that cannot see is the behaviour this had all along, and it
    # is much better than no clips.
    raw = ""
    if source_path and provider == GEMINI and ask is None:
        looking = shortlist[:VISION_MAX_CANDIDATES]
        why = ""
        try:
            parts = build_vision_contents(looking, count, source_path)
            images = sum(1 for part in parts if "inline_data" in part)
            if not images:
                why = "no frames could be read from the video"
            else:
                raw, why = _ask_gemini_vision(key, model, parts)
        except Exception as exc:
            why = f"{type(exc).__name__}: {exc}"
        if raw:
            shortlist = looking
            print(f"[Clips] A model watched {len(looking)} candidates.")
        else:
            # Say WHY. A silent fall-through is indistinguishable from
            # the model having no opinion, and "came back empty" is not
            # something anyone can act on either.
            print(f"[Clips] The vision pass failed ({why}) - going on the "
                  f"words instead.")

    if not raw:
        prompt = build_prompt(shortlist, count)
        speak = ask or asker_for(provider)
        try:
            raw = speak(key, model, prompt)
        except Exception:
            return [], shortlist

    picked = parse_reply(raw, len(shortlist))
    if not picked:
        # Once more, asking for NO TITLES.
        #
        # The refusal is at the output: "the model output could not be
        # generated, this output contains sensitive words". Writing the
        # title is the only part that makes the model produce the
        # channel's own language - CHOOSING is just numbers. So when it
        # will not write, the numbers are still worth having: which of
        # sixty candidates are actually worth clipping is the half the
        # local scorer cannot do, and the titles it already writes are
        # decent.
        #
        # Losing the whole pass over the naming was throwing away the
        # part that mattered more.
        print("[Clips] The model would not write titles - asking it to "
              "just pick, and titling them here.")
        speak = ask or asker_for(provider)
        try:
            raw = speak(key, model,
                        build_prompt(shortlist, count) + NUMBERS_ONLY)
        except Exception:
            raw = ""
        picked = parse_reply(raw, len(shortlist))

    if not picked:
        # "Nothing usable" covered two completely different failures and
        # named neither, so a run that said it twice gave nothing to act
        # on: --check-llm reported the key working, which was true and
        # useless. An answer that could not be READ and no answer at all
        # need different fixes, so say which one happened - and when
        # there IS a reply, show it, because the reply is the evidence.
        if not str(raw or "").strip():
            print("[Clips] The model was asked twice and said nothing at "
                  "all. That is the call failing, not the parsing - check "
                  "the model name in config.json, and --check-llm.")
        else:
            excerpt = " ".join(str(raw).split())[:300]
            print("[Clips] The model answered, and the answer could not be "
                  "read as clip numbers. It said:")
            print(f"[Clips]   {excerpt}")
        return [], shortlist

    return picked, shortlist


def _chosen_from(picked: list, shortlist: list, count: int) -> list:
    """The model's picks, as Highlights, in timeline order."""
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
