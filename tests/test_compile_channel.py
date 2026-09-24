"""
compile_channel: long compilations of Stackswopo's own videos, uploaded to
the VOD channel.

What these pin down:
  - a video is used in one compilation, ever ("different footage"),
  - series take turns, each with its own running number,
  - Shorts, streams and reactions never go in,
  - videos of different sizes, frame rates and loudness join into one
    playable file with chapters that line up,
  - it uploads only to the channel it was told to, and deletes its work
    only after the upload is confirmed - a failed upload is retried from
    the finished file, not rebuilt.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "auto_uploader")):
    if path not in sys.path:
        sys.path.insert(0, path)

import compile_channel as cc  # noqa: E402
from compile_channel import Ledger, Video  # noqa: E402


def _settings(**overrides):
    settings = dict(cc.DEFAULTS)
    settings.update(overrides)
    return settings


def _catalog():
    """Newest first, the way a channel lists them."""
    videos = []
    for n in range(10, 0, -1):
        videos.append(Video(f"wt{n}", f"Whiteboy Trolling Clips #{n}",
                            25 * 60, views=n * 1000))
    for n in range(6, 0, -1):
        videos.append(Video(f"fm{n}", f"Stackswopo Funny Moments #{n}",
                            30 * 60, views=n * 500))
    videos += [
        Video("hal", "Stackswopo Plays The Halloween Game", 26 * 60),
        Video("jc", "Johnny Cox Makes Roleplayers CRASHOUT", 29 * 60),
        Video("unh", "Stackswopo Plays UNHINGED Games", 33 * 60),
        Video("rx", "Stackswopo Reacts To GTA 6 Extended Trailer", 25 * 60),
        Video("short", "he did WHAT #shorts", 40),
        Video("vod", "Full 6 hour stream", 6 * 3600),
    ]
    return videos


# ── what goes in ────────────────────────────────────────────────────────────

def test_series_are_read_from_numbered_titles():
    assert cc.series_of("Whiteboy Trolling Clips #126 (TOP TIER)") == \
        "Whiteboy Trolling Clips"
    assert cc.series_of("Stackswopo Stream Recap #18") == \
        "Stackswopo Stream Recap"
    assert cc.series_of("Stackswopo Plays The Halloween Game") == ""


def test_shorts_streams_and_reactions_are_never_used():
    kept = {v.id for v in cc.eligible(_catalog(), set(), _settings())}
    assert not {"rx", "short", "vod"} & kept
    assert {"wt1", "fm1", "hal"} <= kept


def test_a_used_video_is_never_picked_again(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.json"))
    settings = _settings(target_minutes=60)
    series, first = cc.choose(_catalog(), ledger, settings)
    ledger.record(series, 1, first, "https://youtu.be/x", "t")

    reread = Ledger(str(tmp_path / "ledger.json"))
    for _ in range(4):
        series, picks = cc.choose(_catalog(), reread, settings)
        assert not {v.id for v in picks} & reread.used
        reread.record(series, reread.next_number(series), picks, "u", "t")


def test_series_take_turns_and_count_separately(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.json"))
    settings = _settings(target_minutes=50)
    seen = []
    for _ in range(4):
        series, picks = cc.choose(_catalog(), ledger, settings)
        number = ledger.next_number(series)
        seen.append((series, number))
        ledger.record(series, number, picks, "u", "t")
    assert seen[:3] == [("Whiteboy Trolling Clips", 1),
                        ("Stackswopo Funny Moments", 1),
                        ("Stackswopo Best Moments", 1)]
    assert seen[3] == ("Whiteboy Trolling Clips", 2)


def test_a_series_plays_forward_in_time(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.json"))
    _, picks = cc.choose(_catalog(), ledger, _settings(target_minutes=100))
    numbers = [int(v.title.split("#")[1]) for v in picks]
    assert numbers == sorted(numbers)
    assert numbers[-1] == 10, "starts from the newest unused footage"


def test_it_says_so_when_the_footage_runs_out(tmp_path):
    ledger = Ledger(str(tmp_path / "ledger.json"))
    assert cc.choose(_catalog(), ledger,
                     _settings(target_minutes=100 * 60)) == ("", [])


def test_listing_lines_without_a_duration_are_dropped():
    out = "a\t600\t12\tOne #1\nb\tNA\tNA\tUpcoming premiere\nc\t700.5\tNA\tTwo\n"
    videos = cc.parse_listing(out)
    assert [v.id for v in videos] == ["a", "c"]
    assert videos[1].views == 0


# ── the words ───────────────────────────────────────────────────────────────

def test_the_title_matches_the_compilation_channels_shape():
    title = cc.make_title("Whiteboy Trolling Clips", 7, 2 * 3600 + 240,
                          _settings())
    assert title == "2 Hours of Whiteboy Trolling Clips #7"
    assert cc.length_label(3500) == "1 Hour"
    assert cc.length_label(45 * 60) == "45 Minutes"


def test_chapters_start_at_zero_and_line_up():
    videos = [Video("a", "First", 0), Video("b", "Second", 0),
              Video("c", "Third", 0)]
    text = cc.chapters(videos, [1800.4, 1900.2, 1200])
    assert text.splitlines() == ["0:00:00 First", "0:30:00 Second",
                                 "1:01:40 Third"]


def test_the_description_credits_and_links_every_original():
    videos = [Video("abc", "One", 0), Video("def", "Two", 0),
              Video("ghi", "Three", 0)]
    text = cc.make_description("Series", "2 Hours", videos, [10, 10, 10],
                               _settings())
    assert "https://www.youtube.com/@stackswopo_" in text
    for vid in ("abc", "def", "ghi"):
        assert f"https://youtu.be/{vid}" in text
    assert len(text) <= 5000


# ── building ────────────────────────────────────────────────────────────────

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="ffmpeg not installed")


def _synthetic(path, seconds, size, rate, volume):
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
         "-t", str(seconds), "-af", f"volume={volume}",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
         "-shortest", path], check=True)


@pytest.fixture
def built(tmp_path, monkeypatch):
    """A real build from three mismatched sources, downloads faked."""
    sources = {"a": (6, "1280x720", 60, 0.05), "b": (5, "1920x1080", 30, 1.0),
               "c": (7, "854x480", 25, 0.3)}

    def fake_download(video, folder, settings):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{video.id}.mp4")
        _synthetic(path, *sources[video.id])
        return path

    monkeypatch.setattr(cc, "download", fake_download)
    # No network in a test: the source thumbnail cannot be fetched, so the
    # frame fallback is what gets exercised.
    monkeypatch.setattr(cc, "fetch_thumbnail", lambda videos, folder: "")
    picks = [Video("a", "Clips #1", 6, views=5), Video("b", "Clips #2", 5),
             Video("c", "Clips #3", 7)]
    settings = _settings(width=640, height=360, fps=30, encoder="cpu",
                         fade_seconds=0.3)
    ledger = Ledger(str(tmp_path / "ledger.json"))
    work = str(tmp_path / "work")
    job = cc.build("Clips", picks, settings, ledger, work)
    return job, settings, ledger, work


@needs_ffmpeg
def test_mismatched_sources_join_into_one_video(built):
    job, _, _, work = built
    assert abs(cc.probe_duration(job["final"]) - 18) < 1.0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate", "-of", "json", job["final"]],
        capture_output=True, text=True).stdout
    stream = json.loads(probe)["streams"][0]
    assert (stream["width"], stream["height"]) == (640, 360)
    assert stream["r_frame_rate"] == "30/1"
    leftovers = sorted(os.listdir(work))
    assert leftovers == ["compilation.mp4", "pending.json", "thumbnail.jpg"], \
        "downloads and parts are deleted as soon as they are used"


@needs_ffmpeg
def test_the_chapters_match_the_real_lengths(built):
    job, *_ = built
    lines = job["description"].split("Chapters\n")[1].splitlines()[:3]
    assert lines == ["00:00 Clips #1", "00:06 Clips #2", "00:11 Clips #3"]


class FakeUploader:
    def __init__(self, channel="STACKSWOPOVODS", fail=False,
                 handle="@stackswopo10k"):
        self.channel, self.fail, self.calls = channel, fail, []
        self.handle = handle

    def get_service(self):
        uploader = self

        class _Request:
            def execute(self):
                return {"items": [{"snippet": {"title": uploader.channel,
                                               "customUrl": uploader.handle}}]}

        class _Channels:
            def list(self, **kwargs):
                return _Request()

        class _Service:
            def channels(self):
                return _Channels()

        return _Service()

    def upload(self, path, title, description, tags, **kwargs):
        self.calls.append((path, title))
        if self.fail:
            raise RuntimeError("YouTube upload failed: quota")
        return "https://www.youtube.com/watch?v=NEW"


@needs_ffmpeg
def test_upload_records_the_footage_and_deletes_the_work(built, monkeypatch):
    job, settings, ledger, work = built
    fake = FakeUploader()
    monkeypatch.setattr(cc, "youtube_uploader", lambda s: fake)

    cc.upload(job, settings, ledger, work)

    assert fake.calls[0][1] == "1 Minute of Clips #1"
    assert not os.path.exists(work)
    assert Ledger(ledger.path).used == {"a", "b", "c"}
    assert Ledger(ledger.path).next_number("Clips") == 2


@needs_ffmpeg
def test_it_will_not_upload_to_the_wrong_channel(built, monkeypatch):
    job, settings, ledger, work = built
    fake = FakeUploader(channel="Stackswopo Games", handle="@stackswopogames")
    monkeypatch.setattr(cc, "youtube_uploader", lambda s: fake)

    with pytest.raises(RuntimeError, match="not @STACKSWOPO10K"):
        cc.upload(job, settings, ledger, work)
    assert not fake.calls
    assert os.path.isfile(job["final"]), "kept for the next run"
    assert not Ledger(ledger.path).used


@needs_ffmpeg
def test_a_failed_upload_is_retried_from_the_finished_file(
        built, monkeypatch, tmp_path):
    job, settings, ledger, work = built
    monkeypatch.setattr(cc, "youtube_uploader",
                        lambda s: FakeUploader(fail=True))
    with pytest.raises(RuntimeError):
        cc.upload(job, settings, ledger, work)
    assert os.path.isfile(job["final"])

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"compilation": {
        "work_folder": work, "ledger_path": ledger.path}}))
    fake = FakeUploader()
    monkeypatch.setattr(cc, "youtube_uploader", lambda s: fake)
    monkeypatch.setattr(cc, "plan", lambda *a, **k: pytest.fail(
        "rebuilt instead of uploading what was already made"))

    assert cc.run(["--config", str(config)]) == 0
    assert len(fake.calls) == 1
    assert not os.path.exists(work)


def test_the_channel_is_recognised_by_name_or_handle():
    """STACKSWOPOVODS is @STACKSWOPO10K - either spelling in config must
    match, and the VOD channel's login must not."""
    vods = FakeUploader(channel="STACKSWOPOVODS", handle="@stackswopo10k")
    cc.check_channel(vods, "@STACKSWOPO10K")
    cc.check_channel(vods, "STACKSWOPOVODS")
    games = FakeUploader(channel="Stackswopo Games", handle="@stackswopogames")
    with pytest.raises(RuntimeError):
        cc.check_channel(games, "@STACKSWOPO10K")


