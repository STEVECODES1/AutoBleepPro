"""A real Instagram post read:

    'I Feel Sorry' Stackswoop Stream - Clip 01 CENSORED
    silence-large-v3-turbo-ae263651d1

The file posted was the censor pass's copy, "<clip>_CENSORED_<settings>
.mp4", and the title lookup searched for notes named after THAT file -
there are none, so the caption fell back to the file name. Rumble, posting
the clip itself, carried the real line: "Threatening to hit someone with
an oopsie daisy".
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.clip_queue import clip_key  # noqa: E402
from utils.social_promoter import clip_title, plain_clip_name  # noqa: E402

CLIP = "'I Feel Sorry' Stackswoop Stream - Clip 01"
CENSORED = f"{CLIP}_CENSORED_silence-large-v3-turbo-ae263651d1"


def test_the_censored_copy_finds_the_clips_own_title(tmp_path):
    (tmp_path / f"{CLIP}.txt").write_text(
        "Threatening to hit someone with an oopsie daisy", encoding="utf-8")
    posted = tmp_path / f"{CENSORED}.mp4"
    posted.write_bytes(b"video")

    assert clip_title(str(posted)) == \
        "Threatening to hit someone with an oopsie daisy"


def test_with_no_notes_the_fallback_never_shows_the_censor_settings(tmp_path):
    title = clip_title(str(tmp_path / f"{CENSORED}.mp4"))
    assert "CENSORED" not in title.upper()
    assert "large-v3" not in title


def test_the_censored_copy_is_the_same_clip_for_dedup():
    assert clip_key(f"/watch/{CLIP}.mp4") == \
        clip_key(f"/censored/{CENSORED}.mp4") == \
        clip_key(f"/censored/_vertical_{CENSORED}.mp4")


def test_plain_clip_name_leaves_ordinary_names_alone():
    assert plain_clip_name(CLIP) == CLIP
    assert plain_clip_name(f"_vertical_{CENSORED}") == CLIP
