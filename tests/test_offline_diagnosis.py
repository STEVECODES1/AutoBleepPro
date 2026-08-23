"""A dropped internet connection was reported as a Cloudflare problem.

Through a real outage the recorder printed this, repeatedly, on every
channel:

    ERROR: [kick:live] stackswopo1k: Unable to download JSON metadata:
    curl: (6) Could not resolve host: kick.com
    FIX: Kick sits behind Cloudflare, and yt-dlp needs a browser TLS
    fingerprint to get past it. THE VERSION MATTERS ... (eight lines)

None of which was true. "Could not resolve host" is DNS - the machine
could not look the address up at all. Nothing was reachable, YouTube and
Twitch included, and no package fixes that. The advice matched only
because the word "kick" appeared in the line.

Worse, the advice it gave had gone stale: it named a curl_cffi version to
pin, and yt-dlp has since moved past it - so following it now downgrades
below what yt-dlp is built against and breaks the exact thing the pin was
meant to protect.
"""

from __future__ import annotations

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "tools")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import record_stream  # noqa: E402

OUTAGE = [
    "ERROR: [kick:live] stackswopo1k: Unable to download JSON metadata: "
    "Failed to perform, curl: (6) Could not resolve host: kick.com",
    "ERROR: [twitch:stream] stackswopo: Unable to download JSON metadata: "
    "HTTPSConnection(host='gql.twitch.tv', port=443): Failed to resolve "
    "'gql.twitch.tv' ([Errno 11001] getaddrinfo failed)",
    "ERROR: unable to download video data: <urlopen error [Errno -2] Name "
    "or service not known>",
    "ERROR: Temporary failure in name resolution",
]


@pytest.mark.parametrize("line", OUTAGE)
def test_a_lookup_failure_is_called_what_it_is(line):
    advice = record_stream.known_fix(line)

    assert "internet connection or DNS" in advice
    assert "Cloudflare" not in advice
    assert "pip install" not in advice, (
        "nothing is installable when the machine cannot resolve a name")


def test_it_says_the_recorder_recovers_on_its_own():
    """The right action during an outage is none. Saying so is what stops
    someone reinstalling packages at 3am."""
    advice = record_stream.known_fix(OUTAGE[0])

    assert "keeps retrying" in advice


def test_a_kick_failure_that_is_not_dns_still_gets_the_real_advice():
    advice = record_stream.known_fix(
        "WARNING: no impersonate target is available")

    assert "curl-cffi" in advice


def test_the_impersonation_advice_no_longer_pins_a_version():
    """It named 0.15.0. yt-dlp now builds against 0.16, so that pin
    downgrades below what yt-dlp needs - the advice had become the bug."""
    advice = record_stream._CURL_CFFI_FIX

    assert "curl_cffi==" not in advice
    assert 'yt-dlp[default,curl-cffi]' in advice


def test_the_advice_still_covers_the_standalone_exe_case():
    """A pip-installed curl_cffi is invisible to the bundled yt-dlp.exe,
    which is how this looked like a broken install for an evening."""
    advice = record_stream._CURL_CFFI_FIX

    assert "--list-impersonate-targets" in advice


def test_dns_beats_kick_in_the_matching_order():
    """Both markers appear in the same line. The specific one has to win,
    which is the whole reason the outage was misdiagnosed."""
    both = ("ERROR: [kick:live] stackswopo1k: could not resolve host: "
            "kick.com")

    assert "internet connection or DNS" in record_stream.known_fix(both)


def test_a_working_kick_line_gets_no_advice_at_all():
    """"kick" appears in every ordinary progress line; attaching advice to
    those would put installation instructions on a healthy recording."""
    assert record_stream.known_fix(
        "[kick:live] stackswopo1k: Downloading API JSON") == ""


def test_a_mid_recording_403_is_still_its_own_thing():
    advice = record_stream.known_fix(
        "ERROR: [download] Got error: HTTP Error 403: Forbidden")

    assert "fragment URLs expired" in advice
    assert "DNS" not in advice
