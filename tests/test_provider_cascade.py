"""One provider is one point of failure, and the failure is silent.

When the model gave nothing the run dropped to a local scorer that cannot
tell whether anything was funny, and cut a full set of guesses. That is
where "most of these don't make no sense" came from.

So: every configured provider is tried, in turn, before anything falls
back. A second key turns a bad day into a slightly slower one.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import pytest  # noqa: E402

from autoreel import llm_highlights as llm  # noqa: E402
from autoreel.highlights import Highlight  # noqa: E402
from autoreel.llm_highlights import (ANTHROPIC, CEREBRAS, GEMINI,  # noqa: E402
                                     OPENAI, all_available, asker_for, rank)

GOOD = '{"clips":[{"index":1,"score":90,"title":"picked"}]}'


@pytest.fixture
def no_keys(monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
                 "ANTHROPIC_API_KEY", "CEREBRAS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _candidates(n=3):
    return [Highlight(start=i * 100.0, end=i * 100.0 + 20.0,
                      text=f"line {i}", score=float(n - i)) for i in range(n)]


# ── who is configured ────────────────────────────────────────────────

def test_every_configured_provider_is_listed(no_keys):
    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("OPENAI_API_KEY", "o")
    no_keys.setenv("ANTHROPIC_API_KEY", "a")

    assert [p for p, _ in all_available()] == [GEMINI, OPENAI, ANTHROPIC]


def test_gemini_goes_first_because_it_can_see(no_keys):
    """It is the only one here that gets shown the frames, which is the
    whole reason the picks got good."""
    no_keys.setenv("OPENAI_API_KEY", "o")
    no_keys.setenv("GEMINI_API_KEY", "g")

    assert all_available()[0][0] == GEMINI


def test_a_named_preference_is_honoured(no_keys):
    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("ANTHROPIC_API_KEY", "a")

    assert all_available(ANTHROPIC)[0][0] == ANTHROPIC


def test_no_keys_means_nobody(no_keys):
    assert all_available() == []


# ── the cascade ──────────────────────────────────────────────────────

def test_a_second_provider_is_asked_when_the_first_gives_nothing(
        no_keys, monkeypatch, capsys):
    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "m")
    asked = []

    monkeypatch.setattr(llm, "_ask_gemini",
                        lambda *a: asked.append(GEMINI) or "")
    monkeypatch.setattr(llm, "_ask_openai",
                        lambda *a: asked.append(OPENAI) or GOOD)

    chosen = rank(_candidates(), 1)

    assert asked[0] == GEMINI and OPENAI in asked
    assert chosen and chosen[0].hook == "picked"
    assert "Asking openai instead" in capsys.readouterr().out


def test_the_third_is_reached_if_the_second_fails_too(no_keys, monkeypatch):
    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("OPENAI_API_KEY", "o")
    no_keys.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "m")
    monkeypatch.setattr(llm, "_ask_gemini", lambda *a: "")
    monkeypatch.setattr(llm, "_ask_openai", lambda *a: "not json")
    monkeypatch.setattr(llm, "_ask_anthropic", lambda *a: GOOD)

    chosen = rank(_candidates(), 1)

    assert chosen and chosen[0].hook == "picked"


def test_the_first_provider_that_answers_ends_it(no_keys, monkeypatch):
    """Not a poll of all of them - the second is a backstop, not a vote,
    and every extra call is time and money."""
    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "m")
    monkeypatch.setattr(llm, "_ask_gemini", lambda *a: GOOD)

    def never(*_a):
        raise AssertionError("it asked the backstop for no reason")

    monkeypatch.setattr(llm, "_ask_openai", never)

    assert rank(_candidates(), 1)


def test_one_provider_throwing_does_not_stop_the_next(no_keys, monkeypatch):
    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "m")
    monkeypatch.setattr(llm, "_ask_gemini",
                        lambda *a: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(llm, "_ask_anthropic", lambda *a: GOOD)

    assert rank(_candidates(), 1)


def test_everything_failing_is_still_no_opinion(no_keys, monkeypatch):
    """The scorer's ranking stands, as it always did."""
    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("OPENAI_API_KEY", "o")
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "m")
    monkeypatch.setattr(llm, "_ask_gemini", lambda *a: "")
    monkeypatch.setattr(llm, "_ask_openai", lambda *a: "")

    assert rank(_candidates(), 1) is None


