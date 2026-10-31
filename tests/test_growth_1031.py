"""10-31 growth changes: hook titles, clips right after the stream, 1-hour weekly."""
import inspect
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "auto_uploader"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils import series, title_hook  # noqa: E402

BASE = "Stackswopo - TOPIC BOUL - 10-30-26 (FULL STREAM)"


def _cfg(tmp_path):
    return SimpleNamespace(general=SimpleNamespace(logs_folder=str(tmp_path)))


def test_hook_goes_in_front_and_the_stream_name_stays():
    t = title_hook.hooked(BASE, "Stackswopo Gets EXPOSED By His Ex")
    assert t == "Stackswopo Gets EXPOSED By His Ex | TOPIC BOUL - 10-30-26 (FULL STREAM)"
    assert len(t) <= 100


def test_hook_without_his_name_gets_it():
    assert title_hook.clean_hook("Gets Pulled Over By Cops").startswith("Stackswopo ")


def test_bad_hooks_fall_back_to_the_plain_title():
    for bad in ("Stackswopo Talks To A 15 Year Old", "Stackswopo #GTA #RP Moment",
                "Hi", "Stackswopo " + "really " * 20 + "long"):
        assert title_hook.hooked(BASE, bad) == BASE


def test_too_long_keeps_the_plain_title():
    base = "Stackswopo - " + "A" * 70 + " - 10-30-26 (FULL STREAM)"
    assert title_hook.hooked(base, "Stackswopo Gets EXPOSED By His Ex") == base


def _segments():
    return [{"start": i * 10.0, "text": f"line number {i} something happened here"}
            for i in range(60)]


def test_hook_is_asked_once_and_kept_for_retries(tmp_path):
    calls = []

    def asker(prompt):
        calls.append(prompt)
        return '{"hook": "Stackswopo Gets EXPOSED By His Ex"}'

    cfg = _cfg(tmp_path)
    a = title_hook.for_stream(cfg, "hash1", "x.ts", BASE, "TOPIC BOUL",
                              segments=_segments(), askers=[("m", asker)])
    b = title_hook.for_stream(cfg, "hash1", "x.ts", BASE, "TOPIC BOUL",
                              segments=_segments(), askers=[("m", lambda p: '{"hook": "Other Thing Happens Now"}')])
    assert a == b and a.startswith("Stackswopo Gets EXPOSED")
    assert len(calls) == 1
    saved = json.load(open(os.path.join(tmp_path, "title_hooks.json")))
    assert saved["hash1"]["title"] == a


def test_cleaner_changing_the_hook_means_no_hook(tmp_path):
    t = title_hook.for_stream(_cfg(tmp_path), "h2", "x.ts", BASE, "TOPIC BOUL",
                              clean=lambda s: s.replace("EXPOSED", "***"),
                              segments=_segments(),
                              askers=[("m", lambda p: '{"hook": "Stackswopo Gets EXPOSED By His Ex"}')])
    assert t == BASE


def test_no_transcript_no_ask(tmp_path):
    t = title_hook.for_stream(_cfg(tmp_path), "h3", "x.ts", BASE, "TOPIC BOUL",
                              segments=[], askers=[("m", lambda p: 1 / 0)])
    assert t == BASE


def test_hour_titles():
    assert series.hour_title(3, False) == "1 Hour of Stackswopo Funny Moments #3"
    assert "Johnny Cox" in series.hour_title(3, True)


def test_weekly_is_an_hour():
    from utils import weekly
    assert weekly.WEEKLY_MAX_S == 3600
    src = inspect.getsource(weekly.maybe_make)
    assert "series.HOUR_S" in src and "hour_title" in src


def test_clips_are_cut_right_after_the_censor_pass():
    import main
    assert main.CLIPS_RIGHT_AFTER_STREAM is True
    src = inspect.getsource(inspect.unwrap(main.process_file))
    assert "_start_early_clips()" in src
    # joined before the source is retired, and not cut twice
    assert src.index('_early["thread"].join()') < src.index("retire_source()\n")
    assert "not early_done" in src


def test_full_stream_dupe_check_asks_both_titles():
    import main
    src = inspect.getsource(inspect.unwrap(main.process_file))
    assert "(yt_title, plain_yt_title)" in src


def test_a_second_google_project_has_its_own_day(tmp_path):
    from utils import quota
    main = tmp_path / "main.json"
    other = tmp_path / "other.json"
    main.write_text('{"installed": {"client_id": "111-aaa.apps.googleusercontent.com"}}')
    other.write_text('{"installed": {"client_id": "222-bbb.apps.googleusercontent.com"}}')
    assert quota.project_of(str(main), str(main)) == quota.MAIN
    pid = quota.project_of(str(other), str(main))
    assert pid == "222"
    led = str(tmp_path / "q.json")
    quota.record(9950, "shorts", path=led, project=pid)
    assert quota.spent(led) == 0                      # main day untouched
    assert quota.spent(led, pid) == 9950
    assert quota.allow_extra(path=led) is True        # wopovod side still free
    assert quota.allow_extra(path=led, project=pid) is False
    quota.record(1600, "vod", path=led)
    assert quota.spent(led) == 1600
