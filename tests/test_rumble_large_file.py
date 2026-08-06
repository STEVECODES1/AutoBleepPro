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
    def __init__(self, node_id=7):
        self.node_id = node_id
        self.sent = []

    def send(self, method, params=None):
        self.sent.append((method, params or {}))
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.querySelector":
            return {"nodeId": self.node_id}
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

    method, params = cdp.sent[-1]
    assert method == "DOM.setFileInputFiles"
    assert params["files"] == [str(video)]


def test_the_path_is_absolute(tmp_path, monkeypatch):
    """Chrome opens the path itself, and its working directory is not
    ours - a relative path would resolve somewhere else entirely."""
    video = tmp_path / "stream.mp4"
    video.write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    cdp = FakeCDP()

    _set_file_via_cdp(FakePage(cdp), "input[type='file']", "stream.mp4")
    assert os.path.isabs(cdp.sent[-1][1]["files"][0])


def test_a_missing_input_reports_failure_so_the_caller_falls_back(tmp_path):
    cdp = FakeCDP(node_id=0)     # querySelector found nothing
    assert _set_file_via_cdp(FakePage(cdp), "input[type='file']", "x.mp4") is False


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
