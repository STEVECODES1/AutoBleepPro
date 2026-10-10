"""Monkey drafts: his everyday n-word is muted and kept; every other slur is cut."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "auto_uploader"))

import monkey_prep as mp  # noqa: E402


def _seg(text, start, speaker="Stackswopo"):
    words, t = [], start
    for w in text.split():
        words.append({"word": w, "start": t, "end": t + 0.3, "speaker": speaker})
        t += 0.35
    return {"text": text, "start": start, "end": t, "speaker": speaker, "words": words}


def test_everyday_n_word_from_him_is_only_muted():
    cut, muted = mp.hate_split([_seg("yo what up nigga how you doing", 10)])
    assert cut == [] and len(muted) == 1


def test_hard_r_is_always_cut():
    cut, muted = mp.hate_split([_seg("that nigger said what", 10)])
    assert len(cut) == 1 and muted == []


def test_n_word_from_the_other_person_is_cut():
    cut, muted = mp.hate_split([_seg("shut up nigga", 10, speaker=mp.OTHER)])
    assert len(cut) == 1 and muted == []


def test_n_word_inside_an_insult_is_cut():
    cut, muted = mp.hate_split([_seg("you ugly ass nigga", 10)])
    assert len(cut) == 1 and muted == []


def test_muted_ones_do_not_cut_the_moment_but_are_listed():
    flags = mp.flags_for([], muted_spans=[(10.0, 10.3)])
    assert flags and flags[0]["reason"] == mp.MUTED_N and not mp.is_cut(flags[0])
