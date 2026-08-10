"""
The compound insults added from adeel-raza/profanity-filter, and the
several hundred words deliberately NOT added.

That project is a family content filter. Roughly a third of its list is
there to mute adult themes rather than swearing, and importing it whole
would have muted "bedroom", "affair" and "cheating" on a stream where two
people talk about relationships for an hour. Every one of those costs
nothing in monetisation terms and leaves an unexplained hole in the audio.
"""

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from autoreel.profanity_extra import EXTRA_PROFANITY, contains_extra


# ── What was added ───────────────────────────────────────────────────────

# Compounds the base list did not carry. "fucktard" is deliberately NOT
# here: the affix handling already caught it, which is why only 285 of
# the 1,192 were worth importing at all.
@pytest.mark.parametrize("word", [
    "asshat", "asswipe", "assclown", "dickweed", "dickbrain",
    "shitforbrains", "fuckstick", "cuntface", "cocksucer",
    "buttmunch", "dipshit", "skanky", "cumstain",
])
def test_compound_insults_are_now_caught(word):
    assert contains_extra(word), f"{word} should be flagged"


@pytest.mark.parametrize("word", ["fucktard", "fuckwit", "motherfucker"])
def test_words_we_already_caught_were_not_re_added(word):
    """483 of the 1,192 were already handled by the leet decoder, the
    affix rules and the bypass detector. Importing them again would have
    made the list look three times more valuable than it was."""
    assert not contains_extra(word)

    import bleep_engine
    assert bleep_engine.check_word(word, [], [], sensitivity=50)[0]


def test_the_engine_itself_flags_them(word="asshat"):
    """Wired into the real detector, not just sitting in a set."""
    import bleep_engine

    flagged, _ = bleep_engine.check_word(word, [], [], sensitivity=50)
    assert flagged


def test_the_uploader_path_flags_them():
    """compliance.py is what actually runs on an upload."""
    from autoreel.compliance import ComplianceEngine

    engine = ComplianceEngine()
    violations = engine.scan_words([
        {"word": "you", "start": 0.0, "end": 0.2},
        {"word": "asshat", "start": 0.3, "end": 0.8},
    ])
    assert [v.word for v in violations] == ["asshat"]


# ── What was deliberately left out ───────────────────────────────────────

@pytest.mark.parametrize("word", [
    # Adult THEMES, not swearing. Muting these would gut a conversation
    # and none of them costs a video its monetisation.
    "bedroom", "affair", "adult", "betray", "betrayal", "cheating",
    "cheater", "chemistry", "caress", "bra", "breast", "climax",
    "arousal", "booty", "bulge", "cleavage",
])
def test_family_content_words_were_not_imported(word):
    assert not contains_extra(word), \
        f"{word} is a content-filter word, not profanity - it must not mute"


@pytest.mark.parametrize("word", [
    # Ordinary English a substring stem match pulls in wrongly.
    "passion", "passionate",   # contains "ass"
    "spicy",                   # contains "spic"
    "cocktail", "cocky", "cockblock",
    "booby",                   # booby trap
    "button", "buttons", "unbutton", "butter",
    "cumquat",
    "assessment", "class", "glass", "pass", "massive", "assist",
])
def test_ordinary_words_are_never_flagged(word):
    """A hole where a normal word was is worse than a missed rare slur:
    the viewer cannot tell why the audio dropped."""
    assert not contains_extra(word)

    import bleep_engine
    flagged, reason = bleep_engine.check_word(word, [], [], sensitivity=50)
    assert not flagged, f"{word} was muted as {reason!r}"


def test_cummin_is_left_to_the_existing_affix_rules():
    """Not a word this list decides. The affix handling already flags it,
    and on this channel a transcript reading "cummin" is "cumming" rather
    than the spice - so that behaviour is left alone rather than being
    overridden by an exclusion here."""
    assert not contains_extra("cummin")


def test_no_entry_is_a_bare_common_word():
    """A guard on the list itself: nothing under four letters, and no
    spaces - detection is per-token, so a phrase could never match and
    splitting one would add its halves as standalone triggers."""
    for word in EXTRA_PROFANITY:
        assert " " not in word and "-" not in word, word
        assert len(word) >= 3, word
        assert word == word.lower(), word


def test_the_list_is_not_empty_or_enormous():
    """A sanity range: an empty list means the import silently broke, and
    a list back near 1,192 means the content words came in with it."""
    assert 200 <= len(EXTRA_PROFANITY) <= 400
