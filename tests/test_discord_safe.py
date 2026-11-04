"""Discord messages never spell out slurs or strong words, and long lists go
as an attached .txt (owner 11-04: "it can get my discord banned")."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils import discord_safe, post_refs  # noqa: E402


def test_slurs_and_strong_words_are_starred_normal_words_stay():
    out = discord_safe.clean("slur 'nigga' and niggas, fuck, faggot; damn shit - Stackswopo roasts")
    for bad in ("nigga", "niggas", "fuck", "faggot"):
        assert bad not in out.lower().replace("*", "")[:0] + out.lower()
    assert "n****" in out and "f***" in out
    assert "Stackswopo roasts" in out and "damn" in out


def test_a_long_message_goes_as_a_txt_attachment_and_is_cleaned_inside():
    sent = {}

    def post(url, data, headers):
        sent.update(data=data, headers=headers)
        return True

    body = "\n".join(f"0:{i:02d} hate: racial slur 'nigga'" for i in range(40))
    assert discord_safe.send("https://discord.test/hook", "Cut 40 parts", body, post=post)
    assert sent["headers"]["Content-Type"].startswith("multipart/form-data")
    raw = sent["data"].decode("utf-8")
    assert "filename=\"details.txt\"" in raw
    assert "nigga" not in raw and "n****" in raw


def test_a_short_message_is_plain_json_and_pings_nobody_by_default():
    sent = {}

    def post(url, data, headers):
        sent.update(json.loads(data.decode("utf-8")))
        return True

    assert discord_safe.send("https://discord.test/hook", "Hello", "all good", post=post)
    assert sent["allowed_mentions"] == {"parse": []}
    assert sent["content"].startswith("**Hello**")


def test_post_refs_are_written_and_read_back(tmp_path, monkeypatch):
    monkeypatch.setattr(post_refs, "PATH", str(tmp_path / "refs.jsonl"))
    post_refs.record("watch_folder/A - Clip 01.mp4", "instagram", "1789", "Line one\nline two")
    post_refs.record("watch_folder/A - Clip 01.mp4", "instagram", "True")   # not a ref
    items = post_refs.recent(1)
    assert len(items) == 1 and items[0]["ref"] == "1789" and items[0]["title"] == "Line one"
