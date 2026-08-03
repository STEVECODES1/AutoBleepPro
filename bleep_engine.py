"""
bleep_engine - pure logic for AutoBleep Pro.

No GUI, no threading, no global mutable state. Everything here is
importable and testable without customtkinter, torch, whisper, moviepy,
ffmpeg or a GPU: the heavy dependencies are imported lazily/optionally and
the detection half needs nothing but `better_profanity`.

Public API
----------
Detection : find_profanity_v2, check_word
Device    : detect_device, configure_threads
Models    : load_model_speed, ModelCache, ModelBundle, transcribe_words
Audio     : extract_audio_fast, make_beep, apply_bleeps
Paths     : build_output_path, is_generated_output, safe_remove
"""

from __future__ import annotations

import gc
import itertools
import os
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Iterable, Sequence

from better_profanity import profanity

profanity.load_censor_words()

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

OUTPUT_SUFFIX = "_CLEAN"


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
    return bool(candidate) and profanity.contains_profanity(candidate)


def check_word(
    raw_word: str,
    context_words: Sequence[str],
    custom_words: Sequence[str],
    fuzzy: bool = True,
) -> tuple[bool, str]:
    """Decide whether a single transcribed token should be bleeped.

    `context_words` are the preceding tokens (already lowercased, letters
    only). `fuzzy` enables the minced-oath / mishear heuristics; turn it
    off to flag only real profanity, symbol bypasses and custom words.

    Returns (should_bleep, human-readable reason).
    """
    raw_word = (raw_word or "").strip()
    if not raw_word:
        return False, ""

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

    ctx_str = " ".join(context_words)

    if fuzzy and clean_word in HOMOPHONES:
        if clean_word not in CONTEXT_ONLY:
            return True, f"Likely profanity substitute ('{clean_word}')"
        # Context-only: require a trigger phrase that actually points at
        # the same underlying word ("son of a" -> bitch, for "beach").
        targets = HOMOPHONES[clean_word]
        for trigger, target in CONTEXT_TRIGGERS:
            if target in targets and trigger in ctx_str:
                return True, f"Context homophone ('{clean_word}')"

    if fuzzy and clean_word in WHISPER_MISHEARDS:
        if clean_word not in MISHEARD_CONTEXT_ONLY:
            return True, f"Whisper mishear of '{WHISPER_MISHEARDS[clean_word]}'"
        if any(_profane(cw) or cw in HOMOPHONES for cw in context_words):
            return True, f"Whisper mishear (context) of '{WHISPER_MISHEARDS[clean_word]}'"

    recent = " ".join(context_words[-4:])
    for trigger, target in CONTEXT_TRIGGERS:
        if clean_word == target and trigger in recent:
            return True, f"Context trigger ('{trigger} {target}')"

    # Custom words. Multi-word entries ("rival brand") can never match a
    # single token, so they're tested against the trailing context plus
    # this word - which is what the GUI's own placeholder text promises.
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
) -> list[dict]:
    """Scan a transcription result and return the words to bleep.

    Each hit is {"word", "start", "end", "reason"}. Hits are deduplicated
    by start timestamp (rounded to the millisecond) so a token that trips
    two different rules is only bleeped once.
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

        is_bad, reason = check_word(raw, context, custom_words, fuzzy=fuzzy)
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


def transcribe_words(bundle: ModelBundle, audio_path: str) -> dict:
    """Transcribe with word timestamps, normalised to whisper's dict shape."""
    if bundle.backend == "openai-whisper":
        return bundle.model.transcribe(audio_path, word_timestamps=True)

    result = bundle.model.transcribe(audio_path, word_timestamps=True)
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


def _match_params(seg, ref):
    """Coerce `seg` to `ref`'s frame rate / channels / sample width."""
    if seg.frame_rate != ref.frame_rate:
        seg = seg.set_frame_rate(ref.frame_rate)
    if seg.channels != ref.channels:
        seg = seg.set_channels(ref.channels)
    if seg.sample_width != ref.sample_width:
        seg = seg.set_sample_width(ref.sample_width)
    return seg


def merge_spans(
    words: Iterable[dict],
    total_ms: int,
    min_ms: int = 50,
) -> list[tuple[int, int]]:
    """Word timings -> sorted, clamped, non-overlapping (start, end) ms spans.

    Spans shorter than `min_ms` are widened *around their centre* rather
    than by pushing the end out, and overlaps are merged. Both matter:
    without merging, two overlapping spans produce duplicated audio when
    the track is rebuilt.
    """
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
    method: str = "beep",
    freq_hz: int = 1000,
    min_ms: int = 50,
    progress: Callable[[int, int], None] | None = None,
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
    spans = merge_spans(words, total_ms, min_ms)
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
        if method == "silence":
            filler = AudioSegment.silent(duration=span_ms, frame_rate=frame_rate)
        else:
            filler = make_beep(span_ms, freq_hz)
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
