"""Catch up collabs a restart or a powered-off PC missed (11-03).

The uploader starts a collab after each new full stream (utils/collab.py),
but that process dies if the PC restarts before it finishes. This looks at
wopovod's newest uploads and runs the collab for any full stream that is
public, over an hour old and not yet done. Windows task
"AutoBleepPro Collab Catchup" runs it daily and 20 min after sign-in.
Read cost: a few YouTube API units.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "auto_uploader")
sys.path.insert(0, APP)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.chdir(APP)


def candidates(svc, newest: int = 6) -> list:
    mine = svc.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    playlist = mine["contentDetails"]["relatedPlaylists"]["uploads"]
    items = svc.playlistItems().list(part="contentDetails", playlistId=playlist,
                                     maxResults=newest).execute().get("items", [])
    ids = [i["contentDetails"]["videoId"] for i in items]
    if not ids:
        return []
    videos = svc.videos().list(part="snippet,status", id=",".join(ids)).execute()["items"]
    out = []
    for v in videos:
        title = v["snippet"]["title"]
        published = v["snippet"].get("publishedAt", "")
        try:
            age_h = (time.time() - time.mktime(time.strptime(published[:19], "%Y-%m-%dT%H:%M:%S"))
                     + time.timezone) / 3600
        except ValueError:
            age_h = 0
        if (v["status"].get("privacyStatus") == "public" and "FULL STREAM" in title.upper()
                and 1 <= age_h <= 24 * 7):
            out.append((v["id"], title))
    return out


def main() -> int:
    from rumble_cta_backfill import service
    from utils import collab
    from utils.live_now import is_live

    if is_live():
        print("live now - skipping")
        return 0
    todo = [(vid, t) for vid, t in candidates(service("youtube_token.json"))
            if not collab.done(vid)]
    print(f"{time.strftime('%m-%d %H:%M')} {len(todo)} stream(s) need a collab")
    for vid, title in todo:
        print(f"  {vid} {title}")
        if "--dry" not in sys.argv:
            collab.run(f"https://www.youtube.com/watch?v={vid}", say=print)
    return 0


if __name__ == "__main__":
    sys.exit(main())
