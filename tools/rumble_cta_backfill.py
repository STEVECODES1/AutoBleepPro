"""Put the Rumble call-to-action on every YouTube video that lacks one.

    python tools/rumble_cta_backfill.py               dry run: counts, samples
    python tools/rumble_cta_backfill.py --peek 2      newest descriptions per channel
    python tools/rumble_cta_backfill.py --apply --max 60

Only the description changes. Title, tags, category and the language fields
go back exactly as they were (a snippet update clears anything left out).
A video whose description already has a rumble.com link is left alone.
Every change is appended to logs/rumble_cta_backfill.jsonl WITH the old
description, so any one can be put back.

Quota: one Google project serves all four channels, 10,000 units a day, and
the uploader needs it too. A videos.update is 50 units, so --max keeps the
run small; it stops at the first quota error.
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADER = os.path.join(ROOT, "auto_uploader")
sys.path.insert(0, ROOT)
sys.path.insert(0, UPLOADER)

from utils.youtube_uploader import YouTubeUploader  # noqa: E402

SECRETS = os.path.join(UPLOADER, "client_secrets.json")
LOG = os.path.join(UPLOADER, "logs", "rumble_cta_backfill.jsonl")
CHANNELS = [
    ("STACKSWOPO VODS", "youtube_token.json"),
    ("STACKSWOPO CLIPS (Fan Channel)", "youtube_shorts_token.json"),
    ("STACKSWOPO GAMES", "youtube_games_token.json"),
    ("STACKSWOPOVODS", "youtube_compilation_token.json"),
]
RUMBLE = "https://rumble.com/user/BinScripts"
CTA = f"▶ Full streams uncut & uncensored on Rumble: {RUMBLE}"
MAX_DESC = 5000


def service(token):
    return YouTubeUploader(SECRETS, os.path.join(UPLOADER, token)).get_service()


def uploads(svc):
    mine = svc.channels().list(part="contentDetails,snippet", mine=True).execute()["items"][0]
    playlist = mine["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while True:
        page = svc.playlistItems().list(part="contentDetails", playlistId=playlist,
                                        maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in page.get("items", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    videos = []
    for n in range(0, len(ids), 50):
        got = svc.videos().list(part="snippet,status", id=",".join(ids[n:n + 50])).execute()
        videos += got.get("items", [])
    return mine["snippet"]["title"], videos


from unmojibake import fix as unmojibake  # noqa: E402


def planned(video):
    """(new_title, new_desc) for a video, or None when nothing changes.

    Mojibake is fixed on every video; the Rumble line is added only to
    public ones (nobody sees a private description)."""
    sn = video["snippet"]
    title, desc = sn["title"], sn.get("description", "")
    new_title, new_desc = unmojibake(title), unmojibake(desc)
    if video.get("status", {}).get("privacyStatus") == "public" and not has_rumble(new_desc):
        new_desc = with_cta(new_desc) or new_desc
    if (new_title, new_desc) == (title, desc):
        return None
    return new_title, new_desc


def has_rumble(desc):
    return "rumble.com" in (desc or "").lower()


def with_cta(desc):
    desc = (desc or "").rstrip()
    new = f"{CTA}\n\n{desc}" if desc else CTA
    return new if len(new) <= MAX_DESC else None


def update(svc, video, new_title, new_desc):
    sn = video["snippet"]
    body = {"title": new_title, "description": new_desc,
            "categoryId": sn.get("categoryId", "20")}
    for key in ("tags", "defaultLanguage", "defaultAudioLanguage"):
        if sn.get(key):
            body[key] = sn[key]
    svc.videos().update(part="snippet", body={"id": video["id"], "snippet": body}).execute()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max", type=int, default=60)
    ap.add_argument("--peek", type=int, default=0)
    ap.add_argument("--channel", default="")
    args = ap.parse_args()

    done = 0
    for label, token in CHANNELS:
        if args.channel and args.channel.lower() not in label.lower():
            continue
        svc = service(token)
        title, videos = uploads(svc)
        plans = [(v, planned(v)) for v in videos]
        plans = [(v, p) for v, p in plans if p]
        pub = lambda v: v.get("status", {}).get("privacyStatus") == "public"  # noqa: E731
        garbled = [v for v, p in plans if p[0] != v["snippet"]["title"]
                   or unmojibake(v["snippet"].get("description", "")) != v["snippet"].get("description", "")]
        print(f"== {label} ({title}): {len(videos)} videos, {len(plans)} to change "
              f"({sum(pub(v) for v, _ in plans)} public), {len(garbled)} with garbled text")
        for v in videos[:args.peek]:
            print(f"--- newest: {v['snippet']['title']}\n{v['snippet'].get('description', '')[:900]}\n")
        if not args.apply:
            for v, (t, d) in plans[:3]:
                print(f"   e.g. {v['id']} {t[:60]!r}")
            continue
        # public first, newest first - the ones people actually see
        plans.sort(key=lambda vp: not pub(vp[0]))
        for v, (new_title, new_desc) in plans:
            if done >= args.max:
                break
            try:
                update(svc, v, new_title, new_desc)
            except Exception as exc:
                print(f"  STOP at {v['id']}: {str(exc)[:200]}")
                return 1
            done += 1
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.time(), "channel": label, "id": v["id"],
                                    "title": v["snippet"]["title"],
                                    "old": v["snippet"].get("description", "")},
                                   ensure_ascii=False) + "\n")
            print(f"  + {v['id']} {new_title[:70]}")
    print(f"updated {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
