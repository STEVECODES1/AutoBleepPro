"""
Showing the model the frames, not just the transcript.

A Monkey-app clip lands on a face reaction and the transcript says
"...what". Someone gets run over in GTA and the transcript says nothing
at all. A better language model reading the same blind transcript still
cannot see the joke - the frames are the missing input.
"""

import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from autoreel.highlights import Highlight
from autoreel.llm_highlights import (VISION_MAX_CANDIDATES, VISION_NOTE,
                                     build_vision_contents, rank)
from autoreel.vision_frames import (FRAMES_PER_CANDIDATE, SAMPLE_POINTS,
                                    as_inline_data, sample_points, still_args)


def _candidates(n):
    return [Highlight(start=i * 100.0, end=i * 100.0 + 30.0,
                      text=f"line {i}", score=1.0) for i in range(n)]


def test_frames_are_not_taken_from_the_opening():
    """A clip's first frame is usually the tail of whatever came before,
    and the payoff sits about two thirds through."""
    assert 0.0 not in SAMPLE_POINTS
    assert max(SAMPLE_POINTS) > 0.5

    points = sample_points(100.0, 140.0)
    assert points[0] > 100.0 and points[-1] < 140.0


def test_stills_are_small():
    """A model reads an image at a fixed token cost whatever its size, so
    a 1080p still costs the same and takes twenty times longer to send."""
    args = still_args("/in.mp4", 42.0, "/out.jpg")

    assert "scale=512:-2" in " ".join(args)
    assert args[args.index("-frames:v") + 1] == "1"


def test_the_seek_comes_before_the_input():
    """Forty candidates is eighty of these. -ss after -i decodes up to
    the mark every time, which on a three-hour VOD is minutes each."""
    args = still_args("/in.mp4", 4000.0, "/out.jpg")

    assert args.index("-ss") < args.index("-i")


def test_each_candidate_gets_its_text_and_its_frames():
    parts = build_vision_contents(_candidates(2), 1, "/in.mp4",
                                  grab=lambda *a: [b"jpeg1", b"jpeg2"])

    images = [p for p in parts if "inline_data" in p]
    assert len(images) == 2 * FRAMES_PER_CANDIDATE
    assert any("line 0" in p.get("text", "") for p in parts)
    assert any("line 1" in p.get("text", "") for p in parts)


def test_a_candidate_whose_frames_fail_is_still_judged_on_its_words():
    """One unreadable stretch must not cost the whole pass."""
    def flaky(source, start, end):
        if start == 0.0:
            raise OSError("unreadable")
        return [b"jpeg"]

    parts = build_vision_contents(_candidates(2), 1, "/in.mp4", grab=flaky)

    assert len([p for p in parts if "inline_data" in p]) == 1
    assert any("line 0" in p.get("text", "") for p in parts)


def test_only_the_top_candidates_get_pictures():
    """Every image is tokens and upload time, and the text pass has
    already sorted the list."""
    assert VISION_MAX_CANDIDATES < 60


def test_the_model_is_told_the_frames_are_the_point():
    assert "SEE" in VISION_NOTE
    assert "visual" in VISION_NOTE.lower()


def test_an_image_is_sent_as_base64_jpeg():
    part = as_inline_data(b"\xff\xd8ffd8")

    assert part["inline_data"]["mime_type"] == "image/jpeg"
    assert isinstance(part["inline_data"]["data"], str)


def test_vision_is_skipped_without_a_source_file(monkeypatch):
    """Nothing to sample - the text path is the whole behaviour, exactly
    as it was before this existed."""
    import autoreel.llm_highlights as llm

    monkeypatch.setattr(llm, "available", lambda p="": ("gemini", "key"))
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "gemini-x")

    def explode(*a, **k):
        raise AssertionError("it tried to sample frames with no source")

    monkeypatch.setattr(llm, "build_vision_contents", explode)
    monkeypatch.setattr(llm, "_ask_gemini",
                        lambda *a: '{"clips":[{"index":1,"score":90,"title":"t"}]}')

    chosen = rank(_candidates(3), 1, source_path="")

    assert chosen and chosen[0].hook == "t"


def test_a_failed_vision_call_falls_back_to_the_words(monkeypatch):
    """A model that cannot see is the behaviour this had all along, and
    it is much better than no clips."""
    import autoreel.llm_highlights as llm

    monkeypatch.setattr(llm, "available", lambda p="": ("gemini", "key"))
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "gemini-x")
    monkeypatch.setattr(llm, "build_vision_contents",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no ffmpeg")))
    monkeypatch.setattr(llm, "_ask_gemini",
                        lambda *a: '{"clips":[{"index":2,"score":80,"title":"words"}]}')

    chosen = rank(_candidates(3), 1, source_path="/in.mp4")

    assert chosen and chosen[0].hook == "words"


def test_no_readable_frames_is_named_as_that(monkeypatch, capsys):
    """Different from a rejected request, and fixed a different way."""
    import autoreel.llm_highlights as llm

    monkeypatch.setattr(llm, "available", lambda p="": ("gemini", "key"))
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "gemini-x")
    monkeypatch.setattr(llm, "build_vision_contents", lambda *a, **k: [])
    monkeypatch.setattr(llm, "_ask_gemini",
                        lambda *a: '{"clips":[{"index":1,"score":9,"title":"t"}]}')

    rank(_candidates(2), 1, source_path="/in.mp4")

    assert "no frames could be read" in capsys.readouterr().out


def test_a_failed_vision_pass_says_so(monkeypatch, capsys):
    """Silence here is indistinguishable from the model having no
    opinion, which is how a broken vision pass goes unnoticed."""
    import autoreel.llm_highlights as llm

    monkeypatch.setattr(llm, "available", lambda p="": ("gemini", "key"))
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "gemini-x")
    monkeypatch.setattr(llm, "build_vision_contents",
                        lambda *a, **k: [{"inline_data": {"data": "x"}}])
    monkeypatch.setattr(llm, "_ask_gemini_vision",
                        lambda *a: ("", "HTTP 400: request too large"))
    monkeypatch.setattr(llm, "_ask_gemini",
                        lambda *a: '{"clips":[{"index":1,"score":9,"title":"t"}]}')

    rank(_candidates(2), 1, source_path="/in.mp4")

    told = capsys.readouterr().out
    assert "vision pass failed" in told
    assert "request too large" in told, "the reason has to survive"


def test_the_image_count_is_capped(monkeypatch):
    """A request that grows past the limit is refused for its SIZE, and
    that arrives as an empty reply - identical to no opinion."""
    from autoreel.llm_highlights import VISION_MAX_IMAGES

    parts = build_vision_contents(_candidates(40), 20, "/in.mp4",
                                  grab=lambda *a: [b"a", b"b"])

    assert len([p for p in parts if "inline_data" in p]) <= VISION_MAX_IMAGES


def test_an_unusable_reply_is_reported(monkeypatch, capsys):
    import autoreel.llm_highlights as llm

    monkeypatch.setattr(llm, "available", lambda p="": ("gemini", "key"))
    monkeypatch.setattr(llm, "resolve_model", lambda *a, **k: "gemini-x")
    monkeypatch.setattr(llm, "_ask_gemini", lambda *a: "not json at all")

    assert rank(_candidates(2), 1) is None
    assert "nothing usable" in capsys.readouterr().out
