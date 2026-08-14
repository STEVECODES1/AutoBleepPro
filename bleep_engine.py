"""
bleep_engine - pure logic for AutoBleep Pro.

No GUI, no threading, no global mutable state. Everything here is
importable and testable without customtkinter, torch, whisper, moviepy,
ffmpeg or a GPU: the heavy dependencies are imported lazily/optionally and
the detection half needs nothing but `better_profanity`.

Public API
----------
Detection  : find_profanity_v2, check_word, sensitivity_band
Device     : detect_device, configure_threads
Models     : load_model_speed, ModelCache, ModelBundle, transcribe_words
Audio      : extract_audio_fast, make_beep, make_bleep_segment, apply_bleeps
Transcript : words_to_srt, words_to_txt, bleeps_to_srt, group_into_cues
Video I/O  : extract_audio, render_video
Pipeline   : ProcessOptions, ProcessResult, process_video
Paths      : build_output_path, is_generated_output, safe_remove, list_videos
"""

from __future__ import annotations

import gc
import itertools
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import warnings
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from better_profanity import profanity

profanity.load_censor_words()

try:
    from autoreel.profanity_extra import contains_extra as _contains_extra
except ImportError:  # pragma: no cover - autoreel/ absent in a bare copy
    def _contains_extra(candidate: str) -> bool:
        return False

# ── Optional heavy dependencies ──────────────────────────────────────────────
# Imported defensively so `import bleep_engine` works in CI (and in unit
# tests) on a machine with none of the ML/audio stack installed.

try:  # torch: only ever used for CUDA detection + thread count
    import torch
except ImportError:  # pragma: no cover - exercised only on minimal installs
    torch = None  # type: ignore[assignment]

try:
    from pydub import AudioSegment
    from pydub.generators import Sine
except ImportError:  # pragma: no cover
    AudioSegment = None  # type: ignore[assignment]
    Sine = None  # type: ignore[assignment]

try:
    import stable_whisper
    SPEED_MODE = True
except ImportError:
    stable_whisper = None  # type: ignore[assignment]
    SPEED_MODE = False

try:
    import whisper as openai_whisper
except ImportError:  # pragma: no cover
    openai_whisper = None  # type: ignore[assignment]


VIDEO_EXTS: frozenset[str] = frozenset(
    {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".m4v", ".webm", ".ts"}
)

__version__ = "2.3.0"

OUTPUT_SUFFIX = "_CLEAN"

# ── Detection sensitivity ────────────────────────────────────────────────────
# A plain rule gate, not a score: the number picks which families of rules
# are allowed to fire. Nothing here is probabilistic.
#
#   0-30    LOW     strong direct profanity, leet decodes, symbol bypasses
#                   and custom words only. No minced oaths, no Whisper
#                   mishears, no context inference.
#   31-70   NORMAL  the above, plus minced oaths ("fudge"), Whisper mishears
#                   ("duck"), and context-only candidates when a trigger
#                   phrase points at that word's own target
#                   ("son of a" + "beach").
#   71-100  HIGH    the above, plus context-only candidates on *any* nearby
#                   profanity signal rather than a matching trigger, and a
#                   wider trigger window.
SENSITIVITY_LOW_MAX = 30
SENSITIVITY_NORMAL_MAX = 70
DEFAULT_SENSITIVITY = 70

BAND_LOW = "low"
BAND_NORMAL = "normal"
BAND_HIGH = "high"

# ── Censoring ────────────────────────────────────────────────────────────────
METHOD_BEEP = "beep"
METHOD_SILENCE = "silence"
# Muting is the default: it's the less intrusive edit, and it's what most
# uploads actually want.
DEFAULT_METHOD = METHOD_SILENCE
DEFAULT_BEEP_FREQ = 1000

# large-v3 is the accuracy ceiling and the reason it is offered: a word
# the transcript never contains cannot be muted, so the model is what
# decides how much profanity gets through. It is slow on CPU and
# comfortable on a GPU.
MODEL_CHOICES = ("tiny", "base", "small", "medium", "turbo",
                 "large-v2", "large-v3")

# Widen every mute by this much on each side. Whisper's word timings are
# 100-300ms out, so muting the exact reported span leaves the leading
# syllable audible - and a word whose first syllable survives is not
# censored. This engine had no padding at all.
DEFAULT_PADDING_MS = 250
# How far that pad may cross into the word next to it. Large enough to
# absorb the timing error above, small enough that the neighbouring word
# stays intelligible. See _clamp_to_neighbours.
NEIGHBOUR_BLEED_MS = 120
COMPUTE_CHOICES = ("auto", "int8", "float16", "float32")
ENCODE_CHOICES = ("ultrafast", "fast", "medium", "slow")


def clamp_sensitivity(value: int | float | None) -> int:
    """Coerce anything user-supplied into 0-100."""
    if value is None:
        return DEFAULT_SENSITIVITY
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return DEFAULT_SENSITIVITY


def sensitivity_band(value: int | float | None) -> str:
    value = clamp_sensitivity(value)
    if value <= SENSITIVITY_LOW_MAX:
        return BAND_LOW
    if value <= SENSITIVITY_NORMAL_MAX:
        return BAND_NORMAL
    return BAND_HIGH


# ═════════════════════════════════════════════════════════════════════════════
# WORD DETECTION
# ═════════════════════════════════════════════════════════════════════════════

# Primary leet substitutions, used for the fast single-candidate decode.
LEET_MAP = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
    "6": "g", "7": "t", "8": "b", "9": "g",
    "@": "a", "$": "s", "!": "i", "+": "t", "#": "h",
    "*": "",
})

# Ambiguous leet characters that legitimately stand for more than one
# letter ("f@ck" wants @→u, "@ss" wants @→a). Each position is expanded
# combinatorially, bounded by _MAX_LEET_POSITIONS so a symbol-spammed
# token can't blow up into thousands of candidates.
LEET_VARIANTS: dict[str, str] = {
    "0": "o",
    "1": "il",
    "3": "e",
    "4": "a",
    "5": "s",
    "6": "g",
    "7": "t",
    "8": "b",
    "9": "g",
    "@": "au",
    "$": "s",
    "!": "i",
    "+": "t",
    "#": "h",
    # "f*ck" masks a real letter, not nothing - try each vowel as well as
    # the plain deletion that _deleet() already covers.
    "*": "aeiou",
}
# Hard ceiling on the candidate explosion: a symbol-spammed token must not
# turn into thousands of profanity lookups.
_MAX_LEET_COMBOS = 256

