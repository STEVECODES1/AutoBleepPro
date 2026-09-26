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
CEREBRAS = "cerebras"
XKIRO = "xkiro"
GROQ = "groq"
NVIDIA = "nvidia"
DEEPSEEK = "deepseek"
OPENROUTER = "openrouter"

# Last resort only. Model names are retired faster than a pinned default
# can be maintained - the first key tried against this hit "gemini-2.5-flash
# is no longer available to new users" - so the real answer is to ASK the
# provider what it has and take the best of it. See resolve_model().
DEFAULT_MODELS = {
    # Measured fastest AND most reliable at 48 frames - see
    # PROVIDER_ORDER. Only a fallback; resolve_model still asks.
    GEMINI: "gemini-3.5-flash",
    OPENAI: "gpt-4o-mini",
    ANTHROPIC: "claude-sonnet-5",
    CEREBRAS: "gpt-oss-120b",
    # The ':free' suffix is part of the id on this gateway, not a flag -
    # dropping it is a 404. This one is pinned rather than auto-picked
    # because the catalogue carries 100+ models including PAID ones and
    # choosing off it could quietly start billing.
    XKIRO: "qwen/qwen3.8-omni-flash:free",
    # Matches what an auto-pick settles on, so the two cannot disagree:
    # gpt-oss reasons before answering, which _chat_reply and the
    # generous max-token budget both handle. Pin clips.llm_model to
    # qwen/qwen3.8-27b if you would rather have the faster non-reasoning
    # model.
    GROQ: "openai/gpt-oss-120b",
    # NVIDIA's build.nvidia.com catalogue is ~100 models and the ids are
    # long, so this is pinned rather than auto-picked. "omni" is the
    # multimodal line - it reads the frames, which is the whole reason
    # this provider is worth having rather than a fifth text model.
    NVIDIA: "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    # deepseek-chat, not deepseek-reasoner: this job is reading a
    # transcript and returning a short list. Reasoning buys nothing
    # here and costs latency on every clip. is_reasoning_model already
    # matches "deepseek-r" if you pin the reasoner anyway.
    DEEPSEEK: "deepseek-chat",
    # OpenRouter is a gateway to 443 models, free and paid mixed, so
    # this is pinned like xKiro's - an auto-pick off that catalogue
    # could quietly start billing. Text only: nex-n2.5-pro reads 8
    # frames in 5.8s and returns EMPTY content at 48, which is the
    # number this actually sends.
    OPENROUTER: "nex-agi/nex-n2.5-pro:free",
}

# Model families that cannot do this job, whatever they are called.
# 'guard'/'safeguard' are classifiers, 'orpheus' is text-to-speech - all
# three sit in Groq's catalogue next to the chat models, and an auto-pick
# that lands on one of them answers nothing.
#
# 'compound' is a different case: Groq's agentic routers CAN answer, and
# did return valid JSON in testing, but they are built to call tools and
# inject a hidden system prompt (477 prompt tokens measured for a
# one-line reply). This module makes one strict-JSON completion call per
# ranking pass, which is the wrong shape for an agent, so they are kept
# out of the auto-pick rather than because they fail.
_NOT_TEXT = ("embedding", "aqa", "imagen", "veo", "image", "tts", "audio",
             "vision", "live", "realtime", "whisper", "dall-e", "moderation",
             "guard", "safeguard", "orpheus", "compound")

_KEY_NAMES = {
    GEMINI: ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    OPENAI: ("OPENAI_API_KEY",),
    ANTHROPIC: ("ANTHROPIC_API_KEY",),
    CEREBRAS: ("CEREBRAS_API_KEY",),
    XKIRO: ("XKIRO_API_KEY",),
    GROQ: ("GROQ_API_KEY",),
    NVIDIA: ("NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"),
    DEEPSEEK: ("DEEPSEEK_API_KEY",),
    OPENROUTER: ("OPENROUTER_API_KEY",),
}

# Tried in this order.
#
# Gemini first: it can be shown the FRAMES, which is the whole reason the
# picks got good.
#
# xKiro second because it can ALSO be shown the frames - verified against
# the live API, qwen3.8-omni-flash reads an image and describes it - so
# the vision pass stops being a single point of failure and a Gemini
# refusal costs a retry elsewhere rather than the whole pass. It is free,
# fast, and returns clean content with no reasoning overhead.
#
# Cerebras third and Groq fourth: both free and text-only, and both far
# faster than the paid pair. Cerebras first of the two on measured speed
# (0.017s vs 0.04s for a short completion). Then the paid two.
#
# VISION_PROVIDERS is what the vision pass is allowed to use. It is a
# separate list because being able to answer about words says nothing
# about being able to answer about pictures.
# Measured on 2026-09-21 at the REAL workload - 48 frames, which is what
# the vision pass actually sends - because a model that reads 8 images
# beautifully can be useless at 48, and one of them is:
#
#   gemini-3.5-flash                 4.5s   clean JSON, finishReason STOP
#   gemini-flash-latest             13.7s   clean JSON
#   gemini-3-flash-preview          19.3s   hit MAX_TOKENS
#   nvidia omni (8 images)          22.1s   clean JSON, ~50% of attempts
#   openrouter nex-n2.5-pro:free    32-53s  EMPTY content at 48 frames
#                                           (5.8s and clean at 8)
#
# So Gemini first on merit, not habit: four times faster than anything
# else here and the only one that answered 48 frames every time.
# OpenRouter is text-only below for exactly the reason above.
PROVIDER_ORDER = (GEMINI, NVIDIA, XKIRO, DEEPSEEK, OPENROUTER, CEREBRAS,
                  GROQ, OPENAI, ANTHROPIC)
