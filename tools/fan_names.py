"""Put "(Fan Channel)" in every channel name we run (10-18).

YouTube's impersonation policy: a fan channel must say so in its channel
NAME or HANDLE - "STACKSWOPO CLIPS" (his name + "Clips") was removed for
looking like his own channel. Idempotent.

  python -X utf8 tools/fan_names.py [--dry-run] [token ...]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TOKENS = ("youtube_compilation_token.json", "youtube_games_token.json", "youtube_token.json")
SUFFIX = " (Fan Channel)"


def fan_name(title):
    title = (title or "").strip()
    return title if "fan" in title.lower() else (title + SUFFIX)[:50]


def main():
    dry = "--dry-run" in sys.argv
    tokens = [a for a in sys.argv[1:] if a.endswith(".json")] or TOKENS
    os.chdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto_uploader"))
    from rumble_cta_backfill import service
    for tok in tokens:
        yt = service(tok)
        ch = yt.channels().list(part="brandingSettings,snippet", mine=True).execute()["items"][0]
        old = ch["snippet"]["title"]
        new = fan_name(old)
        if new == old:
            print(tok, "already:", old)
            continue
        if dry:
            print(tok, old, "->", new)
            continue
        channel = dict(ch["brandingSettings"].get("channel", {}))
        channel["title"] = new
        yt.channels().update(part="brandingSettings",
                             body={"id": ch["id"], "brandingSettings": {"channel": channel}}).execute()
        now = yt.channels().list(part="snippet", mine=True).execute()["items"][0]["snippet"]["title"]
        print(tok, old, "->", now, "(took)" if now == new else "(API did not change it - do it in Studio)")


if __name__ == "__main__":
    main()