# Second alternative uses a negative lookahead rather than \b: a token
# ending in "*" ("f***") has no word boundary after the symbols, so the
# \b form never matched it at all.
BYPASS_PATTERN = re.compile(
    r"\b([a-z])[*@#$!]{1,4}([a-z])\b|\b([a-z])[*@#$!]{2,}(?![a-z0-9])",
    re.IGNORECASE,
)

# A mask keeps only the first and last letter or two ("f**k", "b***h").
# Anything with more surviving letters is ordinary text that happens to
# sit next to punctuation, and must not be flagged.
_MAX_BYPASS_LETTERS = 3

# first letter -> plausible last letters of a symbol-masked profanity
BYPASS_STARTS: dict[str, set[str]] = {
    "f": {"k"},
    "s": {"t", "r"},
    "b": {"h", "d"},
    "a": {"e"},
    "c": {"t", "k"},
    "d": {"k"},
    "p": {"s", "y"},
    "h": {"l"},
    "n": {"r", "a"},
    "w": {"e"},
    "j": {"z"},
}

HOMOPHONES: dict[str, list[str]] = {
    "fudge": ["fuck"],
    "frick": ["fuck"],
    "freak": ["fuck"],
    "freaking": ["fucking"],
    "frickin": ["fucking"],
    "frigging": ["fucking"],
    "effing": ["fucking"],
    "shoot": ["shit"],
    "ship": ["shit"],
    "sugar": ["shit"],
    "sheet": ["shit"],
    "dang": ["damn"],
    "darn": ["damn"],
    "dagnabbit": ["goddammit"],
    "crap": ["shit"],
    "crud": ["crap"],
    "witch": ["bitch"],
    "beach": ["bitch"],
    "rich": ["bitch"],
    "bass": ["ass"],
    "butt": ["ass"],
    "behind": ["ass"],
    "heck": ["hell"],
}

# Homophones too common in innocent speech to flag on their own - they
# only count when the surrounding words make the intent obvious.
CONTEXT_ONLY: set[str] = {"witch", "beach", "bass", "rich", "sheet"}

CONTEXT_TRIGGERS: list[tuple[str, str]] = [
    ("son of a", "bitch"),
    ("what the", "hell"),
    ("what the", "heck"),
    ("go to", "hell"),
    ("holy", "shit"),
    ("bull", "shit"),
    ("horse", "shit"),
    ("no", "shit"),
    ("you piece of", "shit"),
    ("piece of", "shit"),
    ("mother", "fucker"),
    ("piece of", "ass"),
    ("dumb", "ass"),
    ("smart", "ass"),
    ("bad", "ass"),
    ("kick", "ass"),
]

WHISPER_MISHEARDS: dict[str, str] = {
    "shirt": "shit",
    "witch": "bitch",
    "batch": "bitch",
    "ditch": "bitch",
    "rich": "bitch",
    "cluck": "fuck",
    "duck": "fuck",
    "luck": "fuck",
    "truck": "fuck",
    "stuck": "fuck",
    "shut": "shit",
    "shot": "shit",
    "ship": "shit",
    "shop": "shit",
}

MISHEARD_CONTEXT_ONLY: set[str] = {"luck", "truck", "stuck", "rich", "shot", "shop"}

_AFFIX_SUFFIXES = ("ing", "ed", "er", "ers", "in", "s")
_AFFIX_PREFIXES = ("mother", "bull", "horse", "cluster", "jack", "dumb",
                   "god", "holy", "un", "out")


def _normalize(text: str) -> str:
    """Lowercase, strip accents, keep letters/digits/leet symbols only."""
    text = unicodedata.normalize("NFD", text.lower())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9@$!*#+]", "", text)


def _deleet(text: str) -> str:
    """Single best-guess leet decode (fast path)."""
    return text.translate(LEET_MAP)


def _deleet_variants(text: str) -> list[str]:
    """Every plausible leet decode of `text`.

    "f@ck" is ambiguous - @ is 'a' in "@ss" and 'u' in "f@ck" - so a single
    substitution table can't decode both. This expands each ambiguous
    position and returns all combinations (bounded), letting the caller
    test them all against the profanity list.
    """
    if not any(ch in LEET_VARIANTS for ch in text):
        return []

    # _deleet() drops "*" entirely; the expansion below substitutes a
    # letter for it. Both are plausible, so try both.
    out = [_deleet(text)]

    options: list[Sequence[str]] = [LEET_VARIANTS.get(ch, ch) for ch in text]
    combos = 1
    for opt in options:
        combos *= len(opt)
        if combos > _MAX_LEET_COMBOS:
            return out

    out.extend("".join(combo) for combo in itertools.product(*options))
    return out


def _strip_affixes(word: str) -> list[str]:
    """`word` plus the plausible stems left after removing common
    prefixes/suffixes, so "motherfucking" reaches "fuck"."""
    candidates = [word]
    for suffix in _AFFIX_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            candidates.append(word[: -len(suffix)])
    for prefix in _AFFIX_PREFIXES:
        if word.startswith(prefix) and len(word) > len(prefix) + 2:
            candidates.append(word[len(prefix):])
    return candidates


def _is_bypass(raw_word: str) -> bool:
    """Detect symbol-masked profanity: f**k, s**t, b***h."""
    if not BYPASS_PATTERN.search(raw_word):
        return False
    letters = re.sub(r"[^a-z]", "", raw_word.lower())
    if not letters or len(letters) > _MAX_BYPASS_LETTERS:
        return False
    # First *letter*, not first character: "*f**k" starts with a symbol.
    first, last = letters[0], letters[-1]
    if last in BYPASS_STARTS.get(first, set()):
        return True
    return len(raw_word) <= 6 and raw_word.count("*") >= 2


def _profane(candidate: str) -> bool:
    if not candidate:
        return False
    # The base list plus the compound insults it does not carry - see
    # autoreel/profanity_extra for where those came from and what was
    # deliberately left out of them.
    return profanity.contains_profanity(candidate) or _contains_extra(candidate)


