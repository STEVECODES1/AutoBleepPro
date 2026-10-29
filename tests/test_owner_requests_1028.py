"""The owner's 10-28 list: clip layout, titles, intro, policy, Rumble link,
recap captions, recorder."""
import os
import shutil
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for extra in (os.path.join(ROOT, "auto_uploader"), os.path.join(ROOT, "tools"), ROOT):
    if extra not in sys.path:
        sys.path.insert(0, extra)


# ── 3. clip layout is locked ─────────────────────────────────────────────

@pytest.mark.parametrize("clips", [
    {"profile": "monkey"}, {"profile": "monkey_stack"}, {"profile": "gta"},
    {"crop_strategy": "face"}, {"crop_strategy": "center"}, {"profile": "auto"}, {}])
def test_every_clip_is_the_blurred_whole_frame(clips):
    from autoreel.crop_strategy import CROP_FIT, resolve_crop_strategy

    assert resolve_crop_strategy({"clips": clips}) == CROP_FIT


def test_only_an_explicit_unlock_changes_the_layout():
    from autoreel.crop_strategy import resolve_crop_strategy

    assert resolve_crop_strategy({"clips": {"unlock_layout": True,
                                            "profile": "monkey"}}) == "region"


# ── 4. titles ────────────────────────────────────────────────────────────

FORMAT = "Stackswopo - {TITLE} - {date} (FULL STREAM)"


@pytest.mark.parametrize("name", [
    "Stackswopo - STIZZ SHOW - 10-25-26 (FULL STREAM)",
    "STIZZ SHOW (FULL STREAM)",
    "STIZZ SHOW - 10-25-26",
    "Stackswopo - STIZZ SHOW - 10-25-26 (FULL STREAM) - 10-25-26 (FULL STREAM)",
    "STIZZ SHOW"])
def test_the_full_stream_frame_is_said_once(name):
    from utils.templating import build_title

    assert build_title(name, "10-25-26", FORMAT) == \
        "Stackswopo - STIZZ SHOW - 10-25-26 (FULL STREAM)"


def test_thumbnail_text_is_the_clean_title_only():
    from autoreel.thumbnail import thumb_words

    assert thumb_words("Stackswopo - STIZZ SHOW - 10-25-26 (FULL STREAM)") == "STIZZ SHOW"
    assert thumb_words("THE RETURN 2026-10-09 14:55") == "THE RETURN"


def test_a_recorder_name_is_recognised():
    from utils.text_guard import looks_like_machine_name

    assert looks_like_machine_name(
        "Stackswopo Youtube Live Stackswopoman10K 14 32 Live 13 (Clip 1)")
    assert not looks_like_machine_name("Somebody call an EMS!")


def test_a_clip_without_a_line_is_never_titled_from_its_file_name(tmp_path):
    import main

    clip = tmp_path / "Stackswopo Youtube Live Stackswopoman10K 14 32 Live 13 - Clip 01.mp4"
    clip.write_bytes(b"x")
    title = main._clip_title_without_filename(str(clip))
    assert "Stackswopoman10K" not in title and "Youtube Live" not in title
    assert title == "Stackswopo moment (Clip 1)"


def test_the_clip_line_is_found_in_the_clips_folder(tmp_path):
    import main

    class G:
        watch_folder = str(tmp_path / "watch")
        uploaded_folder = ""
        censored_folder = ""

    class C:
        general = G()
        project_root = str(tmp_path)

    (tmp_path / "clips").mkdir()
    (tmp_path / "watch").mkdir()
    stem = "Stackswopo Youtube Live Stackswopoman10K 14 32 Live 13 - Clip 01"
    (tmp_path / "clips" / f"{stem}_line.txt").write_text("Call a EMS!", encoding="utf-8")
    clip = tmp_path / "watch" / f"{stem}.mp4"
    clip.write_bytes(b"x")
    assert main._clip_line_anywhere(C(), str(clip)) == "Call a EMS!"


# ── 6. the YouTube title guard ───────────────────────────────────────────

@pytest.mark.parametrize("title", [
    "Locked in a cage with the gorillas and monkeys",
    "he look like a monkey fr",
    "Stackswopo - HE SMASHED HIS OWN COUSIN - 10-25-26 (FULL STREAM)",
    "incest stories with chat"])
