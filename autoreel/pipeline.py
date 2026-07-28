"""
AutoReelPipeline: end-to-end orchestration.

    Full Stream -> transcribe -> compliance pass (bleep) -> highlight
    selection -> vertical clip rendering -> supervisor report

Each stage is a small, independently-testable class (Transcriber,
ComplianceEngine, HighlightScorer, ClipRenderer); this module just wires
them together and produces a human-readable summary of what it did.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

from .clipper import ClipRenderer
from .compliance import ComplianceEngine, Violation
from .highlights import Highlight, HighlightScorer
from .transcription import Transcriber, detect_device
from .utils import format_timestamp


@dataclass
class SupervisorReport:
    source_path: str
    violations: list[Violation]
    clips: list[Highlight]
    clip_paths: list[str]
    output_video_path: str = ""
    device_used: str = ""
    censored: bool = True

    @property
    def is_kid_friendly(self) -> bool:
        return len(self.violations) == 0

    def to_markdown(self) -> str:
        if self.is_kid_friendly:
            status = "✅ PASS"
        elif self.censored:
            status = "⚠️ FAIL (censored)"
        else:
            status = "❌ FAIL (censoring disabled — profanity left in audio)"

        lines = [
            "# AutoReel Supervisor Report",
            "",
            f"**Source:** `{os.path.basename(self.source_path)}`",
            f"**Kid-Friendly:** {status}",
        ]
        if self.device_used:
            lines.append(f"**Transcribed on:** {self.device_used}")
        lines += [
            "",
            f"## Compliance Pass ({len(self.violations)} flagged word{'s' if len(self.violations) != 1 else ''})",
        ]

        if self.violations:
            for v in sorted(self.violations, key=lambda x: x.start):
                lines.append(f"- [{format_timestamp(v.start)}] '{v.word}' — {v.category}")
        else:
            lines.append("- No violations found.")

        lines += ["", f"## Generated Clips ({len(self.clips)})"]
        for i, (clip, path) in enumerate(zip(self.clips, self.clip_paths), start=1):
            duration = clip.end - clip.start
            lines.append(
                f"{i}. [{format_timestamp(clip.start)} - {format_timestamp(clip.end)}] "
                f"({duration:.0f}s, score {clip.score:.1f}) -> `{os.path.basename(path)}`"
            )
            if clip.text:
                lines.append(f"   \"{clip.text.strip()}\"")

        return "\n".join(lines) + "\n"


@dataclass
class AutoReelPipeline:
    output_dir: str
    model_name: str = "base"
    bleep_method: str = "beep"
    custom_words: tuple[str, ...] = ()
    num_clips: int = 3
    clip_min_duration: float = 15.0
    clip_max_duration: float = 60.0
    device: Optional[str] = None  # None = auto-detect (GPU if available, else CPU)
    censor_profanity: bool = True  # False = detect and report violations but leave audio untouched

    transcriber: Transcriber = field(init=False)
    compliance_engine: ComplianceEngine = field(init=False)
    highlight_scorer: HighlightScorer = field(init=False)
    clip_renderer: ClipRenderer = field(init=False)

    def __post_init__(self) -> None:
        self.transcriber = Transcriber(model_name=self.model_name, device=self.device)
        self.compliance_engine = ComplianceEngine(custom_words=self.custom_words)
        self.highlight_scorer = HighlightScorer(
            min_duration=self.clip_min_duration, max_duration=self.clip_max_duration
        )
        self.clip_renderer = ClipRenderer(output_dir=self.output_dir)

    def run(self, source_path: str) -> SupervisorReport:
        os.makedirs(self.output_dir, exist_ok=True)

        from moviepy import AudioFileClip, VideoFileClip
        from pydub import AudioSegment

        device, device_label = (self.device, "") if self.device else detect_device()
        device_used = f"{device.upper()} ({device_label})" if device_label else device.upper()

        video = VideoFileClip(source_path)
        try:
            audio_path = os.path.join(self.output_dir, "_autoreel_audio.wav")
            video.audio.write_audiofile(audio_path, logger=None)

            result = self.transcriber.transcribe(audio_path)
            segments = result["segments"]

            violations = self.compliance_engine.scan_segments(segments)

            cleaned_audio_path = os.path.join(self.output_dir, "_autoreel_audio_clean.wav")
            output_video_path = os.path.join(
                self.output_dir, f"{self._basename(source_path)}_CLEAN.mp4"
            )

            if violations and self.censor_profanity:
                audio_segment = AudioSegment.from_wav(audio_path)
                censored = self.compliance_engine.censor_audio(
                    audio_segment, violations, method=self.bleep_method
                )
                censored.export(cleaned_audio_path, format="wav")

                cleaned_audio = AudioFileClip(cleaned_audio_path)
                compliant_video = video.with_audio(cleaned_audio)
                compliant_video.write_videofile(
                    output_video_path, codec="libx264", audio_codec="aac", logger=None
                )
                compliant_video.close()
                cleaned_audio.close()
                render_source = output_video_path
            else:
                render_source = source_path

            clips = self.highlight_scorer.select_clips(segments, count=self.num_clips)
            clip_paths = self.clip_renderer.render_all(
                render_source, clips, prefix=self._basename(source_path)
            )
        finally:
            video.close()
            for temp_file in ("_autoreel_audio.wav", "_autoreel_audio_clean.wav"):
                temp_path = os.path.join(self.output_dir, temp_file)
                if os.path.exists(temp_path):
                    os.remove(temp_path)

        was_censored = bool(violations) and self.censor_profanity
        return SupervisorReport(
            source_path=source_path,
            violations=violations,
            clips=clips,
            clip_paths=clip_paths,
            output_video_path=output_video_path if was_censored else source_path,
            device_used=device_used,
            censored=self.censor_profanity,
        )

    @staticmethod
    def _basename(path: str) -> str:
        return os.path.splitext(os.path.basename(path))[0]
