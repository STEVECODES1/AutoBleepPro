"""--gpu-check reported OK on the machine that could not transcribe.

It loaded the model and stopped there. ctranslate2 opens its CUDA
libraries LAZILY - the first real decode is what needs cuBLAS - so a
machine with the driver but without a loadable runtime constructs
WhisperModel perfectly and then dies mid-transcript:

    [OK] Loaded on CUDA in 4s using float16 precision.     <- the check
    ...
    [Queue] '...' failed: Library cublas64_12.dll is not
            found or cannot be loaded                      <- the real run

A check that passes on a broken machine is worse than no check: it moves
the search somewhere else. It decodes two seconds of audio now, through
the same path a real censor pass takes.

The probe is a TONE, not silence. The transcriber runs with VAD on and
silence is trimmed before it reaches the model - which would skip the
decode this exists to perform, and pass for the same reason as before.
"""

from __future__ import annotations

import os
import sys
import wave

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def _source() -> str:
    with open(os.path.join(_REPO, "auto_uploader", "main.py"),
              encoding="utf-8") as handle:
        return handle.read()


def _probe_maker():
    """The helper on its own, without importing the whole uploader."""
    source = _source()
    start = source.index("def _gpu_probe_wav")
    end = source.index("def _build_string")
    namespace = {"os": os}
    exec(compile(source[start:end], "main.py", "exec"), namespace)
    return namespace["_gpu_probe_wav"]


# ── the probe audio ──────────────────────────────────────────────────────

def test_the_probe_is_real_audio_not_silence():
    """Silence is trimmed by the VAD before the model sees it, so a silent
    probe would skip the decode and pass on a broken machine - the exact
    failure this replaced."""
    path = _probe_maker()()
    try:
        with wave.open(path) as handle:
            frames = handle.readframes(handle.getnframes())
    finally:
        os.remove(path)

    loudest = max(int.from_bytes(frames[i:i + 2], "little", signed=True)
                  for i in range(0, len(frames), 2))
    assert loudest > 10_000, "the probe is silent"


def test_the_probe_is_long_enough_to_decode():
    path = _probe_maker()()
    try:
        with wave.open(path) as handle:
            seconds = handle.getnframes() / handle.getframerate()
            assert handle.getframerate() == 16_000
            assert handle.getnchannels() == 1
    finally:
        os.remove(path)

    assert seconds >= 1.0


def test_the_probe_is_cleaned_up_even_when_the_decode_fails():
    source = _source()
    spot = source.index("Transcribing two seconds of audio")
    block = source[spot:spot + 900]

    assert "finally:" in block
    assert "os.remove(probe)" in block


# ── what the check actually does ─────────────────────────────────────────

def test_it_decodes_rather_than_only_loading():
    source = _source()
    spot = source.index("[OK] Loaded on")
    after = source[spot:spot + 1500]

    assert "transcriber.transcribe(probe)" in after


def test_a_decode_failure_is_a_failed_check():
    """It used to return 0 in this case, so the check said the GPU was
    fine and the next real run disagreed."""
    source = _source()
    spot = source.index("Loaded, but could not decode")

    assert "return 1" in source[spot:spot + 120]


def test_it_notices_a_fallback_that_happened_during_decoding():
    """Loading on CUDA and finishing on CPU is the lazy CUDA load failing,
    and it is a different problem from never reaching the GPU at all."""
    source = _source()

    assert "fell back to the CPU" in source
    assert "lazy CUDA load failing" in source


def test_it_does_not_advise_installing_what_is_already_installed():
    """The user ran that command and pip answered "Requirement already
    satisfied" three times while the DLLs stayed unreachable."""
    source = _source()
    spot = source.index("cuda_dll_directories()")
    after = source[spot:spot + 800]

    assert "are installed" in after
    assert "driver" in after
