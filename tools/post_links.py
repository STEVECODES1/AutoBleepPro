"""One tidy Discord card per clip, with a link for every platform it is on
(owner 11-04: "every post needs a link").

The publishers only say "posted" (Instagram/Facebook Reels) or hand back a
Buffer id (X/TikTok, whose real link exists ~2 min later), so instead of
plumbing ids through every route this ASKS each platform for its newest
posts, groups them by the clip's headline, and keeps one Discord message
per clip - edited in place as more links go live. Read-only everywhere
except Discord.

Windows task "AutoBleepPro Post Links" runs it every 15 min.
Ledger: auto_uploader/logs/post_links.json.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "auto_uploader")
sys.path.insert(0, APP)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
os.chdir(APP)

LEDGER = os.path.join(APP, "logs", "post_links.json")
GRAPH = "https://graph.facebook.com/v19.0"
WINDOW_H = 48          # posts newer than this get a card
FIRST_RUN_H = 6        # ...but the very first run only looks this far back
ORDER = ("youtube_shorts", "tiktok", "instagram", "facebook", "x", "rumble")
LABEL = {"youtube_shorts": "▶️ YouTube Shorts", "tiktok": "🎵 TikTok",
         "instagram": "📸 Instagram", "facebook": "📘 Facebook", "x": "✖️ X",
         "rumble": "🟢 Rumble"}


def key_of(text: str) -> str:
    """The clip's headline, flattened: first line, letters/digits, 8 words."""
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    words = re.findall(r"[a-z0-9]+", first.lower())
    words = [w for w in words if w not in ("new", "upload", "clip")]
    return " ".join(words[:8])


