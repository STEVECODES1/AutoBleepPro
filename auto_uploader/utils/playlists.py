"""Public playlists on STACKSWOPO VODS (@WOPOVOD), kept current every week.

Playlists rank in YouTube and Google search on their own, so a viewer who
searches "stackswopo" finds a whole shelf of streams on wopovod instead of
one video. Five are kept:

    monkey  Stackswopo Monkey App Trolling - All Episodes   (every episode)
    full    Stackswopo Full Streams & VODs (Latest)         (newest first)
    top     Most Watched Stackswopo Streams                 (top 10 by views)
    clips   Best Stackswopo Clips & Funny Moments           (top 12 Shorts)
    gta     Stackswopo GTA RP Clips                         (top 10 GTA Shorts)

Videos from the other three channels are fine in a wopovod playlist: any
public video can be. maybe_sync() runs after each stream's recap (see
recap.py) and does real work at most once every SYNC_DAYS, in a background
thread so nothing waits on it. Quota is the shared 10,000 units a day: a
playlist or item insert/delete is 50, reads are 1, and a run stops at
`cap` (default 800 - Shorts and full streams need the rest of the day's
quota) and finishes on the next one.

    python -X utf8 tools/playlist_builder.py            sync now (cap 800)
    python -X utf8 tools/playlist_builder.py --dry-run  show what it would do
"""
import datetime as dt
import json
import os
import re
import threading

UPLOADER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS = os.path.join(UPLOADER, "client_secrets.json")
STATE = os.path.join(UPLOADER, "logs", "playlists.json")
SYNC_DAYS = 6
RUMBLE = "https://rumble.com/user/BinScripts"
WOPOVOD = "https://www.youtube.com/@WOPOVOD"
TOKENS = {"vods": "youtube_token.json", "clips": "youtube_shorts_token.json",
          "games": "youtube_games_token.json", "comp": "youtube_compilation_token.json"}
FOOT = ("\n\nWatch the full uncut, uncensored streams on Rumble: " + RUMBLE +
        "\nSTACKSWOPO VODS: " + WOPOVOD)
_lock = threading.Lock()


def secs(iso):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def _service(token):
    from utils.youtube_uploader import YouTubeUploader
    return YouTubeUploader(SECRETS, os.path.join(UPLOADER, token)).get_service()


