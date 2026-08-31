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
    worse than no checker.

    Down to two sources as of 2026-08-31 - @OnlyThaGuys26, Kick and the
    Twitch clips page were all dropped from _RUN_RECORDER.bat to cut
    concurrent drive load, so they are correctly absent here too - this
    function reads the .bat directly rather than a copy of the list,
    which is the point of the test."""
    urls = check_links.watched_urls()

    assert len(urls) == 2
    for expected in ("youtube.com/@stackswopo_/live",
                     "twitch.tv/stackswopo"):
        assert any(expected in url for url in urls), expected
    assert not any("OnlyThaGuys26" in url for url in urls)
    assert not any("kick.com" in url for url in urls)
    assert not any("/clips" in url for url in urls)


def test_the_clips_page_is_recognised_as_a_playlist():
    """is_clips_url() itself, not against watched_urls() - the Twitch
    clips page was dropped from _RUN_RECORDER.bat to cut concurrent
    drive load, so there is no longer one in the watched list to find."""
    from record_stream import is_clips_url

    assert is_clips_url("https://www.twitch.tv/stackswopo/clips?range=7d")
    assert not is_clips_url("https://www.twitch.tv/stackswopo")


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


# ── Cloudflare impersonation, the quiet way Kick dies ────────────────────

def test_no_impersonate_targets_is_reported_as_broken(monkeypatch):
    """Kick comes back 403 on every request without one, and nothing else
    says so - it reads as Kick being difficult."""
    class Done:
        stdout = "[info] Available impersonate targets\nClient  OS  Source\n----\n"
        stderr = ""
    monkeypatch.setattr(check_links.subprocess, "run", lambda *a, **k: Done())

    ok, detail = check_links.impersonation_report()

    assert not ok
    assert "403" in detail
    assert "curl-cffi" in detail


def test_targets_that_all_read_unavailable_are_not_counted(monkeypatch):
    """The failure that cost an evening: curl_cffi installs and imports
    perfectly while every target is unusable because the versions do not
    match."""
    class Done:
        stdout = ("[info] Available impersonate targets\n"
                  "Client      OS        Source\n"
                  "----\n"
                  "Chrome-133  Macos-15  curl_cffi (unavailable)\n"
                  "Safari-18   Ios-18    curl_cffi (unavailable)\n")
        stderr = ""
    monkeypatch.setattr(check_links.subprocess, "run", lambda *a, **k: Done())

    ok, detail = check_links.impersonation_report()

    assert not ok
    assert "unavailable" in detail


def test_working_impersonation_is_counted(monkeypatch):
    class Done:
        stdout = ("[info] Available impersonate targets\n"
                  "Client      OS        Source\n"
                  "--------------------------\n"
                  "Chrome-133  Macos-15  curl_cffi\n"
                  "Safari-18   Ios-18    curl_cffi\n")
        stderr = ""
    monkeypatch.setattr(check_links.subprocess, "run", lambda *a, **k: Done())

    ok, detail = check_links.impersonation_report()

    assert ok
    assert "2 impersonate target" in detail


def test_yt_dlp_being_absent_is_not_a_crash(monkeypatch):
    def explode(*a, **k):
        raise FileNotFoundError("yt-dlp")
    monkeypatch.setattr(check_links.subprocess, "run", explode)

    ok, detail = check_links.impersonation_report()

    assert not ok and "could not ask yt-dlp" in detail
