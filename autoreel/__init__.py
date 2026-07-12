"""
AutoReel - AI Video Post-Production Supervisor

Takes a long-form "Full Stream" video, makes the audio compliant with
YouTube's Terms of Service / "Kid-Friendly" standards, and cuts short,
highlight-driven clips formatted for Instagram Reels and TikTok.
"""

from .pipeline import AutoReelPipeline, SupervisorReport

__all__ = ["AutoReelPipeline", "SupervisorReport"]
