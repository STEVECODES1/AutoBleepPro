"""Thin wrapper around Whisper for word-level transcription.

Kept separate so the rest of AutoReel can be tested against plain
transcript dicts without needing whisper (or its model downloads)
installed.
"""

import os
from dataclasses import dataclass
from typing import Optional


def detect_device() -> tuple[str, str]:
    """Pick the fastest available device: NVIDIA GPU (CUDA) if present, else CPU."""
    import torch

    if torch.cuda.is_available():
        return "cuda", torch.cuda.get_device_name(0)
    return "cpu", f"{os.cpu_count()} CPU cores"


@dataclass
class Transcriber:
    model_name: str = "base"
    device: Optional[str] = None  # None = auto-detect (GPU if available, else CPU)

    def transcribe(self, audio_path: str) -> dict:
        """Return a Whisper-style result: {'segments': [...]}, each
        segment carrying word-level timestamps."""
        import whisper

        device = self.device or detect_device()[0]
        model = whisper.load_model(self.model_name, device=device)
        return model.transcribe(audio_path, word_timestamps=True)