def public_videos(svc):
    """Every public upload on the channel `svc` is signed in to (reads only)."""
    me = svc.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    uploads = me["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, page = [], None
    while True:
        got = svc.playlistItems().list(part="contentDetails", playlistId=uploads,
                                       maxResults=50, pageToken=page).execute()
        ids += [i["contentDetails"]["videoId"] for i in got.get("items", [])]
        page = got.get("nextPageToken")
        if not page:
            break
    out = []
    for n in range(0, len(ids), 50):
        got = svc.videos().list(part="snippet,contentDetails,statistics,status",
                                id=",".join(ids[n:n + 50])).execute()
        for v in got.get("items", []):
            if v.get("status", {}).get("privacyStatus") != "public":
                continue
            out.append({"id": v["id"], "title": v["snippet"]["title"],
                        "date": v["snippet"]["publishedAt"],
                        "dur": secs(v.get("contentDetails", {}).get("duration")),
                        "views": int(v.get("statistics", {}).get("viewCount", 0) or 0)})
    return out


def plans(vids):
    """[(key, title, description, wanted ids in order, ranked)] from each
    channel's public videos. Ranked lists keep only their top N."""
    own, games, comp, clips = vids["vods"], vids["games"], vids["comp"], vids["clips"]
    newest = lambda vs: sorted(vs, key=lambda v: v["date"], reverse=True)  # noqa: E731
    most = lambda vs: sorted(vs, key=lambda v: -v["views"])  # noqa: E731
    monkey = [v for v in games + comp if re.search(r"monkey app", v["title"], re.I)
              and v["dur"] > 600]
    streams = [v for v in own + games if v["dur"] > 1800]
    gta = [v for v in clips if re.search(r"\bGTA\b|\bRP\b", v["title"])]
    ids = lambda vs: [v["id"] for v in vs]  # noqa: E731
    return [
        ("monkey", "Stackswopo Monkey App Trolling - All Episodes",
         "Stackswopo trolling people on the Monkey app - the most watched episodes "
         "in one place." + FOOT, ids(most(monkey)), False),
        ("full", "Stackswopo Full Streams & VODs (Latest)",
         "Every full Stackswopo stream VOD on STACKSWOPO VODS, newest first." + FOOT,
         ids(newest([v for v in own if v["dur"] > 1800])), False),
        ("top", "Most Watched Stackswopo Streams",
         "The most watched Stackswopo full streams, ranked by views." + FOOT,
         ids(most(streams)[:10]), True),
        ("clips", "Best Stackswopo Clips & Funny Moments",
         "The most watched Stackswopo clips and funniest moments." + FOOT,
         ids(most(clips)[:12]), True),
        ("gta", "Stackswopo GTA RP Clips",
         "Stackswopo GTA RP clips - funny moments and arguments from the city." + FOOT,
         ids(most(gta)[:10]), True),
    ]


def _load():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


def _items(svc, pid):
    have, page = [], None
    while True:
        got = svc.playlistItems().list(part="snippet,contentDetails", playlistId=pid,
                                       maxResults=50, pageToken=page).execute()
        have += [(i["contentDetails"]["videoId"], i["id"]) for i in got.get("items", [])]
        page = got.get("nextPageToken")
        if not page:
            return have


class _Budget(Exception):
    pass


def sync(cap=800, dry_run=False, say=print):
    """Bring every playlist up to date. Returns (units spent, finished?)."""
    state = _load()
    ids = state.setdefault("ids", {})
    spent = [0]

    def spend(units):
        if spent[0] + units > cap:
            raise _Budget()
        spent[0] += units

    # A channel that cannot be read leaves its list short, and a short list
    # would look like "these fell out of the top 10" - so nothing is removed
    # on a run that could not see every channel. A missing token is skipped
    # rather than read: reading it would open a sign-in window.
    vids, partial = {}, False
    for k, t in TOKENS.items():
        try:
            if not os.path.exists(os.path.join(UPLOADER, t)):
                raise FileNotFoundError(t)
            vids[k] = public_videos(_service(t))
        except Exception as exc:
            say(f"[Playlists] could not read {k} ({str(exc)[:120]}) - adding only, no removals")
            vids[k], partial = [], True
    if not vids["vods"]:
        say("[Playlists] wopovod could not be read - nothing to do")
        return 0, False
    svc = _service(TOKENS["vods"])
    found = {p["snippet"]["title"]: p["id"] for p in svc.playlists().list(
        part="snippet", mine=True, maxResults=50).execute().get("items", [])}
    finished = True
    try:
        for key, title, desc, want, ranked in plans(vids):
            if not want:
                continue
            pid = ids.get(key) or found.get(title)
            have = _items(svc, pid) if pid else []
            present = [v for v, _ in have]
            missing = [v for v in want if v not in present]
            extra = [(v, item) for v, item in have
                     if ranked and not partial and v not in want]
            if not missing and not extra:
                say(f"[Playlists] {title}: up to date ({len(present)})")
                ids[key] = pid
                continue
            say(f"[Playlists] {title}: +{len(missing)} -{len(extra)}")
            if dry_run:
                continue
            if not pid:
                spend(50)
                pid = svc.playlists().insert(part="snippet,status", body={
                    "snippet": {"title": title, "description": desc, "defaultLanguage": "en"},
                    "status": {"privacyStatus": "public"}}).execute()["id"]
                ids[key] = pid
                say(f"[Playlists] created {title}: https://www.youtube.com/playlist?list={pid}")
            ids[key] = pid
            for v, item in extra:
                spend(50)
                svc.playlistItems().delete(id=item).execute()
                present.remove(v)
            for v in missing:
                # In the wanted order: newest stream on top, best clip first.
                at = len([w for w in want[:want.index(v)] if w in present])
                body = {"snippet": {"playlistId": pid, "position": at,
                                    "resourceId": {"kind": "youtube#video", "videoId": v}}}
                spend(50)
                try:
                    svc.playlistItems().insert(part="snippet", body=body).execute()
                except Exception as exc:
                    if "manualSortRequired" not in str(exc) and "409" not in str(exc):
                        raise
                    del body["snippet"]["position"]  # sorted by YouTube: just add
                    svc.playlistItems().insert(part="snippet", body=body).execute()
                present.insert(at, v)
    except _Budget:
        finished = False
        say(f"[Playlists] stopped at the {cap}-unit cap - the rest goes next run.")
    except Exception as exc:
        finished = False
        say(f"[Playlists] stopped: {str(exc)[:200]}")
    finally:
        now = dt.datetime.now().isoformat(timespec="seconds")
        state["last_try"] = now
        if finished and not dry_run:
            state["last"] = now
        if not dry_run:
            _save(state)
        say(f"[Playlists] {spent[0]} units spent")
    return spent[0], finished


def _due(state, now):
    def age(key, default):
        try:
            return (now - dt.datetime.fromisoformat(state[key])).total_seconds() / 86400
        except (KeyError, TypeError, ValueError):
            return default
    if age("last_try", 99) < 1:      # a stopped run tries again tomorrow, not now
        return False
    return age("last", 99) >= SYNC_DAYS


def maybe_sync(cfg=None, now=None):
    """Start a sync in the background if one is due. Never raises, never blocks."""
    try:
        if not _due(_load(), now or dt.datetime.now()):
            return False
        if not os.path.exists(os.path.join(UPLOADER, TOKENS["vods"])):
            return False

        def run():
            if not _lock.acquire(blocking=False):
                return
            try:
                sync()
            except Exception as exc:
                print(f"[Playlists] sync failed: {exc}")
            finally:
                _lock.release()
        threading.Thread(target=run, name="playlist-sync", daemon=True).start()
        return True
    except Exception as exc:
        print(f"[Playlists] not started: {exc}")
        return False
