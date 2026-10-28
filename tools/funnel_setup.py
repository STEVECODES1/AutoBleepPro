"""Make STACKSWOPO GAMES and STACKSWOPO CLIPS push viewers to wopovod and Rumble.

Owner's ask (10-18): both channels are traffic sources, keep them organized.
Does, each at most once and quota-capped (50 units a write):
  - GAMES About: links to full streams (@WOPOVOD), uncut Rumble, clips.
  - CLIPS About: the same links on top of the current text.
  - GAMES titles with the name twice ("Stackswopo - STACKSWOPO ...") fixed.
  - GAMES playlist "Stackswopo Best Moments - every stream" with the recaps.
Re-running only does what is still missing.

  python -X utf8 tools/funnel_setup.py [--cap 5] [--playlist-only] [--at 03:30] [--dry-run]
"""
import argparse
import datetime
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

WOPOVOD = "https://www.youtube.com/@WOPOVOD"
RUMBLE = "https://rumble.com/user/BinScripts"
GAMES = "https://www.youtube.com/@StackswopoGames"
CLIPS = "https://www.youtube.com/@WOPOCLIPPER"
PLAYLIST_TITLE = "Stackswopo Best Moments - every stream"
# 10-18: YouTube removed @WOPOCLIPPER for impersonation - it looked like
# Stackswopo's own channel. It was restored on 10-22, so the clips channel
# is back in the funnel - but every channel we keep says up top that it is
# fan-run, that the people on screen are actors, and links his real channel.
DISCLOSURE = ("Fan channel - not Stackswopo's own channel. "
              "His channel: https://www.youtube.com/@stackswopo_\n\n"
              "⚠️ DISCLAIMER: GTA RP is role-play - everyone in it is acting. "
              "The non-GTA parts feature actors too. Nothing shown is real.\n\n")
OLD_DISCLOSURES = ("Fan-run channel - not Stackswopo's own channel. Clips and VODs are posted with "
                   "his permission. His official channel: https://www.youtube.com/@stackswopo_\n\n",)
GAMES_ABOUT = DISCLOSURE + (
    "Stackswopo's best moments from every stream - the funniest parts, cut down for you.\n\n"
    f"▶ Full streams, every minute: {WOPOVOD}\n"
    f"▶ Uncut & uncensored on Rumble: {RUMBLE}\n"
    f"▶ Daily clips & Shorts: {CLIPS}\n\n"
    "Subscribe - Onna Bros! 🔔")
CLIPS_ABOUT = DISCLOSURE + (
    "Stackswopo's funniest moments, stream clips and Shorts - cut down for you.\n\n"
    f"▶ Full streams, every minute: {WOPOVOD}\n"
    f"▶ Uncut & uncensored on Rumble: {RUMBLE}\n"
    f"▶ Best moments of every stream: {GAMES}\n\n"
    "Subscribe - Onna Bros! 🔔")


def disclosed(existing):
    """The About with the fan + actors notice on top (unchanged if there)."""
    existing = existing or ""
    for old in OLD_DISCLOSURES:
        existing = existing.replace(old.strip(), "").strip()
    if existing.startswith(DISCLOSURE.strip()):
        return existing
    # A fan-only notice from 10-19: replace it rather than stack a second.
    from utils.disclosure import FAN_LINE, ACTORS_LINE, OLD_ACTORS_LINES

    existing = existing.replace(FAN_LINE, "").replace(ACTORS_LINE, "")
    for old in OLD_ACTORS_LINES:
        existing = existing.replace(old, "")
    existing = existing.strip()
    return (DISCLOSURE + existing)[:1000]


def fixed_title(title):
    return re.sub(r"^\s*stackswopo\s*[-|:]\s*(?=stackswopo\b)", "", title or "", flags=re.I)


def is_recap(title):
    return "best moments" in (title or "").lower()


def to_add(recaps, already, cap):
    have = set(already)
    return [v for v in recaps if v not in have][:max(0, cap)]


def _all(call, **kw):
    items, token = [], None
    while True:
        page = call(pageToken=token, **kw).execute()
        items += page.get("items", [])
        token = page.get("nextPageToken")
        if not token:
            return items


