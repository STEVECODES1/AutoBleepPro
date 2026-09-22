"""A .ts recording attached cleanly and Rumble still refused it.

Confirmed by hand: a browser tab with the title, tags and category
already filled in, "SELECT VIDEO TO UPLOAD" still showing, and "Please
select a valid video file" underneath - for a file that HAD attached.
Renaming the exact same bytes to end in .mp4, nothing else, let the
identical upload proceed.

That is a different failure from the one the CDP-verification fix
covers. That fix catches the browser's input.files staying EMPTY.
This is the browser holding a real file and Rumble's own client-side
check refusing it anyway, by extension.

.ts is not an edge case here - it is what --hls-use-mpegts produces
(tools/record_stream.py) and it is in general.supported_formats, so the
ordinary watch-folder pipeline can hand a live recording straight to
Rumble with that extension whenever it was delivered before the archival
remux step ran, or whenever a .ts file is dropped in by hand.
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.rumble_uploader import _rumble_friendly_alias  # noqa: E402


def test_a_ts_file_gets_an_mp4_named_alias(tmp_path):
    video = tmp_path / "Stackswopo 8-23-26.ts"
    video.write_bytes(b"fake ts content")

    upload_path, alias_made = _rumble_friendly_alias(str(video))

    assert alias_made is True
    assert upload_path.endswith(".mp4")
    assert upload_path != str(video)


def test_the_alias_holds_the_exact_same_bytes(tmp_path):
    video = tmp_path / "s.ts"
    payload = os.urandom(4096)
    video.write_bytes(payload)

    upload_path, _ = _rumble_friendly_alias(str(video))

    with open(upload_path, "rb") as handle:
        assert handle.read() == payload


def test_the_original_file_is_untouched(tmp_path):
    """The alias is a second name (or a copy) - the real recording, still
    playable, still the thing every other stage of the pipeline works
    with, must not move or change."""
    video = tmp_path / "s.ts"
    video.write_bytes(b"original content")

    _rumble_friendly_alias(str(video))

    assert video.exists()
    assert video.read_bytes() == b"original content"


def test_a_hard_link_costs_no_extra_disk(tmp_path):
    """A stream recording can be gigabytes - doubling it on disk for a
    filename workaround would be a real cost, not a rounding error."""
    video = tmp_path / "s.ts"
    video.write_bytes(b"x" * 1_000_000)

    upload_path, _ = _rumble_friendly_alias(str(video))

    assert os.stat(video).st_ino == os.stat(upload_path).st_ino, (
        "the alias is a real copy, not a hard link, on a filesystem "
        "that supports one - this test's tmp_path should support it")


def test_a_failed_hard_link_falls_back_to_a_real_copy(tmp_path, monkeypatch):
    """Crossing drives is the ordinary reason a hard link fails on
    Windows - the fallback must still produce a usable file."""
    video = tmp_path / "s.ts"
    payload = os.urandom(2048)
    video.write_bytes(payload)

    def refuse_link(*a, **k):
        raise OSError("cross-device link")

    monkeypatch.setattr(os, "link", refuse_link)

    upload_path, alias_made = _rumble_friendly_alias(str(video))

    assert alias_made is True
    assert os.path.exists(upload_path)
    with open(upload_path, "rb") as handle:
        assert handle.read() == payload


def test_if_neither_link_nor_copy_works_the_original_is_still_offered(
        tmp_path, monkeypatch):
    """A missing workaround must never be the reason an upload does not
    even attempt."""
    video = tmp_path / "s.ts"
    video.write_bytes(b"x")

    monkeypatch.setattr(os, "link",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    monkeypatch.setattr("shutil.copyfile",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no")))

    upload_path, alias_made = _rumble_friendly_alias(str(video))

    assert alias_made is False
    assert upload_path == str(video)


def test_a_stale_alias_from_a_previous_attempt_is_replaced(tmp_path):
    """A retry after a failed upload must not upload the OLD alias's
    (possibly now-wrong) content, or fail because the name is taken."""
    video = tmp_path / "s.ts"
    video.write_bytes(b"new content")
    stale = tmp_path / "s._rumble_upload.mp4"
    stale.write_bytes(b"leftover from a previous, different attempt")

    upload_path, _ = _rumble_friendly_alias(str(video))

    with open(upload_path, "rb") as handle:
        assert handle.read() == b"new content"


# ── files that are not .ts are never touched ──────────────────────────────

def test_an_mp4_is_offered_unchanged(tmp_path):
    """This is the normal case - the recorder's own finalise() already
    delivers .mp4. No alias, no extra file, no extra disk I/O."""
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x")

    upload_path, alias_made = _rumble_friendly_alias(str(video))

    assert upload_path == str(video)
    assert alias_made is False


def test_other_extensions_are_left_alone_too(tmp_path):
    for suffix in (".mov", ".mkv", ".webm"):
        video = tmp_path / f"s{suffix}"
        video.write_bytes(b"x")

        upload_path, alias_made = _rumble_friendly_alias(str(video))

        assert upload_path == str(video)
        assert alias_made is False


def test_the_check_is_case_insensitive(tmp_path):
    """A .TS from somewhere that uppercases extensions must still get
    the alias."""
    video = tmp_path / "s.TS"
    video.write_bytes(b"x")

    _upload_path, alias_made = _rumble_friendly_alias(str(video))

    assert alias_made is True


# ── wired into the real upload flow, not just the standalone helper ──────

import pytest
from utils import rumble_uploader as ru


class _StopHere(RuntimeError):
    """Used to short-circuit _upload_video partway through, so the test
    does not need to fake the entire Rumble form."""


class _Locator:
    def __init__(self, captured):
        self._captured = captured

    def or_(self, _other):
        return self

    @property
    def first(self):
        return self

    def set_input_files(self, path):
        self._captured.append(path)

    def wait_for(self, **_kwargs):
        # Stands in for every step after the attach - proves the alias
        # is cleaned up even when the upload fails partway through, not
        # only on a clean success.
        raise _StopHere("nothing past the attach is under test here")

    def count(self):
        return 0


class _FakePage:
    def __init__(self, captured, waits=None):
        self._captured = captured
        # Every selector the upload waited for, in order. The real page
        # renders its form with JavaScript after load, so what gets
        # waited for - and whether anything is waited for at all - is
        # the difference between the CDP attach working and not.
        self.waits = waits if waits is not None else []

    def goto(self, *_a, **_k):
        pass

    def wait_for_selector(self, selector, **kwargs):
        self.waits.append((selector, kwargs.get("state")))
        return None

    def locator(self, _selector):
        return _Locator(self._captured)

    def get_by_label(self, _label):
        return _Locator(self._captured)


def _uploader():
    return ru.RumbleUploader(username="", password="", login_url="",
                             upload_url="https://rumble.com/upload.php")


def test_upload_video_attaches_the_alias_not_the_original(tmp_path, monkeypatch):
    video = tmp_path / "Stackswopo 8-23-26.ts"
    video.write_bytes(b"x" * 100)
    captured = []
    # Forces the Playwright fallback path so _Locator.set_input_files is
    # what actually receives the path - the same argument the CDP path
    # would receive.
    monkeypatch.setattr(ru, "_set_file_via_cdp", lambda page, sel, path: False)

    with pytest.raises(_StopHere):
        _uploader()._upload_video(_FakePage(captured), str(video), "t", "d",
                                  [], "public", "", None)

    assert captured, "the file was never attached at all"
    assert captured[0].endswith(".mp4")
    assert captured[0] != str(video), "the original .ts path was attached directly"


def test_the_alias_is_removed_even_when_the_upload_fails_partway_through(
        tmp_path, monkeypatch):
    """The whole point of wrapping this in try/finally: a multi-gigabyte
    alias left behind on every failed attempt would fill a drive."""
    video = tmp_path / "s.ts"
    video.write_bytes(b"x" * 100)
    captured = []
    monkeypatch.setattr(ru, "_set_file_via_cdp", lambda page, sel, path: False)

    with pytest.raises(_StopHere):
        _uploader()._upload_video(_FakePage(captured), str(video), "t", "d",
                                  [], "public", "", None)

    alias_path = captured[0]
    assert not os.path.exists(alias_path), "the alias was left behind"
    assert video.exists(), "the original recording must never be touched"


def test_an_mp4_recording_needs_no_alias_and_none_is_created(tmp_path, monkeypatch):
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x" * 100)
    captured = []
    monkeypatch.setattr(ru, "_set_file_via_cdp", lambda page, sel, path: False)

    with pytest.raises(_StopHere):
        _uploader()._upload_video(_FakePage(captured), str(video), "t", "d",
                                  [], "public", "", None)

    assert captured == [str(video)]


# ── why every real upload failed ──────────────────────────────────────

def test_the_file_input_is_waited_for_before_cdp_is_asked_for_it(
        tmp_path, monkeypatch):
    """page.goto() returns on the load event; Rumble renders the upload
    form with JavaScript afterwards.

    A Playwright locator hides that by auto-waiting. The raw CDP
    DOM.querySelector inside _set_file_via_cdp does not - it asks once,
    and at that moment the honest answer is "no such element". So the
    bypass returned False for a reason that had nothing to do with CDP,
    the code fell through to Playwright, and THAT auto-waited, found the
    input, and died on the single limit the bypass exists to avoid:

        Locator.set_input_files: Cannot transfer files larger than 50Mb
        to a browser not co-located with the server
    """
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x" * 100)
    waits = []
    seen = []

    def fake_cdp(page, selector, path):
        seen.append((list(page.waits), selector))
        return True

    monkeypatch.setattr(ru, "_set_file_via_cdp", fake_cdp)

    with pytest.raises(_StopHere):
        _uploader()._upload_video(_FakePage([], waits), str(video), "t", "d",
                                  [], "public", "", None)

    assert seen, "the CDP attach was never attempted"
    waited_before, selector = seen[0]
    assert waited_before, "CDP was asked for an element nobody waited for"
    assert "input[type='file']" in waited_before[0][0]
    # The same selector, so the wait cannot pass while the query misses.
    assert waited_before[0][0] == selector


def test_it_waits_for_attached_not_visible(tmp_path, monkeypatch):
    """The input is hidden behind Rumble's own styled button, as nearly
    every upload form's is. Waiting for it to be VISIBLE would time out
    on a page that is working perfectly."""
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x" * 100)
    waits = []
    monkeypatch.setattr(ru, "_set_file_via_cdp", lambda p, sel, path: True)

    with pytest.raises(_StopHere):
        _uploader()._upload_video(_FakePage([], waits), str(video), "t", "d",
                                  [], "public", "", None)

    assert waits[0][1] == "attached"


def test_a_big_file_refuses_rather_than_taking_the_doomed_path(
        tmp_path, monkeypatch):
    """Playwright's 50MB ceiling is arithmetic, not weather. A stream
    recording must not go down that path and spend 60s + 300s + 900s of
    backoff confirming its own size three times."""
    video = tmp_path / "stream.mp4"
    video.write_bytes(b"x" * 100)
    monkeypatch.setattr(ru, "_set_file_via_cdp", lambda p, sel, path: False)
    monkeypatch.setattr(ru.os.path, "getsize", lambda _p: 4200 * 1_000_000)

    captured = []
    with pytest.raises(ru.RumbleSetupError) as raised:
        _uploader()._upload_video(_FakePage(captured), str(video), "t", "d",
                                  [], "public", "", None)

    message = str(raised.value)
    assert "4200 MB" in message
    assert "50 MB" in message
    assert captured == [], "it tried the path that cannot work anyway"


def test_a_small_file_still_falls_back_to_playwright(tmp_path, monkeypatch):
    """The fallback is not dead code - under the ceiling it is a real
    second route, and a clip is well under it."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x" * 100)
    monkeypatch.setattr(ru, "_set_file_via_cdp", lambda p, sel, path: False)

    captured = []
    with pytest.raises(_StopHere):
        _uploader()._upload_video(_FakePage(captured), str(video), "t", "d",
                                  [], "public", "", None)

    assert captured == [str(video)]
