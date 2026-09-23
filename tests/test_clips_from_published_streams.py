"""
A stream that is already on both platforms is a stream nobody has
clipped yet.

The run that found this did everything right and produced nothing:

    [YouTube] 'aye bruh wtw' ... already exists -> https://youtu.be/...
              (skipping, still trying Rumble)
    [Rumble]  Video already exists on Rumble -> https://rumble.com/...
    [Report]  Posted the receipt to Discord: All 2 landed.
    [Watch]   Done with 'aye bruh wtw' ... Watching for the next one...

Two platforms, both green, 3.2 GB of stream, and not one clip. There
was no error and nothing said a step had been skipped.

The cause: clip-cutting sat inside `if newly_uploaded:`, so it fired
only when THIS run pushed bytes somewhere. "Both platforms already have
it" and "do not clip it" are unrelated facts, and the first was being
read as the second.

It survived as long as it did because there are TWO "already uploaded"
checks and only one of them had ever been fixed:

  - the content-hash ledger, which returns early and called the
    clipper on its way out - that path worked;
  - the per-platform checks (YouTube's channel listing, Rumble's local
    title history), which let the run continue, skip both uploads,
    leave newly_uploaded empty and fall straight past the clipper.

Which path a given stream took depended on whether its hash happened to
be in the ledger. Same shape as the crop default and the rumble config
before it: a fix lands on one route while a second route keeps the old
behaviour, and nothing fails loudly enough to notice.
"""

import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

_MAIN = os.path.join(_REPO, "auto_uploader", "main.py")


def _source():
    with open(_MAIN, encoding="utf-8") as handle:
        return handle.read()


# ── the gate itself ───────────────────────────────────────────────────

def test_clipping_does_not_sit_inside_the_newly_uploaded_gate():
    """The call must be at function indent, not nested in `if
    newly_uploaded:`."""
    lines = _source().split("\n")
    calls = [n for n, line in enumerate(lines)
             if "cut_clips_from_stream(" in line and "def " not in line]
    assert calls, "nothing calls cut_clips_from_stream any more"

    for number in calls:
        # Walk back to the nearest enclosing block header and check that
        # newly_uploaded is not it.
        indent = len(lines[number]) - len(lines[number].lstrip())
        for previous in range(number - 1, max(0, number - 400), -1):
            body = lines[previous]
            if not body.strip():
                continue
            here = len(body) - len(body.lstrip())
            if here < indent:
                assert "newly_uploaded" not in body, (
                    f"line {number + 1} clips only when something was "
                    f"newly uploaded: {body.strip()}")
                break


def test_the_clipper_runs_before_the_source_is_retired():
    """retire_source() honours cleanup.source_video, and 'delete' means
    the VOD stops existing. Clipping it afterwards clips nothing."""
    body = _source()
    cut = body.rindex("cut_clips_from_stream(")
    retire = body.rindex("\n    retire_source()")
    assert cut < retire, (
        "the VOD is retired before the clips are cut - with "
        "cleanup.source_video 'delete' that is a stream deleted unclipped")


def test_the_upload_path_clips_in_exactly_one_place():
    """Two copies of this logic inside one function is what let one of
    them keep the bug - the hash-ledger route was fixed and the
    already-on-the-channel route, twenty lines of near-identical code
    away, was not.

    --clips and --clips-from call make_clips directly and are meant to:
    those are someone typing a command, not the upload deciding."""
    body = _source()
    assert body.count("def cut_clips_from_stream(") == 1

    start = body.index("\ndef process_file(")
    end = body.index("\ndef ", start + 10)
    process_file = body[start:end]
    assert "make_clips(" not in process_file, (
        "process_file cuts clips itself instead of going through "
        "cut_clips_from_stream")
    assert "cut_clips_from_stream(" in process_file


def test_the_hash_ledger_path_shares_the_same_clipper():
    body = _source()
    start = body.index("def _clip_already_uploaded(")
    end = body.index("\ndef ", start + 10)
    assert "cut_clips_from_stream(" in body[start:end]


# ── cut once, not once per route ──────────────────────────────────────

class _Cfg:
    class general:
        pass

    clips = {"auto_from_streams": True, "count": 3}


