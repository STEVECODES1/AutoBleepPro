"""X through Buffer's free plan."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from publishers import buffer as B  # noqa: E402


def test_links_count_as_23_characters():
    text = "watch " + "https://rumble.com/" + "a" * 200
    assert B.x_length(text) == len("watch ") + 23


def test_a_long_message_is_cut_but_keeps_its_link():
    link = "https://rumble.com/v7ggb28-my-name-johnny-cox.html"
    message = "New upload: " + "word " * 100 + "\n" + link
    out = B.fit_for_x(message, link)
    assert link in out and B.x_length(out) <= 280


def test_a_short_message_is_untouched():
    link = "https://youtu.be/x"
    assert B.fit_for_x(f"hi {link}", link) == f"hi {link}"


def test_x_goes_through_buffer_when_its_key_is_set(monkeypatch):
    from utils.social_promoter import _publisher_for

    monkeypatch.setenv("BUFFER_API_KEY", "test")
    assert type(_publisher_for("x", {})).__name__ == "BufferPublisher"


def test_buffer_posts_now_to_the_x_channel(monkeypatch):
    monkeypatch.setenv("BUFFER_API_KEY", "test")
    calls = []

    def fake_call(self, query, variables=None):
        calls.append((query, variables))
        if "organizations" in query:
            return {"account": {"organizations": [{"id": "o1", "name": "me"}]}}
        if "channels" in query:
            return {"channels": [{"id": "c-ig", "service": "instagram"},
                                 {"id": "c-x", "service": "twitter"}]}
        return {"createPost": {"post": {"id": "p1"}}}

    monkeypatch.setattr(B.BufferPublisher, "_call", fake_call)
    assert B.BufferPublisher({}).post_link("new clip", "https://r.com/v") is True
    sent = calls[-1][1]["input"]
    assert sent["channelId"] == "c-x" and sent["mode"] == "shareNow"
    assert "https://r.com/v" in sent["text"]
