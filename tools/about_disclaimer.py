"""Put today's fan + role-play notice at the top of every channel's About.

Owner, 10-28: every channel About says GTA RP is role-play, everyone is
acting, the non-GTA parts are actors, nothing is real. An older wording is
replaced in place (utils.disclosure.for_about); the rest of the About is
kept. Every other branding field is sent back as it was, and the old
settings go to logs/branding_backup.jsonl.

    python -X utf8 tools/about_disclaimer.py            show what would change
    python -X utf8 tools/about_disclaimer.py --apply    change it (50 units a channel)
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADER = os.path.join(ROOT, "auto_uploader")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, UPLOADER)
from rumble_cta_backfill import service  # noqa: E402
from utils.disclosure import ACTORS_LINE, for_about  # noqa: E402

TOKENS = ("youtube_token.json", "youtube_shorts_token.json",
          "youtube_games_token.json", "youtube_compilation_token.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    bad = 0
    for token in TOKENS:
        if not os.path.isfile(os.path.join(UPLOADER, token)):
            print(f"{token}: not signed in - skipped")
            continue
        try:
            svc = service(token)
            me = svc.channels().list(part="brandingSettings,snippet", mine=True).execute()["items"][0]
        except Exception as exc:
            print(f"{token}: could not read the channel ({type(exc).__name__}: {exc})")
            bad += 1
            continue
        name = me["snippet"]["title"]
        branding = me.get("brandingSettings") or {}
        old = (branding.get("channel") or {}).get("description", "") or ""
        new = for_about(old)
        if ACTORS_LINE in old and new == old.strip():
            print(f"{name}: About already carries the notice")
            continue
        print(f"{name}: About {'updated' if args.apply else 'would be updated'}")
        if not args.apply:
            continue
        with open(os.path.join(UPLOADER, "logs", "branding_backup.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), "channel": me["id"],
                                "brandingSettings": branding}, ensure_ascii=False) + "\n")
        branding.setdefault("channel", {})["description"] = new
        svc.channels().update(part="brandingSettings",
                              body={"id": me["id"], "brandingSettings": branding}).execute()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
