"""
A clip's title is transcribed speech, and it is published.

On 2026-09-19 these went to Rumble as public, MONETIZED video titles,
straight off the transcript with nothing in between:

    "Oh this nigga must be stupid what must be stupid nigga pussy nigga"
    "Oh what am i desh you niggas your dick sucked"
    "Your mouth my nigga no human should have that many teeth yo what's"

The hook caption burned into the frame had already been fixed at its
own choke point in captions.py. This path - the one that decides the
UPLOAD title on every platform - was left on the old behaviour, which
is the same shape as most of this project's bugs: the fix lands on one
path while a second, independent one keeps the old behaviour.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from autoreel.safe_text import clean_title  # noqa: E402

# Verbatim from the Rumble dashboard, which is why they read like this.
PUBLISHED_WITH_SLURS = (
    "Oh this nigga must be stupid what must be stupid nigga pussy nigga",
    "Oh what am i desh you niggas your dick sucked",
    "Your mouth my nigga no human should have that many teeth yo whats",
)


FILENAME = "Stackswopo - Idk - Full Stream - Clip 03"


def test_a_line_with_a_slur_is_refused_not_starred():
    """Starring a slur fools nobody and dropping it wrecks the sentence,
    so the line simply is not usable as a title. The caller passes the
    filename and gets it straight back."""
    for spoken in PUBLISHED_WITH_SLURS:
        assert clean_title(spoken, fallback=FILENAME) == FILENAME, \
            f"this would have been published: {spoken!r}"


def test_the_stump_is_never_what_gets_published():
    """The subtle half. With the slurs merely dropped the line becomes

        "Oh this must be stupid what must be stupid p****"

    which is truthy, reads like a title, and is not one. A caller that
    asks for nothing back (fallback="") is handed exactly that stump -
    which is right for a hook and wrong for a title - so the clip-title
    path passes a fallback it can RECOGNISE instead."""
    stump = clean_title(PUBLISHED_WITH_SLURS[0], fallback="")
    assert stump, "an empty fallback returns best-effort, by design"
    assert stump != PUBLISHED_WITH_SLURS[0]

    unusable = "\x00-no-safe-title-\x00"
    assert clean_title(PUBLISHED_WITH_SLURS[0], fallback=unusable) == unusable


def test_ordinary_swearing_is_masked_and_keeps_its_sentence():
    """The difference that matters. A MASKED word leaves a readable
    title; a DROPPED one leaves "Who the is this snitch over here",
    which is what actually got printed on 2026-09-21 and is not a
    sentence anybody wrote."""
    cleaned = clean_title("Who the fuck is this snitch over here nah I never snitch",
                          fallback="")

    assert cleaned, "ordinary swearing should not cost the whole title"
    assert "fuck" not in cleaned
    assert cleaned.startswith("Who the f")
    assert cleaned.endswith("nah I never snitch")
    # The words either side of the swear are still there.
    assert "is this snitch over here" in cleaned


def test_a_clean_line_is_left_exactly_as_spoken():
    spoken = "Holy I got a car battery put it in the go-kart lets go"
    assert clean_title(spoken, fallback="") == spoken


def test_the_fallback_is_returned_rather_than_an_empty_title():
    """An empty title is not a safe outcome - it uploads as untitled."""
    assert clean_title(PUBLISHED_WITH_SLURS[0], fallback=FILENAME) == FILENAME


def test_the_uploader_cleans_the_spoken_line_before_using_it():
    """The wiring, not just the helper. The helper was already correct
    and correct-but-uncalled is what published the titles above."""
    source = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                  encoding="utf-8").read()

    marker = "[Clip] Title from the clip itself"
    assert marker in source
    before = source.split(marker)[0]
    # The import and the call both have to be on the path that reaches
    # that print, not somewhere else in the file.
    tail = before[-2000:]
    assert "clean_title" in tail, \
        "the spoken line reaches the upload title without being cleaned"
