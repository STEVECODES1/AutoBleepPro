"""Showing the model the room instead of asking it to be funny.

The picker chose against a general idea of funny - sixty candidates, two
frames each, no notion of what THIS audience laughs at. It picked
competently and the clips landed flat, and the answer kept being "your
clips aren't funny".

The channel already knows the answer: twenty posts with real view counts,
four million down to twenty-nine thousand. "Imma switch yo ahh" did four
million at twenty seconds.
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel import hits as H  # noqa: E402
from autoreel.hits import (MIN_VIEWS, for_prompt, load,  # noqa: E402
                           parse_found, typical_seconds)

FOUND = """Clips found 2026-08-10 20:55

 4,000,000   20s   imstackswopo       Imma switch yo ahh #fyp #stackswopo
 https://www.tiktok.com/@imstackswopo/video/7525270411781721399

 1,600,000   39s   stackswopos        stop wait a minute #stackswopo
 https://www.tiktok.com/@stackswopos/video/7589085225024572727

     1,200   12s   stackswopos        nobody watched this #stackswopo
 https://www.tiktok.com/@stackswopos/video/1
"""


# ── reading what worked ──────────────────────────────────────────────

def test_the_real_table_is_read():
    found = parse_found(FOUND)

    assert found[0]["views"] == 4_000_000
    assert found[0]["seconds"] == 20
    assert found[0]["caption"] == "Imma switch yo ahh"


def test_hashtags_are_not_the_joke():
    """They are noise in an example - every post has the same ones."""
    found = parse_found(FOUND)

    assert "#" not in found[0]["caption"]
    assert "fyp" not in found[0]["caption"]


def test_a_truncated_hashtag_does_not_leave_a_stray_hash():
    """The table cuts long captions mid-tag: '... 😂😂😂 #'."""
    assert H._clean("Santa gon make it happen 😂 #") == "Santa gon make it happen 😂"


def test_a_post_nobody_watched_is_not_an_example():
    """Including everything teaches it that anything goes, which is what
    it already believed."""
    found = [h for h in parse_found(FOUND) if h["views"] >= MIN_VIEWS]

    assert all(h["views"] >= MIN_VIEWS for h in found)
    assert not any("nobody watched" in h["caption"] for h in found)


def test_urls_are_not_read_as_hits():
    assert all("tiktok.com" not in h["caption"] for h in parse_found(FOUND))


# ── the file ─────────────────────────────────────────────────────────

def test_the_shipped_hits_are_real():
    """Seeded from this channel's own clips_found.txt, not invented."""
    with open(os.path.join(_REPO, "auto_uploader", "hits.json"),
              encoding="utf-8") as handle:
        data = json.load(handle)

    hits = data["hits"]
    assert len(hits) >= 10
    assert hits[0]["views"] >= 1_000_000
    assert all(h["seconds"] > 0 and h["caption"] for h in hits)


def test_it_loads_json(tmp_path):
    path = tmp_path / "hits.json"
    path.write_text(json.dumps({"hits": [
        {"views": 4_000_000, "seconds": 20, "caption": "Imma switch yo ahh"}]}))

    assert load(str(path))[0]["views"] == 4_000_000


def test_a_clips_found_table_can_be_used_as_the_file(tmp_path):
    """So the examples can be refreshed by pasting, not hand-editing."""
    path = tmp_path / "hits.json"
    path.write_text(FOUND)

    assert load(str(path))[0]["caption"] == "Imma switch yo ahh"


def test_the_biggest_comes_first(tmp_path):
    path = tmp_path / "hits.json"
    path.write_text(FOUND)

    found = load(str(path))
    assert found == sorted(found, key=lambda h: -h["views"])


def test_no_file_is_not_a_crash(tmp_path):
    assert load(str(tmp_path / "gone.json")) == []
    assert load("") == []


def test_a_broken_file_is_not_a_crash(tmp_path):
    path = tmp_path / "hits.json"
    path.write_text("{{{ not anything")

    assert load(str(path)) == []


# ── what the model is shown ──────────────────────────────────────────

def test_the_block_names_the_audience():
    said = for_prompt(load(os.path.join(_REPO, "auto_uploader", "hits.json")))

    assert "THIS CHANNEL" in said
    assert "Imma switch yo ahh" in said
    assert "4,000,000" in said


def test_it_says_only_what_the_numbers_say():
    """An invented rule is a false statement in a prompt, and the model
    has no way to check it."""
    said = for_prompt(load(os.path.join(_REPO, "auto_uploader", "hits.json")))

    assert "did the WORST" not in said
    assert "The biggest one is 20s" in said


def test_the_length_that_wins_is_stated():
    """The one part of 'what works here' a picker can act on directly."""
    found = load(os.path.join(_REPO, "auto_uploader", "hits.json"))

    assert typical_seconds(found)
    assert f"under {typical_seconds(found)} seconds" in for_prompt(found)


def test_nothing_to_show_shows_nothing():
    assert for_prompt([]) == ""


def test_the_list_is_kept_short():
    """The prompt is about the video. The examples are context, not a
    catalogue."""
    many = [{"views": 100_000 + i, "seconds": 20, "caption": f"line {i}"}
            for i in range(50)]

    assert for_prompt(many).count("views") <= H.MAX_EXAMPLES + 2


# ── both prompts get them ────────────────────────────────────────────

def test_the_text_prompt_shows_the_room():
    from autoreel.highlights import Highlight
    from autoreel.llm_highlights import build_prompt

    said = build_prompt([Highlight(start=0.0, end=20.0, text="a moment", score=1.0)], 1)

    assert "THIS CHANNEL" in said


def test_the_vision_prompt_shows_it_too():
    """That is the pass that actually runs. A picker shown the room in
    one path and not the other behaves differently depending on whether
    the frames could be read."""
    from autoreel.highlights import Highlight
    from autoreel.llm_highlights import build_vision_contents

    parts = build_vision_contents(
        [Highlight(start=0.0, end=20.0, text="a moment", score=1.0)], 1, "/v.mp4",
        grab=lambda *a: [])

    assert "THIS CHANNEL" in parts[0]["text"]


def test_a_missing_hits_file_does_not_break_the_prompt(monkeypatch):
    from autoreel import llm_highlights
    from autoreel.highlights import Highlight

    monkeypatch.setattr(llm_highlights, "hits_path", lambda: "/no/such.json")
    said = llm_highlights.build_prompt(
        [Highlight(start=0.0, end=20.0, text="a moment", score=1.0)], 1)

    assert "Pick AT MOST 1" in said