def test_it_has_its_own_login_not_the_vod_uploaders():
    assert cc.DEFAULTS["token_path"] != "youtube_token.json"
    assert cc.DEFAULTS["token_path"].endswith("_token.json"), \
        "matches .gitignore's auto_uploader/*_token.json"


# ── downloading ─────────────────────────────────────────────────────────────
# The first real run: yt-dlp fetched the HLS video (format 616) and then
# got 403 on the audio - YouTube's audio URLs need a JavaScript runtime to
# unscramble - and the tool took "<id>.f616.mp4", a video with no sound,
# and handed it to ffmpeg, which failed on "-map 0:a:0".

def _fake_ytdlp(monkeypatch, make):
    """cc._run with yt-dlp replaced by `make(folder, video_id)`; ffprobe
    still runs for real so the audio check is the real one."""
    real = cc._run

    def run(command, timeout):
        if "-o" in command:
            template = command[command.index("-o") + 1]
            folder = os.path.dirname(template)
            vid = os.path.basename(template).split(".")[0]
            make(folder, vid)
            return subprocess.CompletedProcess(
                command, 1, "", "ERROR: unable to download video data: "
                                "HTTP Error 403: Forbidden")
        return real(command, timeout)

    monkeypatch.setattr(cc, "_run", run)
    monkeypatch.setattr(cc, "_js_runtime_args", lambda: [])
    # These fakes always answer 403. A real pip upgrade has no place here.
    monkeypatch.setattr(cc, "update_yt_dlp", lambda: (False, "not in tests"))


