"""What people went back and watched again.

Chat is the audience reacting. The most-replayed heatmap is the audience
RETURNING - going back, on purpose, to watch a stretch a second time.
Rewatching costs effort in a way that typing does not, and YouTube
publishes it per video.

It needs nothing new from the recorder: every VOD from this channel is
already on YouTube, so the curve for it already exists.
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.replay_heat import (WINDOW_SECONDS, _spread,  # noqa: E402
                                  dump_args, heat_bonus, heat_for_url,
                                  heat_over)


def _flat_with_peak(at=70.0, value=0.95, span=300.0, floor=0.1):
    """A dull video with one stretch everybody rewound to."""
    markers = [{"start_time": i * 10.0, "end_time": i * 10.0 + 10.0,
                "value": floor} for i in range(int(span // 10))]
    markers[int(at // 10)] = {"start_time": at, "end_time": at + 10.0,
                              "value": value}
    return markers


class _Ran:
    def __init__(self, out="", code=0):
        self.stdout = out
        self.returncode = code
        self.stderr = ""


# ── reading it ───────────────────────────────────────────────────────

def test_the_curve_is_laid_out_per_second():
    values = _spread([{"start_time": 0, "end_time": 10, "value": 0.4}])

    assert len(values) == int(10 // WINDOW_SECONDS) + 1
    assert values[5] == 0.4


def test_nothing_is_invented_between_markers():
    """The marker IS the resolution YouTube measured at. A curve drawn
    between two of them would be data nobody collected."""
    values = _spread([{"start_time": 0, "end_time": 10, "value": 0.2},
                      {"start_time": 10, "end_time": 20, "value": 0.8}])

    assert set(values[:10]) == {0.2}
    assert set(values[10:20]) == {0.8}


def test_junk_markers_are_dropped_not_guessed():
    values = _spread([
        {"start_time": 0, "end_time": 10, "value": 0.5},
        {"start_time": "x", "end_time": 20, "value": 0.9},
        {"end_time": 30, "value": 0.9},
        "not a marker",
        {"start_time": 40, "end_time": 30, "value": 0.9},
    ])

    assert values and max(values) == 0.5


def test_no_markers_is_no_opinion():
    assert _spread([]) == []
    assert _spread(None) == []


# ── how hot is hot ───────────────────────────────────────────────────

def test_a_rewound_stretch_reads_far_over_normal():
    values = _spread(_flat_with_peak())

    assert heat_over(values, 70, 80) > 5.0
    assert heat_over(values, 200, 210) <= 1.0


def test_it_is_measured_against_this_video_not_a_fixed_number():
    """A heatmap is normalised per video, so 'high' only means anything
    relative to the rest of THIS one."""
    quiet = _spread(_flat_with_peak(value=0.3, floor=0.05))
    busy = _spread(_flat_with_peak(value=0.95, floor=0.6))

    assert heat_over(quiet, 70, 80) > 3.0
    assert heat_over(busy, 70, 80) < 2.0


def test_the_bonus_is_capped():
    """A heatmap covers the whole video including its intro. Letting it
    dominate would re-cut whatever was already popular."""
    values = _spread(_flat_with_peak(value=1.0, floor=0.001))

    assert heat_bonus(values, 70, 80) <= 1.5


def test_a_normal_stretch_gets_nothing():
    values = _spread(_flat_with_peak())

    assert heat_bonus(values, 200, 210) == 1.0


def test_no_heatmap_changes_no_score():
    assert heat_bonus([], 0, 30) == 1.0
    assert heat_over([], 0, 30) == 0.0


# ── fetching it ──────────────────────────────────────────────────────

def test_only_metadata_is_fetched():
    """Downloading a four-hour VOD to read its heatmap would be absurd."""
    args = dump_args("https://youtu.be/abc")

    assert "--skip-download" in args
    assert "--dump-single-json" in args


def test_a_real_looking_answer_is_read():
    body = json.dumps({"id": "abc", "heatmap": _flat_with_peak()})

    values = heat_for_url("https://youtu.be/abc",
                          runner=lambda *a, **k: _Ran(body))

    assert values and heat_over(values, 70, 80) > 5.0


def test_a_video_with_no_heatmap_is_normal_not_an_error():
    """YouTube only publishes one once enough people have watched."""
    body = json.dumps({"id": "abc"})

    assert heat_for_url("https://youtu.be/abc",
                        runner=lambda *a, **k: _Ran(body)) == []


def test_yt_dlp_failing_is_not_a_crash():
    assert heat_for_url("https://youtu.be/abc",
                        runner=lambda *a, **k: _Ran("", code=1)) == []


def test_junk_back_is_not_a_crash():
    assert heat_for_url("https://youtu.be/abc",
                        runner=lambda *a, **k: _Ran("<html>nope</html>")) == []


def test_yt_dlp_missing_is_not_a_crash():
    def explode(*_a, **_k):
        raise FileNotFoundError("yt-dlp")

    assert heat_for_url("https://youtu.be/abc", runner=explode) == []


def test_no_url_asks_for_nothing():
    called = []
    heat_for_url("", runner=lambda *a, **k: called.append(1) or _Ran("{}"))

    assert not called


# ── it reaches the scorer ────────────────────────────────────────────

def test_the_scorer_takes_it():
    from autoreel.highlights import HighlightScorer

    assert HighlightScorer(heat=[0.1, 0.9]).heat == [0.1, 0.9]


def test_a_rewound_moment_outscores_an_identical_one(monkeypatch):
    """The whole point: same words, different replay, different score."""
    from autoreel.highlights import HighlightScorer

    segments = [{"start": i * 30.0, "end": i * 30.0 + 30.0,
                 "text": "OH MY GOD what the hell was that bro holy",
                 "words": []} for i in range(10)]
    values = _spread(_flat_with_peak(at=150.0, span=300.0))

    blind = HighlightScorer().select_clips(segments, count=10, min_gap=1.0)
    hot = HighlightScorer(heat=values).select_clips(segments, count=10,
                                                    min_gap=1.0)

    def near(picks, when):
        return next((p for p in picks if abs(p.start - when) < 45), None)

    before, after = near(blind, 150.0), near(hot, 150.0)
    assert before and after
    assert after.score > before.score


def test_the_runner_asks_for_it_by_name():
    """A signal inserted into a positional call once landed llm_rank in
    `heat`, and a boolean read as a heatmap is not a crash - it is wrong
    clips."""
    body = open(os.path.join(_REPO, "autoreel", "clip_maker.py"),
                encoding="utf-8").read()

    assert "heat=heat," in body
    assert "llm_rank=self.llm_rank," in body
