"""Put a banner (and optionally a new About text) on a YouTube channel.

    python -X utf8 tools/set_banner.py youtube_token.json auto_uploader/thumbnails/banner_wopovod.png
    python -X utf8 tools/set_banner.py youtube_token.json banner.png --about about.txt

Make the banner with tools/make_banner.py first. Every other branding
field is read and sent back as it was (a brandingSettings update clears
whatever is left out). The old settings go to logs/branding_backup.jsonl
so they can be put back. Costs ~100 units (banner 50, channel update 50).
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from rumble_cta_backfill import UPLOADER, service  # noqa: E402
from googleapiclient.http import MediaFileUpload  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("token")
    ap.add_argument("banner")
    ap.add_argument("--about", default="", help="a UTF-8 text file with the new channel description")
    args = ap.parse_args()

    svc = service(args.token)
    me = svc.channels().list(part="brandingSettings,snippet", mine=True).execute()["items"][0]
    branding = me["brandingSettings"]
    with open(os.path.join(UPLOADER, "logs", "branding_backup.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.time(), "channel": me["id"], "brandingSettings": branding},
                           ensure_ascii=False) + "\n")
    url = svc.channelBanners().insert(
        media_body=MediaFileUpload(args.banner, mimetype="image/png")).execute()["url"]
    branding.setdefault("image", {})["bannerExternalUrl"] = url
    if args.about:
        with open(args.about, encoding="utf-8") as f:
            branding.setdefault("channel", {})["description"] = f.read().strip()
    svc.channels().update(part="brandingSettings",
                          body={"id": me["id"], "brandingSettings": branding}).execute()
    print(f"banner set on {me['snippet']['title']}" + (" (+ About text)" if args.about else ""))


if __name__ == "__main__":
    main()