def test_youtube_never_gets_these_titles(title):
    from utils import text_guard

    assert text_guard.youtube_title_risk(title)
    assert text_guard.youtube_title_risk(text_guard.title(title)) == ""


@pytest.mark.parametrize("title", [
    "MONKEY APP TROLLING", "MONKEY COMPILATION #12 | Stackswopo",
    "Somebody call an EMS!", "Stackswopo - STIZZ SHOW - 10-25-26 (FULL STREAM)"])
def test_ordinary_titles_pass(title):
    from utils import text_guard

    assert text_guard.youtube_title_risk(title) == ""


def test_a_blocked_full_stream_keeps_its_date_frame():
    from utils import text_guard

    assert text_guard.title("Stackswopo - THE APE SHOW - 10-25-26 (FULL STREAM)") == \
        "Stackswopo - 10-25-26 (FULL STREAM)"


def test_rumble_titles_are_not_changed_by_the_youtube_rule():
    from utils import text_guard

    title = "Locked in a cage with the gorillas"
    assert text_guard.title(title, youtube=False) == title


# ── 7. disclaimer wording ────────────────────────────────────────────────

def test_old_disclaimers_are_upgraded_not_doubled():
    from utils import disclosure

    old = "Stream.\n\n" + disclosure.OLD_ACTORS_LINES[0] + "\n\n#tags"
    out = disclosure.for_description(old)
    assert disclosure.ACTORS_LINE in out and disclosure.OLD_ACTORS_LINES[0] not in out
    assert out.count("Nothing shown is real") == 1
    assert "GTA RP is role-play" in disclosure.for_about("About text")


# ── 5. intro ─────────────────────────────────────────────────────────────

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg")


@needs_ffmpeg
@pytest.mark.parametrize("main_ext", [".ts", ".mp4"])
def test_the_intro_joins_a_stream_without_re_encoding_it(tmp_path, main_ext):
    from utils.ffmpeg_tools import media_duration, prepend_intro

    intro, main_ts = str(tmp_path / "intro.mp4"), str(tmp_path / "main.ts")
    run = lambda *a: subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)
    run("-f", "lavfi", "-i", "testsrc=size=640x360:rate=60:duration=3",
        "-f", "lavfi", "-i", "sine=f=300:duration=3:sample_rate=44100", "-ac", "2",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", intro)
    run("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=12",
        "-f", "lavfi", "-i", "sine=f=500:duration=12:sample_rate=48000", "-ac", "2",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
        "-f", "mpegts", main_ts)
    source = main_ts
    if main_ext == ".mp4":
        source = str(tmp_path / "main.mp4")
        run("-i", main_ts, "-c", "copy", "-bsf:a", "aac_adtstoasc", source)
    out = str(tmp_path / "out.mp4")
    assert prepend_intro(intro, source, out)
    assert abs((media_duration(out) or 0) - 15) < 1.5


def test_the_intro_is_found_in_the_repo_when_the_configured_path_is_gone(tmp_path, monkeypatch):
    import main

    home = tmp_path / "assets" / "intro.mp4"
    monkeypatch.setattr(main, "INTRO_HOME", str(home))

    class Y:
        intro_path = r"C:/Users/Omert/AppData/Local/CapCut/Videos/intro.mp4"

    class C:
        youtube = Y()

    monkeypatch.delenv("YOUTUBE_INTRO_PATH", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nowhere"))
    assert main.find_intro(C()) in ("", os.path.join(os.path.dirname(main.__file__), "intro.mp4"))
    home.parent.mkdir(parents=True)
    home.write_bytes(b"video")
    assert main.find_intro(C()) == os.path.abspath(str(home))


# ── 10. Rumble Related Video ─────────────────────────────────────────────

def test_a_new_clip_waits_for_its_full_stream_on_rumble(tmp_path, monkeypatch):
    import main
    from utils import stream_links

    clip = tmp_path / "x - Clip 01.mp4"
    clip.write_bytes(b"x")
    monkeypatch.setattr(stream_links, "source_of",
                        lambda path, folders=(): {"stream_title": "STIZZ SHOW"})
    assert main._rumble_waits_for_stream(str(clip))
    old = time.time() - (main.RUMBLE_RELATED_WAIT_HOURS + 1) * 3600
    os.utime(clip, (old, old))
    assert not main._rumble_waits_for_stream(str(clip))
    monkeypatch.setattr(stream_links, "source_of", lambda path, folders=(): {})
    assert not main._rumble_waits_for_stream(str(clip))


# ── 9. recap captions on the video's clock, stale caches refused ────────

def test_captions_move_by_the_audio_lead():
    from utils.recap import shifted

    seg = [{"start": 1.0, "end": 2.0, "words": [{"start": 1.0, "end": 1.5, "word": "a"}]}]
    out = shifted(seg, 1.4)
    assert out[0]["start"] == pytest.approx(2.4)
    assert out[0]["words"][0]["end"] == pytest.approx(2.9)
    assert seg[0]["start"] == 1.0


def test_a_stale_transcript_cache_is_not_used(tmp_path):
    import json

    from utils.recap import transcript_of

    class G:
        censored_folder = str(tmp_path)

    class C:
        general = G()

    video = tmp_path / "stream.ts"
    video.write_bytes(b"x" * 100)
    cache = tmp_path / "stream_transcript_words.json"
    cache.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": "hi",
                                               "words": [{"start": 0, "end": 1, "word": "hi"}]}],
                                 "source_size": 100}))
    assert transcript_of(C(), str(video), align=False)
    video.write_bytes(b"x" * 200)          # a different recording, same name
    assert transcript_of(C(), str(video), align=False) == []