def _get(url: str, headers=None, timeout=30) -> dict:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "AutoBleep"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _ts(value: str) -> float:
    value = str(value or "").replace("Z", "+00:00")
    try:
        import datetime as dt

        return dt.datetime.fromisoformat(value.replace("+0000", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ── what was posted: logs/post_refs.jsonl (written at post time) + Rumble ──

PLATFORM_OF = {"buffer_x": "x", "zernio_twitter": "x", "postproxy_x": "x",
               "buffer_tiktok": "tiktok", "zernio_tiktok": "tiktok",
               "postproxy_tiktok": "tiktok", "postproxy_instagram": "instagram",
               "postproxy_facebook": "facebook", "postproxy_youtube": "youtube_shorts"}


def _buffer_links() -> dict:
    """Buffer post id -> the real X/TikTok link (there once it has sent)."""
    from publishers.buffer import BufferPublisher

    query = ("query Recent($input: PostsInput!) { posts(input: $input, first: 50) "
             "{ edges { node { id status externalLink } } } }")
    out = {}
    for service in ("twitter", "tiktok"):
        try:
            b = BufferPublisher({}, service)
            if not b.ready():
                continue
            ch = b.channel_id()
            data = b._call(query, {"input": {"organizationId": b._org,
                                             "filter": {"channelIds": [ch]},
                                             "sort": [{"field": "createdAt",
                                                       "direction": "desc"}]}})
        except Exception:
            continue
        for e in (data.get("posts") or {}).get("edges") or []:
            n = e.get("node") or {}
            if n.get("externalLink"):
                out[str(n.get("id"))] = n["externalLink"]
    return out


def _instagram_link(media_id: str) -> str:
    token = os.getenv("IG_PAGE_TOKEN", "")
    if not token:
        return ""
    q = urllib.parse.urlencode({"fields": "permalink", "access_token": token})
    try:
        return _get(f"{GRAPH}/{media_id}?{q}").get("permalink", "")
    except Exception:
        return ""


def resolve(platform: str, ref: str, buffer_links: dict) -> str:
    ref = str(ref or "").strip()
    if ref.startswith("http"):
        return ref
    if ref.startswith("buffer post "):
        return buffer_links.get(ref.split()[-1], "")
    if platform == "instagram" and ref.isdigit():
        return _instagram_link(ref)
    if platform == "facebook" and ref.isdigit():
        return f"https://www.facebook.com/reel/{ref}"
    if platform == "youtube_shorts" and re.fullmatch(r"[\w-]{11}", ref):
        return f"https://www.youtube.com/shorts/{ref}"
    return ""


def from_rumble(since: float):
    """(clip_key, url, at) for clips Rumble took, from its upload log."""
    from utils.clip_queue import clip_key

    path = os.path.join(APP, "logs", "rumble.log")
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()[-3000:]
    except OSError:
        return
    pat = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[INFO\] (.+?\.(?:mp4|ts|mkv)): "
                     r"uploaded successfully -> (https://rumble\.com/\S+)")
    for line in lines:
        m = pat.match(line)
        if not m or "full-stream" in m.group(3):
            continue
        at = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        if at >= since:
            yield clip_key(m.group(2)), m.group(3).rstrip("."), at


def collect(since: float) -> dict:
    """{clip_key: {"title", "links": {platform: url}, "first": ts}}"""
    from utils.post_refs import recent

    hours = (time.time() - since) / 3600
    buffer_links = None
    clips = {}
    for item in recent(hours):
        platform = PLATFORM_OF.get(item["platform"], item["platform"])
        if buffer_links is None and str(item.get("ref", "")).startswith("buffer post "):
            buffer_links = _buffer_links()
        url = resolve(platform, item.get("ref", ""), buffer_links or {})
        c = clips.setdefault(item["clip"], {"title": "", "links": {}, "first": item["at"]})
        if url:
            c["links"].setdefault(platform, url)
        if item.get("title") and (platform == "youtube_shorts" or not c["title"]):
            c["title"] = item["title"]
        c["first"] = min(c["first"], item["at"])
    for key, url, at in from_rumble(since) or ():
        c = clips.setdefault(key, {"title": "", "links": {}, "first": at})
        c["links"].setdefault("rumble", url)
        if not c["title"]:
            slug = url.rsplit("/", 1)[-1].split("-", 1)[-1].replace(".html", "")
            c["title"] = slug.replace("-", " ").capitalize()
    return {k: c for k, c in clips.items() if c["links"]}


def card(clip: dict) -> str:
    from utils.discord_safe import clean

    lines = [f"🎬 **{clean(clip['title'] or 'Clip')}**"]
    for p in ORDER:
        if p in clip["links"]:
            lines.append(f"{LABEL[p]}: <{clip['links'][p]}>")
    return "\n".join(lines)[:1900]


def _webhook() -> str:
    return (os.getenv("DISCORD_LINKS_WEBHOOK_URL", "") or os.getenv("DISCORD_JOB_WEBHOOK_URL", "")
            or os.getenv("DISCORD_WEBHOOK_URL", "")).strip()


def _send(text: str, message_id: str = "") -> str:
    hook = _webhook()
    if not hook:
        return ""
    body = json.dumps({"content": text, "username": "AutoBleep links",
                       "allowed_mentions": {"parse": []}}).encode("utf-8")
    url, method = (f"{hook}/messages/{message_id}", "PATCH") if message_id else (f"{hook}?wait=true", "POST")
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json", "User-Agent": "AutoBleep"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return str(json.loads(r.read().decode() or "{}").get("id") or message_id)


def main() -> int:
    from utils.config import load_config

    load_config("config.json")             # loads .env
    try:
        ledger = json.load(open(LEDGER, encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}
    first_run = not ledger
    since = time.time() - (FIRST_RUN_H if first_run else WINDOW_H) * 3600
    clips = collect(since)
    sent = 0
    for k, clip in sorted(clips.items(), key=lambda kv: kv[1]["first"]):
        old = ledger.get(k) or {}
        if old.get("links") == clip["links"]:
            continue
        merged = {**(old.get("links") or {}), **clip["links"]}
        clip["links"] = merged
        try:
            mid = _send(card(clip), old.get("message", ""))
        except Exception as exc:
            print(f"  discord: {str(exc)[:100]}")
            continue
        ledger[k] = {"title": clip["title"], "links": merged, "message": mid,
                     "first": clip["first"]}
        sent += 1
        time.sleep(1.2)                      # Discord rate limit
    # forget cards older than a week
    cutoff = time.time() - 7 * 86400
    ledger = {k: v for k, v in ledger.items() if v.get("first", 0) > cutoff}
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    json.dump(ledger, open(LEDGER, "w", encoding="utf-8"), indent=1)
    print(f"{time.strftime('%m-%d %H:%M')} {len(clips)} clip(s) seen, {sent} card(s) posted/updated")
    return 0


if __name__ == "__main__":
    if "--dry" in sys.argv:
        from utils.config import load_config

        load_config("config.json")
        for k, c in collect(time.time() - 48 * 3600).items():
            print(card(c)); print("---")
        sys.exit(0)
    sys.exit(main())
