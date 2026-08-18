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


# ── --check-llm ──────────────────────────────────────────────────────────

def test_check_says_so_when_nothing_is_configured(no_keys):
    from autoreel.llm_highlights import check

    ok, detail = check()
    assert ok is False
    assert "GEMINI_API_KEY" in detail


def test_check_reports_the_providers_own_reason(no_keys, monkeypatch):
    """A rejected key must say WHY - "it did not work" helps nobody."""
    from autoreel import llm_highlights

    no_keys.setenv("GEMINI_API_KEY", "AQ.wrong-shape")
    monkeypatch.setattr(
        llm_highlights, "_post_detailed",
        lambda url, payload, headers: (None, "HTTP 400: API key not valid"))

    ok, detail = llm_highlights.check()

    assert ok is False
    assert "API key not valid" in detail


def test_check_confirms_a_working_key(no_keys, monkeypatch):
    from autoreel import llm_highlights

    no_keys.setenv("GEMINI_API_KEY", "AIzaSyLooksRight")
    monkeypatch.setattr(llm_highlights, "_post_detailed",
                        lambda url, payload, headers: ({"candidates": []}, ""))

    ok, detail = llm_highlights.check()

    assert ok is True and "works" in detail


# ── Model discovery ──────────────────────────────────────────────────────
#
# Pinning a model name in code was a real bug: "gemini-2.5-flash is no
# longer available to new users" is a 404, and a 404 reads exactly like a
# broken key. Providers retire models on their own schedule, so the name
# has to come from the provider.

def test_a_retired_pin_is_not_what_gets_called(no_keys, monkeypatch):
    from autoreel import llm_highlights

    no_keys.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(llm_highlights, "list_models",
                        lambda provider, key: ["gemini-3-flash",
                                               "gemini-2.5-flash"])
    assert llm_highlights.resolve_model("gemini", "k") == "gemini-3-flash"


def test_a_configured_model_is_never_second_guessed(no_keys, monkeypatch):
    from autoreel import llm_highlights

    monkeypatch.setattr(llm_highlights, "list_models",
                        lambda provider, key: ["gemini-3-flash"])
    assert llm_highlights.resolve_model(
        "gemini", "k", "gemini-4-pro") == "gemini-4-pro"


def test_an_unreachable_model_list_still_leaves_a_name(no_keys, monkeypatch):
    from autoreel import llm_highlights

    monkeypatch.setattr(llm_highlights, "list_models", lambda p, k: [])
    assert llm_highlights.resolve_model("gemini", "k")


def test_models_that_cannot_read_text_are_never_chosen():
    from autoreel.llm_highlights import usable_models

    chosen = usable_models([
        "text-embedding-004", "imagen-4.0-generate", "veo-3.0",
        "gemini-3-flash", "gemini-live-2.5-flash",
    ])
    assert chosen[0] == "gemini-3-flash"
    assert not any("embedding" in n or "imagen" in n or "veo" in n
                   for n in chosen)


def test_a_newer_version_wins():
    from autoreel.llm_highlights import usable_models

    assert usable_models(["gemini-2.5-flash", "gemini-3-flash"])[0] == \
        "gemini-3-flash"


def test_a_preview_loses_to_a_stable_release():
    """A preview name can disappear mid-week; a stable one cannot."""
    from autoreel.llm_highlights import usable_models

    assert usable_models(["gemini-4-flash-preview-06-01",
                          "gemini-3-flash"])[0] == "gemini-3-flash"


def test_flash_is_preferred_over_lite_and_over_pro():
    """Reading a few thousand words and returning a short list does not
    need a reasoning model, and lite is the weaker sibling."""
    from autoreel.llm_highlights import usable_models

    order = usable_models(["gemini-3-pro", "gemini-3-flash-lite",
                           "gemini-3-flash"])
    assert order[0] == "gemini-3-flash"


