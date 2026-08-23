"""A missing CUDA library killed a finished four-hour recording.

    [Timing] audio extract took 31.3s
    [Queue] 'REACTING TO BRANDRISK BOXING' ... failed:
            Library cublas64_12.dll is not found or cannot be loaded

The machine had a fresh Python with the optional CUDA runtime not
reinstalled. config.json forces censor_device "cuda", and there WAS a
fallback for that - but only around loading the model.

ctranslate2 opens its CUDA libraries lazily: the first real decode is
what needs cuBLAS. So WhisperModel() constructed perfectly, the audio was
extracted, and the failure arrived in the middle of transcribing, where
nothing was catching it. A 4 GB stream that had recorded successfully was
lost to an optional speed-up being absent.

A transcript produced slowly is the whole job. A GPU is an optimisation.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel import transcription  # noqa: E402
from autoreel.transcription import (BACKEND_FASTER, Transcriber,  # noqa: E402
                                    looks_like_a_gpu_problem)

CUBLAS = "Library cublas64_12.dll is not found or cannot be loaded"


class FakeInfo:
    language = "en"


class FakeWord:
    def __init__(self):
        self.word = " hello"
        self.start = 0.0
        self.end = 0.4
        self.probability = 0.9


class FakeSegment:
    id = 0
    start = 0.0
    end = 1.0
    text = " hello"

    def __init__(self):
        self.words = [FakeWord()]


class Model:
    """Fails while the transcript is being CONSUMED, which is how a
    lazily-loaded CUDA library actually fails."""

    def __init__(self, fails=None):
        self.fails = fails
        self.calls = 0

    def transcribe(self, audio_path, **kwargs):
        self.calls += 1
        if self.fails:
            def explode():
                raise RuntimeError(self.fails)
                yield  # pragma: no cover
            return explode(), FakeInfo()
        return iter([FakeSegment()]), FakeInfo()


# ── which failures are the GPU's ─────────────────────────────────────────

@pytest.mark.parametrize("message", [
    CUBLAS,
    "cudnn64_9.dll not found",
    "CUDA out of memory",
    "no kernel image is available for execution on the device",
    "libcublas.so.12: cannot open shared object file",
])
def test_a_cuda_failure_is_recognised(message):
    assert looks_like_a_gpu_problem(RuntimeError(message))


@pytest.mark.parametrize("message", [
    "unsupported compute type",
    "'segments'",
    "invalid literal for int() with base 10",
    "No such file or directory: 'audio.wav'",
])
def test_our_own_bugs_are_not_blamed_on_the_gpu(message):
    """Retrying a real bug on the CPU would hide it and produce the same
    failure twice as slowly."""
    assert not looks_like_a_gpu_problem(ValueError(message))


# ── the fallback ─────────────────────────────────────────────────────────

def _on_gpu(monkeypatch, first_fails=CUBLAS):
    made = []

    def load(self):
        model = Model(fails=first_fails if not made else None)
        made.append(self._resolved_device or self.device)
        self._model = model
        return model

    monkeypatch.setattr(Transcriber, "_load", load)
    speaker = Transcriber(model_name="base", device="cuda",
                          backend=BACKEND_FASTER)
    speaker._resolved_device = "cuda"
    return speaker, made


def test_a_mid_transcript_cuda_failure_still_produces_a_transcript(monkeypatch):
    speaker, _made = _on_gpu(monkeypatch)

    result = speaker.transcribe("a.wav")

    assert result["segments"][0]["words"], "the censor pass got nothing"


def test_the_retry_actually_moves_to_the_cpu(monkeypatch):
    speaker, made = _on_gpu(monkeypatch)

    speaker.transcribe("a.wav")

    assert speaker._resolved_device == "cpu"
    assert made[-1] == "cpu", f"reloaded on {made[-1]}, not the CPU"


def test_it_does_not_retry_forever(monkeypatch):
    """If the CPU path fails too, that is a real failure and must be
    raised - not retried until the run never ends."""
    speaker, _made = _on_gpu(monkeypatch, first_fails=CUBLAS)

    def always_fail(self):
        self._model = Model(fails=CUBLAS)
        return self._model

    monkeypatch.setattr(Transcriber, "_load", always_fail)

    with pytest.raises(RuntimeError):
        speaker.transcribe("a.wav")


def test_a_real_bug_is_raised_not_retried(monkeypatch):
    speaker, made = _on_gpu(monkeypatch, first_fails="'segments'")

    with pytest.raises(RuntimeError):
        speaker.transcribe("a.wav")

    assert speaker._resolved_device == "cuda", "it moved off the GPU for a bug"


def test_a_machine_already_on_the_cpu_does_not_pretend_to_fall_back(monkeypatch):
    speaker, _made = _on_gpu(monkeypatch)
    speaker._resolved_device = "cpu"
    speaker.device = "cpu"

    with pytest.raises(RuntimeError):
        speaker.transcribe("a.wav")


def test_the_batched_pipeline_is_dropped_with_the_gpu(monkeypatch):
    """It holds a reference to the model that just failed, and reusing it
    fails the same way."""
    speaker, _made = _on_gpu(monkeypatch)
    speaker._batch = object()

    speaker.transcribe("a.wav")

    assert speaker._batch is None


def test_it_says_what_happened_and_how_to_get_the_gpu_back(monkeypatch, capsys):
    speaker, _made = _on_gpu(monkeypatch)

    speaker.transcribe("a.wav")
    printed = capsys.readouterr().out

    assert "cublas" in printed.lower()
    assert "nvidia-cublas-cu12" in printed
    assert "slower" in printed.lower()
