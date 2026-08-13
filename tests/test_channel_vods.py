"""
Taking your own past uploads back off your own channel.

This is the legitimate half of a request this project otherwise declines:
a channel you own, your own footage, your own edit. Nothing here searches
or discovers - it takes the one address it is handed.
"""

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
_TOOLS = os.path.join(_REPO, "tools")
for _path in (_REPO, _UPLOADER, _TOOLS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.channel_vods import (DEFAULT_LIMIT, channel_video_urls,
                                download_args, fetch, fetch_channel,
                                channel_slug, describe_page,
                                fetch_via_listing, is_url, listing_url,
                                owned_only, short_id, video_args,
                                video_links_on)

CHANNEL = "https://rumble.com/user/stackswopo10k"

# Trimmed to the shape that matters: a thumbnail link and a title link
# to the same video, which is why the parser has to dedupe.
PAGE_ONE = """<html><body>
  <div class="videostream">
    <a class="videostream__link link" href="/v6aaaaa-monkey-app-night.html">
      <img src="thumb.jpg"></a>
    <h3><a href="/v6aaaaa-monkey-app-night.html">Monkey app night</a></h3>
  </div>
  <div class="videostream">
    <a class="videostream__link link" href="/v6bbbbb-gta-rp.html">x</a>
  </div>
  <div class="videostream">
    <a class="videostream__link link" href="/v6ccccc-older-one.html">x</a>
  </div>
  <a href="/videos">All videos</a>
</body></html>"""

PAGE_TWO = """<html><body>
  <a class="videostream__link link" href="/v6ddddd-page-two.html">x</a>
</body></html>"""


def test_a_url_is_told_apart_from_a_folder():
    assert is_url(CHANNEL)
    assert not is_url(r"D:\videos stizz")
    assert not is_url("")


def test_each_video_is_only_ever_fetched_once():
    """This is meant to be run daily; without the archive it would
    re-download the same hours of video every time."""
    args = download_args(CHANNEL, "/out", "/out/archive.txt")

    assert "--download-archive" in args
    assert "/out/archive.txt" in args


def test_the_default_limit_is_small():
    """A channel holds hundreds and each one costs a download plus a full
    Whisper pass. Taking them all by default fills a disk overnight."""
    assert DEFAULT_LIMIT <= 5

    args = download_args(CHANNEL, "/out", "/a.txt")
    assert args[args.index("--playlist-end") + 1] == str(DEFAULT_LIMIT)


def test_filenames_are_windows_safe():
    """Emoji and punctuation in a stream title produce a filename Windows
    cannot open - the recorder learned this the hard way."""
    assert "--restrict-filenames" in download_args(CHANNEL, "/out", "/a.txt")


def test_only_new_arrivals_are_returned(tmp_path, monkeypatch):
    """Worked out from the folder, not from parsing yt-dlp's output: the
    output format changes between versions and the folder does not."""
    from utils import channel_vods

    existing = tmp_path / "old stream [aaa].mp4"
    existing.write_bytes(b"x")

    def fake_run(args, **kwargs):
        (tmp_path / "new stream [bbb].mp4").write_bytes(b"x")

        class Done:
            returncode = 0
            stdout = b""
        return Done()

    monkeypatch.setattr(channel_vods.subprocess, "run", fake_run)

    paths, problem = fetch(CHANNEL, str(tmp_path), (".mp4",))

    assert problem == ""
    assert [os.path.basename(p) for p in paths] == ["new stream [bbb].mp4"]


def test_nothing_new_is_not_an_error(tmp_path, monkeypatch):
    """Every recent video already taken is the normal outcome of a daily
    run, not a failure."""
    from utils import channel_vods

    def fake_run(args, **kwargs):
        class Done:
            returncode = 0
            stdout = b""
        return Done()

    monkeypatch.setattr(channel_vods.subprocess, "run", fake_run)

    paths, problem = fetch(CHANNEL, str(tmp_path), (".mp4",))

    assert paths == [] and problem == ""


def test_a_403_is_named_as_cloudflare_not_a_wrong_url(tmp_path, monkeypatch):
    """Rumble answers a plain Python request with 403 however correct the
    URL is. "check the URL" sends you looking in the wrong place."""
    from utils import channel_vods

    def fake_run(args, **kwargs):
        class Done:
            returncode = 1
            stdout = b""
        return Done()

    monkeypatch.setattr(channel_vods.subprocess, "run", fake_run)

    paths, problem = fetch(CHANNEL, str(tmp_path), (".mp4",))

    assert paths == []
    assert "Cloudflare" in problem
    assert "curl_cffi" in problem or "--browser" in problem, \
        "a refusal has to say what to do next"


def test_it_escalates_rather_than_impersonating_by_default(tmp_path, monkeypatch):
    """A curl_cffi that is present but unusable makes yt-dlp fail on
    channels a plain request would have read."""
    from utils import channel_vods

    seen = []

    def fake_run(args, **kwargs):
        seen.append("--impersonate" in args)

        class Done:
            returncode = 1
            stdout = b""
        return Done()

    monkeypatch.setattr(channel_vods.subprocess, "run", fake_run)
    monkeypatch.setattr(channel_vods, "have_impersonation", lambda: True)

    fetch(CHANNEL, str(tmp_path), (".mp4",))

    assert seen[0] is False, "the first try must be the plain one"
    assert True in seen, "it never escalated"


def test_a_page_read_with_nothing_new_is_not_retried(tmp_path, monkeypatch):
    """Every recent video already taken is the normal daily outcome -
    hammering Cloudflare over it would be the wrong lesson."""
    from utils import channel_vods

    calls = []

    def fake_run(args, **kwargs):
        calls.append(1)

        class Done:
            returncode = 0
            stdout = b""
        return Done()

    monkeypatch.setattr(channel_vods.subprocess, "run", fake_run)
    monkeypatch.setattr(channel_vods, "have_impersonation", lambda: True)

    paths, problem = fetch(CHANNEL, str(tmp_path), (".mp4",))

    assert paths == [] and problem == ""
    assert len(calls) == 1


def test_a_missing_yt_dlp_is_reported_not_raised(tmp_path, monkeypatch):
    from utils import channel_vods

    def explode(args, **kwargs):
        raise FileNotFoundError("yt-dlp")

    monkeypatch.setattr(channel_vods.subprocess, "run", explode)

    paths, problem = fetch(CHANNEL, str(tmp_path), (".mp4",))
    assert "yt-dlp is not installed" in problem


def test_importable_is_not_the_test_for_impersonation(monkeypatch):
    """curl_cffi 0.16 imports perfectly and yt-dlp still reports every
    target unavailable - so `pip install -U curl_cffi` silently breaks a
    working setup, and an import check would call it healthy."""
    from utils import channel_vods

    class Done:
        stdout = b"chrome     (unavailable)\nchrome-110 (unavailable)\n"

    monkeypatch.setattr(channel_vods.subprocess, "run",
                        lambda *a, **k: Done())
    assert channel_vods.have_impersonation() is False


def test_a_working_install_is_recognised(monkeypatch):
    from utils import channel_vods

    class Done:
        stdout = b"Client  OS\nchrome-110  windows-10\nchrome-124  windows-10\n"

    monkeypatch.setattr(channel_vods.subprocess, "run",
                        lambda *a, **k: Done())
    assert channel_vods.have_impersonation() is True


def test_the_advice_pins_the_version(tmp_path, monkeypatch):
    """The fix that broke it was `pip install -U curl_cffi`."""
    from utils import channel_vods

    monkeypatch.setattr(channel_vods.subprocess, "run",
                        lambda *a, **k: type(
                            "D", (), {"returncode": 1, "stdout": b""})())
    monkeypatch.setattr(channel_vods, "have_impersonation", lambda: False)

    _, problem = fetch(CHANNEL, str(tmp_path), (".mp4",))

    assert "0.15.0" in problem
    assert "-U curl_cffi" not in problem


def test_there_is_no_rss_feed_to_fall_back_on():
    """Rumble does not publish one - not <page>/index.xml, not /rss.
    config.json has an index.xml address in rumble.rss_url, which is why
    that route looked plausible and returned an HTML page with HTTP 200.
    The listing address is the channel page itself."""
    assert listing_url(CHANNEL) == CHANNEL
    assert listing_url("https://rumble.com/c/BinScripts") == \
        "https://rumble.com/c/BinScripts"


def test_a_share_token_is_dropped_and_pages_are_numbered():
    """The channel URL as pasted from the address bar carries ?e9s=..."""
    assert listing_url(CHANNEL + "/?e9s=src_v1_upp") == CHANNEL
    assert listing_url(CHANNEL, 2) == CHANNEL + "?page=2"


def test_only_rumble_channel_pages_are_read_this_way():
    """Everything else must stay on the yt-dlp route, which works."""
    assert listing_url("https://www.youtube.com/@OnlyThaGuys26") == ""
    assert listing_url("https://rumble.com/v6aaaaa-a-video.html") == ""
    assert listing_url("") == ""


def test_the_video_id_survives_a_retitle():
    """Rumble's ID is the leading token; the rest of the slug is the
    title at the time of posting and can change under you."""
    assert short_id("https://rumble.com/v6aaaaa-monkey-app-night.html") == "v6aaaaa"
    assert short_id("v6aaaaa-monkey-app-night") == "v6aaaaa"


def test_videos_are_read_off_the_page_in_order_and_deduped():
    """A channel page links the same video twice - once from the
    thumbnail, once from the title."""
    links = video_links_on(PAGE_ONE)

    assert links == ["https://rumble.com/v6aaaaa-monkey-app-night.html",
                     "https://rumble.com/v6bbbbb-gta-rp.html",
                     "https://rumble.com/v6ccccc-older-one.html"]


def test_the_videos_listing_link_is_not_mistaken_for_a_video():
    """/videos starts with a v too."""
    assert video_links_on('<a href="/videos">All</a>') == []


def test_a_video_is_found_however_the_page_writes_the_link():
    """Betting on one markup shape lost twice in one evening. Rumble's
    page is React-rendered and the markup moves; the video PATH is the
    part that cannot change without breaking every link they have ever
    published, so that is what gets matched."""
    root = '<a href="/v6aaaaa-monkey-app-night.html">x</a>'
    absolute = '<a href="https://rumble.com/v6aaaaa-monkey-app-night.html">x</a>'
    in_json = '{"videoUrl":"https:\\/\\/rumble.com\\/v6aaaaa-monkey-app-night.html"}'
    wanted = ["https://rumble.com/v6aaaaa-monkey-app-night.html"]

    assert video_links_on(root) == wanted
    assert video_links_on(absolute) == wanted, "absolute hrefs missed"
    assert video_links_on(in_json) == wanted, "escaped JSON missed"


def test_something_that_merely_ends_in_html_is_not_a_video():
    """A Rumble ID is short. A long slug before the first dash is some
    other page that happens to start with a v."""
    assert video_links_on(
        '<a href="/verylongidentifierthatisnotanid-x.html">x</a>') == []


def test_a_page_that_parses_nothing_hands_over_the_evidence():
    """This route exists because a parser went stale silently. When this
    one goes stale it has to say what it saw, in one go, rather than
    costing a round of diagnostic commands."""
    told = describe_page('<html><a href="/something">x</a></html>')

    assert "characters:" in told
    assert "href" in told


def test_the_listing_stops_as_soon_as_it_has_enough(monkeypatch):
    """Walking every page of a channel to find three videos would be
    rude to Rumble and slow for no reason."""
    from utils import channel_vods

    asked = []

    def fake_fetch(url):
        asked.append(url)
        return PAGE_ONE, ""

    monkeypatch.setattr(channel_vods, "_fetch_html", fake_fetch)

    links, why = channel_video_urls(CHANNEL, "", limit=2)

    assert why == ""
    assert len(asked) == 1, "it asked for a second page it did not need"
    assert links == ["https://rumble.com/v6aaaaa-monkey-app-night.html",
                     "https://rumble.com/v6bbbbb-gta-rp.html"]


def test_it_walks_on_to_the_next_page_when_it_needs_more(monkeypatch):
    from utils import channel_vods

    pages = {CHANNEL: PAGE_ONE, CHANNEL + "?page=2": PAGE_TWO}
    monkeypatch.setattr(channel_vods, "_fetch_html",
                        lambda url: (pages.get(url, ""), ""))

    links, why = channel_video_urls(CHANNEL, "", limit=4)

    assert why == ""
    assert links[-1] == "https://rumble.com/v6ddddd-page-two.html"


def test_videos_already_taken_are_skipped(tmp_path, monkeypatch):
    """The archive is what makes a daily run safe. It has to be honoured
    on this route too, or the same VOD is re-downloaded every night."""
    from utils import channel_vods

    archive = tmp_path / "archive.txt"
    archive.write_text("rumble v6aaaaa-monkey-app-night\n")
    monkeypatch.setattr(channel_vods, "_fetch_html",
                        lambda url: (PAGE_ONE, ""))

    links, why = channel_video_urls(CHANNEL, str(archive), limit=2,
                                    max_pages=1)

    assert why == ""
    assert links == ["https://rumble.com/v6bbbbb-gta-rp.html",
                     "https://rumble.com/v6ccccc-older-one.html"]


def test_a_page_with_no_videos_on_it_says_the_layout_changed(monkeypatch):
    """This whole route exists because a parser stopped matching a page.
    If it happens again it has to be obvious, not silent."""
    from utils import channel_vods

    monkeypatch.setattr(channel_vods, "_fetch_html",
                        lambda url: ("<html><body>nothing</body></html>", ""))

    links, why = channel_video_urls(CHANNEL, "", limit=3)

    assert links == []
    assert "layout" in why


def test_a_challenge_page_is_not_read_as_an_empty_channel(monkeypatch):
    """Cloudflare serves challenges with HTTP 200, so the status code
    proves nothing - "no videos here" would be the wrong diagnosis."""
    from utils import channel_vods

    challenge = "<html><head><title>Just a moment...</title></head></html>"
    monkeypatch.setattr(channel_vods, "_fetch_impersonated",
                        lambda url: (None, "Cloudflare served a challenge page"))

    class Fake:
        def read(self):
            return challenge.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(channel_vods.urllib.request, "urlopen",
                        lambda *a, **k: Fake())

    html, why = channel_vods._fetch_html(CHANNEL)

    assert html is None
    assert "challenge" in why


def test_each_video_is_fetched_on_its_own(tmp_path, monkeypatch):
    """Single Rumble videos always downloaded fine - it is only the
    channel listing that yt-dlp cannot parse."""
    from utils import channel_vods

    asked = []

    def fake_run(args, **kwargs):
        asked.append(args[-1])
        (tmp_path / f"vod {len(asked)}.mp4").write_bytes(b"x")

        class Done:
            returncode = 0
            stdout = b""
        return Done()

    monkeypatch.setattr(channel_vods, "_fetch_html",
                        lambda url: (PAGE_ONE, ""))
    monkeypatch.setattr(channel_vods, "have_impersonation", lambda: False)
    monkeypatch.setattr(channel_vods, "_channel_of",
                        lambda *a, **k: "https://rumble.com/user/stackswopo10k")
    monkeypatch.setattr(channel_vods.subprocess, "run", fake_run)

    paths, why = fetch_via_listing(CHANNEL, str(tmp_path), (".mp4",), limit=2)

    assert why == ""
    assert asked == ["https://rumble.com/v6aaaaa-monkey-app-night.html",
                     "https://rumble.com/v6bbbbb-gta-rp.html"]
    assert len(paths) == 2


def test_another_creators_video_on_the_page_is_not_taken(capsys):
    """A channel page carries recommended videos from OTHER creators,
    and a path match cannot tell those from yours. Downloading one would
    put someone else's video through the clipper and out to your
    accounts under your name."""
    mine = "https://rumble.com/v6aaaaa-mine.html"
    theirs = "https://rumble.com/v6zzzzz-theirs.html"
    who = {mine: "https://rumble.com/user/stackswopo10k Stackswopo",
           theirs: "https://rumble.com/user/someoneelse SomeoneElse"}

    import utils.channel_vods as channel_vods
    original = channel_vods._channel_of
    channel_vods._channel_of = lambda link, *a, **k: who[link]
    try:
        kept = owned_only([mine, theirs], CHANNEL)
    finally:
        channel_vods._channel_of = original

    assert kept == [mine]
    assert "someone else" in capsys.readouterr().out


def test_an_unreadable_channel_keeps_the_video_and_says_so(capsys):
    """Refusing on a field yt-dlp could not fill would mean an
    unreadable value silently stopped the whole run - the exact failure
    that cost this evening. Say it out loud instead."""
    import utils.channel_vods as channel_vods

    original = channel_vods._channel_of
    channel_vods._channel_of = lambda link, *a, **k: ""
    try:
        kept = owned_only(["https://rumble.com/v6aaaaa-x.html"], CHANNEL)
    finally:
        channel_vods._channel_of = original

    assert len(kept) == 1
    assert "Could not confirm" in capsys.readouterr().out


def test_the_channel_slug_is_read_off_the_url():
    assert channel_slug(CHANNEL) == "stackswopo10k"
    assert channel_slug("https://rumble.com/c/BinScripts") == "BinScripts"
    assert channel_slug("https://www.youtube.com/@OnlyThaGuys26") == ""


def test_a_single_video_download_never_walks_a_playlist():
    """The listing has already been read off the page; letting yt-dlp
    expand it again would take the whole channel."""
    args = video_args("https://rumble.com/v6aaaaa-a.html", "/out", "/a.txt")

    assert "--no-playlist" in args
    assert "--playlist-end" not in args
    assert "--download-archive" in args


def test_a_listing_that_parses_zero_videos_falls_through(tmp_path, monkeypatch):
    """The bug this route exists for: yt-dlp reads five pages of a real
    channel, parses zero videos and exits 0. From the outside that is
    identical to "nothing new", so the page is read directly anyway."""
    from utils import channel_vods

    downloaded = []

    def fake_run(args, **kwargs):
        if args[-1] != CHANNEL:
            downloaded.append(args[-1])
            (tmp_path / f"vod {len(downloaded)}.mp4").write_bytes(b"x")

        class Done:
            returncode = 0
            stdout = b""
        return Done()

    monkeypatch.setattr(channel_vods, "_fetch_html",
                        lambda url: (PAGE_ONE, ""))
    monkeypatch.setattr(channel_vods, "have_impersonation", lambda: False)
    monkeypatch.setattr(channel_vods, "_channel_of",
                        lambda *a, **k: "https://rumble.com/user/stackswopo10k")
    monkeypatch.setattr(channel_vods.subprocess, "run", fake_run)

    paths, problem = fetch_channel(CHANNEL, str(tmp_path), (".mp4",), limit=1)

    assert problem == ""
    assert downloaded == ["https://rumble.com/v6aaaaa-monkey-app-night.html"]
    assert len(paths) == 1


def test_the_page_is_not_read_when_yt_dlp_worked(tmp_path, monkeypatch):
    """One route is better than two, and this only exists as a fallback
    for a listing that came back empty."""
    from utils import channel_vods

    def fake_run(args, **kwargs):
        (tmp_path / "new stream [bbb].mp4").write_bytes(b"x")

        class Done:
            returncode = 0
            stdout = b""
        return Done()

    def explode(url):
        raise AssertionError("the page was read despite a working listing")

    monkeypatch.setattr(channel_vods, "_fetch_html", explode)
    monkeypatch.setattr(channel_vods.subprocess, "run", fake_run)

    paths, problem = fetch_channel(CHANNEL, str(tmp_path), (".mp4",))

    assert problem == "" and len(paths) == 1


def test_a_non_rumble_channel_never_reaches_this_route(tmp_path, monkeypatch):
    """YouTube channel listings work; there is nothing to fall back on
    and nothing to fall back for."""
    from utils import channel_vods

    def explode(url):
        raise AssertionError("scraped a YouTube channel page")

    monkeypatch.setattr(channel_vods, "_fetch_html", explode)
    monkeypatch.setattr(channel_vods.subprocess, "run",
                        lambda *a, **k: type(
                            "D", (), {"returncode": 0, "stdout": b""})())

    paths, problem = fetch_channel("https://www.youtube.com/@OnlyThaGuys26",
                                   str(tmp_path), (".mp4",))

    assert paths == [] and problem == ""


def test_both_routes_failing_says_both_were_tried(tmp_path, monkeypatch):
    """Otherwise the advice reads "read the page" when reading the page
    was the thing that just failed."""
    from utils import channel_vods

    monkeypatch.setattr(channel_vods, "_fetch_html",
                        lambda url: (None, "direct: 403; as a browser: 403"))
    monkeypatch.setattr(channel_vods, "have_impersonation", lambda: False)
    monkeypatch.setattr(channel_vods.subprocess, "run",
                        lambda *a, **k: type(
                            "D", (), {"returncode": 1, "stdout": b""})())

    paths, problem = fetch_channel(CHANNEL, str(tmp_path), (".mp4",))

    assert paths == []
    assert "Cloudflare" in problem
    assert "channel page" in problem


def test_the_recorder_advice_pins_it_too():
    """The command it prints must be the pinned one. It may still MENTION
    `-U` while explaining why that is the trap."""
    from record_stream import _CURL_CFFI_FIX

    commands = [line.strip() for line in _CURL_CFFI_FIX.splitlines()
                if line.strip().startswith("python -m pip install")]
    assert commands, "it has to print a command"
    for command in commands:
        assert "curl_cffi==0.15.0" in command or "curl_cffi" not in command


def test_the_framing_flag_beats_a_leftover_crop_strategy():
    """crop_strategy outranks a profile, so a leftover one in config.json
    would quietly win over the profile just typed on the command line -
    and a wrongly-cropped clip is invisible until it is already posted."""
    from autoreel.crop_strategy import resolve_crop_strategy

    assert resolve_crop_strategy(
        {"clips": {"profile": "gta", "crop_strategy": ""}},
        "gameplay") == "motion"
    assert resolve_crop_strategy(
        {"clips": {"profile": "monkey", "crop_strategy": ""}},
        "gameplay") == "stack"
    assert resolve_crop_strategy(
        {"clips": {"profile": "whole", "crop_strategy": ""}},
        "gameplay") == "fit"
