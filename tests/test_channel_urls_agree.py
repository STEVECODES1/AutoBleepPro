"""One channel, one URL, everywhere.

Rumble serves two different address shapes - rumble.com/c/NAME and
rumble.com/user/NAME - and they are not interchangeable. This repo used
both:

    clip_maker.WATERMARK_TEXT    rumble.com/c/BinScripts     <- burned in
    youtube_upload.CHANNEL_RUMBLE rumble.com/c/BinScripts
    config rumble.channel_url    https://rumble.com/c/BinScripts
    config descriptions          https://rumble.com/user/BinScripts
    config rss_url               https://rumble.com/user/BinScripts/...

The watermark is the expensive one: it is rendered into the picture of
every clip, so a wrong address there ships on work that is already
published and cannot be edited afterwards.

There is a second name in this project that is NOT the channel and must
never be substituted for it: stackswopo10k is the source the VODs are
pulled FROM. Clips are republished to BinScripts. Reading the source as
the destination is exactly the mistake this file exists to stop.
"""

from __future__ import annotations

import json
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHANNEL = "rumble.com/user/BinScripts"
WRONG_SHAPE = "rumble.com/c/"

# Where the VODs come from. A different channel, on purpose.
SOURCE = "rumble.com/user/stackswopo10k"

SEARCHED = (".py", ".json", ".bat", ".md")


def _files():
    for root, dirs, names in os.walk(_REPO):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "__pycache__", "tests", "node_modules"}]
        for name in names:
            if name.endswith(SEARCHED):
                yield os.path.join(root, name)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return ""


def test_nothing_uses_the_other_url_shape():
    offenders = [os.path.relpath(p, _REPO) for p in _files()
                 if WRONG_SHAPE in _read(p)]

    assert not offenders, (
        f"these still use rumble.com/c/, which is a different address "
        f"from the channel: {offenders}")


def test_the_watermark_burned_into_every_clip_is_right():
    """It is rendered into the picture. A wrong address here ships on
    published work and cannot be edited afterwards."""
    from autoreel.clip_maker import WATERMARK_TEXT

    assert WATERMARK_TEXT == CHANNEL


def test_the_configured_channel_url_matches_the_watermark():
    """The watermark and the link in the caption point the same viewer at
    the same place."""
    from autoreel.clip_maker import WATERMARK_TEXT

    with open(os.path.join(_REPO, "auto_uploader", "config.json"),
              encoding="utf-8") as handle:
        shipped = json.load(handle)

    assert shipped["rumble"]["channel_url"].endswith(WATERMARK_TEXT)


def test_the_rss_url_names_the_same_channel():
    with open(os.path.join(_REPO, "auto_uploader", "config.json"),
              encoding="utf-8") as handle:
        shipped = json.load(handle)

    assert CHANNEL in shipped["rumble"]["rss_url"]


def test_every_rumble_link_in_the_shipped_config_agrees():
    """Descriptions, captions and the channel_url are read by different
    code paths and drift apart quietly."""
    raw = _read(os.path.join(_REPO, "auto_uploader", "config.json"))
    found = set(re.findall(r"rumble\.com/(?:c|user)/([A-Za-z0-9_-]+)", raw))

    assert found <= {"BinScripts", "stackswopo10k"}, (
        f"an unexpected Rumble channel is named in config.json: {found}")


# ── the source is not the destination ────────────────────────────────────

def test_the_vod_source_is_still_the_source():
    """CLIP-VODS.bat pulls from stackswopo10k. That is deliberate and
    separate from where clips are published."""
    body = _read(os.path.join(_REPO, "CLIP-VODS.bat"))

    assert SOURCE in body


def test_the_source_channel_is_never_used_as_the_destination():
    """Promoting the channel the VODs were taken FROM sends every viewer
    somewhere that is not this account."""
    from autoreel.clip_maker import WATERMARK_TEXT

    with open(os.path.join(_REPO, "auto_uploader", "config.json"),
              encoding="utf-8") as handle:
        shipped = json.load(handle)

    assert "stackswopo10k" not in WATERMARK_TEXT
    assert "stackswopo10k" not in shipped["rumble"]["channel_url"]
    promote = shipped.get("zernio", {}).get("promote", {})
    for destination, template in promote.items():
        assert "stackswopo10k" not in template, destination


def test_the_promo_line_resolves_to_the_real_channel():
    """{rumble} is filled from rumble.channel_url, so this is what a
    TikTok viewer actually reads."""
    import sys

    for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
        if path not in sys.path:
            sys.path.insert(0, path)
    from utils.clip_queue import promo_line

    with open(os.path.join(_REPO, "auto_uploader", "config.json"),
              encoding="utf-8") as handle:
        shipped = json.load(handle)

    line = promo_line("zernio_tiktok", shipped)

    assert CHANNEL in line
    assert "stackswopo10k" not in line