def test_list_models_drops_anything_that_cannot_generate(monkeypatch):
    from autoreel import llm_highlights

    class Fake:
        def read(self):
            return json.dumps({"models": [
                {"name": "models/gemini-3-flash",
                 "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/text-embedding-004",
                 "supportedGenerationMethods": ["embedContent"]},
            ]}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(llm_highlights.urllib.request, "urlopen",
                        lambda *a, **k: Fake())
    assert llm_highlights.list_models("gemini", "k") == ["gemini-3-flash"]


def test_listing_failures_are_silent(monkeypatch):
    from autoreel import llm_highlights

    def explode(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(llm_highlights.urllib.request, "urlopen", explode)
    assert llm_highlights.list_models("gemini", "k") == []


# ═════════════════════════════════════════════════════════════════════════════
# THE MODEL DECLINING IS NOT THE MODEL HAVING NO OPINION
#
# --check-llm reported the key working while every batch came back
# scorer-titled. The key WAS working: the request asks the model to read a
# transcript of a loud, sweary Monkey-app call, and on Gemini's default
# thresholds it declines to answer at all. No candidate comes back, the
# parse yields "", and the caller prints "the model returned nothing
# usable" - which reads as "it looked and liked none of them".
# ═════════════════════════════════════════════════════════════════════════════

def test_the_request_carries_safety_thresholds():
    """Without these the channel's own content is refused outright."""
    from autoreel.llm_highlights import _SAFETY

    assert _SAFETY, "no safety settings are sent at all"
    categories = {entry["category"] for entry in _SAFETY}
    assert "HARM_CATEGORY_HARASSMENT" in categories
    assert "HARM_CATEGORY_HATE_SPEECH" in categories
    assert all(entry["threshold"] == "BLOCK_ONLY_HIGH" for entry in _SAFETY), \
        "the severe end must still block - this is not 'off'"


def test_both_calls_send_them(monkeypatch):
    """The vision pass sends FRAMES of the same call, and was refused for
    the same reason."""
    from autoreel import llm_highlights

    sent = []
    monkeypatch.setattr(llm_highlights, "_post",
                        lambda url, payload, headers: sent.append(payload) or {})
    monkeypatch.setattr(llm_highlights, "_post_detailed",
                        lambda url, payload, headers, timeout=None:
                        (sent.append(payload), ({}, ""))[1])

    llm_highlights._ask_gemini("k", "m", "prompt")
    llm_highlights._ask_gemini_vision("k", "m", [{"text": "x"}])

    assert len(sent) == 2
    assert all("safetySettings" in payload for payload in sent)


@pytest.mark.parametrize("reply,expected", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "blocked"),
    ({"candidates": [{"finishReason": "SAFETY"}]}, "stopped"),
    ({}, "no candidates"),
    ({"candidates": [{"finishReason": "STOP"}]}, ""),
])
def test_a_refusal_is_named_not_swallowed(reply, expected):
    """Swallowed, this is indistinguishable from a model that read the
    clips and liked none of them - which is exactly how it was read."""
    from autoreel.llm_highlights import _refusal

    said = _refusal(reply)
    if expected:
        assert expected in said
    else:
        assert said == "", "a normal finish must not read as a refusal"


# ── the OUTPUT was blocked, not the input ────────────────────────────
#
#   finishReason: SAFETY
#   "The model output could not be generated. This output contains
#    sensitive words that violate Google's ... policy"
#
# It read the transcript fine. It was told to title each clip "in the
# streamer's own words", those words include slurs, and it will not write
# one. safetySettings cannot reach that - output policy is not
# configurable - so the slur never goes in.

def test_a_slur_never_reaches_the_model():
    from autoreel.llm_highlights import for_the_model

    sent = for_the_model("yo this nigga said what the fuck bro")

    assert "nigga" not in sent
    assert "n****" not in sent, "a starred slur is still a word to echo"
    assert "yo this" in sent and "bro" in sent, "the moment was destroyed"


def test_ordinary_swearing_is_removed_too():
    """Masking was tried first and did not clear the refusal - "f***" is
    still a sensitive word to the filter."""
    from autoreel.llm_highlights import for_the_model

    sent = for_the_model("what the fuck bro")
    assert "f***" not in sent and "fuck" not in sent
    assert "bro" in sent


def test_the_candidate_list_is_masked(monkeypatch):
    from autoreel.llm_highlights import build_prompt

    class _H:
        start, end = 10.0, 40.0
        text = "yo this nigga said what the fuck"

    prompt = build_prompt([_H()], count=1, lessons=[])

    assert "nigga" not in prompt
    assert "f***" not in prompt and "fuck" not in prompt
    assert "yo this" in prompt, "the moment itself was destroyed"


def test_the_model_is_told_not_to_echo_a_masked_word():
    """Masking the input is most of it; saying so out loud covers the
    case where it would have copied the stars across instead."""
    from autoreel.llm_highlights import SYSTEM_PROMPT

    assert "NEVER reproduce a slur" in SYSTEM_PROMPT
    assert "stars" in SYSTEM_PROMPT


# ── refusing to WRITE is not refusing to CHOOSE ──────────────────────
#
# The refusal is at the output. Writing the title is the only part that
# makes the model produce the channel's own language - choosing is just
# numbers. Losing the whole pass over the naming threw away the half the
# local scorer cannot do.

