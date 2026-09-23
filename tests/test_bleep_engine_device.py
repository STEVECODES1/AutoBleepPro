"""
The GUI app transcribing on CPU with a working GPU sitting idle.

"why this slow and the streams are faster? are they not using the same
setting?" - the streams (auto_uploader) transcribe a multi-hour VOD in
under six minutes on CUDA. This app's own transcription showed a
progress bar reading "22:23:30" remaining for the same shape of job -
CPU speed.

bleep_engine.detect_device() was a plain copy of an OLDER version of
autoreel/transcription.py's own detect_device() - the one that asked
only torch whether a GPU exists, before that file was fixed to also ask
ctranslate2 (the library faster-whisper - this app's preferred backend -
actually runs on). The comment left on that fix says exactly what
happens when it is missing: "that machine would have transcribed every
stream on its CPU while owning a GPU that worked, and nothing would
have looked wrong." bleep_engine.py never received the same fix, so the
GUI kept the exact bug the CLI tool already found and fixed once,
in a different file.

It also never called register_cuda_dlls() - the fix for a separate,
related problem (pip installs the CUDA libraries under site-packages;
the Windows loader searches PATH, not site-packages) that
autoreel/transcription.py already solved. Reused here rather than
re-solved.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

import bleep_engine  # noqa: E402


@pytest.fixture
def no_torch(monkeypatch):
    """The common real shape of the bug: torch present but reporting no
    CUDA (a CPU-only wheel), which is exactly what a packaged/frozen
    build can end up bundling even when the machine has a working GPU."""
    fake_torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False))
    monkeypatch.setattr(bleep_engine, "torch", fake_torch)
    return fake_torch


@pytest.fixture
def no_dll_registration(monkeypatch):
    """Isolate detect_device() from whatever the real autoreel.transcription
    module would actually do in this test environment."""
    monkeypatch.setattr(
        "autoreel.transcription.register_cuda_dlls", lambda: [])


def test_torch_reporting_no_cuda_is_not_the_final_answer(
        no_torch, no_dll_registration, monkeypatch):
    """The exact bug: torch says no, ctranslate2 says yes - the old code
    stopped at torch and reported CPU regardless."""
    fake_ct2 = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)

    device, label = bleep_engine.detect_device()

    assert device == "cuda"
    assert "ctranslate2" in label


def test_neither_backend_sees_a_gpu_is_a_real_cpu_fallback(
        no_torch, no_dll_registration, monkeypatch):
    fake_ct2 = types.SimpleNamespace(get_cuda_device_count=lambda: 0)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)

    device, label = bleep_engine.detect_device()

    assert device == "cpu"
    assert "CPU" in label


def test_ctranslate2_not_installed_falls_back_to_cpu_not_a_crash(
        no_torch, no_dll_registration, monkeypatch):
    monkeypatch.setitem(sys.modules, "ctranslate2", None)

    device, _label = bleep_engine.detect_device()

    assert device == "cpu"


def test_the_dll_registration_is_attempted_before_the_gpu_check(
        no_torch, monkeypatch):
    """Must run before ctranslate2 is asked - it resolves its CUDA
    libraries as it is imported, and a directory added afterwards is
    too late. Confirmed here by call order, not just that it happens."""
    calls = []
    monkeypatch.setattr("autoreel.transcription.register_cuda_dlls",
                        lambda: calls.append("registered"))

    def checked(*, _calls=calls):
        _calls.append("checked-cuda")
        return 0

    fake_ct2 = types.SimpleNamespace(get_cuda_device_count=checked)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)

    bleep_engine.detect_device()

    assert calls == ["registered", "checked-cuda"]


def test_a_missing_transcription_module_does_not_break_detection(
        no_torch, monkeypatch):
    """Best-effort: this project's own autoreel package might not be
    importable from wherever this app is actually packaged/run - that
    must cost the DLL registration, not the whole device check."""
    monkeypatch.setitem(sys.modules, "autoreel", None)
    monkeypatch.setitem(sys.modules, "autoreel.transcription", None)
    fake_ct2 = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)

    device, _label = bleep_engine.detect_device()

    assert device == "cuda"


def test_landing_on_cpu_prints_a_loud_warning_not_a_routine_line(
        no_torch, no_dll_registration, monkeypatch, capsys):
    """The exact complaint: the device was technically shown, buried in
    a routine status line nobody was watching for it. A CPU fallback
    must say so unmistakably, at the moment the model is loaded."""
    monkeypatch.setitem(sys.modules, "ctranslate2", None)
    monkeypatch.setattr(bleep_engine, "SPEED_MODE", False)
    monkeypatch.setattr(bleep_engine, "stable_whisper", None)

    class _FakeOpenAIWhisper:
        @staticmethod
        def load_model(name, device):
            return object()

    monkeypatch.setattr(bleep_engine, "openai_whisper", _FakeOpenAIWhisper)

    bleep_engine.load_model_speed("tiny", "auto")

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "CPU" in out


def test_landing_on_cuda_prints_no_such_warning(
        no_torch, no_dll_registration, monkeypatch, capsys):
    fake_ct2 = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ct2)
    monkeypatch.setattr(bleep_engine, "SPEED_MODE", False)
    monkeypatch.setattr(bleep_engine, "stable_whisper", None)

    class _FakeOpenAIWhisper:
        @staticmethod
        def load_model(name, device):
            return object()

    monkeypatch.setattr(bleep_engine, "openai_whisper", _FakeOpenAIWhisper)

    bleep_engine.load_model_speed("tiny", "auto")

    assert "WARNING" not in capsys.readouterr().out
