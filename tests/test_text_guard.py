"""utils/text_guard.py: broken bits never reach a viewer, real text is untouched."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "auto_uploader"))
from utils import text_guard as g  # noqa: E402

PIN = "\U0001F4CC Uncut on Rumble"
GARBLED = PIN.encode("utf-8").decode("cp1252")


@pytest.mark.parametrize("raw,want", [
    ("Stackswopo - BEST OF THE WEEK (10-09-26 to )", "Stackswopo - BEST OF THE WEEK (10-09-26)"),
    ("Stackswopo - BEST OF THE WEEK ( to 10-15-26)", "Stackswopo - BEST OF THE WEEK (10-15-26)"),
    ("Stackswopo - BEST OF THE WEEK ()", "Stackswopo - BEST OF THE WEEK"),
    ("MONKEY COMPILATION #4 (None)", "MONKEY COMPILATION #4"),
    ("\"[STREAM TITLE]\" {date} Stackswopo Stream", "Stackswopo Stream"),
    ("Stackswopo rumble v7gx6ca edge.ts", "Stackswopo rumble v7gx6ca edge"),
    ("Stackswopo - - GTA RP -", "Stackswopo - GTA RP"),
    ("  Stackswopo   GTA RP  ", "Stackswopo GTA RP"),
    ("a <3 b > c", "a ‹3 b › c"),
])
def test_a_broken_title_is_tidied(raw, want):
    assert g.title(raw) == want


@pytest.mark.parametrize("clean", [
    "Stackswopo - BEST OF THE WEEK (10-09-26 to 10-15-26)",
    "\"EXPOSING\" 10/15/26 Stackswopo Stream",
    "How to (how to) - Stackswopo",
    "\U0001F525 Stackswopo GTA RP · #shorts",
    "Señor Ángel: ¿qué? - Ça va",
    "MONKEY COMPILATION #12 | Stackswopo",
])
def test_a_good_title_comes_back_exactly(clean):
    assert g.title(clean) == clean
    assert g.problems(clean, is_title=True) == []


def test_the_start_time_leaves_a_description_but_never_a_title():
    # Two untitled streams on one day differ only by start time, and the
    # retry dedup matches on the title - so a title keeps it.
    assert g.title("Stackswopo 2026-10-09 14:55") == "Stackswopo 2026-10-09 14:55"
    assert g.text('with the title "THE RETURN 2026-10-09 14:55" on the channel') == \
        'with the title "THE RETURN" on the channel'
    assert g.text("first aired 2026-10-09 14:55 in Atlanta") == \
        "first aired 2026-10-09 14:55 in Atlanta"


def test_a_title_is_cut_at_a_word_and_never_left_empty():
    long = "Stackswopo " + "word " * 40
    out = g.title(long)
    assert len(out) <= 100 and not out.endswith(" ") and out.startswith("Stackswopo word")
    assert g.title("( - )", fallback="Stackswopo Stream") == "Stackswopo Stream"
    assert g.title(None, fallback="Stackswopo") == "Stackswopo"


def test_a_description_keeps_its_lines_links_and_emoji():
    desc = ("\U0001F4CC Uncut on Rumble: https://rumble.com/user/BinScripts\n\n"
            "0:00 Intro\n12:34 The chase\n\n- one\n- two\n\n#stackswopo #gta")
    assert g.text(desc) == desc
    assert g.youtube_description(desc) == desc
    assert g.problems(desc, youtube=True) == []


def test_a_description_loses_only_the_broken_parts():
    desc = (f"{GARBLED}: https://rumble.com/user/BinScripts\n\n\n\n"
            "• THE RETURN 2026-10-09 14:55 (10-09-26): https://youtu.be/x\n"
            "•\nThis stream first aired on [DATE].  Enjoy  ")
    assert g.text(desc) == (f"{PIN}: https://rumble.com/user/BinScripts\n\n"
                            "• THE RETURN (10-09-26): https://youtu.be/x\n\n"
                            "This stream first aired on. Enjoy")


def test_youtube_description_fits_and_has_no_angle_brackets():
    out = g.youtube_description("a <b>\n" + ("\U0001F525 line\n" * 1000))
    assert "<" not in out and ">" not in out
    assert len(out.encode("utf-8")) <= 5000 and out.endswith("line")


def test_a_caption_is_tidied_but_kept_as_written():
    cap = "Bro said WHAT \U0001F602 · · #stackswopo\n\nYouTube: @BinScript"
    assert g.text(cap) == "Bro said WHAT \U0001F602 · #stackswopo\n\nYouTube: @BinScript"


def test_tidying_twice_changes_nothing_more():
    for raw in ["X (10-09-26 to ) -", f"{GARBLED} ()  y", "A - - B 2026-10-09 14:55"]:
        once = g.title(raw)
        assert g.title(once) == once
        assert g.text(g.text(raw)) == g.text(raw)


def test_it_never_raises_and_passes_non_text_through():
    assert g.text(None) is None
    assert g.text("") == ""
    assert g.youtube_description(None) is None
    assert g.title(12345) == "12345"


def test_problems_names_what_is_wrong():
    found = g.problems("Stackswopo - BEST OF THE WEEK (10-09-26 to )", is_title=True)
    assert any("half-filled range" in p for p in found)
    assert any("link label with no link" in p
               for p in g.problems("Full stream on Rumble:\n\nthanks"))
    assert g.problems("ALL LINKS:\nhttps://rumble.com/user/BinScripts") == []
