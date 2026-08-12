"""
Highlight scoring and clip selection for AutoReel.

Turns a Whisper-style transcript (segments made of word-level timestamps)
into a ranked list of candidate short-form clips, then greedily selects a
non-overlapping set of them sized for Reels/TikTok.

WHY THIS SCORES WINDOWS AND NOT SEGMENTS
----------------------------------------
The first version scored one transcript segment at a time, took the ten
best, and padded each out to length. On a three-hour stream that produced
ten clips that made no sense, and the reason is arithmetic: "bro" is worth
a point and gets said four hundred times, so the ten winners were ten
unrelated seconds of someone saying "bro", each padded with whatever
happened to sit either side of it.

A clip is not a moment, it is a stretch. So candidates here are WINDOWS -
every run of consecutive segments between min and max length - and a
window is judged on what the whole run contains:

- **Sustained reaction**, not one spike. The window's score is the sum of
  its segments', divided by the square root of its length, so a burst of
  twenty excited seconds beats sixty seconds carrying one "wow".
- **No dead air.** A window whose segments do not cover most of its span
  is mostly silence, and silence is what a viewer swipes past.
- **No internal cliff.** A gap of several seconds mid-window is where the
  moment actually ended; the window is dropped rather than stretched
  across it.
- **Payoff near the end.** The peak segment is placed around two thirds
  through, so the clip plays as build-up then punchline instead of
  opening on the punchline and trailing off into aftermath.
- **Clean edges.** Windows that start and end on a natural pause are
  preferred over ones that cut into the middle of a word.
- **Conversation.** Many short segments in a span means people talking
  over each other, which is the footage worth clipping. One long segment
  means a monologue.
- **No Whisper loops.** A transcript that repeats the same line five
  times is the model hallucinating over music or silence, not a moment.

This module is pure Python (no video/audio libraries) so it can be
exercised with plain dicts in unit tests.
"""

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# Phrases/words that tend to signal an engaging, clip-worthy moment.
# Weighted so stronger reactions score higher than mild ones.
DEFAULT_ENGAGEMENT_KEYWORDS: dict[str, float] = {
    "insane": 3, "unbelievable": 3, "oh my god": 3, "no way": 3,
    "clutch": 3, "let's go": 2.5, "amazing": 2, "incredible": 2,
    "crazy": 2, "wow": 2, "wtf": 2, "what": 1, "huge": 1.5,
    "epic": 2, "unreal": 2.5, "poggers": 2, "gg": 1.5,
    # Laughter
    "hahaha": 2.5, "haha": 2, "lmao": 2.5, "lol": 1.5, "lmfao": 3,
    # Streamer/chat callouts
    "clip that": 3, "clip it": 3, "chat": 1, "clipped": 2,
    # Disbelief / hype
    "no shot": 3, "no freaking way": 3, "what just happened": 3,
    "let's gooo": 3, "actually insane": 3.5, "sheesh": 2, "oh my gosh": 2.5,
    "bro": 1, "dude": 1, "holy": 1.5,
}

# ── Window quality thresholds ────────────────────────────────────────────

# Below this share of the window actually containing speech, the clip is
# mostly dead air however good the one line inside it was.
MIN_SPEECH_RATIO = 0.45

# A silence this long inside a window is where the moment ended.
MAX_INTERNAL_GAP = 3.5

# Where the peak segment should sit in the clip: build-up, then payoff,
# then just enough after it to land. Not 1.0 - a clip that ends on the
# exact frame of the punchline reads as clipped short.
PEAK_POSITION = 0.68
PLACEMENT_WEIGHT = 0.35

# A pause at least this long either side means the window starts and ends
# between sentences rather than through one.
BOUNDARY_PAUSE = 0.35

# A window needs more than a couple of stray "bro"s to be worth cutting.
MIN_WINDOW_SCORE = 3.0

# Whisper loops on music and silence, emitting the same line over and
# over. Below this share of distinct lines, the window is a hallucination.
MIN_DISTINCT_RATIO = 0.5
_LOOP_MIN_SEGMENTS = 3

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_END_PUNCT = ".!?…"

# A title reads best in this range: long enough to say something, short
# enough to survive every platform's truncation.
_HOOK_MIN_CHARS = 18
_HOOK_MAX_CHARS = 85


@dataclass
class Highlight:
    start: float
    end: float
    score: float
    text: str = ""
    # The one line worth putting on the clip as a title. Kept apart from
    # `text` because a caption wants the whole window and a title wants a
    # sentence - the same string cannot be both.
    hook: str = ""


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(_clean(text)) if s.strip()]


