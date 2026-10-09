"""Whose line is whose reaches the clip picker - and only the picker."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from autoreel.highlights import Highlight  # noqa: E402
from autoreel.llm_highlights import build_prompt  # noqa: E402
from autoreel.speaker_id import tagged  # noqa: E402


def test_lines_are_tagged_where_the_voice_changes():
    segs = [{"text": "yo", "speaker": "Stackswopo"}, {"text": "what", "speaker": "Stackswopo"},
            {"text": "nah", "speaker": "other person"}, {"text": "huh", "speaker": ""}]
    assert tagged(segs) == "STACKS: yo what THEM: nah ?: huh"


def test_the_picker_sees_who_said_it_and_is_told_what_the_tags_mean():
    h = Highlight(start=0, end=20, score=1, text="yo nah", said_by="STACKS: yo THEM: nah")
    prompt = build_prompt([h], 1, lessons=[], hits="", bible="")
    assert "STACKS: yo THEM: nah" in prompt
    assert "STACKS: is Stackswopo" in prompt


def test_without_voices_the_prompt_is_as_before():
    h = Highlight(start=0, end=20, score=1, text="yo nah")
    prompt = build_prompt([h], 1, lessons=[], hits="", bible="")
    assert "yo nah" in prompt and "marked by voice" not in prompt
