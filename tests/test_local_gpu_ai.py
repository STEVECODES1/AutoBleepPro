"""The model on this PC's GPU (autoreel/local_llm), and the rule that a
paid account is never used without ALLOW_PAID_AI."""
import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from autoreel import llm_highlights as H  # noqa: E402
from autoreel import local_llm  # noqa: E402


def _keys(monkeypatch, *names):
    for name in names:
        monkeypatch.setenv(name, "k-" + name.lower())


def test_a_paid_key_is_not_permission_to_spend(monkeypatch):
    monkeypatch.delenv("ALLOW_PAID_AI", raising=False)
    _keys(monkeypatch, "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
          "DEEPSEEK_API_KEY", "GROQ_API_KEY")
    names = [p for p, _ in H.all_available()]
    assert names == [H.GROQ]
    # Not even when config names it as the one to use.
    assert [p for p, _ in H.all_available(H.ANTHROPIC)] == [H.GROQ]


def test_allowing_paid_brings_them_back_after_the_free_ones(monkeypatch):
    monkeypatch.setenv("ALLOW_PAID_AI", "1")
    _keys(monkeypatch, "ANTHROPIC_API_KEY", "GROQ_API_KEY")
    assert [p for p, _ in H.all_available()] == [H.GROQ, H.ANTHROPIC]


def test_the_gpu_comes_right_after_gemini(monkeypatch):
    monkeypatch.delenv("ALLOW_PAID_AI", raising=False)
    monkeypatch.setattr(local_llm, "ready", lambda model="": True)
    _keys(monkeypatch, "GEMINI_API_KEY", "NVIDIA_API_KEY")
    assert [p for p, _ in H.all_available()] == [H.GEMINI, H.LOCAL, H.NVIDIA]
    assert H.LOCAL in H.VISION_PROVIDERS
    assert H.vision_asker_for(H.LOCAL) is not None


def test_no_ollama_no_gpu_provider(monkeypatch):
    monkeypatch.setattr(local_llm, "ready", lambda model="": False)
    assert H.api_key(H.LOCAL) == ""


def test_the_gpu_model_is_its_own_setting(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    assert H.resolve_model(H.LOCAL, "local", "gemini-3.5-flash") == \
        local_llm.DEFAULT_MODEL
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3-vl:8b")
    assert H.resolve_model(H.LOCAL, "local") == "qwen3-vl:8b"


def test_frames_reach_the_gpu_marked_by_candidate(monkeypatch):
    seen = {}

    def chat(system, prompt, model="", images=None, **kw):
        seen.update(system=system, prompt=prompt, images=images)
        return '{"clips": []}', ""

    monkeypatch.setattr(local_llm, "chat", chat)
    parts = []
    for n in range(1, 25):
        parts += [{"text": f"[{n}] candidate"},
                  {"inline_data": {"mime_type": "image/jpeg", "data": f"img{n}"}}]
    reply, why = H._ask_local_vision("local", "qwen3-vl:4b", parts)
    assert reply == '{"clips": []}' and why == ""
    # Thinned to what a 4B model on 8 GB is given, one per candidate.
    assert len(seen["images"]) == H.images_allowed(H.LOCAL) == 16
    assert "[image 1]" in seen["prompt"]
    assert seen["system"].startswith(H.SYSTEM_PROMPT[:40])


class _Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_request_asks_for_json_and_no_thinking(monkeypatch):
    sent = []

    def urlopen(request, timeout=None):
        sent.append(json.loads(request.data))
        return _Reply(json.dumps({"message": {"content": '{"ok":1}'}}).encode())

    monkeypatch.setattr(local_llm.urllib.request, "urlopen", urlopen)
    text, why = local_llm.chat("sys", "hi", "qwen3-vl:4b", images=["b64"])
    assert (text, why) == ('{"ok":1}', "")
    body = sent[0]
    assert body["think"] is False and body["format"] == "json"
    assert body["messages"][1]["images"] == ["b64"]
    assert body["stream"] is False


def test_an_ollama_that_refuses_think_is_asked_again_without_it(monkeypatch):
    sent = []

    def urlopen(request, timeout=None):
        body = json.loads(request.data)
        sent.append(body)
        if "think" in body:
            raise urllib.error.HTTPError(
                request.full_url, 400, "bad", {},
                io.BytesIO(b'{"error":"think value not supported"}'))
        return _Reply(json.dumps({"message": {"content": "ok"}}).encode())

    monkeypatch.setattr(local_llm.urllib.request, "urlopen", urlopen)
    assert local_llm.chat("s", "p") == ("ok", "")
    assert "think" not in sent[-1]


def test_ready_reads_the_pulled_models(monkeypatch):
    # The conftest stand-in is replaced by the real check here.
    import importlib

    real = importlib.reload(local_llm)
    monkeypatch.setattr(real, "_get", lambda path, timeout=2.0: {
        "models": [{"name": "qwen3-vl:4b"}, {"name": "llama3.2:latest"}]})
    monkeypatch.delenv("OLLAMA_DISABLED", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    assert real.ready() is True
    real._STATE["checked"] = 0.0
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2")
    assert real.ready() is True
    real._STATE["checked"] = 0.0
    monkeypatch.setenv("OLLAMA_DISABLED", "1")
    assert real.ready() is False
    real._STATE["checked"] = 0.0
