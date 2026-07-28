"""Thin wrapper around Whisper for word-level transcription.

Kept separate so the rest of AutoReel can be tested against plain
transcript dicts without needing whisper (or its model downloads)
installed.
"""

from dataclasses import dataclass


@dataclass
class Transcriber:
    model_name: str = "base"

    def transcribe(self, audio_path: str) -> dict:
        """Return a Whisper-style result: {'segments': [...]}, each
        segment carrying word-level timestamps."""
        import whisper

        model = whisper.load_model(self.model_name)
        return model.transcribe(audio_path, word_timestamps=True)
