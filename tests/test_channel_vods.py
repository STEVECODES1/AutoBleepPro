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

from utils.channel_vods import DEFAULT_LIMIT, download_args, fetch, is_url

CHANNEL = "https://rumble.com/user/stackswopo10k"


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


def test_the_recorder_advice_pins_it_too():
    """The command it prints must be the pinned one. It may still MENTION
    `-U` while explaining why that is the trap."""
    from record_stream import _CURL_CFFI_FIX

    commands = [line.strip() for line in _CURL_CFFI_FIX.splitlines()
                if line.strip().startswith("python -m pip install")]
    assert commands, "it has to print a command"
    for command in commands:
        assert "curl_cffi==0.15.0" in command or "curl_cffi" not in command
