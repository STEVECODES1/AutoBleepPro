"""Put the fan + actors notice into the descriptions of videos already up.

The owner asked (10-22) that every channel say plainly it is fan-run and
that the people on screen are actors. New uploads get it from
`utils/disclosure.py`; this tool is for everything published before that
change. Without it a viewer who opens an old video sees the old text.

Quota is the reason this is a tool and not a startup job: one
videos.update is 50 units of the shared 10,000/day, and a full stream
upload needs 1,600. `--max` caps the work per channel (default 10), so a
run can never starve an upload. `--all` does every public video on the
named channels.

    python -X utf8 tools/disclaimer_backfill.py                 dry run
    python -X utf8 tools/disclaimer_backfill.py --apply --max 10
    python -X utf8 tools/disclaimer_backfill.py --apply --all --only clips

Only public videos are touched: nobody sees a private description. Every
change is appended to logs/disclaimer_backfill.jsonl WITH the old
description, so any one can be put back. Title, tags, category and the
language fields go back exactly as they were - an omitted snippet field
is cleared by videos.update.
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADER = os.path.join(ROOT, "auto_uploader")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, UPLOADER)

from utils.disclosure import for_description, has_disclaimer  # noqa: E402

CHANNELS = (
    ("vods", "youtube_token.json"),
    ("clips", "youtube_shorts_token.json"),
    ("games", "youtube_games_token.json"),
    ("compilations", "youtube_compilation_token.json"),
)
# Everything videos.update needs to put back, not just the description.
SNIPPET_FIELDS = ("title", "description", "tags", "categoryId",
                  "defaultLanguage", "defaultAudioLanguage")
LOG = os.path.join(UPLOADER, "logs", "disclaimer_backfill.jsonl")


def _all(call, **kw):
    items, token = [], None
    while True:
        page = call(pageToken=token, **kw).execute()
        items += page.get("items", [])
        token = page.get("nextPageToken")
        if not token:
            return items


def uploads(yt):
    """Every video on the channel, newest first."""
    ch = yt.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    playlist = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    ids = [i["contentDetails"]["videoId"] for i in
           _all(yt.playlistItems().list, part="contentDetails",
                playlistId=playlist, maxResults=50)]
    out = []
    for start in range(0, len(ids), 50):
        out += yt.videos().list(part="snippet,status",
                                id=",".join(ids[start:start + 50])
                                ).execute().get("items", [])
    return out


def planned(video):
    """The new description, or None when the video needs nothing."""
    if video.get("status", {}).get("privacyStatus") != "public":
        return None
    snippet = video.get("snippet", {}) or {}
    old = snippet.get("description", "") or ""
    if has_disclaimer(old):
        return None
    new = for_description(old)
    return new if new != old else None


def update(yt, video, description):
    snippet = video.get("snippet", {}) or {}
    body = {key: snippet[key] for key in SNIPPET_FIELDS if key in snippet}
    body["description"] = description
    yt.videos().update(part="snippet",
                       body={"id": video["id"], "snippet": body}).execute()


def _log(entry):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default: count only)")
    ap.add_argument("--max", type=int, default=10,
                    help="videos per channel (default 10; ignored by --all)")
    ap.add_argument("--all", action="store_true",
                    help="every public video on the selected channels")
    ap.add_argument("--only", default="",
                    help="comma-separated: vods,clips,games,compilations")
    a = ap.parse_args()

    wanted = {name.strip() for name in a.only.split(",") if name.strip()}
    channels = [c for c in CHANNELS if not wanted or c[0] in wanted]
    if not channels:
        print(f"Nothing matches --only {a.only!r}. Choose from: "
              f"{', '.join(name for name, _ in CHANNELS)}")
        return 2

    os.chdir(UPLOADER)
    from rumble_cta_backfill import service
    from googleapiclient.errors import HttpError

    total = 0
    for name, token in channels:
        print(f"== {name} ({token})")
        try:
            yt = service(token)
            videos = uploads(yt)
        except Exception as exc:
            print(f"   could not read the channel: {exc}")
            continue
        pending = [(v, planned(v)) for v in videos]
        pending = [(v, new) for v, new in pending if new is not None]
        print(f"   {len(videos)} videos, {len(pending)} need the notice")
        if not a.apply:
            for video, _ in pending[:3]:
                print(f"   would update: {video['snippet']['title'][:80]}")
            total += len(pending[:a.max] if not a.all else pending)
            continue
        chosen = pending if a.all else pending[:max(0, a.max)]
        done = 0
        for video, new in chosen:
            snippet = video.get("snippet", {}) or {}
            try:
                update(yt, video, new)
            except HttpError as exc:
                text = str(exc)
                print(f"   update failed: {text[:300]}")
                if "quota" in text.lower():
                    print("   daily quota reached - stopping here.")
                    break
                continue
            except Exception as exc:
                print(f"   update failed: {exc}")
                continue
            _log({"at": datetime.datetime.now().isoformat(timespec="seconds"),
                  "channel": name, "video": video["id"],
                  "title": snippet.get("title", ""),
                  "old": snippet.get("description", ""), "new": new})
            done += 1
            total += 1
            if done % 10 == 0:
                print(f"   {done}/{len(chosen)} done")
        print(f"   updated {done}")
    print(f"\n{'updated' if a.apply else 'would update'} {total} video(s).")
    if not a.apply:
        print("Add --apply to write them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
