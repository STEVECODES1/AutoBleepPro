"""
instagram_free.py imported ChallengeRequired, FeedbackRequired, LoginRequired,
NotFound, RetryAfterContent and UploadError from instagrapi.exceptions.
Three of those six names were never real:

    NotFound            ->  the real class is NotFoundError
    RetryAfterContent   ->  does not exist; the real class is RateLimitError
    UploadError         ->  does not exist; the real class is ClipNotUpload

`from x import (a, b, c)` fails the WHOLE statement on the first bad name,
so this module never imported, on every run, on a machine that had
instagrapi 3.0.5 correctly installed - "Requirement already satisfied" and
"could not be imported" were both true at once. The message printed then
blamed a Pillow/moviepy version clash, which was a guess made without the
package in hand and was wrong: the actual error was
`cannot import name 'NotFound'`, an API mismatch with nothing to do with
Pillow.

Confirmed against the real package: instagrapi 3.0.5 was downloaded and
its exceptions.py read directly for the names asserted below, and
_insta_exc()/instagrapi_problem() were run against it with PYTHONPATH
pointed at an install of it, showing _INSTA_OK is True and all four
mapped names resolve to real instagrapi classes.
"""

from __future__ import annotations

import os
import sys
import types

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_UPLOADER = os.path.join(_REPO, "auto_uploader")
for _path in (_REPO, _UPLOADER):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from publishers import instagram_free as m  # noqa: E402


# ── the three names that were wrong ─────────────────────────────────────

def test_the_import_no_longer_names_a_symbol_instagrapi_does_not_have():
    """Read straight out of the source: `from instagrapi.exceptions
    import (... NotFound ...)` is exactly the statement that raised
    ImportError on a correctly-installed instagrapi. It must be gone."""
    path = os.path.join(_UPLOADER, "publishers", "instagram_free.py")
    with open(path, encoding="utf-8") as handle:
        body = handle.read()

    assert "from instagrapi.exceptions import" not in body, (
        "a direct `from instagrapi.exceptions import (...)` is exactly "
        "what fails the whole statement on one bad name - the module "
        "must read each name defensively instead")


def test_not_found_maps_to_the_real_class_name():
    assert m._insta_exc("NotFound") is m._NoSuchInstagrapiError
    # The real name, confirmed by reading instagrapi 3.0.5's
    # exceptions.py: NotFoundError, not NotFound.
    path = os.path.join(_UPLOADER, "publishers", "instagram_free.py")
    body = open(path, encoding="utf-8").read()
    assert 'NotFound = _insta_exc("NotFoundError")' in body


def test_retry_after_content_maps_to_a_real_class():
    """RetryAfterContent was never a real instagrapi name - the closest
    real class for "wait and retry" is RateLimitError."""
    path = os.path.join(_UPLOADER, "publishers", "instagram_free.py")
    body = open(path, encoding="utf-8").read()
    assert 'RetryAfterContent = _insta_exc("RateLimitError")' in body


def test_upload_error_maps_to_a_real_class():
    """UploadError was never real either - ClipNotUpload is the actual
    class for a Reel upload that fails to configure."""
    path = os.path.join(_UPLOADER, "publishers", "instagram_free.py")
    body = open(path, encoding="utf-8").read()
    assert 'UploadError = _insta_exc("ClipNotUpload")' in body


# ── the defensive resolver ───────────────────────────────────────────────

def test_a_missing_name_resolves_to_a_never_raised_placeholder():
    """The whole point: one bad or renamed class disables only its own
    except branch, not the entire publisher."""
    fake = types.SimpleNamespace()  # no attributes at all
    saved = m._insta_exceptions
    try:
        m._insta_exceptions = fake
        assert m._insta_exc("SomeFutureRename") is m._NoSuchInstagrapiError
    finally:
        m._insta_exceptions = saved


def test_a_present_name_resolves_to_the_real_class():
    fake = types.SimpleNamespace(RealOne=ValueError)
    saved = m._insta_exceptions
    try:
        m._insta_exceptions = fake
        assert m._insta_exc("RealOne") is ValueError
    finally:
        m._insta_exceptions = saved


def test_the_placeholder_is_never_raised_by_anything_real():
    """It exists purely so `except SomeMissingClass:` is unreachable dead
    code instead of a NameError - it must never itself be the type of a
    real error."""
    with pytest.raises(ValueError):
        try:
            raise ValueError("a real instagrapi failure")
        except m._NoSuchInstagrapiError:
            pytest.fail("the placeholder must not swallow real errors")


def test_no_instagrapi_installed_maps_every_name_to_the_placeholder():
    saved = m._insta_exceptions
    try:
        m._insta_exceptions = None
        for name in ("ChallengeRequired", "FeedbackRequired", "LoginRequired",
                    "NotFoundError", "RateLimitError", "ClipNotUpload"):
            assert m._insta_exc(name) is m._NoSuchInstagrapiError
    finally:
        m._insta_exceptions = saved


# ── the message ───────────────────────────────────────────────────────────

def test_a_missing_package_still_says_pip_install():
    saved_ok, saved_err = m._INSTA_OK, m._INSTA_IMPORT_ERROR
    try:
        m._INSTA_OK = False
        m._INSTA_IMPORT_ERROR = "ModuleNotFoundError: No module named 'instagrapi'"
        assert "pip install instagrapi" in m.instagrapi_problem()
    finally:
        m._INSTA_OK, m._INSTA_IMPORT_ERROR = saved_ok, saved_err


def test_a_pillow_clash_is_named_when_the_error_actually_mentions_pillow():
    saved_ok, saved_err = m._INSTA_OK, m._INSTA_IMPORT_ERROR
    try:
        m._INSTA_OK = False
        m._INSTA_IMPORT_ERROR = ("ImportError: cannot import name 'Resampling' "
                                 "from 'PIL.Image' - Pillow too old")
        problem = m.instagrapi_problem()
        assert "Pillow" in problem
    finally:
        m._INSTA_OK, m._INSTA_IMPORT_ERROR = saved_ok, saved_err


def test_a_bad_symbol_name_is_not_blamed_on_pillow():
    """The exact failure this file is named after: a real, correctly
    installed instagrapi, an ImportError with no Pillow in it anywhere.
    The old message blamed Pillow unconditionally and sent someone
    chasing the wrong fix."""
    saved_ok, saved_err = m._INSTA_OK, m._INSTA_IMPORT_ERROR
    try:
        m._INSTA_OK = False
        m._INSTA_IMPORT_ERROR = ("ImportError: cannot import name 'NotFound' "
                                 "from 'instagrapi.exceptions'")
        problem = m.instagrapi_problem()
        assert "Pillow" not in problem
        assert "NotFound" in problem
    finally:
        m._INSTA_OK, m._INSTA_IMPORT_ERROR = saved_ok, saved_err
