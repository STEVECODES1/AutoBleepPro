"""Every clip links to the full stream it was cut from (utils/stream_links)."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils import stream_links as S  # noqa: E402

YT = "https://www.youtube.com/watch?v=abc123"
RB = "https://rumble.com/v7glxyz-thotbreaker.html"


def test_a_finished_stream_is_found_by_its_clips(tmp_path):
    store = str(tmp_path / "links.json")
    assert S.remember("thotbreaker", "10/8/26", {"youtube": YT, "rumble": RB},
                      store=store)
    assert S.link_for("thotbreaker", "10/8/26", "youtube", store=store) == YT
    assert S.link_for("Thotbreaker!", "10/8/26", "instagram", store=store) == YT
    # Rumble clips link the Rumble upload - same site, and the uncut one.
    assert S.link_for("thotbreaker", "10/8/26", "rumble", store=store) == RB


def test_failed_uploads_are_not_links(tmp_path):
    store = str(tmp_path / "links.json")
    assert not S.remember("x", "1/1/26", {"youtube": "FAILED: quota",
                                         "rumble": "skipped"}, store=store)
    assert S.remember("y", "1/1/26", {"youtube": "FAILED", "rumble": RB},
                      store=store)
    assert S.link_for("y", "1/1/26", "youtube", store=store) == RB


def test_a_stray_rumble_link_is_not_kept(tmp_path):
    store = str(tmp_path / "links.json")
    stray = "https://rumble.com/v7glom0-monkey-trolling-on-omegle.html"
    assert S.remember("thotbreaker", "10/8/26", {"youtube": YT, "rumble": stray},
                      store=store)
    assert S.link_for("thotbreaker", "10/8/26", "rumble", store=store) == YT


def test_a_different_day_or_an_old_stream_is_not_matched(tmp_path):
    store = str(tmp_path / "links.json")
    S.remember("thotbreaker", "10/8/26", {"youtube": YT}, store=store)
    assert S.link_for("thotbreaker", "10/9/26", store=store) == ""
    assert S.link_for("other", "10/8/26", store=store) == ""
    later = time.time() + 30 * 86400
    assert S.link_for("thotbreaker", "10/8/26", store=store, now=later) == ""


def test_the_clip_note_is_read_where_the_clip_was_delivered(tmp_path):
    store = str(tmp_path / "links.json")
    S.remember("thotbreaker", "10/8/26", {"youtube": YT}, store=store)
    notes = tmp_path / "watch"
    notes.mkdir()
    (notes / "Stackswopo 18 48 Live 01 - Clip 01_source.json").write_text(
        json.dumps({"stream_title": "thotbreaker", "stream_date": "10/8/26"}))
    # Posted from elsewhere, as its censored 9:16 copy.
    posted = tmp_path / "queue" / (
        "_vertical_Stackswopo 18 48 Live 01 - Clip 01_CENSORED_x.mp4")
    assert S.link_for_clip(str(posted), "youtube", folders=[str(notes)],
                           store=store) == YT


def test_a_rumble_clip_description_leads_with_the_full_stream():
    from utils.templating import build_clip_description

    text = build_clip_description("Shorts: https://youtube.com/@wopoclipper\n"
                                  "Tags: #StacksWopo #FullVOD",
                                  "thotbreaker", "10/8/26", full_stream=RB)
    lines = text.split("\n\n")
    assert lines[1] == f"▶ Watch the full stream: {RB}"
    assert "on the channel" not in text and "#FullVOD" not in text
    plain = build_clip_description("", "thotbreaker", "10/8/26")
    assert "Watch the full stream on the channel." in plain


def test_a_short_carries_the_full_stream_under_its_caption(monkeypatch):
    from publishers import youtube_shorts

    monkeypatch.setattr(S, "link_for_clip", lambda path, platform="",
                        folders=(), store="": YT)
    pub = youtube_shorts.YouTubeShortsPublisher.__new__(
        youtube_shorts.YouTubeShortsPublisher)
    pub.settings = {"description_template": "[CAPTION]\n\nMore: https://x"}
    body = pub.description_for("He calls her bluff", "clip.mp4")
    assert body.startswith(f"He calls her bluff\n\n▶ Full stream: {YT}")
    assert "More: https://x" in body
