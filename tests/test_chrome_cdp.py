"""
Getting a debuggable Chrome on the port, instead of falling back.

Rumble uploads drive a real logged-in browser. When nothing was listening
on the CDP port the uploader dropped to launching a fresh Playwright
browser and typing the password into Rumble's login form - a logged-out
profile, guessed selectors, and whatever captcha Rumble decides to show.
These cover starting Chrome on the port instead.
"""

from __future__ import annotations

import os
import socket
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.chrome_cdp import (  # noqa: E402
    DEFAULT_PROFILE_DIR,
    cdp_port,
    ensure_chrome,
    is_listening,
    launch_args,
)


def test_the_port_is_read_from_the_url():
    assert cdp_port("http://localhost:9222") == 9222
    assert cdp_port("localhost:9222") == 9222
    assert cdp_port("http://127.0.0.1:9333") == 9333


def test_a_url_with_no_port_is_refused():
    """Defaulting to 80 would check the web server, find nothing, and
    start a second Chrome - a confusing failure instead of a clear one."""
    assert cdp_port("http://localhost") is None
    assert cdp_port("nonsense") is None
    assert cdp_port("") is None
    assert cdp_port(None) is None


def test_a_free_port_reads_as_not_listening():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    assert is_listening(free, timeout=0.2) is False


def test_a_bound_port_reads_as_listening():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        assert is_listening(server.getsockname()[1], timeout=1.0) is True


def test_an_already_running_chrome_is_not_restarted():
    """Launching a second one would fail to bind and leave a stray window."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        ready, detail = ensure_chrome(f"http://localhost:{port}")
    assert ready is True
    assert "already listening" in detail


def test_a_bad_url_fails_without_launching_anything(monkeypatch):
    import utils.chrome_cdp as mod

    monkeypatch.setattr(mod, "find_chrome",
                        lambda: (_ for _ in ()).throw(AssertionError("launched")))
    ready, detail = ensure_chrome("http://localhost")
    assert ready is False and "not a usable CDP URL" in detail


def test_launch_uses_a_separate_profile_directory():
    """Chrome refuses --remote-debugging-port on the default profile, so
    this is required, not a nicety."""
    args = launch_args("chrome.exe", 9222, DEFAULT_PROFILE_DIR)
    assert f"--remote-debugging-port=9222" in args
    assert any(a.startswith("--user-data-dir=") for a in args)


def test_the_profile_is_persistent_so_the_login_survives():
    """A temp profile would mean logging into Rumble on every single run."""
    args = launch_args("chrome.exe", 9222, DEFAULT_PROFILE_DIR)
    profile = [a for a in args if a.startswith("--user-data-dir=")][0]
    assert "tmp" not in profile.lower()


def test_chrome_opens_on_rumble_so_a_login_prompt_makes_sense():
    assert any("rumble.com" in a for a in launch_args("chrome.exe", 9222, "p"))


def test_a_missing_chrome_reports_rather_than_raises(monkeypatch, tmp_path):
    import utils.chrome_cdp as mod

    monkeypatch.setattr(mod, "is_listening", lambda *a, **k: False)
    monkeypatch.setattr(mod, "find_chrome", lambda: None)
    ready, detail = ensure_chrome("http://localhost:9222")
    assert ready is False
    assert "9222" in detail, "the message must name the port to start it on"
