"""
Claude as a clip/thumbnail model, through the Anthropic SDK.

The old path could not work on a current model: it sent `temperature`
(rejected with a 400) and read content[0], which is a thinking block now
that thinking is always on. Nothing here reaches the API.
"""

import os
import sys
from types import SimpleNamespace

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

anthropic = pytest.importorskip("anthropic")

from autoreel import llm_highlights as lh  # noqa: E402


class _FakeClient:
    calls = []
    reply = None

    def __init__(self, **kwargs):
        self.beta = SimpleNamespace(messages=self)

    def create(self, **kwargs):
        _FakeClient.calls.append(kwargs)
        return _FakeClient.reply


def _reply(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop,
                           stop_details=None)


@pytest.fixture
def fake(monkeypatch):
    _FakeClient.calls = []
    monkeypatch.setattr(anthropic, "Anthropic", _FakeClient)
    return _FakeClient


def test_the_text_is_read_past_the_thinking_block(fake):
    fake.reply = _reply(SimpleNamespace(type="thinking", thinking=""),
                        SimpleNamespace(type="text", text='{"frame": 3}'))

    text, why = lh._claude("k", "claude-opus-5-5", "sys", "hi")

    assert (text, why) == ('{"frame": 3}', "")
    call = fake.calls[0]
    assert "temperature" not in call            # a 400 on current models
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in call["betas"]


def test_a_refusal_is_a_reason_not_an_empty_answer(fake):
    fake.reply = _reply(stop="refusal")
    text, why = lh._claude("k", "m", "", "hi")
    assert text == "" and "declined" in why


def test_frames_become_claude_image_blocks():
    blocks = lh.to_claude_content([
        {"text": "pick one"},
        {"inline_data": {"mime_type": "image/jpeg", "data": "QUJD"}}])
    assert blocks[0] == {"type": "text", "text": "pick one"}
    assert blocks[1]["type"] == "image"
    assert blocks[1]["source"] == {"type": "base64",
                                   "media_type": "image/jpeg", "data": "QUJD"}


def test_claude_can_be_shown_the_frames():
    assert lh.ANTHROPIC in lh.VISION_PROVIDERS
    assert lh.vision_asker_for(lh.ANTHROPIC) is lh._ask_anthropic_vision
    assert lh.DEFAULT_MODELS[lh.ANTHROPIC] == "claude-opus-5-5"
    assert lh.resolve_model(lh.ANTHROPIC, "k") == "claude-opus-5-5"


def test_the_thumbnail_asks_the_next_model_when_gemini_is_busy(monkeypatch):
    """Gemini answering 503 all evening meant every thumbnail was a
    fallback frame, whatever else was configured."""
    from autoreel import thumbnail

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    asked = []

    def busy(key, model, parts):
        asked.append("gemini")
        return "", "HTTP 503: high demand"

    def claude(key, model, parts):
        asked.append("claude")
        return '{"frame": 2}', ""

    monkeypatch.setattr(lh, "vision_asker_for", lambda p: {
        lh.GEMINI: busy, lh.ANTHROPIC: claude}.get(p))
    monkeypatch.setattr(lh, "resolve_model", lambda p, k, c="": "m")

    assert thumbnail._choose([b"a", b"b", b"c"]) == 1
    assert asked == ["gemini", "claude"]


def test_a_preferred_provider_goes_first(monkeypatch):
    from autoreel import thumbnail

    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    asked = []
    monkeypatch.setattr(lh, "vision_asker_for", lambda p: (
        lambda k, m, parts: (asked.append(p) or '{"frame": 1}', "")))
    monkeypatch.setattr(lh, "resolve_model", lambda p, k, c="": "m")

    thumbnail._choose([b"a"], provider="anthropic")
    assert asked == ["anthropic"]