# ── talking to Claude ────────────────────────────────────────────────

def test_each_provider_has_its_own_caller():
    assert asker_for(GEMINI) is llm._ask_gemini
    assert asker_for(OPENAI) is llm._ask_openai
    assert asker_for(ANTHROPIC) is llm._ask_anthropic


def test_anthropic_is_called_the_way_anthropic_expects(monkeypatch):
    sent = {}

    def note(url, payload, headers):
        sent["url"] = url
        sent["payload"] = payload
        sent["headers"] = headers
        return {"content": [{"text": GOOD}]}

    monkeypatch.setattr(llm, "_post", note)

    assert llm._ask_anthropic("k", "claude-sonnet-5", "prompt") == GOOD
    assert sent["url"].endswith("/v1/messages")
    assert sent["headers"]["x-api-key"] == "k"
    assert sent["headers"]["anthropic-version"]
    assert sent["payload"]["max_tokens"] > 0
    assert sent["payload"]["system"] == llm.SYSTEM_PROMPT


def test_a_junk_response_from_anthropic_is_just_empty(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda *a, **k: {"error": "nope"})

    assert llm._ask_anthropic("k", "m", "p") == ""


# ── checking the keys ────────────────────────────────────────────────
#
# --check-llm reported "gemini answered - the key works" the moment after
# a second key was pasted in, and said nothing about the key that had just
# been added. A backstop nobody has verified gets found out on the day the
# first provider fails, which is the worst moment available.

def test_every_configured_key_is_checked(no_keys, monkeypatch):
    from autoreel.llm_highlights import check_all

    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("OPENAI_API_KEY", "o")
    asked = []

    def one(provider="", model=""):
        asked.append(provider)
        return True, f"{provider} answered"

    monkeypatch.setattr(llm, "check", one)

    results = check_all()

    assert [name for name, _ok, _d in results] == [GEMINI, OPENAI]
    assert asked == [GEMINI, OPENAI]


def test_no_keys_at_all_names_every_provider(no_keys):
    """The list is derived from the key table, so adding a provider
    cannot leave this message naming fewer than are supported."""
    from autoreel.llm_highlights import check_all

    (_name, ok, detail), = check_all()

    assert not ok
    for name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "CEREBRAS_API_KEY"):
        assert name in detail


def test_one_bad_key_does_not_hide_a_good_one(no_keys, monkeypatch):
    from autoreel.llm_highlights import check_all

    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setattr(
        llm, "check",
        lambda provider="", model="": (provider == ANTHROPIC, provider))

    results = check_all()

    assert dict((n, ok) for n, ok, _ in results) == {GEMINI: False,
                                                     ANTHROPIC: True}


def test_a_configured_model_is_not_forced_onto_a_second_provider(
        no_keys, monkeypatch):
    """clips.llm_model names a model for ONE provider. Passing
    'gemini-flash-latest' to OpenAI would report its key as broken."""
    from autoreel.llm_highlights import check_all

    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("OPENAI_API_KEY", "o")
    seen = []
    monkeypatch.setattr(
        llm, "check",
        lambda provider="", model="": (seen.append(model), (True, provider))[1])

    check_all(model="gemini-flash-latest")

    assert seen == ["", ""], "a provider-specific model name was reused"


# ── talking to Cerebras ──────────────────────────────────────────────
#
# Cerebras is OpenAI-shaped, but its models are REASONING models: they
# spend completion tokens on a `reasoning` field before writing any
# `content`. Measured on the live API with max_tokens=10 the reply was
#
#   {"finish_reason":"length","message":{"reasoning":"The user asks: ..."}}
#
# with no `content` key at all. Every test here exists because of that.