VISION_PROVIDERS = (GEMINI, NVIDIA, XKIRO)

# The OpenAI-shaped providers, and where each one lives. Adding another
# is a line here rather than a new branch in check().
_CHAT_URLS = {
    OPENAI: "https://api.openai.com/v1/chat/completions",
    CEREBRAS: "https://api.cerebras.ai/v1/chat/completions",
    XKIRO: "https://api.xkiro.com/v1/chat/completions",
    GROQ: "https://api.groq.com/openai/v1/chat/completions",
    NVIDIA: "https://integrate.api.nvidia.com/v1/chat/completions",
    DEEPSEEK: "https://api.deepseek.com/v1/chat/completions",
    OPENROUTER: "https://openrouter.ai/api/v1/chat/completions",
}

# Models that THINK before they answer. Their replies can carry a
# `reasoning` field and no `content` at all when the budget runs out, so
# an empty `content` must not be read as "the model had no opinion".
#
# This is a property of the MODEL, not of the provider: Cerebras, xKiro
# and Groq all serve gpt-oss alongside models that do not reason, so a
# per-provider list would be wrong on at least one of them. Matched on
# the model id because that is what the caller actually asked for.
_REASONING_MODEL_MARKERS = ("gpt-oss", "qwq", "deepseek-r", "reasoning")


def is_reasoning_model(model: str) -> bool:
    """Whether this model spends tokens thinking before it writes."""
    name = str(model or "").lower()
    return any(marker in name for marker in _REASONING_MODEL_MARKERS)


def _chat_reply(data, provider: str, model: str) -> str:
    """The text out of an OpenAI-shaped reply, or "".

    Shared by every gateway-shaped provider because the reasoning-model
    case is the same on all of them: a valid reply whose budget ran out
    carries a `reasoning` field and NO `content`, and returning a bare
    "" for that is indistinguishable from a model that read the
    candidates and liked none of them.
    """
    if not isinstance(data, dict):
        return ""
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return ""
    content = message.get("content")
    if content:
        return content
    if is_reasoning_model(model):
        try:
            reason = str(data["choices"][0].get("finish_reason") or "")
        except (KeyError, IndexError, TypeError):
            reason = ""
        if reason == "length":
            print(f"[Clips] {provider} ({model}) hit the token ceiling before "
                  f"writing an answer - the budget went on reasoning.")
    return ""


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

# The prompt tells the model to "score anything you are unsure about
# below 50 and leave it out" - which is a request, not a guarantee. A
# model that pads its answer, or drifts on the instruction, can still
# return a candidate at 15 and have it posted if fewer than `count` came
# back. This is the code-side floor that makes the instruction actually
# binding: it is enforced here whether or not the model honoured it.
MIN_CLIP_SCORE = 50.0

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

# ...and what each provider will actually accept in one request, where
# that is lower. NVIDIA answered a 48-image request with
#
#   HTTP 500: VLLMValidationError: At most 12 image(s) may be provided
#   in one prompt. (parameter=image)
#
# every single time, then burned four retries on it, because the retry
# rule only knows "500 is transient" and a request that is too big is
# not going to get smaller by waiting. Gemini and xKiro take all 48.
PROVIDER_MAX_IMAGES = {}


def images_allowed(provider: str) -> int:
    return PROVIDER_MAX_IMAGES.get(provider, VISION_MAX_IMAGES)


def thin_images(parts: list, limit: int) -> list:
    """The same candidates, with at most `limit` frames between them.

    Truncating the list would be simpler and much worse: the images are
    grouped behind the candidate they belong to, so cutting at 12 would
    describe the first six candidates in pictures and leave the other
    eighteen as text the model has already been given. Keeping ONE frame
    per candidate instead means twelve of them are seen rather than six,
    which is the point of showing it pictures at all.
    """
    if limit <= 0:
        return [part for part in parts if "inline_data" not in part]

    images = sum(1 for part in parts if "inline_data" in part)
    if images <= limit:
        return list(parts)

    kept: list = []
    used = 0
    first_of_candidate = False
    for part in parts:
        if "inline_data" not in part:
            kept.append(part)
            first_of_candidate = True
            continue
        # One per candidate, in order, until the budget runs out.
        if first_of_candidate and used < limit:
            kept.append(part)
            used += 1
        first_of_candidate = False
    return kept


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

JUDGE IT ON WHAT IS SAID AND WHAT HAPPENS, not on how it looks. A frame
of someone's face mid-sentence is not evidence of a reaction, and loud
audio is not evidence of a joke landing - both of those are hints, not
verdicts. The transcript is what tells you whether the moment actually
resolves: a setup with no punchline, an argument that trails off, a
"reaction" to something you cannot identify from the words - none of
that is a clip no matter what the frame shows. If you cannot say in one
sentence what is funny or what happens, using only what was said, reject
it.

TWO SEPARATE QUESTIONS, both required: is it FUNNY (or a genuine
moment - an argument, someone caught out, a story landing), AND does it
MAKE SENSE on its own with no other context. A clip can fail either one.
A rant that is well-delivered but requires knowing who's being talked
about is a sense failure. A clean, self-contained line that just is not
funny is a funny failure. Score both, out loud to yourself, before
scoring the clip - do not let a strong score on one stand in for the
other.

DO NOT PICK A CANDIDATE FOR WHAT IT MIGHT LOOK LIKE OUT OF CONTEXT ON
ITS OWN, cut loose from the stream it came from and read by someone who
was not there. A line about someone else's kid, health, family, or
appearance can play as a joke live, mid-conversation, tone audible - and
read as cruel or as making light of something serious once it is a
fifteen-second clip with a title on it. If the moment's humor depends
entirely on delivery, tone, or "you had to be there," and the words
alone are ugly, leave it out. This is not the same test as "does it make
sense" - a line can be perfectly clear and still wrong to post.