def _video_only(path):
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30",
                    "-t", "1", "-c:v", "libx264", "-preset", "ultrafast",
                    path], check=True)


@needs_ffmpeg
def test_half_a_download_is_not_taken_for_a_video(tmp_path, monkeypatch):
    _fake_ytdlp(monkeypatch, lambda folder, vid: _video_only(
        os.path.join(folder, f"{vid}.f616.mp4")))
    with pytest.raises(RuntimeError, match="403"):
        cc.download(Video("abc", "Clips #1", 60), str(tmp_path), _settings())
    assert os.listdir(tmp_path) == [], "the useless half is cleaned up"


@needs_ffmpeg
def test_a_merged_file_with_no_sound_is_refused(tmp_path, monkeypatch):
    _fake_ytdlp(monkeypatch, lambda folder, vid: _video_only(
        os.path.join(folder, f"{vid}.mp4")))
    with pytest.raises(RuntimeError, match="without its audio"):
        cc.download(Video("abc", "Clips #1", 60), str(tmp_path), _settings())


@needs_ffmpeg
def test_a_proper_download_is_returned(tmp_path, monkeypatch):
    _fake_ytdlp(monkeypatch, lambda folder, vid: _synthetic(
        os.path.join(folder, f"{vid}.mp4"), 1, "1280x720", 30, 1.0))
    path = cc.download(Video("abc", "Clips #1", 60), str(tmp_path),
                       _settings())
    assert os.path.basename(path) == "abc.mp4"