def videos(yt):
    ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids = [i["contentDetails"]["videoId"] for i in
           _all(yt.playlistItems().list, part="contentDetails", playlistId=up, maxResults=50)]
    out = []
    for i in range(0, len(ids), 50):
        out += yt.videos().list(part="snippet,status", id=",".join(ids[i:i + 50])).execute().get("items", [])
    return out  # newest first


def set_about(yt, make, dry):
    ch = yt.channels().list(part="brandingSettings", mine=True).execute()["items"][0]
    channel = dict(ch.get("brandingSettings", {}).get("channel", {}))
    old = channel.get("description", "")
    new = make(old)
    if new == old:
        return False
    if not dry:
        channel["description"] = new
        yt.channels().update(part="brandingSettings",
                             body={"id": ch["id"], "brandingSettings": {"channel": channel}}).execute()
    return True


def fix_titles(yt, vids, cap, dry):
    done = 0
    for v in vids:
        sn = v["snippet"]
        title = fixed_title(sn["title"])
        if title == sn["title"] or done >= cap:
            continue
        body = {k: sn[k] for k in ("description", "tags", "categoryId", "defaultLanguage",
                                   "defaultAudioLanguage") if k in sn}
        body["title"] = title
        if not dry:
            yt.videos().update(part="snippet", body={"id": v["id"], "snippet": body}).execute()
        print(f"title: {title[:80]}")
        done += 1
    return done


def playlist(yt, vids, cap, dry):
    pls = _all(yt.playlists().list, part="snippet", mine=True, maxResults=50)
    pid = next((p["id"] for p in pls if p["snippet"]["title"] == PLAYLIST_TITLE), None)
    fresh = not pid
    if fresh and not dry:
        pid = yt.playlists().insert(part="snippet,status", body={
            "snippet": {"title": PLAYLIST_TITLE, "description":
                        "The best moments of every Stackswopo stream.\n"
                        f"Full streams: {WOPOVOD}\nUncut on Rumble: {RUMBLE}"},
            "status": {"privacyStatus": "public"}}).execute()["id"]
    have = [i["contentDetails"]["videoId"] for i in
            _all(yt.playlistItems().list, part="contentDetails", playlistId=pid, maxResults=50)
            ] if pid and not fresh else []  # a just-made playlist 404s for a while
    recaps = [v["id"] for v in vids if is_recap(v["snippet"]["title"])
              and v.get("status", {}).get("privacyStatus") == "public"]
    adds = to_add(recaps, have, cap)
    top = recaps.index(have[0]) if have and have[0] in recaps else None
    for vid in adds:
        snippet = {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": vid}}
        if top is not None and recaps.index(vid) < top:
            snippet["position"] = 0  # newer than what is on top: goes first
        if not dry:
            yt.playlistItems().insert(part="snippet", body={"snippet": snippet}).execute()
    print(f"playlist: {len(have)} in, {len(adds)} added, {len(recaps) - len(have) - len(adds)} still to add")
    return len(adds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=5)
    ap.add_argument("--playlist-only", action="store_true")
    ap.add_argument("--at", default="", help="HH:MM local time to start (waits until then)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.at:
        h, m = map(int, a.at.split(":"))
        now = datetime.datetime.now()
        start = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if start <= now:
            start += datetime.timedelta(days=1)
        print(f"waiting until {start:%m-%d %H:%M}", flush=True)
        time.sleep((start - now).total_seconds())
    os.chdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto_uploader"))
    from rumble_cta_backfill import service
    games = service("youtube_games_token.json")
    vids = videos(games)
    if not a.playlist_only:
        print("GAMES about changed:", set_about(games, lambda old: GAMES_ABOUT, a.dry_run))
        print("titles fixed:", fix_titles(games, vids, a.cap, a.dry_run))
        for tok in ("youtube_token.json", "youtube_compilation_token.json"):
            print(tok, "about changed:", set_about(service(tok), disclosed, a.dry_run))
        print("CLIPS about changed:", set_about(
            service("youtube_shorts_token.json"), lambda old: CLIPS_ABOUT, a.dry_run))
    playlist(games, vids, a.cap, a.dry_run)


if __name__ == "__main__":
    main()
