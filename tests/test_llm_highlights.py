"""
The model-read pass, and every way it is allowed to fail.

The rule this file exists to hold: a clip pipeline must never stop
working because a model provider is slow, down, out of quota, or replied
with something unexpected. Every failure returns None, and None means the
local scorer's own ranking stands.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoreel.highlights import Highlight
from autoreel.llm_highlights import (
    GEMINI,
    OPENAI,
    available,
    build_prompt,
    parse_reply,
    rank,
)


@pytest.fixture
def no_keys(monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def candidates(n=5):
    return [
        Highlight(start=i * 200.0, end=i * 200.0 + 30.0, score=float(n - i),
                  text=f"candidate {i} said something", hook=f"local hook {i}")
        for i in range(n)
    ]


def replying(payload):
    """An `ask` that returns a fixed body, ignoring what it was sent."""
    def ask(key, model, prompt):
        ask.prompt = prompt
        return payload
    return ask


# ── Provider selection ───────────────────────────────────────────────────

def test_no_key_means_no_opinion(no_keys):
    assert rank(candidates(), 2) is None


def test_gemini_is_preferred_when_both_exist(no_keys):
    """Its free tier covers this workload, so it costs nothing to be on."""
    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("OPENAI_API_KEY", "o")
    assert available() == (GEMINI, "g")


def test_openai_is_used_when_it_is_the_only_one(no_keys):
    no_keys.setenv("OPENAI_API_KEY", "o")
    assert available() == (OPENAI, "o")


def test_a_named_provider_wins(no_keys):
    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("OPENAI_API_KEY", "o")
    assert available(OPENAI) == (OPENAI, "o")


# ── Failure is always silent ─────────────────────────────────────────────

def test_a_provider_that_raises_returns_no_opinion(no_keys):
    no_keys.setenv("GEMINI_API_KEY", "g")

    def explode(key, model, prompt):
        raise RuntimeError("network is down")

    assert rank(candidates(), 2, ask=explode) is None


@pytest.mark.parametrize("reply", [
    "", "   ", "sorry, I can't help with that",
    "{}", '{"clips": []}', '{"clips": "nope"}', "[1, 2, 3]",
    '{"clips": [{"index": 99}]}',       # out of range
    '{"clips": [{"index": "two"}]}',    # not a number
])
def test_unusable_replies_return_no_opinion(no_keys, reply):
    no_keys.setenv("GEMINI_API_KEY", "g")
    assert rank(candidates(), 2, ask=replying(reply)) is None


def test_a_fenced_reply_is_still_read(no_keys):
    """Models wrap JSON in a code fence constantly; that is not a failure."""
    no_keys.setenv("GEMINI_API_KEY", "g")
    body = '```json\n{"clips": [{"index": 2, "score": 90, "title": "A real title here"}]}\n```'

    chosen = rank(candidates(), 1, ask=replying(body))

    assert chosen is not None and len(chosen) == 1
    assert chosen[0].hook == "A real title here"


def test_a_bare_list_is_accepted(no_keys):
    no_keys.setenv("GEMINI_API_KEY", "g")
    body = '[{"index": 1, "score": 80, "title": "He walked straight into it"}]'

    chosen = rank(candidates(), 1, ask=replying(body))

    assert chosen is not None and chosen[0].hook == "He walked straight into it"


# ── Choosing ─────────────────────────────────────────────────────────────

def test_the_model_picks_and_its_titles_are_used(no_keys):
    no_keys.setenv("GEMINI_API_KEY", "g")
    body = json.dumps({"clips": [
        {"index": 4, "score": 95, "title": "That is not what he said at all"},
        {"index": 2, "score": 70, "title": "The whole lobby turned on him"},
    ]})

    chosen = rank(candidates(), 2, ask=replying(body))

    assert [h.start for h in chosen] == [200.0, 600.0], \
        "clips should come back in timeline order"
    assert chosen[0].hook == "The whole lobby turned on him"
    assert chosen[1].hook == "That is not what he said at all"


def test_more_picks_than_asked_for_are_trimmed_by_score(no_keys):
    no_keys.setenv("GEMINI_API_KEY", "g")
    body = json.dumps({"clips": [
        {"index": 1, "score": 10, "title": "the weakest one of the lot"},
        {"index": 3, "score": 99, "title": "the strongest one of the lot"},
        {"index": 5, "score": 50, "title": "somewhere in the middle here"},
    ]})

    chosen = rank(candidates(), 1, ask=replying(body))

    assert len(chosen) == 1
    assert chosen[0].hook == "the strongest one of the lot"


def test_a_repeated_index_is_only_counted_once(no_keys):
    no_keys.setenv("GEMINI_API_KEY", "g")
    body = json.dumps({"clips": [
        {"index": 1, "score": 90, "title": "first time it was chosen"},
        {"index": 1, "score": 95, "title": "second time it was chosen"},
    ]})

    chosen = rank(candidates(), 2, ask=replying(body))

    assert len(chosen) == 1


def test_a_pick_with_no_title_keeps_the_local_one(no_keys):
    """The scorer's sentence is a real fallback, not a placeholder."""
    no_keys.setenv("GEMINI_API_KEY", "g")
    body = json.dumps({"clips": [{"index": 1, "score": 90, "title": ""}]})

    chosen = rank(candidates(), 1, ask=replying(body))

    assert chosen[0].hook == "local hook 0"


# ── The prompt ───────────────────────────────────────────────────────────

def test_the_prompt_carries_what_was_said_and_when(no_keys):
    prompt = build_prompt(candidates(3), 2)

    assert "[1]" in prompt and "[3]" in prompt
    assert "candidate 0 said something" in prompt
    assert "0m00s" in prompt and "6m40s" in prompt


def test_parse_reply_rejects_indexes_outside_the_shortlist():
    assert parse_reply('{"clips": [{"index": 0}]}', 5) == []
    assert parse_reply('{"clips": [{"index": 6}]}', 5) == []


# ── The pipeline still works with nothing configured ─────────────────────

def test_specs_fall_back_to_the_scorer_without_a_key(no_keys):
    """The whole pipeline must run on a machine with no API key at all."""
    from autoreel.clip_maker import specs_from_segments

    segments = []
    at = 0.0
    for n in range(12):
        segments.append({"start": at, "end": at + 4.0,
                         "text": f"No way, that was actually insane number {n}!",
                         "words": []})
        at += 4.2

    specs = specs_from_segments(segments, count=1, min_seconds=10,
                                max_seconds=40, min_gap_seconds=5)

    assert len(specs) == 1
    assert specs[0].title, "a clip with no title reaches the platform unnamed"


def test_turning_the_model_pass_off_never_calls_out(no_keys, monkeypatch):
    from autoreel import clip_maker, llm_highlights

    def explode(*args, **kwargs):
        raise AssertionError("the model was asked despite llm_rank=False")

    monkeypatch.setattr(llm_highlights, "rank", explode)
    segments = [{"start": 0.0, "end": 20.0,
                 "text": "No way, that was actually insane, unbelievable!",
                 "words": []}]

    clip_maker.specs_from_segments(segments, count=1, min_seconds=10,
                                   max_seconds=40, llm_rank=False)
