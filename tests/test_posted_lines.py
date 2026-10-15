"""A new Monkey draft never reuses a moment BinScripts posted or an earlier
draft took - matched on what is said, which survives the heavy edit."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "auto_uploader"))

from utils import posted_lines as pl  # noqa: E402

LINE = ("bro you really think you can just come on here and tell me "
        "that you got the best hairline in the whole state")


def test_same_conversation_counts_as_used():
    posted = pl.shingles("intro music " + LINE + " outro")
    assert pl.already_used(LINE, posted)


def test_one_shared_phrase_is_chance():
    posted = pl.shingles("what are you doing bro come on")
    assert not pl.already_used(LINE, posted)


def test_empty_never_counts():
    assert not pl.already_used("", pl.shingles(LINE))
    assert not pl.already_used(LINE, set())


def test_captions_json3_and_vtt(tmp_path):
    (tmp_path / "a.en.json3").write_text(json.dumps(
        {"events": [{"segs": [{"utf8": "bro you really"}, {"utf8": " think you can"}]}]}),
        encoding="utf-8")
    (tmp_path / "b.en.vtt").write_text(
        "WEBVTT\n\n00:00.000 --> 00:01.000\njust come on <c>here</c> and tell me\n",
        encoding="utf-8")
    got = pl.posted_shingles(str(tmp_path))
    assert "bro you really think" in got
    assert "just come on here" in got


def test_earlier_drafts_count_but_not_this_one(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(), new.mkdir()
    pl.write_used(str(old), "old.ts", [{"where": "1:00", "said": LINE}])
    pl.write_used(str(new), "new.ts", [{"where": "2:00", "said": "something else entirely here now"}])
    known = pl.drafted_shingles(str(tmp_path), skip=str(new))
    assert pl.already_used(LINE, known)
    assert not pl.already_used("something else entirely here now", known)


def test_old_review_pages_are_indexed(tmp_path):
    folder = tmp_path / "draft"
    folder.mkdir()
    (folder / "review.html").write_text(
        "<h2>In the cut (1 moments)</h2><table><tr><th>At</th></tr>"
        f"<tr><td>0:00</td><td>1:00</td><td>20s</td><td>-</td><td>{LINE}</td></tr></table>"
        "<h2>Left out (1 good moments that were flagged)</h2><table>"
        "<tr><td>2:00</td><td>age</td><td>left out words stay out of it</td></tr></table>",
        encoding="utf-8")
    assert pl.backfill_used(str(tmp_path), say=lambda *_: None) == 1
    data = json.load(open(os.path.join(folder, pl.USED), encoding="utf-8"))
    assert [m["said"] for m in data["moments"]] == [LINE]


def test_prepare_leaves_repeats_out(tmp_path, monkeypatch):
    import monkey_prep

    monkeypatch.setattr(pl, "refresh", lambda *a, **k: 0)
    posted_dir = tmp_path / "_posted_binscripts"
    posted_dir.mkdir()
    (posted_dir / "x.en.vtt").write_text(f"WEBVTT\n\n{LINE}\n", encoding="utf-8")
    pool = [{"said": LINE, "score": 1.0}, {"said": "a brand new moment nobody has seen yet", "score": 0.5}]
    fresh, repeats = monkey_prep._not_used_before(pool, str(tmp_path), str(tmp_path / "me"),
                                                  say=lambda *_: None)
    assert [m["score"] for m in fresh] == [0.5]
    assert repeats[0]["reasons"] == ["already posted on BinScripts"]
