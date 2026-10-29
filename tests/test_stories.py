"""Story episodes for STACKSWOPOVODS (utils/stories) - owner, 10-29."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "auto_uploader"))

from utils import stories  # noqa: E402


def seg(t, text):
    return {"start": float(t), "end": float(t) + 4, "text": text}


def test_the_model_answer_is_kept_only_when_it_makes_sense():
    reply = "sure: " + json.dumps({"stories": [
        {"start": 600, "end": 2400, "title": "Stackswopo Responds To The EXPOSERS", "thumb": "THEY LIED"},
        {"start": 1000, "end": 2000, "title": "overlaps the first"},
        {"start": 3000, "end": 3100, "title": "too short"},
        {"start": 4000, "end": 5200, "title": ""},
        {"start": 5000, "end": 99999, "title": "Stackswopo Plays Scam", "thumb": "SCAMMED"}]})
    got = stories.parse(reply, 7200, 600, 2700)
    assert [g["title"] for g in got] == ["Stackswopo Responds To The EXPOSERS", "Stackswopo Plays Scam"]
    assert got[1]["end"] == 7200


def test_pieces_skip_the_risky_parts():
    assert stories.keep_pieces(100, 1000, [(200, 260), (250, 300), (990, 1200)]) == \
        [(100, 200), (300, 990)]


def test_titles_lead_with_his_name_and_pass_the_youtube_guard():
    t = stories.story_title("Argues with his girlfriend on stream")
    assert t.lower().startswith("stackswopo")
    assert stories.story_title("Stackswopo Plays Scam With Your Friends!") == \
        "Stackswopo Plays Scam With Your Friends!"


def test_pick_asks_the_model_with_the_whole_transcript():
    segs = [seg(t, f"line {t}") for t in range(0, 3600, 5)]
    seen = []

    def ask(prompt):
        seen.append(prompt)
        return json.dumps({"stories": [{"start": 300, "end": 1800, "title": "Stackswopo X",
                                        "thumb": "WOW"}]})
    got = stories.pick(segs, 3600, 1, 600, 2700, askers=[("fake", ask)])
    assert got and got[0]["model"] == "fake"
    assert "line 3595" in seen[0]   # the end of the stream is in it


def test_the_description_links_both_full_streams_and_carries_the_notice():
    from utils.disclosure import ACTORS_LINE
    d = stories.description("Stackswopo Plays Scam", "he scams his friends", "HEY MY BABES",
                            "10-28-26", "https://youtu.be/full", "https://rumble.com/uncut")
    assert "https://youtu.be/full" in d and "https://rumble.com/uncut" in d
    assert ACTORS_LINE in d


def test_it_is_wired_after_the_recap():
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "auto_uploader",
                            "main.py"), encoding="utf-8").read()
    i = src.index("make_recap(cfg, source, title, recap_date, moments)")
    assert "stories.make(" in src[i:i + 800]


def test_a_stray_brace_after_the_answer_is_fine():
    reply = ('{"stories": [{"start": 10476, "end": 11197, "title": "Stackswopo Breaks Up '
             'Before GTA 6", "thumb": "BREAKUP"}]}}')
    assert stories.parse(reply, 12000, 600, 2700)[0]["start"] == 10476


def test_thumbnail_words_are_never_about_kids():
    assert "PEDO" not in stories.thumb_text("SHE CALLED ME A PEDO?", "Stackswopo Addresses The Drama").upper()
    assert stories.thumb_text("YOU'RE JUST A DORK", "x") == "YOU'RE JUST A DORK"


def test_a_slur_only_flag_is_left_to_the_bleep_but_more_is_cut():
    assert stories.muted_already({"category": "hate", "why": "uses racial slur 'n' in chat"})
    assert not stories.muted_already({"category": "hate", "why": "racial slur comparing to monkey"})
    assert not stories.muted_already({"category": "sexual", "why": "slur and explicit sexual act"})
    assert not stories.muted_already({"category": "hate", "why": "Dehumanizing racial attack"})


def test_stranger_parts_are_hidden_from_the_picker():
    text = stories.condensed([seg(10, "hello there"), seg(100, "random stranger on cam")],
                             blocked=[(90, 200)])
    assert "hello there" in text and "random stranger" not in text and "not usable" in text
