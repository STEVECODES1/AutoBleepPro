"""The recap's edit: topic tags, censored captions, cards, description."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))
sys.path.insert(0, ROOT)

from utils import recap_edit  # noqa: E402
from utils.recap import build_from, recap_description  # noqa: E402


def _ask(titles):
    return lambda system, prompt: json.dumps({"titles": titles})


def test_topic_tags_that_needed_starring_or_touch_bodies_are_dropped():
    got = recap_edit.topic_titles(["a", "b", "c", "d"], ask=_ask([
        "Stacks negotiates with the medics", "Stacks discusses dick inflation",
        "Roasting the fat guy", "ok"]))
    assert got == ["Stacks negotiates with the medics", "", "", ""]


def test_no_model_means_no_tags_not_a_failed_recap():
    assert recap_edit.topic_titles(["a"], ask=lambda s, p: "not json") == [""]


def test_captions_are_starred_and_the_tag_is_on_screen(tmp_path):
    segs = [{"start": 10, "end": 14, "text": "yo what the fuck nigga",
             "words": [{"word": w, "start": 10 + i * 0.5, "end": 10.4 + i * 0.5}
                       for i, w in enumerate(["yo", "what", "the", "fuck", "nigga"])]}]
    path = recap_edit.piece_ass(str(tmp_path / "m.ass"), segs, 9, 20, "Pulled over again")
    text = open(path, encoding="utf-8").read()
    assert "PULLED OVER AGAIN" in text and "Topic" in text
    assert "FUCK" not in text.upper().replace("F***", "") and "NIGGA" not in text.upper()


def test_the_description_sends_viewers_to_both_full_streams():
    cfg = SimpleNamespace(youtube=SimpleNamespace(description_template="ALL LINKS\nX: x"))
    text = recap_description(cfg, "THE RETURN", "10-09-26", "https://yt/1", "https://rumble/1",
                             [(3, 40, 0), (40, 90, 1), (90, 140, 2)],
                             ["Pulled over again", "The pastor wants his tithe", "Medics"],
                             intro_s=5.0)
    assert text.index("https://yt/1") < text.index("https://rumble/1") < text.index("ALL LINKS")
    # Intro + card are under 10 s, so the first moment's chapter is 0:00.
    assert "0:00 Pulled over again" in text and "0:45 The pastor wants his tithe" in text
    long_intro = recap_description(cfg, "THE RETURN", "10-09-26", "", "",
                                   [(3, 40, 0), (40, 90, 1), (90, 140, 2)],
                                   ["A b c", "D e f", "G h i"], intro_s=20.0)
    assert "0:00 THE RETURN" in long_intro and "0:23 A b c" in long_intro


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_an_edited_recap_renders_with_cards(tmp_path):
    src, out = str(tmp_path / "s.mp4"), str(tmp_path / "r.mp4")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=s=640x360:r=30:d=20", "-f", "lavfi", "-i", "sine=d=20",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", src], check=True)
    card = {"still": (src, 2), "ass": recap_edit.card_ass(str(tmp_path / "c.ass"), "THE RETURN", "BEST"),
            "end_ass": recap_edit.card_ass(str(tmp_path / "e.ass"), "FULL", "X",
                                           seconds=recap_edit.END_CARD_S)}
    placed = build_from([(src, 1, 4), (src, 10, 14)], out, "", decor=[None, None], card=card)
    assert [i for _, _, i in placed] == [0, 1] and placed[0][0] > 2.5     # after the card
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", out], capture_output=True, text=True).stdout)
    assert 3 + 7 + 7 - 0.5 < dur < 3 + 7 + 7 + 1.5
