"""Word-level transcription, on the fastest backend available.

Two backends, same output shape:

- **faster-whisper** (CTranslate2) is preferred. It produces word
  timestamps without torch's dynamic-time-warping pass, which is the part
  of openai-whisper that tries to JIT a Triton kernel and, on a machine
  with no CUDA toolkit, prints

      UserWarning: Failed to launch Triton kernels ... falling back to a
      slower median kernel implementation

  once per call before doing the slow thing anyway. Skipping that path is
  both the fix for the warning and the largest speed win here: int8 on
  CPU runs several times faster than fp32 openai-whisper.
- **openai-whisper** is the fallback, so an existing install keeps
  working. On that path the Triton warning is silenced, because it
  reports a condition the user cannot act on (installing the CUDA
  toolkit on a GPU-less box is not a fix) and it fires per call.

The model is loaded ONCE per Transcriber and held. It used to be loaded
inside transcribe(), which quietly defeated the model cache in
auto_uploader/utils/censor.py: a 20-file batch paid the full load 20
times while believing it was reusing one model.

Kept separate from the rest of AutoReel so everything downstream can be
tested against plain transcript dicts with no model installed.
"""

from __future__ import annotations

import os
import warnings

# Set before huggingface_hub is imported by faster-whisper. It warns on
# every model load that Windows cannot symlink its cache without
# Developer Mode; the cache works either way, and the only "fix" is to
# turn on a Windows developer setting, which is not something a
# censoring tool should be asking for.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
from dataclasses import dataclass, field
from typing import Any, Optional

BACKEND_FASTER = "faster-whisper"
BACKEND_OPENAI = "openai-whisper"

# Whisper is trained on cleaned-up transcripts and will quietly sanitise
# swearing - writing "f***", softening a slur, or dropping it entirely.
# For a censoring tool that is the whole ballgame: a word the transcript
# never contains cannot be muted, and it reaches the upload untouched.
#
# initial_prompt is the documented lever. Whisper conditions the decode on
# it, so a prompt written in the register of the audio biases the model
# toward transcribing verbatim instead of tidying. It is not part of the
# output - it only shapes how the audio is read.
VERBATIM_PROMPT = (
    "The following is an unedited, verbatim gaming stream transcript. "
    "It contains explicit language, insults and swearing, transcribed "
    "exactly as spoken with no censoring, no asterisks and no omissions. "
    "Example: Oh shit, what the fuck was that, you damn idiot, holy crap."
)


def _has_faster_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def detect_device() -> tuple[str, str]:
    """Pick the fastest available device: NVIDIA GPU (CUDA) if present, else CPU.

    torch is optional - faster-whisper does not need it - so a missing
    torch means CPU rather than an ImportError.
    """
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda", torch.cuda.get_device_name(0)
    except Exception:
        pass
    return "cpu", f"{os.cpu_count()} CPU cores"


def default_compute_type(device: str) -> str:
    """int8 on CPU, float16 on GPU - the usual accuracy/speed trade.

    int8 costs very little accuracy for this job: the words being matched
    are profanity from a fixed list, and the timings only need to be good
    enough for a padded mute.
    """
    return "float16" if device == "cuda" else "int8"


def _normalise_faster_whisper(segments_iter, info) -> dict:
    """CTranslate2 Segment/Word objects -> the Whisper-shaped dict the
    rest of the codebase already consumes."""
    segments = []
    for seg in segments_iter:
        words = []
        for word in (getattr(seg, "words", None) or []):
            words.append({
                "word": word.word,
                "start": float(word.start),
                "end": float(word.end),
                "probability": float(getattr(word, "probability", 1.0) or 1.0),
            })
        segments.append({
            "id": getattr(seg, "id", len(segments)),
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text,
            "words": words,
        })
    return {
        "segments": segments,
        "text": "".join(s["text"] for s in segments),
        "language": getattr(info, "language", "") or "",
    }


