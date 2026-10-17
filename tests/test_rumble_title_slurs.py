"""A slur in a Rumble title is masked; the words around it are not."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils.rumble_uploader import mask_slurs  # noqa: E402


def test_slurs_are_masked_and_the_line_still_reads():
    assert mask_slurs("and a nigga goes to center city") == "and a n***a goes to center city"
    assert mask_slurs("NIGGAS be wild") == "N****S be wild"
    assert mask_slurs("called him a faggot") == "called him a f****t"


def test_ordinary_words_are_left_alone():
    for clean in ("Spicy fagioli", "Stackswopo - THE RETURN - 10-09-26 (FULL STREAM)",
                  "Snigger at the trigger"):
        assert mask_slurs(clean) == clean
