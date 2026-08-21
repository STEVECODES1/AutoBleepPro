"""Measuring whether chat changed the picks, before changing anything.

The chat signal was wired in without a single clip having been produced
by it. This is the harness that answers the only question that matters -
does the audience's reaction move the selection - by running the SAME
scorer twice over one recording, blind and with chat, and diffing.

It must not modify scoring, must not invent signals it does not have,
and must say plainly when chat was unavailable rather than showing a
column of zeroes that reads as "chat was quiet".
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel import chat_report as R  # noqa: E402
from autoreel.chat_report import (LAUGHTER, Pick, biggest_climbers,  # noqa
                                  chosen_without_chat, compare,
                                  loudest_rejected, render, verdict)


def _segments(minutes=40):
    """Alternating dull and loud stretches, one per minute."""
    out = []
    for i in range(minutes):
        loud = i % 4 == 0
        out.append({
            "start": i * 60.0, "end": i * 60.0 + 60.0,
            "text": ("OH MY GOD what the hell was that bro holy no way"
                     if loud else "just walking around and talking"),
            "words": []})
    return out


def _rates(span_seconds, spikes=()):
    """One entry per second; `spikes` are (at_second, messages)."""
    rates = [1] * int(span_seconds)
    for at, many in spikes:
        if 0 <= int(at) < len(rates):
            rates[int(at)] = many
    return rates


# ── it compares rather than changes ──────────────────────────────────

def test_it_runs_the_same_scorer_both_ways():
    segments = _segments()
    rates = _rates(2400, spikes=[(1200, 90)])

    with_chat, without = compare(segments, rates, count=5)

    assert with_chat and without
    assert all(isinstance(p, Pick) for p in with_chat + without)


def test_chat_moves_a_window_up_the_order():
    """The whole question. A window with a huge spike should rank higher
    with chat on than it did blind."""
    segments = _segments()
    # Spike inside the fourth loud stretch, not the first.
    rates = _rates(2400, spikes=[(722, 200), (723, 200), (724, 200)])

    with_chat, without = compare(segments, rates, count=10)

    lifted = [p for p in with_chat if p.spike > 2.0]
    assert lifted, "the spike did not land inside any candidate window"
    assert any(p.climb > 0 for p in lifted) or any(
        p.rank_blind is None for p in lifted)


def test_the_scoring_algorithm_is_not_touched():
    """The instruction was to measure first. A report that changes the
    thing it measures is worthless."""
    body = open(os.path.join(_REPO, "autoreel", "chat_report.py"),
                encoding="utf-8").read()

    assert "HighlightScorer" in body          # it USES the scorer
    assert "def select_clips" not in body     # it does not reimplement it
    assert "chat_bonus" not in body           # nor reweight anything


# ── every column the report promises ─────────────────────────────────

def test_a_pick_carries_what_was_asked_for():
    segments = _segments()
    rates = _rates(2400, spikes=[(600, 120)])

    with_chat, _ = compare(segments, rates, count=3, levels=[])

    pick = with_chat[0]
    for field in ("start", "end", "seconds", "per_second", "spike",
                  "loudness", "score_blind", "score_chat", "movement",
                  "why", "laughter"):
        assert hasattr(pick, field), field


def test_laughter_says_nobody_looked():
    """A column of zeroes labelled laughter would read as 'no laughter
    here' rather than 'there is no detector'."""
    assert "not measured" in LAUGHTER
    assert Pick(start=0, end=10).laughter == LAUGHTER


def test_the_report_names_the_missing_detector():
    segments = _segments()
    rates = _rates(2400, spikes=[(600, 120)])
    with_chat, without = compare(segments, rates, count=3)

    said = render(with_chat, without, [], rates, name="s.ts")

    assert "no detector exists yet" in said


# ── the six sections ─────────────────────────────────────────────────

def test_all_six_sections_are_there():
    segments = _segments()
    rates = _rates(2400, spikes=[(600, 150), (1800, 200)])
    with_chat, without = compare(segments, rates, count=5)
    rejected = loudest_rejected(segments, rates, with_chat)

    said = render(with_chat, without, rejected, rates, name="s.ts")

    for heading in ("1. TOP WITH CHAT ENABLED",
                    "2. TOP WITH CHAT DISABLED",
                    "3. WHERE CHAT MADE THE BIGGEST DIFFERENCE",
                    "4. BIG CHAT SPIKES THAT WERE NOT SELECTED",
                    "5. SELECTED WITH LITTLE OR NO CHAT",
                    "6. VERDICT"):
        assert heading in said, heading


def test_rejected_spikes_are_outside_the_selections():
    """Otherwise it is listing clips it already chose."""
    segments = _segments()
    rates = _rates(2400, spikes=[(2350, 400)])
    with_chat, _ = compare(segments, rates, count=3)

    rejected = loudest_rejected(segments, rates, with_chat)

    for missed in rejected:
        for taken in with_chat:
            assert not (taken.start <= missed.start <= taken.end)


def test_quiet_selections_are_listed():
    segments = _segments()
    rates = _rates(2400)          # flat - nothing spikes
    with_chat, _ = compare(segments, rates, count=5)

    assert chosen_without_chat(with_chat), "everything looked chat-driven"


def test_climbers_are_ordered_by_how_far_they_moved():
    picks = [Pick(start=0, end=10, rank_blind=9, rank_chat=8),
             Pick(start=20, end=30, rank_blind=20, rank_chat=2),
             Pick(start=40, end=50, rank_blind=3, rank_chat=3)]

    moved = biggest_climbers(picks)

    assert [p.climb for p in moved] == [18, 1]


# ── the verdict is allowed to say no ─────────────────────────────────

def test_it_says_so_when_chat_changed_nothing():
    picks = [Pick(start=0, end=10, rank_blind=1, rank_chat=1)]

    said = verdict(picks, picks, [1, 1, 1])

    assert "changed NOTHING" in said


def test_it_does_not_claim_the_clips_are_better():
    """It measures what changed. Whether they are funnier is a question
    for the clips."""
    picks = [Pick(start=0, end=10, rank_blind=5, rank_chat=1, spike=4.0)]

    said = verdict(picks, [], [1, 1, 1])

    assert "BETTER" in said and "not for this report" in said


# ── no chat is a first-class answer ──────────────────────────────────

def test_no_chat_is_stated_not_faked():
    """The failure worth guarding: a column of zeroes that reads as
    'chat was quiet here'."""
    said = render([], [], [], [], name="stream.ts")

    assert "NO CHAT WAS AVAILABLE" in said
    assert "1. TOP WITH CHAT ENABLED" not in said


def test_no_chat_explains_why_it_might_be_missing():
    said = render([], [], [], [], name="stream.ts")

    assert "no chat replay" in said
    assert "remember_source" in said


def test_no_chat_does_not_crash_the_comparison():
    segments = _segments()

    with_chat, without = compare(segments, [], count=3)

    assert len(with_chat) == len(without)
    assert all(p.spike == 0.0 for p in with_chat)


def test_no_chat_makes_no_verdict_about_chat():
    assert "tells you nothing" in verdict([], [], [])


def test_an_empty_video_is_not_a_crash():
    with_chat, without = compare([], [1, 2, 3], count=5)

    assert with_chat == [] and without == []
    assert loudest_rejected([], [1, 2, 3], []) is not None