DO NOT FILL THE BATCH FROM ONE SCENE. A long, static conversation - an
interrogation, a courtroom bit, two people sitting at one table with the
camera locked - produces dozens of candidates that are each individually
defensible and, picked together, are one clip four times. A viewer
scrolling past four Shorts that all show the same room, the same two
people, the same unmoving camera does not see four different moments -
they see the algorithm repeating itself, whether or not the words
happened to be different each time. If several strong candidates come
from the same continuous scene or conversation, pick the ONE or TWO
that land hardest and let the rest go, even if they would have scored
well on their own. Spread picks across DIFFERENT moments of the
stream - different scenes, different setups, different parts of what
happened - over cramming the batch with one bit's leftovers.

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

    Free variants outrank everything else. On a gateway that mixes free
    and paid models under the same family (xKiro lists both
    `qwen3.8-max` and `qwen3.8-max:free`), auto-picking by quality alone
    is how a run silently starts billing.
    """
    name = name.lower().rsplit("/", 1)[-1]
    free = 1 if name.endswith(":free") else 0
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
    return (free, stable, family, version)


def usable_models(names: list) -> list:
    """The named models that could do this, best first."""
    keep = [n for n in names
            if n and not any(bad in n.lower() for bad in _NOT_TEXT)]
    return sorted(keep, key=_model_rank, reverse=True)


def list_models(provider: str, key: str) -> list:
    """What this key can actually reach. Empty on any failure."""
    if provider == CEREBRAS:
        # Cerebras answers this unauthenticated-free and lists only what
        # the key can call, which is worth asking: its catalogue is small
        # and turns over (llama-3.3-70b was retired in favour of gpt-oss).
        return _list_models_openai_style(
            "https://api.cerebras.ai/v1/models", key)
    if provider == XKIRO:
        # 100+ models, free and paid mixed under the same family names.
        # Worth listing so an operator can see them, but see _model_rank:
        # free variants sort first precisely because this list is not all
        # free, and the default is pinned rather than chosen from here.
        return _list_models_openai_style(
            "https://api.xkiro.com/v1/models", key)
    if provider == GROQ:
        # Small (13) but mixed - whisper transcribers, prompt-guard
        # classifiers and a TTS voice sit in it next to the chat models.
        # _NOT_TEXT is what keeps an auto-pick off those.
        return _list_models_openai_style(
            "https://api.groq.com/openai/v1/models", key)
    if provider in (NVIDIA, OPENROUTER, DEEPSEEK):
        # Listed so --check-llm can show what a key reaches. NVIDIA and
        # OpenRouter are in _PINNED_MODEL_PROVIDERS, so this is for
        # READING, not for auto-picking: NVIDIA's list offers ~80 models
        # and two of them (nvidia/vila,
        # microsoft/phi-3-vision-128k-instruct) answered 404 "Not found
        # for account" on a key that had just been shown them.
        return _list_models_openai_style(
            _CHAT_URLS[provider].replace("/chat/completions", "/models"), key)
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


def _list_models_openai_style(url: str, key: str) -> list:
    """Model ids from an OpenAI-shaped `GET /v1/models` reply."""
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {key}", **_GATEWAY_HEADERS})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    names = []
    for entry in data.get("data") or []:
        if isinstance(entry, dict):
            name = str(entry.get("id") or "").strip()
            if name:
                names.append(name)
    return names


# Providers whose model must NOT be auto-picked from their catalogue.
#
# xKiro: 100+ models with free and PAID under the same family names, so
# an auto-pick could quietly start billing.
#
# NVIDIA: its /v1/models lists ~80 models for everyone, and listing is
# not the same as reachable. Measured on this account, nvidia/vila and
# microsoft/phi-3-vision-128k-instruct both answered
# 404 "Not found for account" - so an auto-pick that took the
# best-sounding name off that list would 404 on a name the catalogue
# had just offered. Which models a key can call is a fact about the
# key, and the list does not carry it.
_PINNED_MODEL_PROVIDERS = (XKIRO, NVIDIA, OPENROUTER)


def resolve_model(provider: str, key: str, configured: str = "") -> str:
    """The model to call: whatever was configured, else the best on offer.

    Pinning a name in code was the bug. Providers retire models on their
    own schedule and a pinned default fails with a 404 that reads like a
    broken key - which is exactly how this was found. Asking costs one
    request and survives the next retirement without an edit.

    The exception is _PINNED_MODEL_PROVIDERS, where the catalogue is
    actively misleading - see the comment above it.
    """
    if configured:
        return configured
    if provider in _PINNED_MODEL_PROVIDERS:
        return DEFAULT_MODELS[provider]
    available_names = usable_models(list_models(provider, key))
    return available_names[0] if available_names else DEFAULT_MODELS[provider]


# Last resort only, same reason as DEFAULT_MODELS: Google retires model
# names on its own schedule (gemini-2.5-flash-lite itself retires on
# 2026-10-16) and resolve_lite_model() asks the catalogue first.
GEMINI_LITE_FALLBACK = "gemini-3.5-flash-lite"


def resolve_lite_model(key: str, avoid: str = "") -> str:
    """The best "lite" Gemini model on offer, or "" if there is none.

    For when the day's free allowance on the strong flash model runs out
    mid-run: measured directly (see the comment on _EXHAUSTED_MARKERS),
    the free tier gives that model ~20 requests a day - three streams'
    worth - while its own flash-lite sibling is still fully multimodal,
    still Gemini's own prompt handling this project has actually tuned
    against, and its free allowance is roughly 25x larger. Falling
    straight through PROVIDER_ORDER to a different provider (nvidia's
    vision pass alone was measured failing half the time) the moment the
    day's 20 ran out skipped a same-provider option that was still free
    and still working.

    Asks the catalogue rather than pinning a name, for the same reason
    resolve_model() does. Never returns `avoid`, so a lite model that
    has itself just run out of quota cannot be offered back as its own
    fallback.
    """
    names = [n for n in usable_models(list_models(GEMINI, key))
            if "lite" in n.lower()]
    for name in names:
        if name != avoid:
            return name
    return "" if avoid == GEMINI_LITE_FALLBACK else GEMINI_LITE_FALLBACK


def _timestamp(seconds: float) -> str:
    seconds = int(max(0.0, seconds))
    return f"{seconds // 60}m{seconds % 60:02d}s"


VISION_NOTE = """\

