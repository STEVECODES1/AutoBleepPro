"""Every public video gets the ALL LINKS block and a links comment.

    python tools/links_backfill.py                 dry run: what is missing
    python tools/links_backfill.py --apply --max 40

Description: the ALL LINKS block (the uploader's own footer) is added at the
bottom when it is missing; nothing else in the description changes.
Comment: one comment from the channel with the Rumble link and every
channel, unless the channel already left one with a rumble.com link.
Shorts get the description block only - YouTube does not make links in
Shorts comments clickable, and a wall of dead links under a Short reads as
spam.

Quota: one Google project for all four channels, 10,000 units a day, and
the uploader needs it too. videos.update and commentThreads.insert are 50
units each; --max caps the writes (both kinds together) and the run stops
at the first quota error. Run it again the next day for the rest - what is
done is skipped. Old descriptions are kept in logs/links_backfill.jsonl.
"""
import argparse
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADER = os.path.join(ROOT, "auto_uploader")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, UPLOADER)

from rumble_cta_backfill import CHANNELS, service, update  # noqa: E402
from utils.stream_links import youtube_footer  # noqa: E402
from utils.unmojibake import fix  # noqa: E402

LOG = os.path.join(UPLOADER, "logs", "links_backfill.jsonl")
RUMBLE = "https://rumble.com/user/BinScripts"
MAX_DESC = 5000
_DUR = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def seconds(iso):
    m = _DUR.fullmatch(iso or "")
    if not m:
        return 0
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def footer():
    with open(os.path.join(UPLOADER, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    return fix(youtube_footer(cfg["youtube"].get("description_template", ""),
                              cfg.get("rumble", {}).get("channel_url", "")))


def public_videos(svc):
    mine = svc.channels().list(part="contentDetails", mine=True).execute()["items"][0]
    playlist = mine["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while True:
        page = svc.playlistItems().list(part="contentDetails", playlistId=playlist,
                                        maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in page.get("items", [])]
        token = page.get("nextPageToken")
        if not token:
            break
    out = []
    for n in range(0, len(ids), 50):
        got = svc.videos().list(part="snippet,status,contentDetails",
                                id=",".join(ids[n:n + 50])).execute()
        out += [v for v in got.get("items", [])
                if v["status"].get("privacyStatus") == "public"]
    return mine["id"], out


def has_link_comment(svc, channel_id, video_id):
    try:
        got = svc.commentThreads().list(part="snippet", videoId=video_id, maxResults=100,
                                        searchTerms="rumble.com").execute()
    except Exception as exc:  # comments off on the video: nothing to add to
        return "off" if "disabled" in str(exc).lower() else False
    for item in got.get("items", []):
        top = item["snippet"]["topLevelComment"]["snippet"]
        if top.get("authorChannelId", {}).get("value") == channel_id:
            return True
    return False


def comment_text(desc, foot):
    m = re.search(r"https://rumble\.com/v[\w.-]+", desc or "")
    first = (f"▶ This stream uncut & uncensored on Rumble: {m.group(0)}" if m
             else f"▶ Full streams uncut & uncensored on Rumble: {RUMBLE}")
    return f"{first}\n\n{foot}"


def shorts_comment_text(desc):
    """Under a Short: the full video first - "where's the full video" is
    what people ask - then the uncut one on Rumble, then every stream.
    YouTube does not make these clickable under a Short; they are there to
    be read and copied."""
    m = re.search(r"Full stream: (https://www\.youtube\.com/watch\?v=[\w-]+)", desc or "")
    full = m.group(1) if m else ""
    rumble = ""
    if full:
        try:
            with open(os.path.join(UPLOADER, "logs", "stream_links.json"), encoding="utf-8") as f:
                for entry in json.load(f):
                    if entry.get("youtube") == full and str(entry.get("rumble", "")).startswith("https://rumble.com/"):
                        rumble = entry["rumble"]
        except (OSError, ValueError):
            pass
    lines = [f"▶ Full video: {full}"] if full else []
    lines.append(f"▶ Uncut & uncensored on Rumble: {rumble or RUMBLE}")
    lines.append("▶ Every full stream: https://www.youtube.com/@wopovod")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--channel", default="")
    ap.add_argument("--shorts", action="store_true",
                    help="also comment under Shorts (the clips channel)")
    ap.add_argument("--comments-first", action="store_true",
                    help="comments only this run (the ones viewers ask for)")
    args = ap.parse_args()
    foot = footer()
    writes = 0
    for label, token in CHANNELS:
        if args.channel and args.channel.lower() not in label.lower():
            continue
        svc = service(token)
        channel_id, videos = public_videos(svc)
        need_desc, need_comment = [], []
        for v in videos:
            desc = v["snippet"].get("description", "")
            if "ALL LINKS" not in desc and len(desc) + len(foot) + 2 <= MAX_DESC:
                need_desc.append(v)
            short = seconds(v["contentDetails"].get("duration")) <= 180
            v["_short"] = short
            if (not short or args.shorts) and has_link_comment(svc, channel_id, v["id"]) is False:
                need_comment.append(v)
        print(f"== {label}: {len(videos)} public, missing ALL LINKS {len(need_desc)}, "
              f"missing links comment {len(need_comment)}")
        if not args.apply:
            continue
        for v in ([] if args.comments_first else need_desc):
            if writes >= args.max:
                break
            old = v["snippet"].get("description", "")
            new = f"{fix(old).rstrip()}\n\n{foot}"
            try:
                update(svc, v, fix(v["snippet"]["title"]), new)
            except Exception as exc:
                print(f"  STOP at {v['id']}: {str(exc)[:200]}")
                return 1
            writes += 1
            v["snippet"]["description"] = new
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.time(), "what": "footer", "channel": label,
                                    "id": v["id"], "old": old}, ensure_ascii=False) + "\n")
            print(f"  + links block: {v['id']} {v['snippet']['title'][:60]}")
        for v in need_comment:
            if writes >= args.max:
                break
            desc = v["snippet"].get("description", "")
            text = shorts_comment_text(desc) if v.get("_short") else comment_text(desc, foot)
            try:
                svc.commentThreads().insert(part="snippet", body={"snippet": {
                    "videoId": v["id"],
                    "topLevelComment": {"snippet": {"textOriginal": text}}}}).execute()
            except Exception as exc:
                print(f"  STOP at {v['id']}: {str(exc)[:200]}")
                return 1
            writes += 1
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.time(), "what": "comment", "channel": label,
                                    "id": v["id"]}) + "\n")
            print(f"  + comment: {v['id']} {v['snippet']['title'][:60]}")
    print(f"writes {writes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