@dataclass
class HighlightScorer:
    """Scores transcript segments and selects clip-worthy windows."""

    min_duration: float = 15.0
    max_duration: float = 60.0
    keywords: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_ENGAGEMENT_KEYWORDS))

    # Everything below is a quality gate rather than a taste setting; the
    # defaults are the module constants and exist as fields so a caller
    # with unusual footage can loosen one without editing the module.
    min_speech_ratio: float = MIN_SPEECH_RATIO
    max_internal_gap: float = MAX_INTERNAL_GAP
    peak_position: float = PEAK_POSITION
    boundary_pause: float = BOUNDARY_PAUSE
    min_window_score: float = MIN_WINDOW_SCORE
    # Streams open on a waiting screen and close on goodbyes; neither is
    # a clip. 0 disables, which is what a already-trimmed video wants.
    skip_intro_seconds: float = 0.0
    skip_outro_seconds: float = 0.0
    # dB-per-second for the whole video, from audio_energy.measure().
    # Empty means "no opinion" and every window scores as it did before -
    # the words decide on their own, which is the correct fallback for a
    # file whose audio could not be read.
    energy: list = field(default_factory=list)

    # ── Segment scoring ──────────────────────────────────────────────────

    def score_segment(self, segment: dict) -> float:
        original_text = segment.get("text", "")
        text = original_text.lower()
        score = 0.0

        for phrase, weight in self.keywords.items():
            if phrase in text:
                score += weight

        score += text.count("!") * 0.75
        score += text.count("?") * 0.25

        # ALL-CAPS words often mean Whisper is rendering shouted/emphasized
        # speech; each one is a signal of an excited, clip-worthy moment.
        caps_words = [
            w for w in re.findall(r"[A-Za-z']+", original_text)
            if len(w) >= 3 and w.isupper()
        ]
        score += len(caps_words) * 1.5

        # Elongated words ("noooo", "yesss", "heeey") signal a drawn-out
        # reaction - a single hit is enough of a signal on its own.
        if re.search(r"([a-z])\1{2,}", text):
            score += 1.5

        # Longer bursts of speech in a segment suggest higher energy talk.
        word_count = len(segment.get("words", [])) or len(text.split())
        duration = max(0.001, segment.get("end", 0) - segment.get("start", 0))
        words_per_second = word_count / duration
        if words_per_second > 2.5:
            score += 1.0

        return score

    def score_segments(self, segments: Iterable[dict]) -> list[Highlight]:
        return [
            Highlight(
                start=segment["start"],
                end=segment["end"],
                score=self.score_segment(segment),
                text=_clean(segment.get("text", "")),
            )
            for segment in segments
        ]

    # ── Titles ───────────────────────────────────────────────────────────

    def _line_score(self, line: str) -> float:
        """How well one sentence would work as a clip title."""
        score = self.score_segment({"text": line, "start": 0, "end": 3})
        length = len(line)
        if _HOOK_MIN_CHARS <= length <= _HOOK_MAX_CHARS:
            score += 2.0
        elif length < _HOOK_MIN_CHARS:
            # Two words is not a title, however excited those words were.
            score -= 2.0
        else:
            score -= 1.0
        if line[-1:] in _END_PUNCT:
            # A complete thought, rather than the middle of one.
            score += 1.0
        if len(line.split()) >= 4:
            score += 0.5
        return score

    def best_line(self, candidates: Iterable[str]) -> str:
        """The sentence among these that reads best as a title."""
        lines: list[str] = []
        for candidate in candidates:
            lines.extend(_sentences(candidate))
        if not lines:
            return ""
        return max(lines, key=self._line_score)

    # ── Window evaluation ────────────────────────────────────────────────

    def _evaluate(self, ordered: list, scores: list,
                  first: int, last: int) -> Optional[Highlight]:
        """Score one run of consecutive segments, or None if unusable."""
        segments = ordered[first:last + 1]
        start = float(segments[0]["start"])
        end = float(segments[-1]["end"])
        duration = end - start
        if duration <= 0:
            return None

        base = sum(scores[first:last + 1])
        if base <= 0 or base < self.min_window_score:
            return None

        # A silence mid-window is the end of the moment, not part of it.
        for earlier, later in zip(segments, segments[1:]):
            if float(later["start"]) - float(earlier["end"]) > self.max_internal_gap:
                return None

        spoken = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in segments)
        speech_ratio = min(1.0, spoken / duration)
        if speech_ratio < self.min_speech_ratio:
            return None

        texts = [_clean(s.get("text", "")).lower() for s in segments]
        said = [t for t in texts if t]
        distinct = len(set(said))
        if len(said) >= 2 and distinct == 1:
            # The same line twice in a row is already the model looping.
            return None
        if len(said) >= _LOOP_MIN_SEGMENTS and \
                distinct / len(said) < MIN_DISTINCT_RATIO:
            # Whisper repeating itself over music or silence.
            return None

        # Payoff placement: where the strongest segment sits in the clip.
        peak = max(range(first, last + 1), key=lambda k: scores[k])
        peak_mid = (float(ordered[peak]["start"]) + float(ordered[peak]["end"])) / 2
        position = (peak_mid - start) / duration
        placement = 1.0 - PLACEMENT_WEIGHT * abs(position - self.peak_position)

        # Clean edges: a pause before and after means the cut lands
        # between sentences rather than through one.
        lead = (start - float(ordered[first - 1]["end"])
                if first > 0 else self.boundary_pause)
        tail = (float(ordered[last + 1]["start"]) - end
                if last + 1 < len(ordered) else self.boundary_pause)
        boundary = 1.0
        boundary += 0.08 if lead >= self.boundary_pause else -0.05
        boundary += 0.08 if tail >= self.boundary_pause else -0.05

        # Back-and-forth: several short segments in a span is two people
        # talking; one long segment is somebody narrating.
        turns = len(segments) / duration
        conversation = 1.0 + min(0.25, turns * 0.5)

        words = sum(len(s.get("words") or []) or len(_clean(s.get("text", "")).split())
                    for s in segments)
        words_per_second = words / duration
        density = 1.0 + min(0.2, max(0.0, (words_per_second - 2.0) * 0.1))

        # How loud the room got. Applied LAST and capped small: it can
        # promote a moment the words already liked above another the
        # words also liked, and it can never carry a window on its own.
        # The other ordering would clip every gunshot.
        loud = 1.0
        if self.energy:
            from .audio_energy import energy_bonus

            loud = energy_bonus(self.energy, start, end)

        # Divided by the root of the length so a long window has to earn
        # its extra seconds instead of winning by accumulating them.
        intensity = base / (duration ** 0.5)
        score = (intensity * placement * boundary * conversation * density
                 * (0.6 + 0.4 * speech_ratio) * loud)

        text = _clean(" ".join(t for t in (s.get("text", "") for s in segments) if t))
        hook = self.best_line([ordered[peak].get("text", "")]) \
            or self.best_line(t for t in (s.get("text", "") for s in segments))
        return Highlight(start=start, end=end, score=score, text=text, hook=hook)

    def _sweep(self, ordered: list, scores: list, floor: float,
               ceiling: float) -> list[Highlight]:
        total = len(ordered)
        found: list[Highlight] = []
        for first in range(total):
            if float(ordered[first]["start"]) < floor:
                continue
            for last in range(first, total):
                if float(ordered[last]["end"]) > ceiling:
                    break
                duration = float(ordered[last]["end"]) - float(ordered[first]["start"])
                if duration > self.max_duration:
                    break
                if duration < self.min_duration:
                    continue   # still growing
                highlight = self._evaluate(ordered, scores, first, last)
                if highlight is not None:
                    found.append(highlight)
        return found

    def candidate_windows(self, segments: Iterable[dict]) -> list[Highlight]:
        """Every usable window, best first.

        A window is any run of consecutive segments between min_duration
        and max_duration. Under-length windows are considered in exactly
        one case: the whole transcript is shorter than min_duration, where
        the only honest candidate is all of it. Allowing them generally
        would hand every clip to a four-second burst, because score is
        divided by the root of the length - the burst would always beat
        the same burst with the build-up that makes it make sense.
        """
        ordered = sorted((s for s in segments), key=lambda s: float(s["start"]))
        if not ordered:
            return []

        last_end = float(ordered[-1]["end"])
        floor = self.skip_intro_seconds
        ceiling = last_end - self.skip_outro_seconds if self.skip_outro_seconds else last_end
        if ceiling <= floor:
            # The trims overlap on a short video; honour the video.
            floor, ceiling = 0.0, last_end

        scores = [self.score_segment(s) for s in ordered]
        found = self._sweep(ordered, scores, floor, ceiling)
        if not found and (last_end - float(ordered[0]["start"])) < self.min_duration:
            whole = self._evaluate(ordered, scores, 0, len(ordered) - 1)
            found = [whole] if whole is not None else []

        found.sort(key=lambda h: h.score, reverse=True)
        return found

    # ── Selection ────────────────────────────────────────────────────────

    def _take(self, candidates: list, count: int,
              min_gap: float) -> list[Highlight]:
        selected: list[Highlight] = []
        for candidate in candidates:
            overlaps = any(
                candidate.start < existing.end + min_gap
                and candidate.end > existing.start - min_gap
                for existing in selected
            )
            if overlaps:
                continue
            selected.append(candidate)
            if len(selected) >= count:
                break
        return selected

    @staticmethod
    def _gap_ladder(min_gap: float) -> list[float]:
        """The gaps to try, widest first. Never wider than asked for."""
        ladder = [min_gap, min_gap / 2, min_gap / 4, 0.0]
        return [gap for i, gap in enumerate(ladder)
                if gap <= min_gap and gap not in ladder[:i]]

    def select_clips(
        self,
        segments: Iterable[dict],
        count: int = 3,
        min_gap: float = 5.0,
    ) -> list[Highlight]:
        """Pick the top non-overlapping windows, in timeline order.

        Windows are ranked, then taken greedily as long as each is at
        least `min_gap` clear of everything already chosen. `min_gap` is
        what stops ten clips coming out of the same two minutes: one good
        moment produces dozens of overlapping high-scoring windows, and
        without a gap the answer would be all of them.

        If that leaves fewer clips than asked for, the gap is relaxed and
        tried again. A stream whose best moments happen to fall close
        together should still produce a full set - spreading them out is
        a preference, and coming back with one clip to honour it is not
        what anybody wanted.
        """
        candidates = self.candidate_windows(segments)
        selected: list[Highlight] = []
        for gap in self._gap_ladder(min_gap):
            selected = self._take(candidates, count, gap)
            if len(selected) >= count:
                break

        selected.sort(key=lambda h: h.start)
        return selected