@dataclass
class Transcriber:
    model_name: str = "base"
    device: Optional[str] = None       # None = auto-detect
    compute_type: Optional[str] = None  # None = int8 on CPU, float16 on GPU
    backend: Optional[str] = None      # None = faster-whisper if installed
    _model: Any = field(default=None, init=False, repr=False)
    _resolved_device: str = field(default="", init=False, repr=False)
    # What was ACTUALLY used, which is not always what was asked for.
    _resolved_compute: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        if self.backend is None:
            self.backend = BACKEND_FASTER if _has_faster_whisper() else BACKEND_OPENAI

    # ── Model loading (once) ─────────────────────────────────────────────

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        self._resolved_device = self.device or detect_device()[0]

        if self.backend == BACKEND_FASTER:
            from faster_whisper import WhisperModel

            compute = self.compute_type or default_compute_type(self._resolved_device)
            try:
                self._model = WhisperModel(
                    self.model_name, device=self._resolved_device,
                    compute_type=compute)
                self._resolved_compute = compute
            except ValueError:
                # Some builds refuse a compute type the hardware can't do
                # (no AVX2 -> no int8). Fall back rather than fail.
                self._model = WhisperModel(
                    self.model_name, device=self._resolved_device,
                    compute_type="default")
                self._resolved_compute = "default"
            except Exception as exc:
                # A GPU that cannot actually load the model - missing
                # cuDNN, a driver too old, or not enough VRAM for
                # large-v3 - must not end the run. Censoring on the CPU
                # slowly beats uploading uncensored audio, which is what
                # a crash here would leave: the caller falls back to the
                # original file.
                if self._resolved_device != "cuda":
                    raise
                print(f"[Transcribe] GPU unavailable for {self.model_name} "
                      f"({exc}). Falling back to CPU - this is much slower. "
                      "Install the CUDA runtime with: pip install "
                      "nvidia-cublas-cu12 nvidia-cudnn-cu12")
                self._resolved_device = "cpu"
                self._resolved_compute = default_compute_type("cpu")
                self._model = WhisperModel(
                    self.model_name, device="cpu",
                    compute_type=self._resolved_compute)
        else:
            import whisper

            self._model = whisper.load_model(
                self.model_name, device=self._resolved_device)
        return self._model

    def release(self) -> None:
        """Drop the loaded weights (frees GPU/RAM between long runs)."""
        self._model = None

    # ── Transcription ────────────────────────────────────────────────────

    def transcribe(self, audio_path: str, vad_filter: bool = True) -> dict:
        """Return a Whisper-style result: {'segments': [...]}, each
        segment carrying word-level timestamps.

        `vad_filter` is exposed ONLY so --check-sync can run the same
        audio both ways and compare. It cuts the silence out before
        transcribing and then maps the timestamps back afterwards, and
        that mapping is the most likely place for word timings to come
        back shifted - which is a caption that does not match the audio.
        Nothing in the normal path passes it.
        """
        model = self._load()

        if self.backend == BACKEND_FASTER:
            segments_iter, info = model.transcribe(
                audio_path, word_timestamps=True,
                # The default (0) disables VAD; trimming silence is a
                # straight speed win on stream VODs, which are mostly
                # gameplay audio with long gaps between speech.
                vad_filter=vad_filter,
                initial_prompt=VERBATIM_PROMPT,
                # Whisper otherwise feeds each window its own previous
                # output, and on hours of gameplay one bad window makes
                # the next worse - it loops or drifts, and whole minutes
                # come back as repeated filler with the real words gone.
                condition_on_previous_text=False,
                # A wider search costs time and finds words a greedy
                # decode drops. Missing a slur is more expensive here
                # than the extra minutes.
                beam_size=5)
            return _normalise_faster_whisper(segments_iter, info)

        with warnings.catch_warnings():
            # Unactionable and once per call: it fires because there is no
            # CUDA toolkit, which is not something to install on a
            # CPU-only machine. The fallback it announces still runs.
            warnings.filterwarnings(
                "ignore", message=".*Triton kernels.*", category=UserWarning)
            warnings.filterwarnings(
                "ignore", message=".*FP16 is not supported on CPU.*")
            return model.transcribe(
                audio_path, word_timestamps=True,
                initial_prompt=VERBATIM_PROMPT,
                condition_on_previous_text=False)
