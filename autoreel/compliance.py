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
    # Slurs and hate-speech terms. better_profanity catches the common
    # spellings; these are the variants and plurals it misses, and being
    # their own category is what lets them be treated as high severity.
    # This is a MUTE list for the uploader's own recordings, not a
    # judgement about the speech - YouTube removes videos over these under
    # its hate speech policy regardless of intent or who is speaking.
    "hate_speech": [
        "nigger", "niggers", "nigga", "niggas", "niggah", "nigguh",
        "faggot", "faggots", "fag", "fags", "faggy",
        "tranny", "trannies", "shemale",
        "retard", "retards", "retarded", "tard",
        "kike", "kikes", "spic", "spics", "wetback", "wetbacks",
        "chink", "chinks", "gook", "gooks", "coon", "coons",
        "beaner", "beaners", "raghead", "ragheads", "towelhead",
        "dyke", "dykes", "queer bait", "white trash",
    ],
}

# Categories serious enough that muting the single word isn't enough - the
# sentence around it usually carries the same meaning, so the whole
# segment goes. YouTube acts on context, not just the audible word.
HIGH_SEVERITY_CATEGORIES: frozenset = frozenset({"hate_speech"})


@dataclass
class Violation:
    word: str
    start: float
    end: float
    category: str
    # Bounds of the transcript segment this word sat in, so high-severity
    # hits can be muted sentence-wide without censor_audio needing the
    # transcript passed to it separately.
    segment_start: Optional[float] = None
    segment_end: Optional[float] = None

    @property
    def is_high_severity(self) -> bool:
        return self.category in HIGH_SEVERITY_CATEGORIES


@dataclass
class ComplianceEngine:
    """Flags and censors words that break kid-friendly / YouTube ToS rules."""

    custom_words: tuple[str, ...] = ()
    extra_categories: Optional[dict[str, list[str]]] = None
    use_profanity_filter: bool = True
    # Whisper's word timestamps are approximate - typically 100-300ms out.
    # Muting exactly the reported span leaves the first syllable audible,
    # which for a slur is the whole problem. Pad both edges.
    padding_ms: int = 250
    # Mute the entire segment around a high-severity hit, not just the word.
    mute_whole_segment: bool = True

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
            # High-severity categories are checked FIRST. better_profanity
            # knows most slurs, so consulting it first classified them as
            # plain "profanity" and they never reached this list - which
            # silently disabled whole-segment muting for exactly the words
            # it exists to catch.
            for category in HIGH_SEVERITY_CATEGORIES:
                if any(phrase in candidate for phrase in self.categories.get(category, ())):
                    return category

            if any(custom and custom in candidate for custom in self.custom_words):
                return "custom_word"

            for category, phrases in self.categories.items():
                if category in HIGH_SEVERITY_CATEGORIES:
                    continue
                if any(phrase in candidate for phrase in phrases):
                    return category

            if self.use_profanity_filter and HAS_BETTER_PROFANITY and _profanity.contains_profanity(candidate):
                return "profanity"

        return None

    def scan_words(self, words: Iterable[dict],
                   segment_start: Optional[float] = None,
                   segment_end: Optional[float] = None) -> list[Violation]:
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
                        segment_start=segment_start,
                        segment_end=segment_end,
                    )
                )
        return violations

    def scan_segments(self, segments: Iterable[dict]) -> list[Violation]:
        """Scan Whisper-style segments (each with a 'words' list)."""
        violations: list[Violation] = []
        for segment in segments:
            violations.extend(self.scan_words(
                segment.get("words", []),
                segment_start=segment.get("start"),
                segment_end=segment.get("end"),
            ))
        return violations

    def is_kid_friendly(self, violations: Iterable[Violation]) -> bool:
        return not any(True for _ in violations)

    def mute_spans(self, violations: Iterable[Violation], total_ms: int) -> list:
        """Violations -> merged, clamped (start_ms, end_ms) spans to mute.

        Three things happen here that muting each word in isolation misses:

        1. Padding. Whisper's timings are approximate, so the exact span
           leaves the leading syllable audible - for a slur that defeats
           the point.
        2. High-severity hits expand to their whole segment. Muting one
           word out of the sentence leaves the meaning intact, and YouTube
           acts on context.
        3. Overlaps are merged, so the rebuild below stays correct and the
           track length is preserved exactly.
        """
        spans: list = []
        for violation in violations:
            start = violation.start
            end = violation.end
            if (self.mute_whole_segment and violation.is_high_severity
                    and violation.segment_start is not None
                    and violation.segment_end is not None):
                start = min(start, violation.segment_start)
                end = max(end, violation.segment_end)

            start_ms = int(start * 1000) - self.padding_ms
            end_ms = int(end * 1000) + self.padding_ms
            start_ms = max(0, min(start_ms, total_ms))
            end_ms = max(0, min(end_ms, total_ms))
            if end_ms > start_ms:
                spans.append((start_ms, end_ms))

        spans.sort()
        merged: list = []
        for start_ms, end_ms in spans:
            if merged and start_ms <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end_ms))
            else:
                merged.append((start_ms, end_ms))
        return merged

    def censor_audio(self, audio_segment, violations: Iterable[Violation], method: str = "beep"):
        """Return a copy of `audio_segment` with each violation censored.

        `audio_segment` is a pydub AudioSegment; kept as a duck-typed
        parameter so this module has no hard import-time dependency on
        pydub and stays importable/testable without it installed.

        Built in a single pass. The old version did
        `censored = censored[:s] + replacement + censored[e:]` per
        violation, which is O(n^2) - on a stream with hundreds of flagged
        words that is minutes of pure copying.
        """
        from pydub import AudioSegment
        from pydub.generators import Sine

        total_ms = len(audio_segment)
        spans = self.mute_spans(violations, total_ms)
        if not spans:
            return audio_segment

        beep_tone = Sine(1000).to_audio_segment(duration=100)
        pieces: list = []
        cursor = 0
        for start_ms, end_ms in spans:
            if start_ms > cursor:
                pieces.append(audio_segment[cursor:start_ms])
            duration_ms = end_ms - start_ms
            if method == "beep":
                replacement = (beep_tone * (duration_ms // 100 + 1))[:duration_ms]
            else:
                replacement = AudioSegment.silent(
                    duration=duration_ms, frame_rate=audio_segment.frame_rate)
            pieces.append(replacement.set_channels(audio_segment.channels)
                          .set_frame_rate(audio_segment.frame_rate)
                          .set_sample_width(audio_segment.sample_width))
            cursor = end_ms
        if cursor < total_ms:
            pieces.append(audio_segment[cursor:])

        censored = pieces[0]
        for piece in pieces[1:]:
            censored += piece
        return censored[:total_ms]
