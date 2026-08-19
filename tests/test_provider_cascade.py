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
from autoreel.llm_highlights import (ANTHROPIC, GEMINI, OPENAI,  # noqa: E402
                                     all_available, asker_for, rank)

GOOD = '{"clips":[{"index":1,"score":90,"title":"picked"}]}'


@pytest.fixture
def no_keys(monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
                 "ANTHROPIC_API_KEY"):
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