def _has_weak_context(clean_word: str, context_words: Sequence[str],
                      ctx_str: str) -> bool:
    """Any signal at all that the surrounding speech is profane.

    Used only at high sensitivity, where a context-only candidate is
    allowed to fire on a nearby trigger phrase / profanity / minced oath
    rather than on a trigger that points specifically at its own target.
    """
    for trigger, _target in CONTEXT_TRIGGERS:
        if trigger in ctx_str:
            return True
    return any(
        cw and cw != clean_word
        and (_profane(cw) or cw in HOMOPHONES or cw in WHISPER_MISHEARDS)
        for cw in context_words
    )


def check_word(
    raw_word: str,
    context_words: Sequence[str],
    custom_words: Sequence[str],
    fuzzy: bool = True,
    sensitivity: int = DEFAULT_SENSITIVITY,
) -> tuple[bool, str]:
    """Decide whether a single transcribed token should be bleeped.

    `context_words` are the preceding tokens (already lowercased, letters
    only). `sensitivity` (0-100) selects how many rule families run - see
    `sensitivity_band` for the exact bands.

    `fuzzy=False` is retained for backwards compatibility and simply
    clamps `sensitivity` into the low band.

    Returns (should_bleep, human-readable reason).
    """
    if not fuzzy:
        sensitivity = min(sensitivity, SENSITIVITY_LOW_MAX)
    band = sensitivity_band(sensitivity)

    raw_word = (raw_word or "").strip()
    if not raw_word:
        return False, ""

    # ── Always on, at every sensitivity ──────────────────────────────────
    if _is_bypass(raw_word):
        return True, "Symbol bypass (f**k style)"

    norm = _normalize(raw_word)
    if not norm:
        return False, ""

    stripped = re.sub(r"^[^a-z]+|[^a-z]+$", "", norm)
    clean_word = re.sub(r"[^a-z]", "", stripped)

    # Leet decodes first: they're the most specific signal, and reporting
    # them as such is more useful than a generic "profanity detected".
    for variant in _deleet_variants(norm):
        if variant == norm:
            continue
        for stem in _strip_affixes(variant):
            if _profane(stem):
                return True, "Leet-speak profanity"

    candidates: set[str] = set()
    for base in (norm, stripped, clean_word):
        candidates.update(_strip_affixes(base))
    for candidate in candidates:
        if _profane(candidate):
            return True, "Profanity detected"

    # Custom words are an explicit user instruction, so they are honoured
    # even in the low band. Multi-word entries ("rival brand") can never
    # match a single token, so they're tested against the trailing context
    # plus this word - which is what the GUI's placeholder text promises.
    if custom_words:
        phrase = " ".join([*context_words[-6:], clean_word]).strip()
        for cw in custom_words:
            if not cw:
                continue
            if " " in cw:
                if cw in phrase:
                    return True, "Custom phrase"
            elif cw in norm or cw in stripped:
                return True, "Custom word"

    if band == BAND_LOW:
        return False, ""

    # ── Normal band and above: minced oaths, mishears, context ───────────
    ctx_str = " ".join(context_words)
    aggressive = band == BAND_HIGH

    if clean_word in HOMOPHONES:
        if clean_word not in CONTEXT_ONLY:
            return True, f"Likely profanity substitute ('{clean_word}')"
        # Context-only: normally require a trigger phrase that points at
        # the same underlying word ("son of a" -> bitch, for "beach").
        targets = HOMOPHONES[clean_word]
        for trigger, target in CONTEXT_TRIGGERS:
            if target in targets and trigger in ctx_str:
                return True, f"Context homophone ('{clean_word}')"
        if aggressive and _has_weak_context(clean_word, context_words, ctx_str):
            return True, f"Context homophone, weak context ('{clean_word}')"

    if clean_word in WHISPER_MISHEARDS:
        heard = WHISPER_MISHEARDS[clean_word]
        if clean_word not in MISHEARD_CONTEXT_ONLY:
            return True, f"Whisper mishear of '{heard}'"
        if any(_profane(cw) or cw in HOMOPHONES for cw in context_words):
            return True, f"Whisper mishear (context) of '{heard}'"
        if aggressive and _has_weak_context(clean_word, context_words, ctx_str):
            return True, f"Whisper mishear, weak context ('{heard}')"

    # The trigger window widens in the aggressive band.
    recent = " ".join(context_words[-(5 if aggressive else 4):])
    for trigger, target in CONTEXT_TRIGGERS:
        if clean_word == target and trigger in recent:
            return True, f"Context trigger ('{trigger} {target}')"

    return False, ""


def _flatten_words(result: Any) -> list[dict]:
    """Pull a flat word list out of a whisper-style result dict."""
    words: list[dict] = []
    for segment in (result or {}).get("segments", []) or []:
        for word_info in (segment or {}).get("words", []) or []:
            if isinstance(word_info, dict):
                words.append(word_info)
    return words


