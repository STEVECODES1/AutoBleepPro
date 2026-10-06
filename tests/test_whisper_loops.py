"""A Whisper loop must not be read as speech (and muted end to end)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from utils.censor import transcript_loops, unloop  # noqa: E402


def _seg(text):
    words, t = [], 0.0
    for w in text.split():
        words.append({"word": " " + w, "start": t, "end": t + 0.25})
        t += 0.25
    return {"start": 0.0, "end": t, "text": text, "words": words}


def test_real_speech_is_not_a_loop():
    seg = _seg("stop looking at my ass bro stop looking at my ass bro "
               "what the fuck wrong with you johnny")
    assert transcript_loops([seg]) == 0


def test_a_phrase_said_thirty_times_is_a_loop():
    seg = _seg("I know you fucking want " + "fuckin' niggah, " * 30)
    assert transcript_loops([seg]) > 50


def test_unloop_keeps_the_first_two_repeats_and_the_rest_of_the_line():
    seg = _seg("hello there " + "fuck " * 20 + "okay bye")
    out = unloop([seg])[0]
    words = [w["word"].strip() for w in out["words"]]
    assert words == ["hello", "there", "fuck", "fuck", "okay", "bye"]


def test_unloop_leaves_clean_segments_alone():
    seg = _seg("nothing repeats here at all")
    assert unloop([seg])[0] is seg
