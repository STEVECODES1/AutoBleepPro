"""10-17: "Stackswopo - STACKSWOPO EXPOSED FOR BEING A FAKE GANGSTER - ..."
went up on wopovod, Rumble and the recap - the name twice. And the
recaps of 10-15..10-17 read "... - (BEST MOMENTS)": the file was named
after the stream ("exposing.ts"), so no date was found for them."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils import stream_links  # noqa: E402
from utils.templating import build_title  # noqa: E402

FULL = "Stackswopo - {TITLE} - {date} (FULL STREAM)"
RECAP = "Stackswopo - {TITLE} - {date} (BEST MOMENTS)"


def test_a_stream_named_after_stacks_says_the_name_once():
    assert (build_title("STACKSWOPO EXPOSED FOR BEING A FAKE GANGSTER", "10-17-26", FULL)
            == "STACKSWOPO EXPOSED FOR BEING A FAKE GANGSTER - 10-17-26 (FULL STREAM)")
    assert (build_title("stackswopo exposed", "10-17-26", RECAP)
            == "STACKSWOPO EXPOSED - 10-17-26 (BEST MOMENTS)")


def test_other_streams_keep_the_name_in_front():
    assert (build_title("THE BEST TROLL!", "10-16-26", FULL)
            == "Stackswopo - THE BEST TROLL! - 10-16-26 (FULL STREAM)")


def test_a_possessive_is_the_name_and_a_longer_word_is_not():
    assert (build_title("Stackswopo's new car", "10-18-26", FULL)
            == "STACKSWOPO'S NEW CAR - 10-18-26 (FULL STREAM)")
    assert build_title("StackswopoGames collab", "10-18-26", FULL).startswith(
        "Stackswopo - STACKSWOPOGAMES")


def test_a_format_that_quotes_the_name_is_left_alone():
    fmt = '"{title}" {date} Stackswopo Stream'
    assert (build_title("Stackswopo live", "10/18/26", fmt)
            == '"Stackswopo live" 10/18/26 Stackswopo Stream')


def test_the_recap_finds_the_date_its_full_stream_went_up_with(tmp_path):
    store = str(tmp_path / "stream_links.json")
    stream_links.remember("exposing", "10-16-26",
                          {"youtube": "https://www.youtube.com/watch?v=abc"}, store=store)
    assert stream_links.date_for("exposing", store=store) == "10-16-26"
    assert stream_links.date_for("EXPOSING", store=store) == "10-16-26"
    assert stream_links.date_for("another stream", store=store) == ""