# ── 2. recorder ──────────────────────────────────────────────────────────

def test_only_one_recorder_process_can_hold_the_lock(tmp_path):
    import record_stream

    first = record_stream.single_instance_lock(str(tmp_path))
    assert first is not None
    code = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r); import record_stream as r; "
         "sys.exit(0 if r.single_instance_lock(%r) is None else 1)"
         % (os.path.join(ROOT, "tools"), str(tmp_path))]).returncode
    assert code == 0
    first.close()


def test_a_stream_that_comes_back_joins_the_same_recording(monkeypatch, tmp_path):
    import record_stream

    rec = record_stream.Recorder(url="https://www.youtube.com/@x/live",
                                 staging=str(tmp_path), watch_folder=str(tmp_path))
    answers = iter([False, True])
    monkeypatch.setattr(record_stream, "channel_is_live", lambda url: next(answers, True))
    monkeypatch.setattr(record_stream, "REJOIN_POLL_S", 0)
    monkeypatch.setattr(record_stream.time, "sleep", lambda s: None)
    assert rec._wait_for_return(grace_s=60)


def test_a_second_channel_records_nothing_while_the_main_one_does(monkeypatch, tmp_path):
    import record_stream

    rec = record_stream.Recorder(url="https://www.youtube.com/@b/live",
                                 staging=str(tmp_path), watch_folder=str(tmp_path),
                                 primary=False)
    monkeypatch.setattr(rec, "_record_one_stream",
                        lambda: pytest.fail("must not start a second recording"))
    record_stream.MAIN_RECORDING.set()
    try:
        assert rec.record_one_stream() is None
    finally:
        record_stream.MAIN_RECORDING.clear()
    monkeypatch.setattr(record_stream, "channel_is_live", lambda url: False)
    assert rec.record_one_stream() is None


def test_a_clip_finds_its_stream_when_the_stored_title_carries_the_recorder_time(tmp_path):
    import json
    import time as _t

    from utils.stream_links import link_for

    store = tmp_path / "links.json"
    store.write_text(json.dumps([{"title": "i got mail 2026-10-27 11:32", "date": "10-27-26",
                                  "rumble": "https://rumble.com/v7hh9ba-x.html",
                                  "youtube": "https://www.youtube.com/watch?v=a0wMh3oXGOQ",
                                  "at": _t.time()}]))
    assert link_for("i got mail", "10-27-26", "rumble", store=str(store)) == \
        "https://rumble.com/v7hh9ba-x.html"


@pytest.mark.parametrize("raw, out", [
    ("Streamer reacting to chat comments about knife names",
     "Stackswopo reacting to chat comments about knife names"),
    ("He trolls strangers on the Monkey App", "Stackswopo trolls strangers on the Monkey App"),
    ("Somebody call an EMS!", "Somebody call an EMS! | Stackswopo"),
    ("Stackswopo roasts the lobby", "Stackswopo roasts the lobby")])
