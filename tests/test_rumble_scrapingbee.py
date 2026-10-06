"""The Rumble channel page through ScrapingBee: Rumble has no RSS and no
public API, and Cloudflare refuses a plain request for the page."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))

from utils import rumble_checker as rc  # noqa: E402

CHANNEL = "https://rumble.com/user/BinScripts"


def _video(url, title, owner="/user/BinScripts"):
    return {"object_type": "video", "url": url, "title": title,
            "by": {"type": "user", "relative_url": owner}}


def _page(items):
    return ('<html><a href="/v1abc-other.html">sidebar</a>'
            '<script type="application/json">\n        '
            + json.dumps({"items": items}) + "</script></html>")


def test_the_channels_videos_come_from_the_json_in_the_page():
    html = _page([
        _video("https://rumble.com/v7gg5ho-finger-10526-stackswopo-stream.html",
               '"FINGER" 10/5/26 Stackswopo Stream'),
        _video("https://rumble.com/v7getec-wassssup-10426-stackswopo-stream.html",
               '"WASSSSUP" 10/4/26 Stackswopo Stream'),
    ])
    videos = rc._parse_channel_json(html, CHANNEL)
    assert [v.title for v in videos] == ['"FINGER" 10/5/26 Stackswopo Stream',
                                        '"WASSSSUP" 10/4/26 Stackswopo Stream']
    assert videos[0].video_id == "v7gg5ho-finger-10526-stackswopo-stream.html"


def test_other_channels_on_the_same_page_are_not_counted():
    html = _page([
        _video("https://rumble.com/v1-mine.html", "mine 10/5/26"),
        _video("https://rumble.com/v2-theirs.html", "Fear Porn Ep. 2610",
               owner="/c/Bongino"),
    ])
    assert [v.title for v in rc._parse_channel_json(html, CHANNEL)] == \
        ["mine 10/5/26"]


def test_a_page_without_the_json_block_gives_nothing():
    assert rc._parse_channel_json("<html>Just a moment...</html>", CHANNEL) == []


def test_the_list_is_cached_so_restarts_do_not_spend_credits(tmp_path,
                                                            monkeypatch):
    monkeypatch.setattr(rc, "_CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.setattr(rc, "_fetch_plain", lambda url: (None, "403"))
    monkeypatch.setenv("SCRAPINGBEE_API_KEY", "test")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    calls = []

    def fetch(page, key):
        calls.append(page)
        return [rc.ExistingVideo(title="t 10/5/26", video_id="v1", url="u")], ""

    monkeypatch.setattr(rc, "_fetch_via_scrapingbee", fetch)
    first = rc.fetch_rumble_videos(CHANNEL + "/index.xml", None, CHANNEL)
    second = rc.fetch_rumble_videos(CHANNEL + "/index.xml", None, CHANNEL)
    assert len(first) == len(second) == 1
    assert calls == [CHANNEL]


def test_an_old_cache_is_fetched_again(tmp_path, monkeypatch):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({
        "channel": CHANNEL, "fetched_at": time.time() - 7 * 3600,
        "videos": [{"title": "old", "video_id": "v0", "url": "u0"}]}))
    monkeypatch.setattr(rc, "_CACHE_PATH", str(path))
    assert rc._read_cache(CHANNEL) == []


def test_without_a_key_the_failure_says_what_was_tried(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_CACHE_PATH", str(tmp_path / "none.json"))
    monkeypatch.setattr(rc, "_fetch_plain", lambda url: (None, "403"))
    monkeypatch.delenv("SCRAPINGBEE_API_KEY", raising=False)
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    try:
        rc.fetch_rumble_videos(CHANNEL + "/index.xml", None, CHANNEL)
    except RuntimeError as exc:
        assert "403" in str(exc)
    else:
        raise AssertionError("expected a RuntimeError")
