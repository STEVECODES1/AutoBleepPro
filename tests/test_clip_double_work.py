"""
Two ways one clip became two, and got worse on the way.

Both were visible in the filenames and in the log, and neither was read.

1. UPLOADED TWICE.  Rumble's form reads the FILENAME, not the container,
   and refuses .ts - which is what every recording here is. So the
   uploader hard-links the recording to "<name>._rumble_upload.mp4".
   That link is made beside the original, which is IN THE WATCH FOLDER,
   so the watcher saw a new .mp4, found it stable (it is a link to a
   finished file, it never grows) and queued it:

       [Queue] Stackswopo - IDK - 09-20-26 (FULL STREAM)._rumble_upload.mp4
               is next - 2 already waiting.

2. CROPPED TWICE.  Every clip out of ClipMaker is already 1080x1920.
   main.py's vertical_path re-framed it to 9:16 anyway - keeping the
   middle of a frame that is already the middle of a frame, so the shot
   zooms in and its sides are gone. social_promoter._vertical_copy has
   guarded against this for a while, and its comment says why: "it
   re-runs the crop, so a region profile would crop the crop". The
   second copy of the same logic never got the guard. The files said so:

       _vertical__vertical_Stackswopo - Idk ... Clip 01_CENSORED_...mp4
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

from utils.file_watcher import is_intermediate_download  # noqa: E402


# ── 1. the alias must not look like a new video ───────────────────────

def test_the_rumble_alias_is_not_picked_up_as_a_second_video():
    for name in (
        "Stackswopo - IDK - 09-20-26 (FULL STREAM)._rumble_upload.mp4",
        "'just saying hi' 9-20-26 Stackswopo Stream._rumble_upload.mp4",
        "clip 01._RUMBLE_UPLOAD.mp4",
    ):
        assert is_intermediate_download(name), \
            f"{name} would be uploaded a second time"


def test_the_vertical_reframe_is_not_uploaded_as_its_own_clip():
    """The worst of the three. _vertical_copy writes its 9:16 re-frame
    into the same directory as its input - which for a clip is the watch
    folder - so the ALREADY-CROPPED copy appeared as a new video and was
    uploaded as one. main.py's _RENDERED_CLIP knew these were outputs;
    the watcher, which is what decides if something uploads, did not."""
    for name in ("_vertical_Stackswopo - Idk - Clip 01.mp4",
                 "_vertical__vertical_Clip 01_CENSORED_silence.mp4"):
        assert is_intermediate_download(name), f"{name} would upload again"


def test_the_censored_copy_is_not_uploaded_as_its_own_clip():
    assert is_intermediate_download(
        "Clip 01_CENSORED_silence-large-v3-turbo-f9c0fa9813.mp4")


def test_a_users_own_file_is_never_mistaken_for_a_render():
    """The two mistakes do not cost the same. A missed artefact is one
    duplicate upload; a false positive is a real video that never
    uploads and never says why. So the match is anchored on the leading
    underscore every render has."""
    for name in ("vertical video of my stream.mp4",
                 "vertical.mp4",
                 "my rumble upload.mp4",
                 "the censored cut.mp4"):
        assert not is_intermediate_download(name), \
            f"{name} is a real video and would never be uploaded"


def test_the_real_recording_is_still_picked_up():
    """The guard must not be so wide it swallows the actual video."""
    for name in ("Stackswopo - IDK - 09-20-26 (FULL STREAM).ts",
                 "Stackswopo - Idk - Full Stream - Clip 01.mp4",
                 "my rumble upload.mp4"):
        assert not is_intermediate_download(name), f"{name} would never upload"


def test_the_downloader_artefacts_still_match():
    """The pattern this was added to - regressing it would put half-muxed
    yt-dlp output back in the upload queue."""
    for name in ("Stream.f140.mp4", "Stream.f299.mp4", "Stream.temp.mp4",
                 "Stream.part.mp4", "Stream.ytdl.mp4"):
        assert is_intermediate_download(name)


def test_the_batch_path_uses_the_same_filter():
    """--batch builds its own candidate list. If it did not call this,
    the alias would be skipped by --watch and uploaded by --batch."""
    body = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                encoding="utf-8").read()
    candidates = body.index("if not candidates:")
    window = body[max(0, candidates - 700):candidates]
    assert "is_intermediate_download(f)" in window


# ── 2. a 9:16 clip must not be cropped to 9:16 again ──────────────────

def test_vertical_path_checks_the_shape_before_re_encoding():
    """The guard social_promoter has had all along, in the copy that
    never got it. Asserted on the source because the function is a
    closure over a whole upload run and cannot be called directly."""
    body = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                encoding="utf-8").read()

    start = body.index("    def vertical_path(source: str) -> str:")
    end = body.index("    def instagram_clip_path()", start)
    block = body[start:end]

    assert "is_already_vertical" in block, \
        "an already-9:16 clip is re-cropped to 9:16"
    # ...and before the encode, or it has not saved anything.
    assert block.index("is_already_vertical") < block.index("make_vertical("), \
        "the shape is checked after the re-encode it was meant to avoid"


def test_the_other_copy_of_this_logic_still_has_its_guard():
    """Two functions do this job. Fixing one and leaving the other is
    the exact shape of bug that produced the double crop."""
    body = open(os.path.join(_REPO, "auto_uploader", "utils",
                             "social_promoter.py"), encoding="utf-8").read()

    start = body.index("def _vertical_copy(")
    block = body[start:start + 2000]
    assert "is_already_vertical" in block
    assert block.index("is_already_vertical") < block.index("make_vertical(")


def test_a_skipped_reframe_is_remembered_as_itself():
    """vertical_path caches by source. Returning early without recording
    the decision means the shape is probed again for every platform that
    asks - three ffprobe calls per clip instead of one."""
    body = open(os.path.join(_REPO, "auto_uploader", "main.py"),
                encoding="utf-8").read()
    start = body.index("        if is_already_vertical(source):")
    assert "_vertical[source] = source" in body[start:start + 200]