@pytest.fixture
def cfg(tmp_path):
    settings = _Cfg()
    logs = tmp_path / "logs"
    logs.mkdir()
    watch = tmp_path / "watch"
    watch.mkdir()
    settings.general.logs_folder = str(logs)
    settings.general.watch_folder = str(watch)
    # get_stream_title reads this when no title is handed in.
    settings.general.filename_channel_prefixes = []
    return settings


@pytest.fixture
def vod(tmp_path):
    path = tmp_path / "'aye bruh wtw' 9-18-26 Stackswopo Stream.ts"
    path.write_bytes(b"x" * 4096)
    return str(path)


def test_a_stream_already_in_the_ledger_is_not_cut_again(cfg, vod, monkeypatch):
    """Both routes can now reach the clipper for the same file on
    different runs. The ledger is what stops that being two sets of the
    same clips, posted twice."""
    import main
    from utils.clip_watch import remember

    remember(cfg.general.logs_folder, vod, 5)

    def explode(*args, **kwargs):
        raise AssertionError("clipped a stream that was already clipped")

    monkeypatch.setattr("utils.clip_runner.make_clips", explode)
    assert main.cut_clips_from_stream(cfg, vod, is_clip=False) == 0


def test_a_clip_is_never_clipped(cfg, vod, monkeypatch):
    import main

    def explode(*args, **kwargs):
        raise AssertionError("a clip of a clip is not a thing")

    monkeypatch.setattr("utils.clip_runner.make_clips", explode)
    assert main.cut_clips_from_stream(cfg, vod, is_clip=True) == 0


def test_auto_from_streams_off_means_off(cfg, vod, monkeypatch):
    import main

    cfg.clips = {"auto_from_streams": False}

    def explode(*args, **kwargs):
        raise AssertionError("clipped with auto_from_streams off")

    monkeypatch.setattr("utils.clip_runner.make_clips", explode)
    assert main.cut_clips_from_stream(cfg, vod, is_clip=False) == 0


def test_a_crash_is_not_recorded_as_clipped(cfg, vod, monkeypatch):
    """An HTTP 503 from the model is not a verdict about the video. If a
    crash wrote the ledger, one bad night would cost the stream its
    clips permanently."""
    import main
    from utils.clip_watch import was_clipped

    def explode(*args, **kwargs):
        raise RuntimeError("model returned 503")

    monkeypatch.setattr("utils.clip_runner.make_clips", explode)
    assert main.cut_clips_from_stream(cfg, vod, is_clip=False) == 0
    assert not was_clipped(cfg.general.logs_folder, vod)


def test_a_stream_with_nothing_worth_clipping_is_still_done(cfg, vod,
                                                            monkeypatch):
    """Zero clips is an answer. Asking again re-transcribes the whole
    VOD on every pass, forever."""
    import main
    from utils.clip_watch import was_clipped

    class _Run:
        skipped_reason = ""
        clips = []

    monkeypatch.setattr("utils.clip_runner.make_clips",
                        lambda *a, **k: _Run())
    monkeypatch.setattr("utils.clip_runner.print_run", lambda run: None)
    monkeypatch.setattr(main, "_deliver_clips", lambda run, cfg: 0)

    assert main.cut_clips_from_stream(cfg, vod, is_clip=False) == 0
    assert was_clipped(cfg.general.logs_folder, vod)


def test_the_title_the_upload_worked_out_is_reused(cfg, vod, monkeypatch):
    """Re-reading it off a file that has since moved to uploaded/ costs a
    probe and can come back with the generic 'Gaming Stream' fallback -
    which is also what Rumble's title dedup keys on."""
    import main

    seen = {}

    class _Run:
        skipped_reason = ""
        clips = []

    def record(cfg_, source, title, **kwargs):
        seen["title"] = title
        return _Run()

    monkeypatch.setattr("utils.clip_runner.make_clips", record)
    monkeypatch.setattr("utils.clip_runner.print_run", lambda run: None)
    monkeypatch.setattr(main, "_deliver_clips", lambda run, cfg: 0)
    monkeypatch.setattr(main, "get_stream_title",
                        lambda *a, **k: "Gaming Stream")

    main.cut_clips_from_stream(cfg, vod, is_clip=False,
                               title='"aye bruh wtw" 9/18/26 Stackswopo Stream')
    assert seen["title"] == '"aye bruh wtw" 9/18/26 Stackswopo Stream'