def test_cerebras_goes_after_gemini_and_before_the_paid_ones(no_keys):
    """Gemini sees frames; Cerebras is fast and free. The paid backstops
    come last."""
    no_keys.setenv("GEMINI_API_KEY", "g")
    no_keys.setenv("CEREBRAS_API_KEY", "c")
    no_keys.setenv("OPENAI_API_KEY", "o")
    no_keys.setenv("ANTHROPIC_API_KEY", "a")

    assert [p for p, _ in all_available()] == [GEMINI, CEREBRAS, OPENAI,
                                               ANTHROPIC]


def test_cerebras_has_its_own_caller():
    assert asker_for(CEREBRAS) is llm._ask_cerebras


def test_cerebras_asks_for_a_budget_it_can_think_inside(monkeypatch):
    """A reasoning model needs room to think before it writes anything.
    At OpenAI's usual 5-token ceiling it returns no content at all."""
    sent = {}

    def note(url, payload, headers):
        sent["url"] = url
        sent["payload"] = payload
        sent["headers"] = headers
        return {"choices": [{"message": {"content": GOOD}}]}

    monkeypatch.setattr(llm, "_post", note)

    assert llm._ask_cerebras("k", "gpt-oss-120b", "prompt") == GOOD
    assert sent["url"] == "https://api.cerebras.ai/v1/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer k"
    assert sent["payload"]["max_tokens"] >= 1024, \
        "too small for a reasoning model to finish thinking and answer"
    assert sent["payload"]["response_format"] == {"type": "json_object"}
    assert sent["payload"]["messages"][0]["content"] == llm.SYSTEM_PROMPT


def test_a_reasoning_only_reply_is_empty_and_says_why(monkeypatch, capsys):
    """The dangerous case: a valid reply with no `content`. Read as "the
    model had no opinion" it silently hands the run to the next provider."""
    monkeypatch.setattr(llm, "_post", lambda *a, **k: {
        "choices": [{"finish_reason": "length",
                     "message": {"reasoning": "The user asks: Reply with..."}}]})

    assert llm._ask_cerebras("k", "gpt-oss-120b", "p") == ""
    assert "token ceiling" in capsys.readouterr().out


def test_a_reasoning_only_reply_is_not_reported_as_a_bad_key(
        no_keys, monkeypatch):
    """--check-llm must not call a working key broken because the model
    spent its budget thinking."""
    from autoreel.llm_highlights import check

    no_keys.setenv("CEREBRAS_API_KEY", "c")
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "gpt-oss-120b")
    monkeypatch.setattr(llm, "_post_detailed", lambda *a, **k: ({
        "choices": [{"finish_reason": "length",
                     "message": {"reasoning": "thinking..."}}]}, ""))

    ok, detail = check()

    assert not ok
    assert "reasoning" in detail
    assert "rejected the key" not in detail


def test_a_cerebras_models_endpoint_is_asked_for_what_the_key_reaches(
        monkeypatch):
    """Its catalogue is small and turns over, so the module's rule -
    ask the provider rather than pin a name - applies here too."""
    monkeypatch.setattr(llm, "_list_models_openai_style",
                        lambda url, key: ["gpt-oss-120b", "qwen-3.8-27b"])

    assert llm.list_models(CEREBRAS, "c") == ["gpt-oss-120b", "qwen-3.8-27b"]


def test_the_no_key_message_is_derived_from_the_key_table(no_keys):
    """It named three providers and stayed at three after a fourth was
    added, so the operator was told to look for the wrong list."""
    names = llm._all_key_names()

    for provider in llm.PROVIDER_ORDER:
        for var in llm._KEY_NAMES[provider]:
            assert var in names, f"{var} missing from the no-key message"