# ── a refused download ──────────────────────────────────────────────────────
# The second real run: "[1/5] Whiteboy Trolling Clips #122 ... download
# failed: HTTP Error 403: Forbidden", and the whole compilation stopped. A
# public video refused out of nowhere is almost always yt-dlp behind
# YouTube's latest player change.

_REFUSED = "ERROR: unable to download video data: HTTP Error 403: Forbidden"


def _answers(monkeypatch, *outcomes):
    """cc._run answering each yt-dlp download with the next outcome: a
    callable make(merged_path) that succeeds, or a stderr string."""
    queue = list(outcomes)
    calls = []

    def run(command, timeout):
        template = command[command.index("-o") + 1]
        merged = template.replace("%(ext)s", "mp4")
        calls.append(command)
        outcome = queue.pop(0)
        if callable(outcome):
            outcome(merged)
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", outcome)

    monkeypatch.setattr(cc, "_run", run)
    monkeypatch.setattr(cc, "_js_runtime_args", lambda: [])
    return calls


@needs_ffmpeg
def test_a_refused_download_updates_yt_dlp_and_tries_again(tmp_path,
                                                            monkeypatch):
    calls = _answers(monkeypatch, _REFUSED,
                     lambda path: _synthetic(path, 1, "1280x720", 30, 1.0))
    updates = []
    monkeypatch.setattr(cc, "update_yt_dlp",
                        lambda: updates.append(1) or (True, "updated to 2026.9.20"))

    path = cc.download(Video("abc", "Whiteboy Trolling Clips #122", 60),
                       str(tmp_path), _settings())

    assert os.path.basename(path) == "abc.mp4"
    assert len(updates) == 1
    assert len(calls) == 2


def test_the_retry_after_an_update_is_what_gets_returned(tmp_path,
                                                         monkeypatch):
    """Same as above with the ffprobe checks faked, so it runs anywhere."""
    def make(path):
        open(path, "wb").write(b"mp4")

    calls = _answers(monkeypatch, _REFUSED, make)
    monkeypatch.setattr(cc, "update_yt_dlp", lambda: (True, "updated"))
    monkeypatch.setattr(cc, "has_audio", lambda path: True)
    monkeypatch.setattr(cc, "probe_height", lambda path: 1080)

    path = cc.download(Video("abc", "Clips #1", 60), str(tmp_path),
                       _settings())

    assert path == str(tmp_path / "abc.mp4")
    assert len(calls) == 2


def test_a_failed_update_is_not_followed_by_a_pointless_retry(tmp_path,
                                                              monkeypatch):
    calls = _answers(monkeypatch, _REFUSED)
    monkeypatch.setattr(cc, "update_yt_dlp", lambda: (False, "no network"))

    with pytest.raises(RuntimeError, match="403"):
        cc.download(Video("abc", "Clips #1", 60), str(tmp_path), _settings())
    assert len(calls) == 1


def test_still_refused_after_updating_stops_after_one_retry(tmp_path,
                                                            monkeypatch):
    calls = _answers(monkeypatch, _REFUSED, _REFUSED)
    monkeypatch.setattr(cc, "update_yt_dlp",
                        lambda: (True, "already the latest version"))

    with pytest.raises(RuntimeError, match="403"):
        cc.download(Video("abc", "Clips #1", 60), str(tmp_path), _settings())
    assert len(calls) == 2


