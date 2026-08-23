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

from .profanity_extra import contains_extra

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


# Endings a flagged word genuinely takes. "fuck" has to catch "fucking";
# "meth" must NOT catch "something".
_INFLECTIONS = r"(?:s|es|ed|ing|er|ers|y|ies|in|a|az|z)?"


def _matcher(phrases):
    """One regex for a keyword list, matched on WORD BOUNDARIES.

    This was a plain `phrase in word` substring test, and the classic
    result: "meth" is inside "so-meth-ing", so the word "something" was
    flagged as a drug reference. Every caption rendered it "s********"
    and the audio pass bleeped it - on a channel where somebody says
    "something" every few sentences.

    A boundary alone is not enough either, because the list has to catch
    inflections: "fuck" must still flag "fucking". So it is a boundary
    plus the endings a word actually takes, which "something" and
    "methodical" do not have.
    """
    words = sorted({str(p).strip().lower() for p in (phrases or ()) if str(p).strip()},
                   key=len, reverse=True)
    if not words:
        return None
    joined = "|".join(re.escape(word) for word in words)
    return re.compile(rf"\b(?:{joined}){_INFLECTIONS}\b")

# How far a mute may cross into the word next to it. Whisper's word
# timings are 100-300ms out, so a pad that stops dead at the neighbour's
# reported boundary can still leave the flagged syllable audible. This is
# the margin that absorbs that error - large enough to cover the timing
# slop, small enough that a neighbouring word stays intelligible.
NEIGHBOUR_BLEED_MS = 120

# No single mute may run longer than this.
#
# The point of the censor is to take the word and hand the audience the
# next line immediately. A mute long enough to notice is a mute long
# enough to scroll past - on a sixty-second clip, a second of dead air is
# a sixtieth of the whole thing spent on nothing.
#
# This is a backstop, not the normal path: word-level mutes come out
# around 400-900ms. It exists because a bad word timestamp or a merged
# run of flagged words can produce a span far longer than any word, and
# silencing eight seconds of a clip is worse than the word surviving.
#
# It does NOT apply to a mute_whole_segment expansion. That is somebody
# explicitly asking for the sentence, for a platform that acts on context
# rather than on the audible word - a decision, not a runaway - and
# capping it would quietly turn the feature off instead of bounding it.
MAX_MUTE_MS = 2_000


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
    # Where the words either side of this one begin and end. Padding is
    # clamped against these so a mute cannot swallow the speech around it -
    # without them the only choice is a fixed pad that is either too small
    # to cover Whisper's timing error or big enough to eat a neighbour.
    prev_end: Optional[float] = None
    next_start: Optional[float] = None

    @property
    def is_high_severity(self) -> bool:
        return self.category in HIGH_SEVERITY_CATEGORIES


