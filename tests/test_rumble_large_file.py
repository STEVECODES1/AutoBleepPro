"""
Handing Chrome a file path instead of streaming the bytes.

Playwright's set_input_files refuses anything over 50 MB when attached
over CDP - it cannot tell the browser is on this machine, so it assumes
the file would cross a network. Every real stream recording is past that,
so this ceiling blocked every upload with:

    Cannot transfer files larger than 50Mb to a browser not co-located
    with the server
"""

from __future__ import annotations

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.rumble_uploader import _set_file_via_cdp  # noqa: E402


class FakeCDP:
    """Defaults to a file that verifies as attached - files.length == 1 -
    so existing tests describing the send() side keep working. Tests of
    the verification step itself override files_seen or object_id."""

    def __init__(self, node_id=7, files_seen=1, object_id="obj-1"):
        self.node_id = node_id
        self.files_seen = files_seen
        self.object_id = object_id
        self.sent = []

    def send(self, method, params=None):
        self.sent.append((method, params or {}))
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelector":
            return {"nodeId": self.node_id}
        if method == "DOM.resolveNode":
            if not self.object_id:
                return {"object": {}}
            return {"object": {"objectId": self.object_id}}
        if method == "Runtime.callFunctionOn":
            return {"result": {"value": self.files_seen}}
        return {}


class FakePage:
    def __init__(self, cdp):
        self.context = self
        self._cdp = cdp

    def new_cdp_session(self, page):
        return self._cdp


def test_the_path_is_sent_not_the_bytes(tmp_path):
    """This is the whole point: no transfer, so no size limit."""
    video = tmp_path / "stream.mp4"
    video.write_bytes(b"x" * 1024)
    cdp = FakeCDP()

    assert _set_file_via_cdp(FakePage(cdp), "input[type='file']", str(video))

    params = [p for m, p in cdp.sent if m == "DOM.setFileInputFiles"][0]
    assert params["files"] == [str(video)]


def test_the_path_is_absolute(tmp_path, monkeypatch):
    """Chrome opens the path itself, and its working directory is not
    ours - a relative path would resolve somewhere else entirely."""
    video = tmp_path / "stream.mp4"
    video.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    cdp = FakeCDP()

    _set_file_via_cdp(FakePage(cdp), "input[type='file']", "stream.mp4")
    params = [p for m, p in cdp.sent if m == "DOM.setFileInputFiles"][0]
    assert os.path.isabs(params["files"][0])


def test_a_missing_input_reports_failure_so_the_caller_falls_back(tmp_path):
    video = tmp_path / "x.mp4"
    video.write_bytes(b"x")
    cdp = FakeCDP(node_id=0)     # querySelector found nothing
    assert _set_file_via_cdp(FakePage(cdp), "input[type='file']", str(video)) is False


def test_a_cdp_error_is_not_raised_at_the_caller():
    """A browser that does not speak this must fall back, not crash the
    upload."""
    class Broken:
        context = None

        def new_cdp_session(self, page):
            raise RuntimeError("no CDP here")

    broken = Broken()
    broken.context = broken
    assert _set_file_via_cdp(broken, "input[type='file']", "x.mp4") is False


def test_the_document_is_pierced_so_inputs_in_shadow_dom_are_found(tmp_path):
    """Rumble's form may nest the input; without pierce the query stops at
    the shadow boundary and finds nothing."""
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x")
    cdp = FakeCDP()
    _set_file_via_cdp(FakePage(cdp), "input[type='file']", str(video))

    doc_call = [p for m, p in cdp.sent if m == "DOM.getDocument"][0]
    assert doc_call.get("pierce") is True


# ═════════════════════════════════════════════════════════════════════════
# Sending the CDP command is not the same as the browser accepting it.
#
# _set_file_via_cdp used to return True the moment DOM.setFileInputFiles
# was SENT without raising. A stale nodeId, a path Chrome could not read,
# or a selector matching the wrong element could all send successfully
# and leave the input's own .files list empty - and nothing downstream
# could tell. The upload went on to fill in the title, the tags and the
# category (plain DOM text entry, unaffected either way) and left a real
# browser window sitting there with "SELECT VIDEO TO UPLOAD" still
# showing and "Please select a valid video file" under it - a fully
# filled-in form for a video that was never actually attached.
# ═════════════════════════════════════════════════════════════════════════

def test_success_requires_the_files_list_to_actually_contain_one(tmp_path):
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x")
    cdp = FakeCDP(files_seen=1)

    assert _set_file_via_cdp(FakePage(cdp), "input[type='file']", str(video)) is True


def test_a_zero_length_files_list_is_reported_as_failure(tmp_path):
    """This is the exact bug: the send() succeeds, and the browser's own
    input.files is empty."""
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x")
    cdp = FakeCDP(files_seen=0)

    assert _set_file_via_cdp(FakePage(cdp), "input[type='file']", str(video)) is False


def test_verification_reads_the_same_node_the_file_was_set_on(tmp_path):
    """Not a fresh query - Rumble's page has more than one file input
    (video and thumbnail), and a fresh query could resolve a different
    one than the one that was actually set."""
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x")
    cdp = FakeCDP(node_id=42)

    _set_file_via_cdp(FakePage(cdp), "input[type='file']", str(video))

    resolve_call = [p for m, p in cdp.sent if m == "DOM.resolveNode"][0]
    assert resolve_call["nodeId"] == 42


def test_a_node_that_cannot_be_resolved_is_a_failure(tmp_path):
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x")
    cdp = FakeCDP(object_id=None)

    assert _set_file_via_cdp(FakePage(cdp), "input[type='file']", str(video)) is False


def test_a_file_that_does_not_exist_on_disk_fails_fast_without_touching_cdp():
    """The earlier bug shape all session: a path that is stale, moved, or
    was never really there. Failing before any CDP call is sent is
    cheaper and more honest than sending one for a file that cannot
    possibly be attached."""
    cdp = FakeCDP()

    assert _set_file_via_cdp(
        FakePage(cdp), "input[type='file']", "/nowhere/at/all.mp4") is False
    assert cdp.sent == [], "no CDP call should have been made"


def test_a_broken_verification_call_falls_back_rather_than_crashing(tmp_path):
    video = tmp_path / "s.mp4"
    video.write_bytes(b"x")

    class ExplodingCDP(FakeCDP):
        def send(self, method, params=None):
            if method == "Runtime.callFunctionOn":
                raise RuntimeError("devtools went away")
            return super().send(method, params)

    assert _set_file_via_cdp(
        FakePage(ExplodingCDP()), "input[type='file']", str(video)) is False