def test_a_failure_that_is_not_a_refusal_never_updates(tmp_path, monkeypatch):
    _answers(monkeypatch, "ERROR: [youtube] abc: Video unavailable")
    monkeypatch.setattr(cc, "update_yt_dlp", lambda: pytest.fail(
        "a deleted video is not fixed by a newer yt-dlp"))

    with pytest.raises(RuntimeError, match="unavailable"):
        cc.download(Video("abc", "Clips #1", 60), str(tmp_path), _settings())


class _Ran:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_the_updater_brings_the_challenge_solver_along():
    """yt-dlp[default] pins yt-dlp-ejs to one exact version; a bare
    `-U yt-dlp` would leave the old solver behind the new yt-dlp."""
    seen = []
    cc.update_yt_dlp(runner=lambda cmd, **k: seen.append(cmd) or _Ran())
    assert seen[0][0] == sys.executable
    assert seen[0][-1] == "yt-dlp[default]"


def test_the_updater_reports_the_new_version():
    ok, detail = cc.update_yt_dlp(runner=lambda *a, **k: _Ran(
        out="Successfully installed yt-dlp-ejs-0.9.0 yt-dlp-2026.9.20"))
    assert ok and detail == "updated to 2026.9.20"


def test_the_updater_says_when_it_was_already_current():
    ok, detail = cc.update_yt_dlp(
        runner=lambda *a, **k: _Ran(out="Requirement already satisfied"))
    assert ok and "latest" in detail


def test_the_updater_never_raises():
    def explode(*_a, **_k):
        raise OSError("pip is gone")

    ok, detail = cc.update_yt_dlp(runner=explode)
    assert not ok and "pip is gone" in detail
    ok, detail = cc.update_yt_dlp(
        runner=lambda *a, **k: _Ran(code=1, err="ERROR: no such option"))
    assert not ok and "no such option" in detail


def test_the_original_audio_track_is_asked_for_not_a_dub():
    fmt = cc.FORMAT.format(h=1080)
    assert fmt.split("/")[0].endswith("+ba[format_note*=original]")
    assert "[height<=1080]" in fmt


def test_no_javascript_runtime_stops_before_downloading(monkeypatch):
    monkeypatch.setattr(cc.shutil, "which", lambda name: None)
    monkeypatch.setattr(cc, "_deno_from_pip", lambda: "")
    with pytest.raises(RuntimeError, match="pip install --user deno"):
        cc._js_runtime_args()


def test_a_deno_from_pip_is_named_to_yt_dlp(monkeypatch):
    monkeypatch.setattr(cc.shutil, "which", lambda name: None)
    monkeypatch.setattr(cc, "_deno_from_pip", lambda: r"C:\py\Scripts\deno.exe")
    assert cc._js_runtime_args() == ["--js-runtimes",
                                     r"deno:C:\py\Scripts\deno.exe"]


@needs_ffmpeg
def test_there_is_always_a_thumbnail_even_offline(built):
    """The source thumbnail is fetched from YouTube; when that fails the
    upload still gets one, a frame of the video, rather than whatever
    YouTube auto-picks."""
    job, *_ = built
    assert os.path.basename(job["thumbnail"]) == "thumbnail.jpg"
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", job["thumbnail"]], capture_output=True, text=True)
    assert probe.stdout.strip() == "1280,720"


def test_the_thumbnail_goes_up_with_the_video(monkeypatch, tmp_path):
    """Same call the VOD uploader uses: YouTubeUploader.upload(...,
    thumbnail_path=) -> thumbnails().set."""
    seen = {}

    class Uploader(FakeUploader):
        def upload(self, path, title, description, tags, **kwargs):
            seen.update(kwargs)
            return "https://www.youtube.com/watch?v=X"

    monkeypatch.setattr(cc, "youtube_uploader", lambda s: Uploader())
    work = tmp_path / "work"
    work.mkdir()
    thumb = work / "thumbnail.jpg"
    thumb.write_bytes(b"jpg")
    job = {"final": str(work / "c.mp4"), "series": "S", "number": 1,
           "title": "t", "description": "d", "tags": [],
           "thumbnail": str(thumb), "videos": []}
    cc.upload(job, _settings(ledger_path=str(tmp_path / "l.json")),
              Ledger(str(tmp_path / "l.json")), str(work))
    assert seen["thumbnail_path"] == str(thumb)
