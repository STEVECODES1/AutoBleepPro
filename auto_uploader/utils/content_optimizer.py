"""
Turns the transcript the censor pass already produced into an SEO helper
report: title ideas, YouTube chapter markers, high-energy timestamps for
thumbnails, and ~30s clip windows worth turning into Shorts.

Reuses AutoReel's HighlightScorer (same repo) for "is this moment
exciting" scoring instead of reinventing it; degrades to a simple
exclamation-count heuristic if that import ever breaks. Reads the
transcript cache written by utils/censor.py - by default it will NOT
re-run Whisper just for a report (transcription is minutes per stream);
set features.content_optimizer.transcribe_if_missing to change that.

Pure logic (chapters/titles/moments) is dependency-free and unit-tested;
only report I/O touches the filesystem.
"""

import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _score_segment(segment: dict) -> float:
    try:
        from autoreel.highlights import HighlightScorer
        return HighlightScorer().score_segment(segment)
    except Exception:
        text = segment.get("text", "")
        return text.count("!") * 1.0 + text.count("?") * 0.25


def _fmt_ts(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return str(timedelta(seconds=seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def build_chapters(segments: list, interval_s: float = 600) -> list:
    """(timestamp, label) chapter markers roughly every `interval_s`,
    snapped to segment starts. First chapter is always 0:00 (YouTube
    requires it for chapters to activate)."""
    chapters = []
    next_mark = 0.0
    for segment in sorted(segments, key=lambda s: s.get("start", 0)):
        start = float(segment.get("start", 0))
        if start >= next_mark:
            label = " ".join((segment.get("text") or "").split()[:6]) or "Chapter"
            chapters.append((0.0 if not chapters else start, label))
            next_mark = (0.0 if not chapters[:-1] and start > 0 else start) + interval_s
    if chapters and chapters[0][0] != 0.0:
        chapters[0] = (0.0, chapters[0][1])
    return chapters


def top_moments(segments: list, count: int = 3) -> list:
    """Highest-energy segments, each as (start, score, text), best first."""
    scored = [
        (float(s.get("start", 0)), _score_segment(s), (s.get("text") or "").strip())
        for s in segments
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    return [m for m in scored[:count] if m[1] > 0]


def clip_windows(segments: list, count: int = 3, length_s: float = 30) -> list:
    """~30s (start, end) windows around the top moments, for Shorts."""
    windows = []
    for start, _score, _text in top_moments(segments, count):
        clip_start = max(0.0, start - 5)
        windows.append((clip_start, clip_start + length_s))
    return windows


def suggest_titles(stream_title: str, date_str: str, segments: list) -> list:
    """A few alternate title ideas seeded from the strongest moments."""
    suggestions = [f'"{stream_title}" {date_str} Stackswopo Stream']
    for _start, _score, text in top_moments(segments, 2):
        hook = " ".join(text.split()[:8]).strip(" .,!?\"'")
        if hook:
            title = f'{hook}... | "{stream_title}" {date_str}'
            suggestions.append(title[:100])
    return suggestions


def generate_report(video_basename: str, transcript_path: str, stream_title: str,
                    date_str: str, out_dir: str, features: dict = None) -> str:
    """Write `{basename}_optimize.md` next to the uploaded file. Returns
    the report path, or "" when no transcript is available."""
    features = features or {}
    if not os.path.exists(transcript_path):
        if not features.get("transcribe_if_missing", False):
            return ""
        # Deliberately unimplemented auto-transcribe path: running Whisper
        # twice per video just for a report is the kind of silent
        # multi-minute cost this pipeline works hard to avoid.
        return ""

    with open(transcript_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    chapters = build_chapters(segments)
    moments = top_moments(segments)
    windows = clip_windows(segments)
    titles = suggest_titles(stream_title, date_str, segments)

    lines = [f"# Content optimizer report - {video_basename}", ""]
    lines += ["## Title ideas", ""] + [f"- {t}" for t in titles]
    lines += ["", "## YouTube chapters (paste into the description)", ""]
    lines += [f"{_fmt_ts(t)} {label}" for t, label in chapters]
    lines += ["", "## High-energy moments (thumbnail frames / teasers)", ""]
    lines += [f"- {_fmt_ts(t)} (score {score:.1f}): {text[:80]}" for t, score, text in moments] or ["- none scored above zero"]
    lines += ["", "## Suggested ~30s Shorts windows", ""]
    lines += [f"- {_fmt_ts(a)} - {_fmt_ts(b)}" for a, b in windows] or ["- none"]
    lines.append("")

    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, f"{video_basename}_optimize.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return report_path
