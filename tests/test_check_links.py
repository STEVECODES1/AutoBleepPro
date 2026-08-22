"""Telling a quiet channel apart from a dead one.

The recorder prints the same line either way:

    [12:52:40] Waiting for Stackswopo youtube live to go live...
    [12:52:42] Not live yet - checking every 60s.

A channel that is simply not streaming and a handle that no longer
resolves produce that identical output, forever, and only one of them is
fine. The second is a stream that will never be recorded and nothing
will ever say so.

tools/check_links.py exists to separate them, and the separating is done
by reading yt-dlp's error text - so these tests are mostly about that
mapping being right, because getting it backwards is worse than not
having the tool: it would call a live-but-quiet channel broken, or a dead
handle healthy.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "tools")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import check_links  # noqa: E402
from check_links import BLOCKED, EMPTY, LIVE, NOT_FOUND, OFFLINE  # noqa: E402


def _state(output: str, returncode: int = 1) -> str:
    return check_links.classify(returncode, output)[0]


# ── the distinction the whole tool exists for ────────────────────────────

def test_a_quiet_channel_is_not_a_broken_one():
    """This is the one that must never be wrong. Reporting NOT FOUND for
    every offline channel would make the tool noise, and noise gets
    ignored on the night it is right."""
    assert _state("ERROR: [youtube:tab] @stackswopo_: The channel is not "
                  "currently live") == OFFLINE
    assert _state("ERROR: [twitch:stream] stackswopo: The channel is not "
                  "currently live") == OFFLINE


def test_a_handle_that_does_not_resolve_is_reported():
    assert _state("ERROR: [youtube:tab] @nosuchchannel: This channel does "
                  "not exist.") == NOT_FOUND
    assert _state("ERROR: Unable to recognize this URL") == NOT_FOUND


def test_a_suspended_account_is_not_called_offline():
    assert _state("ERROR: This account has been suspended") == NOT_FOUND


# ── refused-by-the-site is a third thing, not a config problem ───────────

def test_a_bot_check_is_blocked_not_missing():
    """It happens on a datacenter IP and on a home connection having a bad
    day. Calling it NOT FOUND would send someone editing a URL that was
    never wrong."""
    assert _state("ERROR: [youtube] abc: Sign in to confirm you're not a "
                  "bot.") == BLOCKED


def test_a_cloudflare_reset_is_blocked():
    assert _state("ERROR: [kick:live] stackswopo1k: Unable to download JSON "
                  "metadata: Failed to perform, curl: (35) Recv failure: "
                  "Connection reset by peer") == BLOCKED


def test_a_bot_check_does_not_read_as_offline_first():
    """Both texts can appear in one run. The order the meanings are tried
    in decides which wins, so it is pinned."""
    mixed = ("WARNING: the channel is not currently live\n"
             "ERROR: Sign in to confirm you're not a bot")
    assert _state(mixed) == OFFLINE, (
        "an explicit 'not currently live' is the channel answering, and "
        "outranks a bot check on some other request")


def test_success_means_live():
    assert _state("dQw4w9WgXcQ", returncode=0) == LIVE


# ── it checks what is actually being watched ─────────────────────────────

def test_the_urls_come_from_the_recorder_itself():
    """A checker testing a different list from the one being watched is
    worse than no checker."""
    urls = check_links.watched_urls()

    assert len(urls) == 5
    for expected in ("youtube.com/@stackswopo_/live",
                     "twitch.tv/stackswopo",
                     "kick.com/stackswopo1k",
                     "youtube.com/@OnlyThaGuys26/live"):
        assert any(expected in url for url in urls), expected


def test_the_clips_page_is_recognised_as_a_playlist():
    from record_stream import is_clips_url

    urls = check_links.watched_urls()
    clips = [u for u in urls if is_clips_url(u)]

    assert len(clips) == 1


def test_an_empty_clips_page_is_healthy():
    """No new clips this week is the normal answer, not a broken link."""
    assert EMPTY in check_links.HEALTHY
    assert OFFLINE in check_links.HEALTHY
    assert NOT_FOUND not in check_links.HEALTHY
    assert BLOCKED not in check_links.HEALTHY


# ── the test recording must not become an upload ─────────────────────────

def test_the_capture_test_writes_somewhere_temporary():
    """A test recording that landed in the watch folder would be censored,
    clipped and posted to five platforms."""
    source = open(os.path.join(_REPO, "tools", "check_links.py"),
                  encoding="utf-8").read()
    body = source.split("def record_test", 1)[1].split("\ndef ", 1)[0]

    assert "tempfile.mkdtemp" in body
    assert "watch_folder" not in body
    assert "shutil.rmtree" in body


def test_the_capture_test_deletes_what_it_made():
    source = open(os.path.join(_REPO, "tools", "check_links.py"),
                  encoding="utf-8").read()
    body = source.split("def record_test", 1)[1].split("\ndef ", 1)[0]

    # Every path out of it removes the workspace, including the failures.
    returns = body.count("return False") + body.count("return ok")
    assert body.count("shutil.rmtree") >= returns - 1


def test_nothing_here_writes_to_the_real_config():
    """It only reads. Checked on the parsed code rather than the text,
    because the comments in that file legitimately mention config.json
    and the watch folder while explaining what it stays away from."""
    import ast

    source = open(os.path.join(_REPO, "tools", "check_links.py"),
                  encoding="utf-8").read()

    writes = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if name != "open":
            continue
        modes = [a for a in node.args[1:] if isinstance(a, ast.Constant)]
        modes += [k.value for k in node.keywords
                  if k.arg == "mode" and isinstance(k.value, ast.Constant)]
        for mode in modes:
            if any(flag in str(mode.value) for flag in ("w", "a", "+", "x")):
                writes.append(f"line {node.lineno}")

    assert not writes, f"this tool opens a file for writing at {writes}"