def test_a_flagged_word_is_removed_from_the_prompt_not_starred():
    """Masking alone did not clear it: "f***" is still a sensitive word
    to the filter, and the refusal came back unchanged."""
    from autoreel.llm_highlights import for_the_model

    sent = for_the_model("yo this nigga said what the fuck bro")

    assert "f***" not in sent and "fuck" not in sent
    assert "nigga" not in sent
    assert "yo this" in sent and "bro" in sent


def test_an_ordinary_sentence_survives_intact():
    from autoreel.llm_highlights import for_the_model

    said = "she said something and I was like bro no way"
    assert for_the_model(said) == said


def test_it_asks_again_without_titles_when_the_model_will_not_write(
        monkeypatch):
    from autoreel import llm_highlights
    from autoreel.highlights import Highlight

    shortlist = [Highlight(start=0.0, end=30.0, text="a moment", score=10.0)]
    asked = []

    def refuses_titles(key, model, prompt):
        asked.append(prompt)
        if llm_highlights.NUMBERS_ONLY.strip() in prompt:
            return '{"clips": [{"index": 1, "score": 88}]}'
        return ""          # blocked at the output

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    chosen = llm_highlights.rank(shortlist, 1, provider="gemini",
                                 model="m", ask=refuses_titles)

    assert len(asked) == 2, "it gave up instead of asking again"
    assert chosen and chosen[0].score == 88, \
        "the model's ranking was thrown away with its titles"


def test_the_scorers_title_stands_when_the_model_writes_none(monkeypatch):
    from autoreel import llm_highlights
    from autoreel.highlights import Highlight

    shortlist = [Highlight(start=0.0, end=30.0, text="a moment",
                           score=10.0, hook="the line the scorer picked")]

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    chosen = llm_highlights.rank(
        shortlist, 1, provider="gemini", model="m",
        ask=lambda k, m, p: ('{"clips": [{"index": 1, "score": 70}]}'
                             if llm_highlights.NUMBERS_ONLY.strip() in p
                             else ""))

    assert chosen[0].hook == "the line the scorer picked"


def test_a_refusal_is_told_apart_from_a_missing_key(monkeypatch):
    """"Check the key with --check-llm" sent people to a command that
    reports the key working - true, and useless, because the provider
    declined the CONTENT. Those two need opposite advice and got the
    same line."""
    from autoreel import llm_highlights
    from autoreel.highlights import Highlight

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    llm_highlights._remember_refusal("")

    def blocked(key, model, prompt):
        llm_highlights._remember_refusal(
            llm_highlights._refusal({"candidates": [{"finishReason": "SAFETY"}]}))
        return ""

    result = llm_highlights.rank(
        [Highlight(start=0.0, end=30.0, text="a moment", score=10.0)],
        1, provider="gemini", model="m", ask=blocked)

    assert result is None
    assert "SAFETY" in llm_highlights.last_refusal()


def test_a_fresh_run_does_not_report_a_stale_reason(monkeypatch):
    from autoreel import llm_highlights

    llm_highlights._remember_refusal("the answer was stopped (SAFETY)")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    llm_highlights.rank([], 1)

    assert llm_highlights.last_refusal() == ""


# ═════════════════════════════════════════════════════════════════════════════
# "NOTHING USABLE" NAMED TWO DIFFERENT FAILURES AND NEITHER OF THEM
#
# A real run ended:
#
#   [Clips] The model would not write titles - asking it to just pick
#   [Clips] The model returned nothing usable - the scorer's own ranking stands
#
# and --check-llm then reported "gemini (gemini-3.7-flash) answered - the
# key works", which was true and left nowhere to go. An answer that could
# not be READ and no answer at all need different fixes.
# ═════════════════════════════════════════════════════════════════════════════

def test_an_unreadable_answer_is_shown(no_keys, capsys):
    """The reply is the evidence, and it was being thrown away."""
    no_keys.setenv("GEMINI_API_KEY", "g")

    assert rank(candidates(), 2,
                ask=replying("I'd rather not rank these, sorry!")) is None

    out = capsys.readouterr().out
    assert "I'd rather not rank these" in out


def test_no_answer_at_all_says_so_instead(no_keys, capsys):
    """A silent call is not a parsing problem, and sending someone to
    --check-llm for it is what wasted the last round."""
    no_keys.setenv("GEMINI_API_KEY", "g")

    assert rank(candidates(), 2, ask=replying("")) is None

    out = capsys.readouterr().out
    assert "said nothing at all" in out
    assert "the call failing, not the parsing" in out


def test_a_long_reply_is_cut_down(no_keys, capsys):
    """Evidence, not the whole transcript back in the terminal."""
    no_keys.setenv("GEMINI_API_KEY", "g")

    rank(candidates(), 2, ask=replying("x" * 5000))

    line = next(l for l in capsys.readouterr().out.splitlines() if "xxx" in l)
    assert len(line) < 400
