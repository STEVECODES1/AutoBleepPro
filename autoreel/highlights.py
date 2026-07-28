"""
Highlight scoring and clip selection for AutoReel.

Turns a Whisper-style transcript (segments made of word-level timestamps)
into a ranked list of candidate short-form clips, then greedily selects a
non-overlapping set of them sized for Reels/TikTok.

This module is pure Python (no video/audio libraries) so it can be
exercised with plain dicts in unit tests.
"""

from dataclasses import dataclass, field
from typing import Iterable, Optional

# Phrases/words that tend to signal an engaging, clip-worthy moment.
# Weighted so stronger reactions score higher than mild ones.
DEFAULT_ENGAGEMENT_KEYWORDS: dict[str, float] = {
    "insane": 3, "unbelievable": 3, "oh my god": 3, "no way": 3,
    "clutch": 3, "let's go": 2.5, "amazing": 2, "incredible": 2,
    "crazy": 2, "wow": 2, "wtf": 2, "what": 1, "huge": 1.5,
    "epic": 2, "unreal": 2.5, "poggers": 2, "gg": 1.5,
}


@dataclass
class Highlight:
    start: float
    end: float
    score: float
    text: str = ""


@dataclass
class HighlightScorer:
    """Scores transcript segments and selects clip-worthy windows."""

    min_duration: float = 15.0
    max_duration: float = 60.0
    keywords: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_ENGAGEMENT_KEYWORDS))

    def score_segment(self, segment: dict) -> float:
        text = segment.get("text", "").lower()
        score = 0.0

        for phrase, weight in self.keywords.items():
            if phrase in text:
                score += weight

        score += text.count("!") * 0.75
        score += text.count("?") * 0.25

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
                text=segment.get("text", "").strip(),
            )
            for segment in segments
        ]

    def _expand_to_min_duration(self, highlight: Highlight, segments: list[dict]) -> Highlight:
        """Pad a short highlight out to `min_duration` using neighboring
        segments as context, without exceeding `max_duration`."""
        start, end = highlight.start, highlight.end
        if end - start >= self.min_duration:
            return highlight

        ordered = sorted(segments, key=lambda s: s["start"])
        idx = next((i for i, s in enumerate(ordered) if s["start"] == highlight.start), None)

        before, after = idx, idx
        while (end - start) < self.min_duration and (before is not None or after is not None):
            grew = False
            if after is not None and after + 1 < len(ordered):
                candidate_end = ordered[after + 1]["end"]
                if candidate_end - start <= self.max_duration:
                    after += 1
                    end = candidate_end
                    grew = True
                else:
                    after = None
            if (end - start) >= self.min_duration:
                break
            if before is not None and before - 1 >= 0:
                candidate_start = ordered[before - 1]["start"]
                if end - candidate_start <= self.max_duration:
                    before -= 1
                    start = candidate_start
                    grew = True
                else:
                    before = None
            if not grew:
                break

        return Highlight(start=start, end=end, score=highlight.score, text=highlight.text)

    def select_clips(
        self,
        segments: Iterable[dict],
        count: int = 3,
        min_gap: float = 5.0,
    ) -> list[Highlight]:
        """Pick the top non-overlapping highlight windows.

        Segments are scored, sorted best-first, then greedily accepted as
        long as they don't overlap (within `min_gap`) an already-selected
        clip. Accepted clips are padded up to `min_duration`.
        """
        segments = list(segments)
        scored = self.score_segments(segments)
        scored.sort(key=lambda h: h.score, reverse=True)

        selected: list[Highlight] = []
        for candidate in scored:
            if candidate.score <= 0:
                continue

            expanded = self._expand_to_min_duration(candidate, segments)

            overlaps = any(
                expanded.start < existing.end + min_gap and expanded.end > existing.start - min_gap
                for existing in selected
            )
            if overlaps:
                continue

            selected.append(expanded)
            if len(selected) >= count:
                break

        selected.sort(key=lambda h: h.start)
        return selected
