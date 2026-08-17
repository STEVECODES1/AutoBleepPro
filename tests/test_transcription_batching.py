"""Keeping the GPU fed.

faster-whisper's ordinary path sends one 30-second window through at a
time, which leaves most of a GPU idle between windows. Its batched
pipeline decodes several at once and is the largest speed-up available on
a machine with an NVIDIA card - and transcription is the slowest stage in
the whole pipeline, so it is the one worth having.

What must stay true whatever the speed:
  * a transcript still comes back when batching is unavailable, refuses,
    or runs out of VRAM - a missing speed-up is not a failed censor pass
  * the words still carry their timestamps, because those are what the
    mute lands on
  * nothing changes on a CPU-only machine
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel import transcription  # noqa: E402
from autoreel.transcription import BACKEND_FASTER, Transcriber  # noqa: E402


class FakeInfo:
    language = "en"


class FakeWord:
    def __init__(self, word, start, end):
        self.word = word
        self.start = start
        self.end = end
        self.probability = 0.9


class FakeSegment:
    id = 0
    start = 0.0
    end = 1.0
    text = " hello there"

    def __init__(self):
        self.words = [FakeWord(" hello", 0.0, 0.4),
                      FakeWord(" there", 0.4, 1.0)]


class FakeModel:
    """The sequential path."""

    def __init__(self):
        self.calls = []

    def transcribe(self, audio_path, **kwargs):
        self.calls.append(kwargs)
        return iter([FakeSegment()]), FakeInfo()


class FakeBatched:
    def __init__(self, fails=None):
        self.calls = []
        self._fails = fails

    def transcribe(self, audio_path, **kwargs):
        self.calls.append(kwargs)
        if self._fails:
            raise self._fails
        return iter([FakeSegment()]), FakeInfo()


class LazilyFailingBatched:
    """Raises while the iterator is CONSUMED, not when it is handed over.

    This is how a batch too big for VRAM actually fails, and a try that
    only wrapped the call would let it through uncaught.
    """

    def transcribe(self, audio_path, **kwargs):
        def explode():
            raise RuntimeError("CUDA out of memory")
            yield  # pragma: no cover

        return explode(), FakeInfo()


def _on_gpu(batched=None, batch_size=8) -> Transcriber:
    t = Transcriber(model_name="base", device="cuda", backend=BACKEND_FASTER,
                    batch_size=batch_size)
    t._model = FakeModel()
    t._resolved_device = "cuda"
    t._batch = batched
    return t


def test_the_gpu_decodes_a_batch_at_a_time():
    batched = FakeBatched()
    result = _on_gpu(batched).transcribe("a.wav")

    assert len(batched.calls) == 1
    assert batched.calls[0]["batch_size"] == 8
    assert result["segments"][0]["words"][0]["word"] == " hello"


def test_batching_does_not_cost_the_word_timings():
    """They are what the mute lands on - a fast transcript without them
    is worse than a slow one with them."""
    result = _on_gpu(FakeBatched()).transcribe("a.wav")

    words = result["segments"][0]["words"]
    assert [w["start"] for w in words] == [0.0, 0.4]
    assert [w["end"] for w in words] == [0.4, 1.0]


def test_the_verbatim_prompt_survives_batching():
    """Whisper cleans up swearing unless asked not to, and the whole
    censor pass is built on it not doing that."""
    batched = FakeBatched()
    _on_gpu(batched).transcribe("a.wav")

    assert batched.calls[0]["initial_prompt"] == transcription.VERBATIM_PROMPT
    assert batched.calls[0]["word_timestamps"] is True


def test_running_out_of_vram_still_returns_a_transcript():
    speaker = _on_gpu(LazilyFailingBatched())

    result = speaker.transcribe("a.wav")

    assert result["segments"][0]["words"], "the censor pass got nothing"
    assert speaker._model.calls, "it never fell back to the sequential path"


def test_an_older_faster_whisper_is_not_a_failure(monkeypatch):
    """BatchedInferencePipeline arrived in 1.1. Older installs must keep
    working, just at the old speed."""
    monkeypatch.setattr(transcription, "_batched", lambda model: None)
    speaker = Transcriber(model_name="base", device="cuda",
                          backend=BACKEND_FASTER)
    speaker._model = FakeModel()
    speaker._resolved_device = "cuda"

    result = speaker.transcribe("a.wav")

    assert result["segments"][0]["text"] == " hello there"


def test_a_batch_that_refuses_is_not_retried_on_the_next_file():
    """One slow fallback per run, not one per clip."""
    speaker = _on_gpu(FakeBatched(fails=RuntimeError("no")))

    speaker.transcribe("a.wav")
    speaker.transcribe("b.wav")

    assert speaker._batch is None
    assert len(speaker._model.calls) == 2


def test_the_sequential_path_still_refuses_to_condition_on_itself():
    """One bad window making the next worse is how whole minutes came
    back as repeated filler."""
    speaker = _on_gpu(None)

    speaker.transcribe("a.wav")

    assert speaker._model.calls[0]["condition_on_previous_text"] is False


def _loaded_on(device, monkeypatch, batch_size=8):
    """Run the real _load() against a stand-in faster_whisper."""
    import types

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = lambda *a, **k: FakeModel()
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    made = []
    monkeypatch.setattr(transcription, "_batched",
                        lambda model: made.append(model) or FakeBatched())

    speaker = Transcriber(model_name="base", device=device,
                          backend=BACKEND_FASTER, batch_size=batch_size)
    speaker._load()
    return speaker, made


def test_a_gpu_gets_the_batched_pipeline(monkeypatch):
    speaker, made = _loaded_on("cuda", monkeypatch)

    assert made, "the GPU was left decoding one window at a time"
    assert speaker._batch is not None


def test_a_cpu_machine_is_left_alone(monkeypatch):
    """Batching costs RAM there for very little, and the CPU path is the
    fallback everything else depends on."""
    speaker, made = _loaded_on("cpu", monkeypatch)

    assert not made, "batching was set up on a CPU-only machine"
    assert speaker._batch is None


def test_turning_the_batch_size_down_skips_the_pipeline(monkeypatch):
    speaker, made = _loaded_on("cuda", monkeypatch, batch_size=1)

    assert not made
    assert speaker._batch is None


def test_batching_can_be_turned_off():
    speaker = Transcriber(model_name="base", device="cuda",
                          backend=BACKEND_FASTER, batch_size=1)
    speaker._model = FakeModel()
    speaker._resolved_device = "cuda"

    speaker.transcribe("a.wav")

    assert speaker._model.calls, "it should have used the sequential path"


def test_releasing_drops_the_batch_too():
    """It holds a reference to the model, so leaving it behind would keep
    the weights on the GPU after a release."""
    speaker = _on_gpu(FakeBatched())

    speaker.release()

    assert speaker._model is None
    assert speaker._batch is None
