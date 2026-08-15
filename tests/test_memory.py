"""The pipeline remembering what worked.

The thing being guarded most here is SILENCE. A system that retunes
itself on four data points is not learning, it is drifting, and it would
be indistinguishable from a bug that quietly stopped picking good clips.
Most of these tests assert that nothing was concluded.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from autoreel.memory import (  # noqa: E402
    Ledger, ClipRecord, Guidance, clip_id, learn, harvest,
    MINIMUM_CLIPS, MINIMUM_PER_BAND, MAX_NUDGE_SECONDS, STALE_AFTER_DAYS)


def _record(key, views=None, duration=40.0, position=0.5, hook="a normal hook",
            picked_by="model", age_days=1.0):
    record = ClipRecord(
        clip_id=key, source="stream", start=10.0, duration=duration,
        position=position, hook=hook, picked_by=picked_by,
        created=time.time() - age_days * 86400)
    if views is not None:
        record.posted["rumble"] = f"https://rumble.com/{key}.html"
        record.views["rumble"] = views
    return record


@pytest.fixture
def ledger(tmp_path):
    return Ledger(str(tmp_path / "mem.json"))


def _fill(ledger, count, **kw):
    for n in range(count):
        ledger.remember(_record(f"c{n}", views=100, **kw))
    return ledger


# ── identity ─────────────────────────────────────────────────────────

def test_the_same_clip_recut_is_one_record(ledger):
    """Filenames get renamed; a re-cut clip must not double-count."""
    key = clip_id("Stream A", 573.04)
    ledger.remember(_record(key, views=50))
    ledger.remember(_record(key, duration=44.0))
    assert len(ledger.records()) == 1


def test_recutting_never_loses_a_view_count(ledger):
    """Views are the one thing here that cannot be recreated."""
    key = clip_id("Stream A", 573.0)
    ledger.remember(_record(key, views=812))
    ledger.remember(_record(key, duration=44.0))
    assert ledger.get(key).views["rumble"] == 812


def test_recutting_updates_what_did_change(ledger):
    key = clip_id("Stream A", 573.0)
    ledger.remember(_record(key, views=10))
    ledger.remember(_record(key, duration=44.0))
    assert ledger.get(key).duration == 44.0


def test_two_clips_from_the_same_stream_are_separate():
    assert clip_id("Stream A", 10.0) != clip_id("Stream A", 400.0)


def test_identity_ignores_case_and_padding():
    assert clip_id(" Stream A ", 10.0) == clip_id("stream a", 10.0)


# ── the file ─────────────────────────────────────────────────────────

def test_it_survives_a_round_trip(tmp_path):
    path = str(tmp_path / "m.json")
    first = Ledger(path)
    first.remember(_record("a", views=7))
    first.save()
    assert Ledger(path).get("a").views["rumble"] == 7


def test_a_missing_file_is_an_empty_ledger(tmp_path):
    assert Ledger(str(tmp_path / "nope.json")).records() == []


def test_a_corrupt_file_does_not_take_the_run_down(tmp_path):
    path = tmp_path / "m.json"
    path.write_text("{not json")
    assert Ledger(str(path)).records() == []


def test_a_record_from_a_newer_version_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps(
        {"version": 2, "clips": [{"clip_id": "a", "invented_field": 1}]}))
    assert Ledger(str(path)).records() == []


def test_an_interrupted_write_cannot_destroy_the_history(tmp_path):
    path = str(tmp_path / "m.json")
    first = Ledger(path)
    first.remember(_record("a", views=5))
    first.save()
    assert not os.path.exists(path + ".tmp")
    assert Ledger(path).get("a") is not None


# ── silence ──────────────────────────────────────────────────────────

def test_nothing_is_concluded_from_nothing(ledger):
    assert not learn(ledger)


def test_nothing_is_concluded_below_the_minimum(ledger):
    _fill(ledger, MINIMUM_CLIPS - 1)
    guidance = learn(ledger)
    assert not guidance
    assert "Not enough" in guidance.summary()


def test_unmeasured_clips_do_not_count_toward_the_minimum(ledger):
    for n in range(30):
        ledger.remember(_record(f"c{n}", views=None))
    assert learn(ledger).sample == 0


def test_identical_clips_teach_nothing(ledger):
    """Same everything, same views - there is no pattern to find."""
    _fill(ledger, 20)
    assert not learn(ledger)


def test_a_lopsided_band_is_not_a_finding(ledger):
    """One long clip that went viral is an anecdote, not evidence."""
    for n in range(15):
        ledger.remember(_record(f"s{n}", views=100, duration=30.0))
    ledger.remember(_record("long", views=99999, duration=59.0))
    for finding in learn(ledger).findings:
        assert finding.sample >= MINIMUM_PER_BAND * 2


def test_stale_records_stop_counting(ledger):
    for n in range(20):
        ledger.remember(_record(f"c{n}", views=100,
                                age_days=STALE_AFTER_DAYS + 10))
    assert learn(ledger).sample == 0


# ── finding something real ───────────────────────────────────────────

def _two_bands(ledger, key, low_value, high_value, low_views, high_views):
    for n in range(8):
        ledger.remember(_record(f"lo{n}", views=low_views, **{key: low_value}))
    for n in range(8):
        ledger.remember(_record(f"hi{n}", views=high_views, **{key: high_value}))


def test_a_real_length_difference_is_found(ledger):
    _two_bands(ledger, "duration", 20.0, 55.0, 100, 400)
    guidance = learn(ledger)
    assert guidance
    assert any("Length" in f.subject for f in guidance.findings)


def test_it_reports_which_side_actually_won(ledger):
    """Short clips winning must read as short clips winning."""
    _two_bands(ledger, "duration", 20.0, 55.0, 500, 100)
    finding = next(f for f in learn(ledger).findings if f.subject == "Length")
    assert "under" in finding.better


def test_placement_in_the_stream_is_found(ledger):
    _two_bands(ledger, "position", 0.1, 0.9, 100, 350)
    assert any(f.subject == "Placement" for f in learn(ledger).findings)


def test_a_question_hook_is_found(ledger):
    for n in range(8):
        ledger.remember(_record(f"st{n}", views=100, hook="he walked in"))
    for n in range(8):
        ledger.remember(_record(f"qu{n}", views=400, hook="did you see that?"))
    findings = learn(ledger).findings
    assert any("question" in f.better for f in findings)


def test_every_finding_carries_its_sample_size(ledger):
    _two_bands(ledger, "duration", 20.0, 55.0, 100, 400)
    for finding in learn(ledger).findings:
        assert finding.sample > 0
        assert str(finding.sample) in finding.sentence()


def test_the_summary_reads_as_sentences(ledger):
    _two_bands(ledger, "duration", 20.0, 55.0, 100, 400)
    summary = learn(ledger).summary()
    assert "beat" in summary and "%" in summary


# ── acting on it, within limits ──────────────────────────────────────

def test_no_guidance_leaves_the_scorer_alone():
    assert Guidance().nudge(15.0, 60.0) == (15.0, 60.0)


def test_a_lesson_moves_the_window_toward_what_worked():
    low, high = Guidance(best_duration=50.0).nudge(15.0, 45.0)
    assert low > 15.0 and high > 45.0


def test_a_lesson_can_never_move_it_further_than_the_cap():
    low, high = Guidance(best_duration=9999.0).nudge(15.0, 60.0)
    assert low <= 15.0 + MAX_NUDGE_SECONDS
    assert high <= 60.0 + MAX_NUDGE_SECONDS


def test_the_window_can_never_invert():
    low, high = Guidance(best_duration=-9999.0).nudge(15.0, 60.0)
    assert low >= 1.0
    assert high > low


def test_the_window_never_collapses_to_nothing():
    low, high = Guidance(best_duration=0.0).nudge(20.0, 22.0)
    assert high - low >= 5.0


def test_lessons_reach_the_model_as_readable_sentences(ledger):
    _two_bands(ledger, "duration", 20.0, 55.0, 100, 400)
    lines = learn(ledger).prompt_lines()
    assert lines
    assert any("beat" in line for line in lines)


def test_the_model_is_told_not_to_obey_blindly(ledger):
    _two_bands(ledger, "duration", 20.0, 55.0, 100, 400)
    assert any("still the" in line for line in learn(ledger).prompt_lines())


def test_no_lessons_means_nothing_is_added_to_the_prompt():
    assert Guidance().prompt_lines() == []


# ── views ────────────────────────────────────────────────────────────

def test_the_best_platform_wins_not_the_sum():
    """One clip on four platforms is one clip, not four times as good."""
    record = ClipRecord(clip_id="a")
    record.views = {"rumble": 30, "youtube_shorts": 12, "instagram": 4}
    assert record.best_views() == 30


def test_never_checked_is_not_zero_views():
    assert ClipRecord(clip_id="a").best_views() is None


def test_harvest_asks_only_about_what_was_posted(ledger, monkeypatch):
    ledger.remember(_record("posted", views=None))
    ledger.get("posted").posted["rumble"] = "https://rumble.com/x.html"
    ledger.remember(ClipRecord(clip_id="never_posted"))

    asked = []
    monkeypatch.setattr("autoreel.memory.views_for",
                        lambda url, **kw: asked.append(url) or 42)
    assert harvest(ledger) == 1
    assert asked == ["https://rumble.com/x.html"]


def test_harvest_leaves_already_counted_clips_alone(ledger, monkeypatch):
    ledger.remember(_record("done", views=99))
    monkeypatch.setattr("autoreel.memory.views_for",
                        lambda url, **kw: pytest.fail("asked again"))
    assert harvest(ledger) == 0


def test_a_view_count_that_cannot_be_read_is_not_a_zero(ledger, monkeypatch):
    ledger.remember(_record("p", views=None))
    ledger.get("p").posted["rumble"] = "https://rumble.com/x.html"
    monkeypatch.setattr("autoreel.memory.views_for", lambda url, **kw: None)
    assert harvest(ledger) == 0
    assert ledger.get("p").best_views() is None


def test_harvest_is_bounded(ledger, monkeypatch):
    for n in range(50):
        record = _record(f"c{n}", views=None)
        record.posted["rumble"] = f"https://rumble.com/{n}.html"
        ledger.remember(record)
    monkeypatch.setattr("autoreel.memory.views_for", lambda url, **kw: 1)
    assert harvest(ledger, limit=5) == 5


def test_a_broken_yt_dlp_returns_nothing_rather_than_raising(monkeypatch):
    from autoreel import memory

    monkeypatch.setattr(memory.subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert memory.views_for("https://rumble.com/x.html") is None


def test_no_url_is_never_asked_about():
    from autoreel import memory

    assert memory.views_for("") is None