def test_clip_titles_say_his_name(raw, out):
    import main

    assert main.with_his_name(raw) == out


def test_clip_files_are_named_after_the_stream(tmp_path):
    from utils.clip_runner import clip_basename

    src = str(tmp_path / "Stackswopo youtube live 2026-10-27 11_32.ts")
    assert clip_basename("i got mail 2026-10-27 11:32", src) == "Stackswopo - i got mail 10-27"
    assert clip_basename("", src) == "Stackswopo youtube live 2026-10-27 11_32"


@needs_ffmpeg
@pytest.mark.parametrize("vcodec", ["libx264", "libsvtav1", "libaom-av1"])
def test_joining_resume_pieces_keeps_the_picture(tmp_path, vcodec):
    """10-28: an AV1 recording joined through MPEG-TS came out sound-only."""
    import record_stream

    have = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                          text=True).stdout
    if vcodec not in have:
        pytest.skip(f"no {vcodec}")
    parts = []
    for i in (1, 2):
        part = tmp_path / f"rec.part0{i}.ts.mp4"
        extra = ["-cpu-used", "8"] if vcodec == "libaom-av1" else []
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=320x240:rate=30:duration=4", "-f", "lavfi", "-i",
                        "sine=duration=4", "-c:v", vcodec, *extra, "-c:a", "aac", "-shortest",
                        str(part)], check=True)
        parts.append(str(part))
    rec = record_stream.Recorder(url="https://www.youtube.com/@x/live", staging=str(tmp_path),
                                 watch_folder=str(tmp_path / "w"))
    out = str(tmp_path / "joined.mp4")
    assert rec._concat(parts, out)
    assert record_stream.video_seconds(out) > 7


def test_youtube_recordings_ask_for_h264():
    import record_stream

    rec = record_stream.Recorder(url="https://www.youtube.com/@x/live", staging=".",
                                 watch_folder=".")
    args = rec.download_args("out.ts")
    assert "-S" in args and args[args.index("-S") + 1].startswith("vcodec:h264")


def test_rumble_clips_have_a_daily_cap(tmp_path):
    import main

    class G:
        logs_folder = str(tmp_path)

    class R:
        clips_per_day = 2

    class C:
        general = G()
        rumble = R()

    assert not main._rumble_clip_cap_reached(C())
    main._note_rumble_clip(C())
    main._note_rumble_clip(C())
    assert main._rumble_clip_cap_reached(C())


def test_a_short_is_never_titled_full_stream():
    # 10-28: a Short went to @WOPOCLIPPER titled "Stackswopo - Yurrrrrr -
    # Full Stream" - the clip file's name. A clip is never a full stream.
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "auto_uploader"))
    from publishers.youtube_shorts import YouTubeShortsPublisher, not_a_full_stream
    from utils.clip_runner import clip_basename

    t = YouTubeShortsPublisher({}).title_for("", "x/Stackswopo - Yurrrrrr - Full Stream - Clip 01.mp4")
    assert "full stream" not in t.lower() and "clip 01" not in t.lower()
    assert "full stream" not in YouTubeShortsPublisher({}).title_for(
        "he trolls strangers (FULL STREAM)", "x/a.mp4").lower()
    assert not_a_full_stream("Stackswopo - Yurrrrrr - Full Stream") == "Stackswopo - Yurrrrrr"
    name = clip_basename("Stackswopo - YURRRRRR - 10-27-26 (FULL STREAM)", "x/rec 2026-10-27 18_55.ts")
    assert "full stream" not in name.lower() and name.startswith("Stackswopo - YURRRRRR")


def test_a_full_stream_asks_the_channel_before_its_first_upload():
    # 10-29: a second copy of "hey my babes" was checked only against the
    # channel list cached at startup and went up twice on wopovod.
    import os
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "auto_uploader", "main.py"), encoding="utf-8").read()
    i = src.index("def yt_attempt():")
    body = src[i:src.index("return yt.upload(", i)]
    assert "if yt_attempts[0] > 1 or not is_clip:" in body and "_already_on_youtube(" in body
