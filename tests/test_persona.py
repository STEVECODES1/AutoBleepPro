"""GTA RP clips are about Johnny Cox, Stackswopo's character (owner 11-04)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from autoreel import persona  # noqa: E402


def test_a_gta_title_names_johnny_cox_and_keeps_stackswopo_once():
    t = persona.name_in_title("Stackswopo gets arrested at the bank", True)
    assert t.startswith("Johnny Cox gets arrested at the bank")
    assert t.endswith("| Stackswopo GTA RP")
    assert t.lower().count("stackswopo") == 1


def test_a_title_without_a_name_gets_johnny_cox_in_front():
    assert persona.name_in_title("Robbed at gunpoint | Stackswopo", True).startswith(
        "Johnny Cox: Robbed at gunpoint")


def test_non_gta_titles_are_left_alone():
    assert persona.name_in_title("Stackswopo reacts to a call", False) == \
        "Stackswopo reacts to a call"


def test_the_frames_decide_first_then_the_words(tmp_path):
    clip = str(tmp_path / "x.mp4")
    assert persona.is_gta_clip(clip, look=lambda p, start: "gta")
    assert not persona.is_gta_clip(clip, "gta rp", look=lambda p, start: "irl")
    # frames say nothing -> the words decide
    assert persona.is_gta_clip(clip, "johnny cox in the city", look=lambda p, start: "")
    assert not persona.is_gta_clip(clip, "talking to chat", look=lambda p, start: "")


def test_the_caption_prompt_gets_the_johnny_cox_note():
    from autoreel import llm_captions

    asked = {}

    def ask(system, model, prompt):
        asked["p"] = prompt
        return '{"captions": {"instagram": "Johnny Cox gets robbed"}}'

    out = llm_captions.write_captions("Robbed", "he took my car", ["instagram"],
                                      ask=ask, gta=True)
    assert "JOHNNY COX" in asked["p"]
    assert out