def test_the_transcript_is_made_when_the_censor_pass_never_ran(cfg, vod,
                                                               monkeypatch):
    """A stream already on YouTube never reaches do_youtube(), so the
    censor pass never runs, so there is no transcript to score. That is
    the exact shape of the run this file is named after."""
    import main

    seen = {}

    class _Run:
        skipped_reason = ""
        clips = []

    def record(cfg_, source, title, **kwargs):
        seen.update(kwargs)
        return _Run()

    monkeypatch.setattr("utils.clip_runner.make_clips", record)
    monkeypatch.setattr("utils.clip_runner.print_run", lambda run: None)
    monkeypatch.setattr(main, "_deliver_clips", lambda run, cfg: 0)

    main.cut_clips_from_stream(cfg, vod, is_clip=False, title="t")
    assert seen.get("transcribe_if_needed") is True


# ═════════════════════════════════════════════════════════════════════════════
# A renamed duplicate recording must not be re-clipped
#
# "'halfa mill' 9-21-26 Stackswopo Stream_2.ts" hash-matched a file the
# upload dedup already knew as fully uploaded - the same broadcast, saved
# under a second name. cut_clips_from_stream() now accepts the hash the
# upload dedup already computed and threads it into the ledger, so a
# renamed duplicate is recognised as the same video instead of getting
# the whole pipeline - transcription, picks, posting - run on it again.
# ═════════════════════════════════════════════════════════════════════════════

def test_both_call_sites_pass_the_already_computed_hash():
    """Read out of the source: file_hash is computed once, early, for
    the upload dedup - both places that can reach cut_clips_from_stream
    must reuse it rather than leave the ledger keyed on name+size."""
    body = _source()

    already_uploaded_call = body[body.index(
        "_clip_already_uploaded(cfg, video_path, is_clip, "
        "content_hash=file_hash)"):]
    assert already_uploaded_call.startswith(
        "_clip_already_uploaded(cfg, video_path, is_clip, "
        "content_hash=file_hash)")

    assert "content_hash=file_hash) or 0" in body, (
        "the main cut_clips_from_stream() call must also pass the "
        "already-computed file_hash")


def test_a_stream_already_clipped_under_another_name_is_recognised(
        cfg, vod, monkeypatch):
    """The exact bug, end to end: a hash match must stop the pipeline
    before it re-transcribes and re-picks a duplicate recording."""
    import main
    from utils.clip_watch import remember

    remember(cfg.general.logs_folder, vod, 7, content_hash="samehash")

    renamed = os.path.join(os.path.dirname(vod),
                           "'halfa mill' Stream_2.ts")
    with open(renamed, "wb") as handle:
        handle.write(b"x" * 4096)

    def explode(*args, **kwargs):
        raise AssertionError(
            "re-transcribed and re-picked a stream already clipped "
            "under a different filename")

    monkeypatch.setattr("utils.clip_runner.make_clips", explode)

    assert main.cut_clips_from_stream(
        cfg, renamed, is_clip=False, content_hash="samehash") == 0


def test_a_different_hash_under_the_same_kind_of_name_is_still_cut(
        cfg, vod, monkeypatch):
    """Genuinely different content must not be blocked just because a
    hash was offered - only a matching one skips the pipeline."""
    import main

    seen = {}

    class _Run:
        skipped_reason = ""
        clips = []

    def record(cfg_, source, title, **kwargs):
        seen["called"] = True
        return _Run()

    monkeypatch.setattr("utils.clip_runner.make_clips", record)
    monkeypatch.setattr("utils.clip_runner.print_run", lambda run: None)
    monkeypatch.setattr(main, "_deliver_clips", lambda run, cfg: 0)

    main.cut_clips_from_stream(cfg, vod, is_clip=False, title="t",
                               content_hash="a-fresh-hash-nobody-has-seen")
    assert seen.get("called") is True