@dataclass
class ComplianceEngine:
    """Flags and censors words that break kid-friendly / YouTube ToS rules."""

    custom_words: tuple[str, ...] = ()
    extra_categories: Optional[dict[str, list[str]]] = None
    # Only flag words in these categories. Empty = all of them, which is
    # the default and what the YouTube-facing pass wants. See __post_init__.
    only_categories: tuple[str, ...] = ()
    use_profanity_filter: bool = True
    # Whisper's word timestamps are approximate - typically 100-300ms out.
    # Muting exactly the reported span leaves the first syllable audible,
    # which for a slur is the whole problem. Pad both edges.
    padding_ms: int = 250
    # Mute the entire segment around a high-severity hit, not just the
    # word. Off: the point of a word-level censor is that the sentence
    # survives. Turn it on only if a platform is acting on context rather
    # than on the audible word.
    mute_whole_segment: bool = False

    def __post_init__(self) -> None:
        self.categories: dict[str, list[str]] = {
            category: list(phrases) for category, phrases in DEFAULT_CATEGORIES.items()
        }
        # Restricting to the categories that get a POST REMOVED rather
        # than demonetised. Instagram removed a clip under hateful
        # conduct while its ordinary swearing broke nothing - those are
        # different rules and they need different answers. Bleeping every
        # swear for Instagram would flatten the channel's voice for no
        # gain; leaving a slur in gets the account taken away.
        #
        # better_profanity is switched off with it: it knows most slurs,
        # but it also knows every ordinary swear, so consulting it would
        # undo the restriction.
        if self.only_categories:
            wanted = set(self.only_categories)
            self.categories = {name: words
                               for name, words in self.categories.items()
                               if name in wanted}
            self.use_profanity_filter = False
        if self.extra_categories:
            for category, phrases in self.extra_categories.items():
                self.categories.setdefault(category, [])
                self.categories[category].extend(phrases)
        self.custom_words = tuple(w.strip().lower() for w in self.custom_words if w.strip())
        self._patterns = {name: _matcher(words)
                          for name, words in self.categories.items()}
        self._custom = _matcher(self.custom_words)

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
                pattern = self._patterns.get(category)
                if pattern and pattern.search(candidate):
                    return category

            if self._custom and self._custom.search(candidate):
                return "custom_word"

            for category, pattern in self._patterns.items():
                if category in HIGH_SEVERITY_CATEGORIES:
                    continue
                if pattern and pattern.search(candidate):
                    return category

            if self.use_profanity_filter and (
                    (HAS_BETTER_PROFANITY and _profanity.contains_profanity(candidate))
                    # Compound insults - "asshat", "dickweed",
                    # "shitforbrains" - that the base list does not carry.
                    # See profanity_extra for what was left out and why.
                    or contains_extra(candidate)):
                return "profanity"

        return None

    def scan_words(self, words: Iterable[dict],
                   segment_start: Optional[float] = None,
                   segment_end: Optional[float] = None) -> list[Violation]:
        """Scan word-level transcript entries (dicts with word/start/end)."""
        # Materialised because each word needs to know about its
        # neighbours, and `words` is often a generator.
        words = list(words)
        violations: list[Violation] = []
        for index, word_info in enumerate(words):
            reason = self._flag_reason(word_info["word"])
            if not reason:
                continue
            previous = words[index - 1] if index > 0 else None
            following = words[index + 1] if index + 1 < len(words) else None
            violations.append(
                Violation(
                    word=word_info["word"],
                    start=word_info["start"],
                    end=word_info["end"],
                    category=reason,
                    segment_start=segment_start,
                    segment_end=segment_end,
                    prev_end=previous.get("end") if previous else None,
                    next_start=following.get("start") if following else None,
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

        The job is to remove the flagged WORD and nothing else. Two things
        pull against each other there, and both are real:

        - Pad too little and the leading syllable survives, because
          Whisper's word timings are 100-300ms out. A slur whose first
          syllable is audible is not censored.
        - Pad too much and the words either side get clipped, which is
          what makes a censored video sound chopped up.

        A fixed pad has to pick one. This does not: it pads generously
        into the SILENCE around the word, and stops at the neighbouring
        word. Muting a gap between words costs nothing, so where there is
        a gap the full padding is used; where the speech is packed tight
        the pad shrinks to fit rather than eating the neighbour.

        NEIGHBOUR_BLEED_MS is the one deliberate exception: the pad may
        cross into a neighbour by that much, because the timing error it
        exists to cover is roughly that size and refusing to cross at all
        would leave the syllable audible in exactly the tight-speech case
        that needs it most.

        Overlaps are merged afterwards, so the rebuild stays correct and
        the track length is preserved exactly.
        """
        # (start_ms, end_ms, bounded) - bounded is False for a span the
        # caller explicitly asked to cover a whole sentence.
        spans: list = []
        for violation in violations:
            start = violation.start
            end = violation.end
            whole_segment = (self.mute_whole_segment and violation.is_high_severity
                             and violation.segment_start is not None
                             and violation.segment_end is not None)
            if whole_segment:
                start = min(start, violation.segment_start)
                end = max(end, violation.segment_end)

            start_ms = int(start * 1000) - self.padding_ms
            end_ms = int(end * 1000) + self.padding_ms

            if not whole_segment:
                # Expanding to the segment is already a decision to take
                # the surrounding speech, so the neighbour clamp only
                # applies to word-level mutes.
                if violation.prev_end is not None:
                    floor = int(violation.prev_end * 1000) - NEIGHBOUR_BLEED_MS
                    start_ms = max(start_ms, min(floor, int(start * 1000)))
                if violation.next_start is not None:
                    ceiling = int(violation.next_start * 1000) + NEIGHBOUR_BLEED_MS
                    end_ms = min(end_ms, max(ceiling, int(end * 1000)))

            start_ms = max(0, min(start_ms, total_ms))
            end_ms = max(0, min(end_ms, total_ms))
            if end_ms > start_ms:
                spans.append((start_ms, end_ms, not whole_segment))

        spans.sort()
        merged: list = []
        for start_ms, end_ms, bounded in spans:
            if merged and start_ms <= merged[-1][1]:
                # A deliberate whole-sentence mute overlapping a word-level
                # one keeps its exemption: the sentence was asked for.
                merged[-1] = (merged[-1][0], max(merged[-1][1], end_ms),
                              merged[-1][2] and bounded)
            else:
                merged.append((start_ms, end_ms, bounded))

        # Trimmed from the END, so the word's own start - which is what
        # the timing is anchored to - keeps its padding, and what gets
        # given back is the tail that was running into the next line.
        out = []
        for start_ms, end_ms, bounded in merged:
            if bounded and end_ms - start_ms > MAX_MUTE_MS:
                end_ms = start_ms + MAX_MUTE_MS
            out.append((start_ms, end_ms))
        return out

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
