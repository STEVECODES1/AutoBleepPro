"""A provider that cannot answer today is not asked on every clip."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from autoreel import llm_highlights as H  # noqa: E402

OK = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
SPENT = ("HTTP 429: You exceeded your current quota, please check your plan "
         "and billing details. Quota exceeded for metric: generate_content_"
         "free_tier_requests, limit: 20. Please retry in 44m4.48s.")


def _model(url):
    return url.split("/models/")[1].split(":")[0]


def _lite(monkeypatch):
    monkeypatch.setattr(H, "resolve_lite_model",
                        lambda key, avoid="": "gemini-3.5-flash-lite")
    monkeypatch.setattr(H, "_BUSY_RETRY_SECONDS", 0)


def test_a_spent_model_is_skipped_until_its_quota_is_back(monkeypatch):
    _lite(monkeypatch)
    asked = []

    def post(url, payload, headers, timeout=None):
        asked.append(_model(url))
        return (OK, "") if "lite" in url else (None, SPENT)

    monkeypatch.setattr(H, "_post_detailed", post)
    assert H._ask_gemini("k", "gemini-3.8-flash", "p") == "ok"
    assert asked == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
    asked.clear()
    assert H._ask_gemini("k", "gemini-3.8-flash", "p") == "ok"
    assert asked == ["gemini-3.5-flash-lite"]
    # The vision call shares what was learned.
    asked.clear()
    assert H._ask_gemini_vision("k", "gemini-3.8-flash", [{"text": "x"}]) \
        == ("ok", "")
    assert asked == ["gemini-3.5-flash-lite"]
    # Back when Google said it would be.
    monkeypatch.setattr(H, "_clock", lambda: time.time() + 45 * 60)
    asked.clear()
    H._ask_gemini("k", "gemini-3.8-flash", "p")
    assert asked[0] == "gemini-3.8-flash"


def test_busy_then_out_of_quota_still_reaches_flash_lite(monkeypatch):
    _lite(monkeypatch)
    replies = iter([(None, "HTTP 503: This model is currently experiencing "
                           "high demand."), (None, SPENT), (OK, "")])
    asked = []

    def post(url, payload, headers, timeout=None):
        asked.append(_model(url))
        return next(replies)

    monkeypatch.setattr(H, "_post_detailed", post)
    assert H._ask_gemini_vision("k", "gemini-3.8-flash", [{"text": "x"}]) \
        == ("ok", "")
    assert asked == ["gemini-3.8-flash", "gemini-3.8-flash",
                     "gemini-3.5-flash-lite"]


def test_an_empty_account_is_not_asked_for_the_words_too(monkeypatch):
    spoke = []
    monkeypatch.setattr(H, "resolve_model", lambda p, k, m="": "m")
    monkeypatch.setattr(H, "build_vision_contents",
                        lambda looking, count, source, **kw:
                        [{"inline_data": {"data": "x"}}])
    monkeypatch.setattr(H, "vision_asker_for", lambda provider: (
        lambda key, model, parts: ("", "HTTP 400: Your credit balance is "
                                       "too low to access the API.")))
    monkeypatch.setattr(H, "asker_for", lambda provider: (
        lambda key, model, prompt: spoke.append(prompt) or ""))
    shortlist = [object(), object()]
    picked, _ = H._ask_one_provider(H.ANTHROPIC, "k", "", shortlist, 1,
                                    "stream.mp4", None)
    assert picked == [] and spoke == []
    assert H._resting_for_credit(H.ANTHROPIC)
