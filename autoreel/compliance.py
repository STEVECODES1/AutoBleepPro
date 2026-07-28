"""
Compliance engine for AutoReel.

Scans word-level transcripts for content that would put a video at odds
with YouTube's Terms of Service or "Made for Kids" / kid-friendly content
standards, and censors the matching audio spans (beep or silence).

Detection is split into categories so a supervisor report can explain
*why* a word was flagged, not just that it was. Profanity detection is
delegated to `better_profanity` when it's installed; the sensitive-topic
categories below are simple, dependency-free keyword lists so the engine
degrades gracefully (and stays fully testable) without that package.
"""

import re
from dataclasses import dataclass
from typing import Iterable, Optional

try:
    from better_profanity import profanity as _profanity

    _profanity.load_censor_words()
    HAS_BETTER_PROFANITY = True
except ImportError:  # pragma: no cover - exercised in envs without the dep
    HAS_BETTER_PROFANITY = False

# Non-explicit keyword phrases for kid-unfriendly topics beyond raw
# profanity. Intentionally clinical/plain terms (drug names, phrasing
# around violence or self-harm) rather than slurs, so the list itself is
# safe to ship and edit.
DEFAULT_CATEGORIES: dict[str, list[str]] = {
    "violence": [
        "kill you", "stab you", "shoot you", "beat you up", "murder",
    ],
    "drugs": [
        "cocaine", "heroin", "meth", "fentanyl", "smoke crack",
    ],
    "self_harm": [
        "kill myself", "self harm", "self-harm", "suicide",
    ],
    "sexual_content": [
        "nsfw", "explicit content", "onlyfans",
    ],
}


@dataclass
class Violation:
    word: str
    start: float
    end: float
    category: str


@dataclass
class ComplianceEngine:
    """Flags and censors words that break kid-friendly / YouTube ToS rules."""

    custom_words: tuple[str, ...] = ()
    extra_categories: Optional[dict[str, list[str]]] = None
    use_profanity_filter: bool = True

    def __post_init__(self) -> None:
        self.categories: dict[str, list[str]] = {
            category: list(phrases) for category, phrases in DEFAULT_CATEGORIES.items()
        }
        if self.extra_categories:
            for category, phrases in self.extra_categories.items():
                self.categories.setdefault(category, [])
                self.categories[category].extend(phrases)
        self.custom_words = tuple(w.strip().lower() for w in self.custom_words if w.strip())

    def _flag_reason(self, word: str) -> Optional[str]:
        normalized = word.strip().lower().strip(".,!?;:\"'")
        if not normalized:
            return None

        # Whisper attaches contractions/possessives directly to tokens (e.g.
        # "word's"), which stops exact-match profanity checks from firing on
        # the base word. Check both forms.
        core = re.sub(r"'(s|ll|d|m|re|ve|t)$", "", normalized)

        for candidate in (normalized, core):
            if self.use_profanity_filter and HAS_BETTER_PROFANITY and _profanity.contains_profanity(candidate):
                return "profanity"

            if any(custom and custom in candidate for custom in self.custom_words):
                return "custom_word"

            for category, phrases in self.categories.items():
                if any(phrase in candidate for phrase in phrases):
                    return category

        return None

    def scan_words(self, words: Iterable[dict]) -> list[Violation]:
        """Scan word-level transcript entries (dicts with word/start/end)."""
        violations: list[Violation] = []
        for word_info in words:
            reason = self._flag_reason(word_info["word"])
            if reason:
                violations.append(
                    Violation(
                        word=word_info["word"],
                        start=word_info["start"],
                        end=word_info["end"],
                        category=reason,
                    )
                )
        return violations

    def scan_segments(self, segments: Iterable[dict]) -> list[Violation]:
        """Scan Whisper-style segments (each with a 'words' list)."""
        violations: list[Violation] = []
        for segment in segments:
            violations.extend(self.scan_words(segment.get("words", [])))
        return violations

    def is_kid_friendly(self, violations: Iterable[Violation]) -> bool:
        return not any(True for _ in violations)

    def censor_audio(self, audio_segment, violations: Iterable[Violation], method: str = "beep"):
        """Return a copy of `audio_segment` with each violation span censored.

        `audio_segment` is a pydub AudioSegment; kept as a duck-typed
        parameter so this module has no hard import-time dependency on
        pydub and stays importable/testable without it installed.
        """
        from pydub.generators import Sine

        beep_tone = Sine(1000).to_audio_segment(duration=100)

        censored = audio_segment
        for violation in sorted(violations, key=lambda v: v.start):
            start_ms = int(violation.start * 1000)
            end_ms = int(violation.end * 1000)
            duration_ms = max(0, end_ms - start_ms)
            if duration_ms == 0:
                continue

            if method == "beep":
                replacement = (beep_tone * (duration_ms // 100 + 1))[:duration_ms]
            else:
                from pydub import AudioSegment

                replacement = AudioSegment.silent(duration=duration_ms)

            censored = censored[:start_ms] + replacement + censored[end_ms:]

        return censored
