"""utils/playlists.py: the wopovod playlists stay current without anyone asking."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils import playlists  # noqa: E402


def v(id_, title, date="2026-10-01T00:00:00Z", dur=7200, views=0):
    return {"id": id_, "title": title, "date": date, "dur": dur, "views": views}


def test_each_playlist_gets_the_right_videos_in_the_right_order():
    vids = {
        "vods": [v("old", "OLD", "2026-09-01T00:00:00Z", views=900),
                 v("new", "NEW", "2026-10-16T00:00:00Z", views=10),
                 v("short", "a short", dur=40)],
        "games": [v("m1", "Monkey App trolling pt 1", dur=900, views=5),
                  v("m2", "MONKEY APP ep 2", dur=900, views=50),
                  v("g", "big stream", views=5000)],
        "comp": [v("mc", "Monkey App compilation", dur=60)],
        "clips": [v("c1", "GTA RP moment", dur=30, views=3),
                  v("c2", "funny", dur=30, views=30)],
    }
    got = {key: (want, ranked) for key, _, _, want, ranked in playlists.plans(vids)}
    assert got["full"] == (["new", "old"], False)          # newest on top, no Shorts
    assert got["monkey"] == (["m2", "m1"], False)           # real episodes only
    assert got["top"] == (["g", "old", "new"], True)        # full-length streams, by views
    assert got["clips"] == (["c2", "c1"], True)
    assert got["gta"] == (["c1"], True)


def test_it_runs_weekly_and_a_stopped_run_waits_a_day():
    now = dt.datetime(2026, 10, 17, 12)
    ago = lambda days: (now - dt.timedelta(days=days)).isoformat()  # noqa: E731
    assert playlists._due({}, now)
    assert not playlists._due({"last": ago(2), "last_try": ago(2)}, now)
    assert playlists._due({"last": ago(7), "last_try": ago(7)}, now)
    assert not playlists._due({"last": ago(9), "last_try": ago(0.2)}, now)
    assert playlists._due({"last": ago(9), "last_try": ago(1.5)}, now)


def test_maybe_sync_never_raises_or_blocks(monkeypatch):
    monkeypatch.setattr(playlists, "_load", lambda: {"last": dt.datetime.now().isoformat(),
                                                     "last_try": dt.datetime.now().isoformat()})
    assert playlists.maybe_sync() is False
    monkeypatch.setattr(playlists, "_load", lambda: (_ for _ in ()).throw(OSError("x")))
    assert playlists.maybe_sync() is False