def find_profanity_v2(
    result: Any,
    custom_words: Sequence[str],
    fuzzy: bool = True,
    sensitivity: int = DEFAULT_SENSITIVITY,
) -> list[dict]:
    """Scan a transcription result and return the words to bleep.

    Each hit is {"word", "start", "end", "reason"}. Hits are deduplicated
    by start timestamp (rounded to the millisecond) so a token that trips
    two different rules is only bleeped once.

    `sensitivity` is 0-100; see the module-level table. `fuzzy=False` is
    kept for backwards compatibility and clamps into the low band.
    """
    all_words = _flatten_words(result)
    found: list[dict] = []
    seen_starts: set[int] = set()

    for idx, word_info in enumerate(all_words):
        raw = str(word_info.get("word", "") or "")
        try:
            start = float(word_info.get("start", 0.0) or 0.0)
            end = float(word_info.get("end", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue

        context = [
            re.sub(r"[^a-z]", "", str(all_words[i].get("word", "") or "").strip().lower())
            for i in range(max(0, idx - 5), idx)
        ]

        is_bad, reason = check_word(raw, context, custom_words,
                                    fuzzy=fuzzy, sensitivity=sensitivity)
        if not is_bad:
            continue

        key = int(round(start * 1000))
        if key in seen_starts:
            continue
        seen_starts.add(key)
        found.append({"word": raw, "start": start, "end": end, "reason": reason})

    return found


# Back-compat alias: v2.2 shipped this as a private helper.
_check_word = check_word


# ═════════════════════════════════════════════════════════════════════════════
# DEVICE / MODELS
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class ModelBundle:
    """A loaded transcription model plus the backend that actually loaded it.

    Tracking `backend` matters: load_model_speed() can fall back to
    openai-whisper even when stable-ts imported fine, and the two return
    completely different result shapes.
    """
    model: Any
    device: str
    backend: str          # "faster-whisper" | "stable-ts" | "openai-whisper"
    label: str


def detect_device() -> tuple[str, str]:
    if torch is not None:
        try:
            if torch.cuda.is_available():
                return "cuda", torch.cuda.get_device_name(0)
        except Exception:  # pragma: no cover - driver-dependent
            pass
    return "cpu", f"{os.cpu_count() or 1} CPU cores"


def configure_threads() -> None:
    """Let torch use every core for CPU inference."""
    if torch is not None:
        try:
            torch.set_num_threads(os.cpu_count() or 1)
        except Exception:  # pragma: no cover
            pass


def load_model_speed(model_name: str, compute_pref: str) -> ModelBundle:
    """Load `model_name`, preferring faster-whisper > stable-ts > openai-whisper."""
    device, dev_label = detect_device()

    if SPEED_MODE and stable_whisper is not None:
        compute_type = compute_pref
        if compute_pref == "auto":
            compute_type = "float16" if device == "cuda" else "int8"
        try:
            model = stable_whisper.load_faster_whisper(
                model_name, device=device, compute_type=compute_type)
            return ModelBundle(
                model, device, "faster-whisper",
                f"faster-whisper [{compute_type}] on {device.upper()} ({dev_label})")
        except Exception as exc:
            print(f"[AutoBleep] faster-whisper failed ({exc}), trying stable-ts...")
        try:
            model = stable_whisper.load_model(model_name, device=device)
            return ModelBundle(
                model, device, "stable-ts",
                f"stable-ts on {device.upper()} ({dev_label})")
        except Exception as exc:
            print(f"[AutoBleep] stable-ts failed ({exc}), falling back to openai-whisper...")

    if openai_whisper is None:
        raise RuntimeError(
            "No transcription backend available. Install one of:\n"
            "  pip install stable-ts[fw]   (recommended, ~4x faster)\n"
            "  pip install openai-whisper")

    model = openai_whisper.load_model(model_name, device=device)
    return ModelBundle(
        model, device, "openai-whisper",
        f"openai-whisper on {device.upper()} ({dev_label})")


class ModelCache:
    """Keeps one loaded model alive across runs.

    Loading a whisper model costs seconds-to-minutes; batch mode and
    repeated single-video runs would otherwise pay it every time. The
    cached model is released (and CUDA memory reclaimed) before a
    different one is loaded, so switching models can't stack two copies on
    the GPU.
    """

    def __init__(self) -> None:
        self._key: tuple[str, str] | None = None
        self._bundle: ModelBundle | None = None

    def get(self, model_name: str, compute_pref: str) -> ModelBundle:
        key = (model_name, compute_pref)
        if key == self._key and self._bundle is not None:
            return self._bundle
        self.release()
        bundle = load_model_speed(model_name, compute_pref)
        self._key, self._bundle = key, bundle
        return bundle

    @property
    def cached_key(self) -> tuple[str, str] | None:
        return self._key

    def release(self) -> None:
        if self._bundle is None:
            return
        self._bundle = None
        self._key = None
        gc.collect()
        if torch is not None:
            try:
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:  # pragma: no cover
                pass


# Whisper is trained on cleaned-up transcripts and will quietly sanitise
# swearing - writing "f***", softening a slur, or dropping it entirely.
# For a censoring tool that is the whole ballgame: a word the transcript
# never contains cannot be muted, so it reaches the export untouched.
#
# initial_prompt is the documented lever. Whisper conditions the decode on
# it, so a prompt written in the register of the audio biases the model
# toward transcribing verbatim instead of tidying. It never appears in the
# output - it only shapes how the audio is read.
VERBATIM_PROMPT = (
    "The following is an unedited, verbatim gaming stream transcript. "
    "It contains explicit language, insults and swearing, transcribed "
    "exactly as spoken with no censoring, no asterisks and no omissions. "
    "Example: Oh shit, what the fuck was that, you damn idiot, holy crap."
)


def transcribe_options(backend: str) -> dict:
    """Decode settings that decide how much profanity is heard at all.

    Kept in one place because the two backends take the same names and
    getting them out of step is invisible - the run succeeds either way
    and simply catches less.
    """
    options = {
        "word_timestamps": True,
        "initial_prompt": VERBATIM_PROMPT,
        # Whisper otherwise feeds each window its own previous output, and
        # over hours of gameplay one bad window makes the next worse - it
        # loops or drifts, and whole minutes come back as repeated filler
        # with the real words gone.
        "condition_on_previous_text": False,
    }
    if backend != "openai-whisper":
        # A wider search costs time and finds words a greedy decode drops.
        # Missing a slur is more expensive here than the extra minutes.
        options["beam_size"] = 5
    return options


def transcribe_words(bundle: ModelBundle, audio_path: str) -> dict:
    """Transcribe with word timestamps, normalised to whisper's dict shape."""
    options = transcribe_options(bundle.backend)
    if bundle.backend == "openai-whisper":
        return bundle.model.transcribe(audio_path, **options)

    result = bundle.model.transcribe(audio_path, **options)
    segments = []
    for seg in getattr(result, "segments", []) or []:
        words = []
        for w in (getattr(seg, "words", None) or []):
            if w is None or w.word is None:
                continue
            words.append({
                "word": w.word,
                "start": float(w.start),
                "end": float(w.end),
            })
        segments.append({"words": words, "text": getattr(seg, "text", "")})
    return {"segments": segments}


# ═════════════════════════════════════════════════════════════════════════════
# AUDIO
# ═════════════════════════════════════════════════════════════════════════════

def extract_audio_fast(video_path: str, wav_path: str) -> bool:
    """Extract 16 kHz mono WAV with ffmpeg. False if ffmpeg is missing or fails."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-ac", "1", "-ar", "16000", "-vn", wav_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False
    return os.path.exists(wav_path) and os.path.getsize(wav_path) > 0


@lru_cache(maxsize=32)
def _beep_base(freq_hz: int):
    """One 100 ms sine per frequency. Bounded by the number of presets."""
    return Sine(freq_hz).to_audio_segment(duration=100)


def make_beep(duration_ms: int, freq_hz: int):
    """A `duration_ms` beep at `freq_hz`.

    Only the 100 ms base tone is cached; v2.2 cached one segment per
    (duration, frequency) pair in a module-level dict that was never
    evicted, so a long video accumulated a distinct cached AudioSegment
    for every distinct word length.
    """
    if AudioSegment is None:  # pragma: no cover
        raise RuntimeError("pydub is required for audio processing")
    duration_ms = max(1, int(duration_ms))
    base = _beep_base(int(freq_hz))
    return (base * (duration_ms // len(base) + 1))[:duration_ms]


@lru_cache(maxsize=8)
def _load_custom_beep_cached(path: str, mtime: float, size: int):
    """Cache key includes mtime+size so editing the WAV takes effect."""
    return AudioSegment.from_file(path)


def load_custom_beep(path: str | os.PathLike[str] | None):
    """Load a user-supplied beep sample, or None if it can't be used.

    Never raises: a bad path is a settings mistake, not a crash - callers
    fall back to the generated tone.
    """
    if not path or AudioSegment is None:
        return None
    try:
        p = os.fspath(path)
        stat = os.stat(p)
        seg = _load_custom_beep_cached(p, stat.st_mtime, stat.st_size)
        return seg if len(seg) > 0 else None
    except Exception:
        return None


def validate_beep_wav(path: str | os.PathLike[str] | None) -> tuple[bool, str]:
    """(usable, human-readable reason). For one-time UI/CLI warnings."""
    if not path:
        return False, "No custom beep selected."
    p = os.fspath(path)
    if not os.path.exists(p):
        return False, f"Custom beep file not found: {p}"
    if AudioSegment is None:  # pragma: no cover
        return False, "pydub is not installed."
    seg = load_custom_beep(p)
    if seg is None:
        return False, (f"Could not read {os.path.basename(p)} as audio "
                       "(is it a valid .wav?)")
    return True, f"Using custom beep: {os.path.basename(p)} ({len(seg)} ms)"


def make_bleep_segment(
    duration_ms: int,
    freq_hz: int | None = None,
    custom_wav: str | os.PathLike[str] | None = None,
):
    """The audio used to cover one censored word, exactly `duration_ms` long.

    With `custom_wav`, the sample is looped if it's too short and trimmed
    if it's too long. Falls back to a `freq_hz` sine when the file is
    missing or unreadable, so a broken path degrades to the old behaviour
    instead of failing the export.
    """
    if AudioSegment is None:  # pragma: no cover
        raise RuntimeError("pydub is required for audio processing")
    duration_ms = max(1, int(duration_ms))

    sample = load_custom_beep(custom_wav)
    if sample is not None:
        looped = sample * (duration_ms // len(sample) + 1)
        return looped[:duration_ms]

    return make_beep(duration_ms, int(freq_hz or DEFAULT_BEEP_FREQ))


def _match_params(seg, ref):
    """Coerce `seg` to `ref`'s frame rate / channels / sample width."""
    if seg.frame_rate != ref.frame_rate:
        seg = seg.set_frame_rate(ref.frame_rate)
    if seg.channels != ref.channels:
        seg = seg.set_channels(ref.channels)
    if seg.sample_width != ref.sample_width:
        seg = seg.set_sample_width(ref.sample_width)
    return seg


def _word_bounds(all_words: Iterable[dict] | None) -> list[tuple[int, int]]:
    """Every word's (start_ms, end_ms), sorted. Used to find what sits
    either side of a hit so the padding can stop there."""
    bounds: list[tuple[int, int]] = []
    for wd in all_words or ():
        try:
            s = int(round(float(wd.get("start", 0.0)) * 1000))
            e = int(round(float(wd.get("end", 0.0)) * 1000))
        except (TypeError, ValueError, AttributeError):
            continue
        bounds.append((min(s, e), max(s, e)))
    bounds.sort()
    return bounds


def _clamp_to_neighbours(s: int, e: int, padded_s: int, padded_e: int,
                         bounds: list[tuple[int, int]]) -> tuple[int, int]:
    """Keep a padded span out of the words either side of it.

    The pad exists to cover Whisper's timing error, which is 100-300ms.
    Applied blindly it also clips whatever was said before and after,
    which is what makes a censored video sound chopped up. Padding into
    the SILENCE around a word costs nothing, so the pad expands freely
    into a gap and stops when it reaches actual speech.

    It may still cross into a neighbour by NEIGHBOUR_BLEED_MS, because
    the neighbour's reported boundary is only approximate too, and
    stopping dead at it would leave the flagged syllable audible in
    exactly the tight-speech case that needs the pad most.
    """
    for b_start, b_end in bounds:
        if b_end <= s:
            padded_s = max(padded_s, min(b_end - NEIGHBOUR_BLEED_MS, s))
        elif b_start >= e:
            padded_e = min(padded_e, max(b_start + NEIGHBOUR_BLEED_MS, e))
            break
    return padded_s, padded_e


def merge_spans(
    words: Iterable[dict],
    total_ms: int,
    min_ms: int = 50,
    padding_ms: int = 0,
    all_words: Iterable[dict] | None = None,
) -> list[tuple[int, int]]:
    """Word timings -> sorted, clamped, non-overlapping (start, end) ms spans.

    Spans shorter than `min_ms` are widened *around their centre* rather
    than by pushing the end out, and overlaps are merged. Both matter:
    without merging, two overlapping spans produce duplicated audio when
    the track is rebuilt.

    `padding_ms` widens each span on both sides, because muting the exact
    reported span leaves the leading syllable audible - Whisper's word
    timings are 100-300ms out, and a word whose first syllable survives
    is not censored. Pass `all_words` (the whole transcript, not just the
    hits) and the padding is clamped to the words either side, so it takes
    the flagged word and leaves the sentence around it intact.
    """
    bounds = _word_bounds(all_words) if padding_ms and all_words else []
    spans: list[tuple[int, int]] = []
    for wd in words:
        try:
            s = int(round(float(wd.get("start", 0.0)) * 1000))
            e = int(round(float(wd.get("end", 0.0)) * 1000))
        except (TypeError, ValueError, AttributeError):
            continue
        if e < s:
            s, e = e, s
        if e - s < min_ms:
            centre = (s + e) // 2
            s, e = centre - min_ms // 2, centre - min_ms // 2 + min_ms
        if padding_ms:
            padded_s, padded_e = s - padding_ms, e + padding_ms
            if bounds:
                padded_s, padded_e = _clamp_to_neighbours(
                    s, e, padded_s, padded_e, bounds)
            s, e = padded_s, padded_e
        s = max(0, min(s, total_ms))
        e = max(0, min(e, total_ms))
        if e > s:
            spans.append((s, e))

    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def apply_bleeps(
    audio_seg,
    words: Sequence[dict],
    method: str = DEFAULT_METHOD,
    freq_hz: int = DEFAULT_BEEP_FREQ,
    min_ms: int = 50,
    progress: Callable[[int, int], None] | None = None,
    custom_wav: str | os.PathLike[str] | None = None,
    padding_ms: int = DEFAULT_PADDING_MS,
    all_words: Sequence[dict] | None = None,
):
    """Return `audio_seg` with every span in `words` replaced by a beep or silence.

    Two things v2.2 got wrong, both fixed here:

    1. It rebuilt the entire track per word
       (`seg = seg[:s] + bleep + seg[e:]`), which is O(n2) in the number of
       hits. This builds the new track in a single pass instead.
    2. That splice was also *incorrect*: the replacement was
       `max(end - start, 50)` ms long but only `end - start` ms were
       removed, so every sub-50 ms hit stretched the track, desynced the
       audio from the video, and shifted every later timestamp out of
       position. Replacements here are always exactly as long as the span
       they replace, so total duration is preserved bit-for-bit.
    """
    if AudioSegment is None:  # pragma: no cover
        raise RuntimeError("pydub is required for audio processing")

    total_ms = len(audio_seg)
    spans = merge_spans(words, total_ms, min_ms, padding_ms, all_words)
    if not spans:
        return audio_seg

    frame_rate, frame_width = audio_seg.frame_rate, audio_seg.frame_width
    raw = audio_seg.raw_data
    n_bytes = len(raw)

    def to_byte(ms: int) -> int:
        return max(0, min(n_bytes, int(ms * frame_rate / 1000.0) * frame_width))

    pieces: list[bytes] = []
    cursor = 0
    for i, (s_ms, e_ms) in enumerate(spans):
        b_start, b_end = to_byte(s_ms), to_byte(e_ms)
        if b_end <= b_start:
            continue
        if b_start > cursor:
            pieces.append(raw[cursor:b_start])

        span_ms = e_ms - s_ms
        if method == METHOD_SILENCE:
            # True digital silence for the censored region - no tone.
            filler = AudioSegment.silent(duration=span_ms, frame_rate=frame_rate)
        else:
            filler = make_bleep_segment(span_ms, freq_hz, custom_wav)
        data = _match_params(filler, audio_seg).raw_data

        want = b_end - b_start
        if len(data) > want:
            data = data[:want]
        elif len(data) < want:
            data += b"\x00" * (want - len(data))  # zero == silence for signed PCM
        pieces.append(data)

        cursor = b_end
        if progress is not None:
            progress(i + 1, len(spans))

    if cursor < n_bytes:
        pieces.append(raw[cursor:])

    return audio_seg._spawn(b"".join(pieces))


# ═════════════════════════════════════════════════════════════════════════════
# TRANSCRIPT EXPORT
# ═════════════════════════════════════════════════════════════════════════════

def _as_word_list(source: Any) -> list[dict]:
    """Accept either a transcribe_words() result or an already-flat list.

    Both `transcribe_words()` output ({"segments": [...]}) and the hit list
    from `find_profanity_v2()` (a plain list of word dicts) are valid
    inputs to the writers below.
    """
    if source is None:
        return []
    if isinstance(source, dict):
        return _flatten_words(source)
    if isinstance(source, (list, tuple)):
        return [w for w in source if isinstance(w, dict)]
    return []


def _srt_timestamp(seconds: float) -> str:
    """SRT wants HH:MM:SS,mmm with a comma before the milliseconds."""
    ms = max(0, int(round(float(seconds) * 1000)))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _short_timestamp(seconds: float) -> str:
    total = max(0, int(float(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return (f"{hours:d}:{minutes:02d}:{secs:02d}" if hours
            else f"{minutes:02d}:{secs:02d}")


def group_into_cues(
    words: Sequence[dict],
    max_words: int = 8,
    max_duration: float = 5.0,
    max_gap: float = 1.2,
) -> list[dict]:
    """Group word timings into readable caption cues.

    A cue is closed when it reaches `max_words`, spans `max_duration`
    seconds, or the silence before the next word exceeds `max_gap`.
    """
    cues: list[dict] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(str(w.get("word", "")).strip() for w in current).strip()
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)
        if text:
            cues.append({
                "start": float(current[0].get("start", 0.0) or 0.0),
                "end": float(current[-1].get("end", 0.0) or 0.0),
                "text": text,
            })
        current.clear()

    for word in words:
        if not str(word.get("word", "")).strip():
            continue
        if current:
            start = float(word.get("start", 0.0) or 0.0)
            span = start - float(current[0].get("start", 0.0) or 0.0)
            gap = start - float(current[-1].get("end", 0.0) or 0.0)
            if len(current) >= max_words or span >= max_duration or gap > max_gap:
                flush()
        current.append(word)
    flush()

    # A zero-length cue is invalid in most players; give it a floor.
    for cue in cues:
        if cue["end"] <= cue["start"]:
            cue["end"] = cue["start"] + 0.5
    return cues


def words_to_srt(
    segments_or_word_list: Any,
    path: str | Path,
    max_words: int = 8,
) -> Path:
    """Write a standard SRT subtitle file. Returns the path written."""
    words = _as_word_list(segments_or_word_list)
    cues = group_into_cues(words, max_words=max_words)

    out = Path(path)
    if out.parent and str(out.parent):
        out.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for index, cue in enumerate(cues, 1):
        lines.append(str(index))
        lines.append(f"{_srt_timestamp(cue['start'])} --> {_srt_timestamp(cue['end'])}")
        lines.append(cue["text"])
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def words_to_txt(
    segments_or_word_list: Any,
    path: str | Path,
    max_words: int = 12,
) -> Path:
    """Write a plain timestamped transcript: "[MM:SS] word word ..."."""
    words = _as_word_list(segments_or_word_list)
    cues = group_into_cues(words, max_words=max_words, max_duration=8.0)

    out = Path(path)
    if out.parent and str(out.parent):
        out.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"[{_short_timestamp(cue['start'])}] {cue['text']}" for cue in cues]
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return out


def bleeps_to_srt(hits: Sequence[dict], path: str | Path) -> Path:
    """A 'bleeps only' SRT: one cue per censored word, with its reason.

    Useful for eyeballing what the detector did without scrubbing the
    whole video.
    """
    out = Path(path)
    if out.parent and str(out.parent):
        out.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for index, hit in enumerate(hits, 1):
        start = float(hit.get("start", 0.0) or 0.0)
        end = float(hit.get("end", 0.0) or 0.0)
        if end <= start:
            end = start + 0.5
        word = str(hit.get("word", "")).strip()
        reason = str(hit.get("reason", "")).strip()
        lines.append(str(index))
        lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        lines.append(f"{word}  [{reason}]" if reason else word)
        lines.append("")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def sidecar_path(video_path: str | Path, suffix: str) -> Path:
    """`/x/y/clip_CLEAN.mp4` + '.srt' -> `/x/y/clip_CLEAN.srt`."""
    p = Path(video_path)
    return p.with_suffix(suffix if suffix.startswith(".") else f".{suffix}")


# ═════════════════════════════════════════════════════════════════════════════
# PATHS
# ═════════════════════════════════════════════════════════════════════════════

def is_generated_output(path: str) -> bool:
    """True for files this tool produced, so batch runs don't re-clean them."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return bool(re.search(re.escape(OUTPUT_SUFFIX) + r"(_\d+)?$", stem))


def build_output_path(
    video_path: str,
    out_dir: str | None,
    avoid_overwrite: bool = True,
) -> str:
    """`<name>_CLEAN.<ext>` in `out_dir` (or alongside the input).

    With `avoid_overwrite`, an existing file gets `_CLEAN_1`, `_CLEAN_2`, ...
    rather than being silently destroyed - which is what v2.2 did, and is
    a real risk in batch mode where the same folder is processed twice.
    """
    base, ext = os.path.splitext(video_path)
    folder = out_dir if out_dir else os.path.dirname(video_path)
    stem = os.path.basename(base) + OUTPUT_SUFFIX
    candidate = os.path.join(folder, stem + ext)
    if not avoid_overwrite:
        return candidate
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(folder, f"{stem}_{counter}{ext}")
        counter += 1
    return candidate


def safe_remove(*paths: str | None) -> None:
    """Best-effort delete. Tolerates missing files and Windows file locks."""
    for p in paths:
        if not p:
            continue
        try:
            os.remove(p)
        except (FileNotFoundError, PermissionError, IsADirectoryError, OSError):
            pass


def new_temp_wav() -> str:
    """A unique temp .wav path, safe for concurrent/batch use."""
    fd, path = tempfile.mkstemp(prefix="autobleep_", suffix=".wav")
    os.close(fd)
    return path


def list_videos(folder: str | Path) -> list[str]:
    """Video files in `folder`, skipping this tool's own `_CLEAN` output."""
    folder = str(folder)
    return [
        os.path.join(folder, name)
        for name in sorted(os.listdir(folder))
        if os.path.splitext(name)[1].lower() in VIDEO_EXTS
        and not is_generated_output(name)
    ]


# ═════════════════════════════════════════════════════════════════════════════
# VIDEO I/O
# ═════════════════════════════════════════════════════════════════════════════

def extract_audio(video_path: str, wav_path: str) -> None:
    """ffmpeg first, moviepy as fallback. Raises with a readable message.

    Lives here rather than in the GUI so the CLI gets the same behaviour
    without importing customtkinter.
    """
    if extract_audio_fast(video_path, wav_path):
        return

    try:
        from moviepy import VideoFileClip
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "ffmpeg failed and moviepy is not installed - install ffmpeg "
            "and put it on PATH, or `pip install moviepy`.") from exc

    clip = None
    try:
        clip = VideoFileClip(video_path)
        if clip.audio is None:
            raise RuntimeError(
                f"{os.path.basename(video_path)} has no audio track - "
                "nothing to bleep.")
        clip.audio.write_audiofile(wav_path, logger=None)
    finally:
        if clip is not None:
            try:
                clip.close()
            except Exception:
                pass


def _mux_stream_copy(video_path: str, audio_path: str, out_path: str) -> bool:
    """Swap the audio track without touching the pictures.

    Censoring only ever changes AUDIO, so re-encoding the video is pure
    loss: it costs hours on a long stream and re-compresses frames that
    did not need touching. Copying the video stream is lossless and
    limited by disk speed rather than the CPU.

    Returns False when ffmpeg is missing or the container refuses the
    source codec, so the caller can fall back to a real encode.
    """
    try:
        completed = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", video_path, "-i", audio_path,
             "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", "-shortest", out_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (FileNotFoundError, OSError):
        return False
    if completed.returncode == 0 and os.path.exists(out_path) \
            and os.path.getsize(out_path) > 0:
        return True
    # A partial file from the failed attempt would look like a success to
    # everything downstream.
    safe_remove(out_path)
    return False


def render_video(video_path: str, audio_path: str, out_path: str,
                 encode_preset: str = "fast", threads: int | None = None,
                 allow_stream_copy: bool = True) -> None:
    """Mux `audio_path` onto `video_path`. Always closes its clips.

    Tries a stream copy first. The moviepy path below is the fallback for
    when ffmpeg is missing or refuses the file - it re-encodes the whole
    video to change the audio, and on a multi-hour 1080p60 stream that is
    the difference between a minute and most of an afternoon.

    It is also where the "N bytes wanted but 0 bytes read ... using the
    last valid frame instead" warnings come from: moviepy trusts the
    frame count ffmpeg reports, which is an estimate, and reads past the
    end of the file when it is too high. Harmless in itself - it repeats
    the final frame - but it buries real output under hundreds of lines.
    """
    if allow_stream_copy and _mux_stream_copy(video_path, audio_path, out_path):
        return

    from moviepy import AudioFileClip, VideoFileClip

    video = audio = final = None
    try:
        with warnings.catch_warnings():
            # See above: an over-estimated frame count, once per frame at
            # the tail of the file. Nothing the user can act on.
            warnings.filterwarnings(
                "ignore", message=".*bytes wanted but 0 bytes read.*")
            video = VideoFileClip(video_path)
            audio = AudioFileClip(audio_path)
            final = video.with_audio(audio)
            final.write_videofile(
                out_path, codec="libx264", audio_codec="aac",
                preset=encode_preset, threads=threads or os.cpu_count() or 4,
                logger=None)
    finally:
        for clip in (final, audio, video):
            if clip is not None:
                try:
                    clip.close()
                except Exception:
                    pass


# ═════════════════════════════════════════════════════════════════════════════
# ONE-FILE PIPELINE (shared by the GUI's batch tab and the CLI)
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ProcessOptions:
    """Everything needed to censor one video, with no UI types involved."""
    model_name: str = "base"
    compute_pref: str = "auto"
    encode_preset: str = "fast"
    method: str = DEFAULT_METHOD
    beep_freq: int = DEFAULT_BEEP_FREQ
    custom_beep_wav: str | None = None
    sensitivity: int = DEFAULT_SENSITIVITY
    custom_words: tuple[str, ...] = field(default=())
    output_dir: str | None = None
    write_video: bool = True
    write_srt: bool = False
    write_txt: bool = False
    # Dead-air removal. OFF unless asked for: the export is what gets
    # published, and a pacing threshold that suits one streamer ruins
    # another's. See autoreel/silence_trim.py for why it needs BOTH a
    # transcript gap and quiet audio before it cuts anything.
    trim_silence: bool = False
    min_silence_s: float = 2.5
    silence_pad_s: float = 0.25


@dataclass
class ProcessResult:
    video_path: str
    hits: list[dict] = field(default_factory=list)
    output_path: str | None = None
    srt_path: str | None = None
    txt_path: str | None = None
    error: str | None = None
    # How much dead air came out, and how many stretches. Zero when the
    # trim was off or found nothing worth cutting.
    trimmed_seconds: float = 0.0
    trimmed_cuts: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


def _apply_silence_trim(result: "ProcessResult", out_path: str,
                        transcript, options: "ProcessOptions",
                        say: Callable[[str], None]) -> None:
    """Remove dead air from a finished export, in place.

    Never fatal. A trim that fails leaves the censored file exactly as it
    was - which is a complete, publishable video - so a pacing feature
    can never cost someone their export.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from autoreel import silence_trim
        from autoreel.audio_energy import measure
    except Exception as exc:
        say(f"Silence trim unavailable ({exc}) - keeping the full length.")
        return

    duration = media_seconds(out_path)
    if duration <= 0:
        say("Could not measure the video - keeping the full length.")
        return

    say("Looking for dead air…")
    cuts = silence_trim.find_dead_air(
        _flatten_words(transcript), duration,
        levels=measure(out_path),
        min_silence_s=options.min_silence_s,
        pad_s=options.silence_pad_s)

    say(silence_trim.describe(cuts, duration))
    if not cuts:
        return

    try:
        silence_trim.apply_trim(out_path, out_path, cuts, duration,
                                preset=options.encode_preset)
    except silence_trim.TrimError as exc:
        say(f"Trim skipped: {exc}")
        return

    result.trimmed_seconds = silence_trim.removed_seconds(cuts)
    result.trimmed_cuts = len(cuts)


def media_seconds(path: str) -> float:
    """Length of a media file in seconds, or 0.0 if it cannot be read."""
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        return float(done.stdout.decode().strip().splitlines()[0])
    except Exception:
        return 0.0


def process_video(
    video_path: str,
    options: ProcessOptions,
    bundle: ModelBundle,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ProcessResult:
    """Transcribe -> detect -> censor -> render, for a single file.

    Never raises: failures land in `ProcessResult.error` so a batch run
    keeps going. Temp files are always cleaned up.
    """
    def say(msg: str) -> None:
        if log is not None:
            log(msg)

    result = ProcessResult(video_path=video_path)
    audio_path = new_temp_wav()
    cleaned_path: str | None = None

    try:
        say("Extracting audio…")
        extract_audio(video_path, audio_path)

        say("Transcribing…")
        transcript = transcribe_words(bundle, audio_path)

        # Sidecars are named after the *output* video when one is being
        # written, so `clip_CLEAN.mp4` sits next to `clip_CLEAN.srt`.
        base_for_sidecars = build_output_path(video_path, options.output_dir) \
            if options.write_video else \
            os.path.join(options.output_dir or os.path.dirname(video_path),
                         os.path.basename(video_path))

        if options.write_txt:
            result.txt_path = str(words_to_txt(
                transcript, sidecar_path(base_for_sidecars, ".txt")))
            say(f"Transcript -> {os.path.basename(result.txt_path)}")
        if options.write_srt:
            result.srt_path = str(words_to_srt(
                transcript, sidecar_path(base_for_sidecars, ".srt")))
            say(f"Captions  -> {os.path.basename(result.srt_path)}")

        hits = find_profanity_v2(transcript, options.custom_words,
                                 sensitivity=options.sensitivity)
        result.hits = hits
        say(f"{len(hits)} word(s) to censor.")

        if not options.write_video:
            return result
        if not hits and not options.trim_silence:
            say("Clean - no video written.")
            return result

        out_path = build_output_path(video_path, options.output_dir)

        if hits:
            if AudioSegment is None:  # pragma: no cover
                raise RuntimeError("pydub is required to censor audio")

            cleaned_path = new_temp_wav()
            censored = apply_bleeps(
                AudioSegment.from_wav(audio_path), hits,
                method=options.method, freq_hz=options.beep_freq,
                custom_wav=options.custom_beep_wav, progress=progress,
                # The whole transcript, not just the hits: padding is clamped
                # to the words either side so the sentence survives.
                all_words=_flatten_words(transcript))
            censored.export(cleaned_path, format="wav")

            say(f"Encoding [{options.encode_preset}]…")
            render_video(video_path, cleaned_path, out_path,
                         options.encode_preset)
        else:
            # Nothing to censor but a trim was asked for. Copy rather
            # than re-encode: the trim below re-encodes anyway, and
            # doing it twice costs a generation of quality for nothing.
            shutil.copy2(video_path, out_path)

        result.output_path = out_path

        if options.trim_silence:
            # Censor FIRST, then trim. The other order would move every
            # word timing the censor pass depends on, and the bleeps
            # would land on the wrong words.
            _apply_silence_trim(result, out_path, transcript, options, say)

        say(f"Saved {os.path.basename(out_path)}")
    except Exception as exc:
        result.error = str(exc) or exc.__class__.__name__
    finally:
        safe_remove(audio_path, cleaned_path)

    return result