You can SEE two frames from each candidate - one early, one near the end.
Use them AS A TIE-BREAKER, not as the reason to pick something. Two
frames a second apart cannot show whether a joke landed, whether a
reaction was to what you think it was to, or whether the thirteen
seconds between them make sense - only the transcript can. What the
frames are good for: catching a purely visual gag the words miss
entirely - someone walking up behind, a fight starting, a face doing
something the transcript renders as silence - and confirming that a
candidate the transcript already made a case for is not, say, an empty
loading screen. A candidate with a weak transcript and a striking frame
is still a weak candidate; do not let the picture outvote the words.

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
    # The examples go in the VISION prompt too - that is the pass that
    # actually runs most of the time, and a picker shown the room in one
    # path and not the other would behave differently depending on
    # whether the frames could be read.
    opening = (f"Pick AT MOST {count} of these {len(candidates)} "
               f"candidates - fewer if fewer are good.\n")
    examples = hits_block()
    if examples:
        opening += examples + "\n"
    parts = [{"text": opening}]
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


def hits_path() -> str:
    """Where the channel's own hits live, beside config.json."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(here, "auto_uploader", "hits.json")


def hits_block(path: str = "") -> str:
    """The channel's biggest posts, as examples. "" when there are none.

    This is the difference between asking somebody to be funny and
    showing them the room. Without it the model picks against a general
    idea of funny, competently, and the clips land flat.
    """
    try:
        from .hits import for_prompt, load

        return for_prompt(load(path or hits_path()))
    except Exception:
        return ""


def build_prompt(candidates: list, count: int, lessons: Optional[list] = None,
                 hits: Optional[str] = None) -> str:
    """The candidate list, as the model sees it."""
    lines = [f"Pick AT MOST {count} of these {len(candidates)} candidates - "
             f"fewer if fewer are good.", ""]
    # Before the candidates, because it changes how they are read.
    examples = hits_block() if hits is None else hits
    if examples:
        lines += [examples, ""]
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


def _all_key_names() -> str:
    """Every api-key variable name, in the order they are tried.

    Derived rather than written out: this line named only three providers
    and stayed that way after a fourth was added, so a run with no keys
    told the operator to look for three when there were four.
    """
    names = []
    for provider in PROVIDER_ORDER:
        names.extend(_KEY_NAMES.get(provider, ()))
    return " / ".join(names)


def check_all(provider: str = "", model: str = "") -> list:
    """[(provider, ok, detail)] for EVERY configured key.

    Checking only the first one reported "gemini answered - the key
    works" the moment after a second key was added, and said nothing
    about the key that had just been pasted in. A backstop nobody has
    verified is found out on the day the first provider fails, which is
    the worst moment available.
    """
    found = all_available(provider)
    if not found:
        return [("", False, f"no {_all_key_names()} in .env")]
    checked = []
    for name, _key in found:
        ok, detail = check(name, model if len(found) == 1 else "")
        checked.append((name, ok, detail))
    return checked


def check(provider: str = "", model: str = "") -> tuple:
    """(ok, detail) for one configured key. One tiny real request.

    A key that is present but wrong looks exactly like a key that works,
    right up until the clips come out chosen by the fallback scorer and
    nobody knows why. This asks.
    """
    provider, key = available(provider)
    if not provider:
        return False, f"no {_all_key_names()} in .env"
    model = resolve_model(provider, key, model)

    if provider == ANTHROPIC:
        data, error = _post_detailed(
            "https://api.anthropic.com/v1/messages",
            {"model": model, "max_tokens": 8,
             "messages": [{"role": "user", "content": "Reply with: ok"}]},
            {"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif provider == GEMINI:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        data, error = _post_detailed(
            url, {"contents": [{"parts": [{"text": "Reply with: ok"}]}]}, {})
    elif provider in _CHAT_URLS:
        # Every OpenAI-shaped provider, so a new one is a line in
        # _CHAT_URLS rather than another branch here.
        #
        # 200 tokens, not the 5 this used to ask for: Cerebras' models are
        # reasoning models and spend tokens thinking before writing
        # anything. At 5, gpt-oss-120b returns finish_reason "length" with
        # a `reasoning` field and NO `content` - which reads exactly like a
        # rejected key, so a working key got reported as broken.
        data, error = _post_detailed(
            _CHAT_URLS[provider],
            {"model": model,
             "messages": [{"role": "user", "content": "Reply with: ok"}],
             "max_tokens": 200},
            _bearer_headers(key))
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
    if is_reasoning_model(model):
        # The reply arrived, but check that the model actually produced
        # text rather than spending the whole budget on reasoning.
        try:
            if not data["choices"][0]["message"].get("content"):
                return False, (f"{provider} ({model}) answered but wrote no "
                               f"content - the token budget was spent on "
                               f"reasoning. Use a non-reasoning model or "
                               f"raise the max-token budget.")
        except (KeyError, IndexError, TypeError):
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


# The last time the provider was simply unavailable (503 overloaded, 429
# busy) - kept apart from refusals. A real run treated a 503 as "the model
# would not write titles", asked again for another 20s wait and another
# 503, then advised checking the model name - none of which was the cause.
_LAST_OUTAGE = {"why": ""}


def last_outage() -> str:
    return _LAST_OUTAGE["why"]


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


# Failures worth one retry: rate-limited (429), the provider's own
# servers erroring out (5xx - this is what actually happened on a real
# run: "HTTP 503: This model is currently experiencing high demand...
# usually temporary" - Gemini's own message says try again, and nothing
# here was doing that), or a slow reply that just needed longer. NOT a
# 400 or 401 - a bad request or a bad key fails exactly the same way a
# second time, so retrying those only doubles how long it takes the
# caller to find out.
_RETRY_CODES = ("429", "500", "502", "503", "504")


# A 429 that means "you have used your allowance for the DAY", as
# opposed to "you are going too fast". Waiting twenty seconds fixes the
# second and cannot fix the first:
#
#   HTTP 429: You exceeded your current quota ... Quota exceeded for
#   metric: generativelanguage.googleapis.com/
#   generate_content_free_tier_requests, limit: 20,
#   model: gemini-3.8-flash
#
# Twenty requests a day is three streams' worth. Once it is gone, every
# remaining clip paid 20 seconds to be told so again, three times per
# clip, when the next provider in the cascade was sitting there ready.
_EXHAUSTED_MARKERS = (
    "exceeded your current quota",
    "quota exceeded for metric",
    "free_tier_requests",
    "insufficient_quota",
    "billing details",
)


def is_quota_exhausted(problem: str) -> bool:
    """True when retrying this provider today cannot possibly work."""
    lowered = str(problem or "").lower()
    return any(marker in lowered for marker in _EXHAUSTED_MARKERS)


def _is_transient(problem: str) -> bool:
    text = str(problem or "")
    # Checked FIRST. An exhausted daily quota arrives as a 429, which
    # is in _RETRY_CODES, so without this it reads as "busy, try again
    # shortly" forever.
    if is_quota_exhausted(text):
        return False
    if any(f"HTTP {code}" in text for code in _RETRY_CODES):
        return True
    lowered = text.lower()
    return "timed out" in lowered or "could not reach the api" in lowered


def _gemini_url(model: str, key: str) -> str:
    return (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={key}")


def _ask_gemini(key: str, model: str, prompt: str) -> str:
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": prompt}]}],
        "safetySettings": _SAFETY,
        "generationConfig": {"responseMimeType": "application/json",
                             "temperature": 0.4},
    }
    active_url = _gemini_url(model, key)
    # _post_detailed rather than _post: the text pass used to swallow a
    # busy/overloaded provider the same as a bad key or a bad prompt, so
    # a 503 here got one silent attempt and nothing else - "the model
    # was asked twice and said nothing at all" was really "the model
    # was briefly down and never asked again". The vision pass already
    # retried a busy model; this path never did.
    data, problem = _post_detailed(active_url, payload, {})
    if problem and is_quota_exhausted(problem) and "lite" not in model.lower():
        # The day's ~20 free requests on the strong flash model are
        # gone; its own flash-lite sibling has roughly 25x the free
        # allowance and is still Gemini, still multimodal, still this
        # project's own tuned prompt - see resolve_lite_model().
        lite = resolve_lite_model(key, avoid=model)
        if lite:
            print(f"[Clips] Gemini ({model}) is out of today's free "
                  f"requests - trying {lite} instead...")
            active_url = _gemini_url(lite, key)
            data, problem = _post_detailed(active_url, payload, {})
    if problem and _is_transient(problem):
        print(f"[Clips] Gemini said {problem} - waiting 20s and trying "
              f"once more...")
        time.sleep(_BUSY_RETRY_SECONDS)
        data, problem = _post_detailed(active_url, payload, {})
    if not isinstance(data, dict):
        if problem and _is_transient(problem):
            _LAST_OUTAGE["why"] = problem
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
    active_url = _gemini_url(model, key)

    # 429/5xx here is "this model is busy or briefly down", not "you are
    # over quota" - both clear on their own. Falling straight back to
    # the words threw away the whole vision pass over a spike a single
    # wait would have ridden out. This used to only recognise 429 - a
    # real run got "HTTP 503: high demand... usually temporary" and
    # never retried at all, because "503" matched neither check.
    data, problem = _post_detailed(active_url, payload, {},
                                   timeout=_VISION_TIMEOUT)
    if problem and is_quota_exhausted(problem) and "lite" not in model.lower():
        # Same reasoning as _ask_gemini: flash-lite is still fully
        # multimodal (confirmed - both the 3.1 and 3.5 lite releases
        # take image input), so a quota-exhausted vision pass has a
        # same-provider fallback worth trying before losing the frames
        # entirely to a text-only provider further down the cascade.
        lite = resolve_lite_model(key, avoid=model)
        if lite:
            print(f"[Clips] Gemini ({model}) is out of today's free "
                  f"requests - trying {lite} instead...")
            active_url = _gemini_url(lite, key)
            data, problem = _post_detailed(active_url, payload, {},
                                           timeout=_VISION_TIMEOUT)
    if problem and _is_transient(problem):
        print(f"[Clips] Gemini said {problem} - waiting 20s and trying "
              f"once more...")
        time.sleep(_BUSY_RETRY_SECONDS)
        data, problem = _post_detailed(active_url, payload, {},
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
    return {GEMINI: _ask_gemini, CEREBRAS: _ask_cerebras,
            XKIRO: _ask_xkiro, GROQ: _ask_groq,
            NVIDIA: _ask_nvidia, DEEPSEEK: _ask_deepseek,
            OPENROUTER: _ask_openrouter,
            OPENAI: _ask_openai,
            ANTHROPIC: _ask_anthropic}.get(provider, _ask_gemini)


# Cerebras is OpenAI-shaped, with one thing that has to be handled
# differently: its current models (gpt-oss-120b) are REASONING models and
# spend completion tokens on a `reasoning` field before writing any
# `content`. Measured on the live API with max_tokens=10:
#
#   {"finish_reason":"length","message":{"reasoning":"The user asks: ..."}}
#
# - no `content` key at all, which _ask_openai would read as the model
# having no opinion and hand the run to the next provider for no reason.
# A real answer needs a budget large enough to cover the thinking, so the
# ceiling is generous and only a floor is pinned.
_CEREBRAS_MAX_TOKENS = 8192

# Two of the providers sit behind a filter that rejects the default
# `Python-urllib/3.x` User-Agent. Measured, both of them:
#
#   Cerebras, default urllib UA -> HTTP 403 Forbidden
#   xKiro,    default urllib UA -> HTTP 403 (Cloudflare error code 1010)
#   either,   any other UA      -> HTTP 200
#
# Without this, a working key is reported as "rejected the key - HTTP
# 403", which is the one message that sends an operator to regenerate a
# key that was never the problem.
_USER_AGENT = "AutoBleepPro/2.0 (+https://github.com/STEVECODES1/AutoBleepPro)"

_GATEWAY_HEADERS = {"User-Agent": _USER_AGENT}


def _bearer_headers(key: str) -> dict:
    """Bearer auth plus a User-Agent the gateways will accept."""
    return {"Authorization": f"Bearer {key}", **_GATEWAY_HEADERS}


def _ask_cerebras(key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
        "max_tokens": _CEREBRAS_MAX_TOKENS,
    }
    data = _post("https://api.cerebras.ai/v1/chat/completions", payload,
                 _bearer_headers(key))
    return _chat_reply(data, "Cerebras", model)


# ── xKiro ────────────────────────────────────────────────────────────
#
# An OpenAI-shaped gateway covering 100+ models from several vendors.
# Two things about it are not interchangeable with the others:
#
#   1. Model ids carry a vendor prefix AND, for the free ones, a ':free'
#      SUFFIX that is part of the id - "qwen/qwen3.8-omni-flash:free".
#      Dropping the suffix is a 404, not a fallback to the paid model.
#   2. It rejects the default urllib User-Agent the same way Cerebras
#      does. See _USER_AGENT.
#
# It is the only provider besides Gemini whose models can be shown the
# frames, which is why it sits second in PROVIDER_ORDER and in
# VISION_PROVIDERS.
_XKIRO_MAX_TOKENS = 4096


def to_openai_content(parts: list) -> list:
    """Gemini `parts` as OpenAI-shaped content blocks.

    The same bytes in a different envelope: Gemini carries an image as
    {"inline_data": {"mime_type", "data"}}, the OpenAI shape wants a
    data: URI. Frames are ~0.6 MB of base64 for 48 images, so this is a
    rewrite and not a re-encode.
    """
    blocks = []
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        inline = part.get("inline_data")
        if inline:
            mime = inline.get("mime_type") or "image/jpeg"
            data = inline.get("data") or ""
            blocks.append({"type": "image_url",
                           "image_url": {"url": f"data:{mime};base64,{data}"}})
        elif part.get("text") is not None:
            blocks.append({"type": "text", "text": part["text"]})
    return blocks


def _ask_xkiro(key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
        "max_tokens": _XKIRO_MAX_TOKENS,
    }
    data = _post(_CHAT_URLS[XKIRO], payload, _bearer_headers(key))
    return _chat_reply(data, "xKiro", model)


# ── Groq ─────────────────────────────────────────────────────────────
#
# OpenAI-shaped, free, and the fastest thing here after Cerebras - a
# short completion measured at 0.04s. Two things to know:
#
#   1. It rejects the default urllib User-Agent (HTTP 403, Cloudflare
#      code 1010), like Cerebras and xKiro. See _USER_AGENT.
#   2. Its catalogue is 13 models and NOT all of them answer questions:
#      two whisper transcribers, two llama-prompt-guard classifiers, and
#      an orpheus text-to-speech voice. _NOT_TEXT filters all five so an
#      auto-pick cannot land on one.
#
# The default is qwen3.8-27b rather than the stronger gpt-oss-120b
# because gpt-oss reasons before every answer - measured at 35 reasoning
# tokens for a one-line reply - and this job is reading a transcript and
# returning a short list, which is latency and tokens for no gain. Pin
# clips.llm_model if you want it anyway; _chat_reply still handles the
# empty-content case properly.
_GROQ_MAX_TOKENS = 4096


def _ask_groq(key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
        "max_tokens": _GROQ_MAX_TOKENS,
    }
    data = _post(_CHAT_URLS[GROQ], payload, _bearer_headers(key))
    return _chat_reply(data, "Groq", model)


# ── NVIDIA (build.nvidia.com / NIM) ──────────────────────────────────
#
# OpenAI-shaped, one key, and the catalogue is ~80 models from a dozen
# vendors - Meta, Mistral, Moonshot, Z.ai and DeepSeek all answer
# through the same endpoint and the same key.
#
# MEASURED against the live API on 2026-09-21, because the catalogue
# listing a model says nothing about whether this account can call it:
#
#   nvidia/nemotron-3-ultra-550b-a55b      clean JSON
#   moonshotai/kimi-k3                     clean JSON
#   z-ai/glm-5.3                           clean JSON
#   deepseek-ai/deepseek-v4.1-flash        clean JSON
#   nvidia/nemotron-3-super-120b-a12b      503, service overloaded
#   nvidia/nemotron-3.5-lightning-30b-a3b  leaked "Here's a thinking
#                                          process:" into content
#   nvidia/vila                            404, not enabled for account
#   microsoft/phi-3-vision-128k-instruct   404, not enabled for account
#   meta/llama-3.2-90b-vision-instruct     timed out at 120s on 8 images
#
# The 404s are the important lesson: they are not "no such model", they
# are "not found for account". Which models a key can reach is a fact
# about the key, so the default here is one that answered on this one
# and resolve_model() still asks the catalogue for the rest.
_NVIDIA_MAX_TOKENS = 8192

# Measured, not guessed: a 48-image request is refused outright with
# "At most 12 image(s) may be provided in one prompt."
PROVIDER_MAX_IMAGES[NVIDIA] = 12

# Providers that must NOT be sent response_format={"type":"json_object"}.
#
# It is an OpenAI parameter and NVIDIA's gateway accepts it without
# complaint, then answers worse. Measured on 2026-09-21, same prompt,
# only this parameter changed:
#
#   nemotron-3-ultra-550b  with:    {"ok":{ "ok": 1 }      <- malformed
#                          without: {"clips":[{"index":1,"score":90}]}
#   deepseek-v4.1-flash    with:    ""                     <- empty
#                          without: {"clips":[{"index":1,"score":90}]}
#   kimi-k3                with:    connection closed after 85s
#
# Two silent failures and a hang, all of them looking from here like
# "the model had no opinion about the clips". Asking for JSON in the
# prompt - which SYSTEM_PROMPT already does - works on every one of
# them, and the reply parser is tolerant of fenced and bare JSON
# anyway, which is what it was written for.
_NO_JSON_MODE = (NVIDIA,)


def _json_mode(provider: str) -> dict:
    """{"response_format": ...} for providers that are better with it."""
    if provider in _NO_JSON_MODE:
        return {}
    return {"response_format": {"type": "json_object"}}


# NVIDIA's preview models run on a shared pool, and a full pool is an
# instant refusal rather than a queue:
#
#   HTTP 503 ResourceExhausted: Worker local total request limit
#   reached (16/16)                              returned in 0.3s
#
# Measured at roughly half of attempts, and a success takes 10-12s. So
# the useful strategy is the opposite of the usual backoff: retry
# several times with a SHORT wait, because a refusal costs a third of a
# second and the next slot may be free immediately. One 20s retry - the
# shared default - turns a 50% failure into a 25% failure and spends 20
# seconds doing it.
_NVIDIA_BUSY_TRIES = 5
_NVIDIA_BUSY_WAIT = 4


def _ask_nvidia(key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": _NVIDIA_MAX_TOKENS,
        **_json_mode(NVIDIA),
    }
    data = _post(_CHAT_URLS[NVIDIA], payload, _bearer_headers(key))
    return _chat_reply(data, "NVIDIA", model)


# ── DeepSeek ─────────────────────────────────────────────────────────
#
# OpenAI-shaped and text-only. Worth having as its own provider rather
# than only through NVIDIA's gateway: a direct key is not subject to
# NVIDIA's shared preview capacity, which is what took the vision model
# down ("Worker local total request limit reached (427/16)").
_DEEPSEEK_MAX_TOKENS = 4096


def _ask_deepseek(key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
        "max_tokens": _DEEPSEEK_MAX_TOKENS,
    }
    data = _post(_CHAT_URLS[DEEPSEEK], payload, _bearer_headers(key))
    return _chat_reply(data, "DeepSeek", model)


# ── OpenRouter ───────────────────────────────────────────────────────
#
# OpenAI-shaped, one key, 443 models from every vendor - and a genuinely
# free tier, which is the reason it is here.
#
# TEXT ONLY, deliberately, and this is the one measurement worth
# repeating: nex-n2.5-pro:free read 8 frames in 5.8s and returned clean
# JSON. At 48 frames - the number the vision pass actually sends - the
# same model took 32s and 53s and returned EMPTY content both times.
# Testing a vision model on a handful of images and concluding it works
# is how a provider gets promoted into VISION_PROVIDERS and then
# silently returns nothing on every real run.
#
# Every other free vision model on the gateway was unusable for a
# different reason, all measured the same day: gemma-4-31b and
# qwen3.8-27b answered 429 rate-limited immediately, ling-3.0-flash-vl
# and nex-n2.5-mini answered 400, inkling is "only available on agentic
# harnesses", and dots-3-note returned prose instead of JSON.
_OPENROUTER_MAX_TOKENS = 4096


def _ask_openrouter(key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
        "max_tokens": _OPENROUTER_MAX_TOKENS,
    }
    data = _post(_CHAT_URLS[OPENROUTER], payload, _bearer_headers(key))
    return _chat_reply(data, "OpenRouter", model)


def _ask_openai_shaped_vision(provider: str, label: str, key: str,
                              model: str, parts: list,
                              max_tokens: int = _XKIRO_MAX_TOKENS,
                              tries: int = 2,
                              wait: int = _BUSY_RETRY_SECONDS) -> tuple:
    """(reply_text, why_not) from any OpenAI-shaped provider, with frames.

    One function rather than one per gateway: xKiro, NVIDIA and anything
    else OpenAI-shaped take byte-identical requests, and the only things
    that differ are the URL, the name in the log line and how many
    tokens to allow. A second copy of this would be a second place for
    the retry-on-busy rule to drift.

    Returns the reason rather than swallowing it - a vision request
    fails for reasons the text one never does, and "came back empty" is
    not something anyone can act on.
    """
    parts = thin_images(parts, images_allowed(provider))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT + VISION_NOTE},
            {"role": "user", "content": to_openai_content(parts)},
        ],
        "temperature": 0.4,
        "max_tokens": max_tokens,
        **_json_mode(provider),
    }
    images = sum(1 for part in parts if "inline_data" in part)
    megabytes = len(json.dumps(payload)) / 1e6

    # A busy gateway is not a reason to throw away the pass. The
    # measured failures here are a plain HTTP 500 "A server error
    # occurred" on xKiro and a 503 "ResourceExhausted" on NVIDIA's
    # shared preview pool; both Geminis answer 503 and "high demand" the
    # same way. All of them clear on a retry.
    #
    # How many retries and how long to wait are per provider, because
    # the shape differs: xKiro's 500 is rare and worth one patient
    # retry, NVIDIA's 503 comes back in 0.3s about half the time and is
    # worth several impatient ones.
    data = problem = None
    for attempt in range(max(1, tries)):
        if attempt:
            print(f"[Clips] {label} said {problem} - waiting {wait}s and "
                  f"trying again ({attempt + 1}/{tries})...")
            time.sleep(wait)
        data, problem = _post_detailed(_CHAT_URLS[provider], payload,
                                       _bearer_headers(key),
                                       timeout=_VISION_TIMEOUT)
        if not problem or not _is_transient(problem):
            break

    if problem:
        return "", f"{problem} ({images} images, {megabytes:.1f} MB)"
    if not isinstance(data, dict):
        return "", f"no response ({images} images, {megabytes:.1f} MB)"
    text = _chat_reply(data, label, model)
    if text:
        return text, ""
    return "", f"reply had no text: {str(data)[:200]}"


def _ask_xkiro_vision(key: str, model: str, parts: list) -> tuple:
    return _ask_openai_shaped_vision(XKIRO, "xKiro", key, model, parts)


def _ask_nvidia_vision(key: str, model: str, parts: list) -> tuple:
    return _ask_openai_shaped_vision(NVIDIA, "NVIDIA", key, model, parts,
                                     max_tokens=_NVIDIA_MAX_TOKENS,
                                     tries=_NVIDIA_BUSY_TRIES,
                                     wait=_NVIDIA_BUSY_WAIT)


def vision_asker_for(provider: str):
    """The function that shows this provider the frames, or None.

    None means the provider is text-only, and the caller should use the
    words - not that the pass has failed.
    """
    return {GEMINI: _ask_gemini_vision, XKIRO: _ask_xkiro_vision,
            NVIDIA: _ask_nvidia_vision}.get(provider)


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

    # Vision is tried FIRST and falls back to the words on any failure: a
    # model that cannot see is the behaviour this had all along, and it
    # is much better than no clips.
    #
    # Which providers get the frames comes from VISION_PROVIDERS rather
    # than a hard-coded GEMINI, because the vision pass used to be a
    # single point of failure: one provider refusing meant every clip
    # that day was picked on words alone.
    raw = ""
    vision_ask = vision_asker_for(provider) if ask is None else None
    if source_path and vision_ask is not None:
        looking = shortlist[:VISION_MAX_CANDIDATES]
        why = ""
        try:
            parts = build_vision_contents(looking, count, source_path)
            images = sum(1 for part in parts if "inline_data" in part)
            if not images:
                why = "no frames could be read from the video"
            else:
                raw, why = vision_ask(key, model, parts)
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
        _LAST_OUTAGE["why"] = ""
        try:
            raw = speak(key, model, prompt)
        except Exception:
            return [], shortlist

    picked = parse_reply(raw, len(shortlist))
    if not picked and not str(raw or "").strip() and last_outage():
        # Down, not refusing: asking again "without titles" only waits
        # through the same outage. The next provider is the answer.
        print(f"[Clips] {provider} is unavailable right now "
              f"({last_outage()}) - moving on.")
        return [], shortlist
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
    """The model's picks, as Highlights, in timeline order.

    The prompt tells the model to score anything it is unsure of below
    50 and leave it out - MIN_CLIP_SCORE enforces that here rather than
    trusting it happened. A model that pads its answer to reach `count`,
    or just drifts on the instruction, can return a real number below the
    floor for a candidate it does not actually believe in; without this,
    that candidate still gets rendered and posted the moment fewer than
    `count` strong ones came back.

    Dropping weak picks here, rather than asking the model to simply not
    send them, also survives a provider that never fully implements
    "leave it out" - the filter does not care why the number was low.
    """
    picked.sort(key=lambda item: item[1], reverse=True)
    strong = [item for item in picked if item[1] >= MIN_CLIP_SCORE]
    if len(strong) < len(picked):
        dropped = len(picked) - len(strong)
        print(f"[Clips] Dropped {dropped} pick(s) that scored below "
              f"{MIN_CLIP_SCORE:.0f} - not confident enough to post.")
    chosen = []
    for index, score, title in strong[:count]:
        highlight = shortlist[index - 1]
        if title:
            # The model read the clip; its title beats the best sentence
            # picked out of it by length and punctuation alone.
            highlight.hook = title
        highlight.score = score or highlight.score
        chosen.append(highlight)
    chosen.sort(key=lambda h: h.start)
    return chosen
