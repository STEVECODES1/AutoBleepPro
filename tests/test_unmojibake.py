"""tools/unmojibake.py: garbled emoji come back, real text is left alone."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from unmojibake import fix, fix_tree  # noqa: E402


def garble(text):
    """What a PowerShell ANSI read of a UTF-8 file does to it."""
    return text.encode("utf-8").decode("cp1252", errors="replace")


def test_emoji_lines_come_back():
    for line in ("📌 Uncut on Rumble", "🔴 Watch live", "🔔 Subscribe", "🎮 Full streams",
                 "Full streams: YouTube STACKSWOPO VODS · Rumble BinScripts"):
        bad = line.encode("utf-8").decode("cp1252")
        assert bad != line
        assert fix(bad) == line


def test_byte_youtube_stripped_is_put_back():
    # ✂️ is E2 9C 82 EF B8 8F; 8F has no cp1252 character and YouTube drops
    # the control character it became.
    posted = "âœ‚ï¸   Best moments"
    assert fix(posted) == "✂️   Best moments"
    assert fix("ðŸ™ thanks") == "\U0001F64F thanks"


def test_real_text_untouched():
    for text in ("café", "naïve été", "ok 😀", "plain ascii", "✂️ already fine", ""):
        assert fix(text) == text


def test_twice_garbled():
    once = "📌".encode("utf-8").decode("cp1252")
    twice = once.encode("utf-8").decode("cp1252")
    assert fix(twice) == "📌"


def test_tree():
    bad = "📌 x".encode("utf-8").decode("cp1252")
    assert fix_tree({"a": [bad, 1], "b": {"c": bad}}) == {"a": ["📌 x", 1], "b": {"c": "📌 x"}}
