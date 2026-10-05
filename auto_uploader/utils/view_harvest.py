"""Real view counts for every posted clip, written into clip memory.

The learning loop (autoreel/memory.learn -> the picker's prompt) has been
starved: every clip in clip_memory.json had empty "posted" and "views",
because the direct Reel publishers return True/False rather than a link,
so nothing ever recorded WHERE a clip went. Without numbers the picker
can never learn which kind of clip this audience actually shares.

This works backwards from what is already on disk and online:
  * clip_jobs.json says which clip file was posted where, when, and with
    which caption;
  * Instagram's Graph API lists the account's posts with caption, time,
    link and view count;
  * Facebook's Graph API does the same for the Page's videos;
  * Rumble/YouTube links are in uploaded_hashes.json, TikTok links in
    the account's public profile listing - views via yt-dlp.
A job is matched to a post by its caption (first 40 characters), or by
time (within 20 minutes) when the caption was changed on the way out.

Read-only everywhere except clip_memory.json. Never raises.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

GRAPH = "https://graph.facebook.com/v19.0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = {"instagram": "instagram", "postproxy_instagram": "instagram",
        "facebook": "facebook", "postproxy_facebook": "facebook",
        "tiktok": "tiktok", "postproxy_tiktok": "tiktok",
        "youtube_shorts": "youtube", "postproxy_youtube": "youtube",
        "postproxy_x": "x"}


def _norm(text: str) -> str:
    text = re.sub(r"[^a-z0-9 ]+", "", (text or "").lower())
    return " ".join(text.split())[:40]


def _ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00").replace("+0000", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0.0


def _get(url: str, params: dict) -> Optional[dict]:
    try:
        import requests
        r = requests.get(url, params=params, timeout=30)
        return r.json() if r.ok else None
    except Exception:
        return None


_DEAD_METRICS: set = set()   # metrics the API refused once - not asked again


def _first_metric(media_id: str, token: str, endpoint: str, metrics: tuple) -> Optional[int]:
    for metric in metrics:
        if (endpoint, metric) in _DEAD_METRICS:
            continue
        data = _get(f"{GRAPH}/{media_id}/{endpoint}", {"metric": metric, "access_token": token})
        if data is None:
            _DEAD_METRICS.add((endpoint, metric))
            continue
        for row in (data or {}).get("data") or []:
            values = row.get("values") or [{}]
            value = values[0].get("value") if values else None
            if isinstance(value, (int, float)):
                return int(value)
    return None


def instagram_posts(max_age_days: int = 21) -> List[dict]:
    token, acct = os.environ.get("IG_PAGE_TOKEN", ""), os.environ.get("IG_BUSINESS_ACCOUNT_ID", "")
    if not (token and acct):
        return []
    out, url = [], f"{GRAPH}/{acct}/media"
    params = {"fields": "id,caption,timestamp,permalink,media_type,media_product_type,like_count,comments_count",
              "limit": 50, "access_token": token}
    cutoff = time.time() - max_age_days * 86400
    for _ in range(6):
        page = _get(url, params)
        if not page:
            break
        for m in page.get("data") or []:
            ts = _ts(m.get("timestamp", ""))
            if ts < cutoff:
                return out
            if m.get("media_type") != "VIDEO":
                continue
            views = _first_metric(m["id"], token, "insights", ("views", "plays", "reach"))
            out.append({"caption": m.get("caption", ""), "time": ts, "url": m.get("permalink", ""),
                        "views": views, "likes": m.get("like_count"), "comments": m.get("comments_count")})
        url = ((page.get("paging") or {}).get("next")) or ""
        params = {}
        if not url:
            break
    return out


def facebook_posts(max_age_days: int = 45) -> List[dict]:
    token, page_id = os.environ.get("FB_PAGE_TOKEN", ""), os.environ.get("FB_PAGE_ID", "")
    if not (token and page_id):
        return []
    seen, out = set(), []
    cutoff = time.time() - max_age_days * 86400
    for edge in ("videos", "video_reels"):
        page = _get(f"{GRAPH}/{page_id}/{edge}",
                    {"fields": "id,description,created_time,permalink_url", "limit": 100,
                     "access_token": token})
        for v in (page or {}).get("data") or []:
            if v.get("id") in seen:
                continue
            seen.add(v.get("id"))
            ts = _ts(v.get("created_time", ""))
            if ts < cutoff:
                continue
            views = _first_metric(v["id"], token, "video_insights",
                                  ("total_video_views", "blue_reels_play_count", "fb_reels_total_plays"))
            link = v.get("permalink_url", "")
            if link.startswith("/"):
                link = "https://www.facebook.com" + link
            out.append({"caption": v.get("description", ""), "time": ts, "url": link, "views": views})
    return out


def tiktok_posts(handle: str = "wopocl1ps", limit: int = 40) -> List[dict]:
    """Public profile listing via yt-dlp. Empty on any failure."""
    import subprocess
    try:
        from utils.clip_finder import ytdlp_command
        cmd = ytdlp_command()
    except Exception:
        cmd = ["yt-dlp"]
    data = {}
    for attempt in range(3):                # TikTok's listing is flaky - try again
        try:
            done = subprocess.run(cmd + ["-J", "--playlist-end", str(limit), "--no-warnings",
                                         f"https://www.tiktok.com/@{handle}"],
                                  capture_output=True, timeout=300)
            data = json.loads(done.stdout.decode("utf-8", "replace") or "{}") or {}
        except Exception:
            data = {}
        if isinstance(data, dict) and data.get("entries"):
            break
        time.sleep(10)
    if not isinstance(data, dict):
        return []
    out = []
    for e in data.get("entries") or []:
        if not isinstance(e, dict):         # removed / private videos
            continue
        out.append({"caption": e.get("description") or e.get("title") or "",
                    "time": float(e.get("timestamp") or 0), "url": e.get("webpage_url") or e.get("url") or "",
                    "views": e.get("view_count")})
    return out


def youtube_posts(limit: int = 60) -> List[dict]:
    """Shorts on the clips channel and the VOD channel, via yt-dlp. Captions only, no times."""
    import subprocess
    try:
        cfg = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
    except (OSError, ValueError):
        cfg = {}
    handles = [h for h in {str((cfg.get("youtube_shorts") or {}).get("channel") or ""),
                           str((cfg.get("youtube") or {}).get("channel") or "")} if h.startswith("@")]
    try:
        from utils.clip_finder import ytdlp_command
        cmd = ytdlp_command()
    except Exception:
        cmd = ["yt-dlp"]
    out = []
    for handle in handles:
        try:
            done = subprocess.run(cmd + ["-J", "--flat-playlist", "--playlist-end", str(limit),
                                         "--no-warnings", f"https://www.youtube.com/{handle}/shorts"],
                                  capture_output=True, timeout=180)
            data = json.loads(done.stdout.decode("utf-8", "replace") or "{}") or {}
        except Exception:
            continue
        for e in (data.get("entries") or []) if isinstance(data, dict) else []:
            if isinstance(e, dict):
                out.append({"caption": e.get("title") or "", "time": 0.0,
                            "url": e.get("url") or f"https://www.youtube.com/shorts/{e.get('id')}",
                            "views": e.get("view_count")})
    return out


def _done_jobs() -> List[dict]:
    try:
        d = json.load(open(os.path.join(ROOT, "clip_jobs.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = d.get("jobs", d) if isinstance(d, dict) else d
    items = list(jobs.values()) if isinstance(jobs, dict) else list(jobs)
    return [j for j in items if isinstance(j, dict) and j.get("state") == "done"]


def _match(job: dict, posts: List[dict], used: set) -> Optional[dict]:
    want = _norm((job.get("caption") or "").split("\n")[0])
    if want:
        for p in posts:
            if id(p) not in used and _norm(p["caption"].split("\n")[0]) == want:
                return p
    t = float(job.get("updated_at") or 0)
    best, gap = None, 20 * 60
    for p in posts:
        if id(p) in used or not p["time"]:
            continue
        if abs(p["time"] - t) < gap:
            best, gap = p, abs(p["time"] - t)
    return best


def harvest_all(say=print, ytdlp_limit: int = 40) -> Dict[str, int]:
    """Fill clip_memory.json with links and view counts. Returns counts per platform."""
    from autoreel.memory import Ledger, ledger_path, views_for

    ledger = Ledger(ledger_path())
    found: Dict[str, int] = {}
    fetchers = {"instagram": instagram_posts, "facebook": facebook_posts,
                "tiktok": tiktok_posts, "youtube": youtube_posts}
    jobs = _done_jobs()
    lists: Dict[str, List[dict]] = {}
    for platform, fetch in fetchers.items():
        try:
            lists[platform] = fetch() or []
        except Exception as exc:
            say(f"[Views] {platform}: could not list posts ({exc})")
            lists[platform] = []
    used: set = set()
    for job in jobs:
        rec = ledger.by_path(job.get("clip_path", ""))
        if rec is None:
            continue
        known = BASE.get(job.get("platform"))
        # Old routes (upload_post, postplanify, zernio) do not say which
        # platform they reached: try every list, by caption only.
        targets = [known] if known in lists else list(lists)
        for platform in targets:
            posts = lists.get(platform) or []
            post = (_match(job, posts, used) if known
                    else next((p for p in posts if id(p) not in used and
                               _norm(p["caption"].split("\n")[0]) ==
                               _norm((job.get("caption") or "").split("\n")[0])
                               and _norm(p["caption"])), None))
            if not post:
                continue
            used.add(id(post))
            if post.get("url"):
                ledger.note_post(rec.clip_id, platform, post["url"])
            if isinstance(post.get("views"), int):
                ledger.note_views(rec.clip_id, platform, post["views"])
                found[platform] = found.get(platform, 0) + 1
                say(f"  {platform:<10} {post['views']:>8,}  {rec.hook[:60]}")
            break

    # Posts older than the job queue remembers: most captions start with
    # the clip's own title, so match the rest straight to the memory.
    by_hook = {}
    for rec in ledger.records():
        key = _norm(rec.hook)
        if len(key) >= 15:
            by_hook.setdefault(key, rec)
    for platform, posts in lists.items():
        for post in posts:
            if id(post) in used:
                continue
            cap = _norm(post["caption"])
            rec = by_hook.get(cap[:40]) or next(
                (r for k, r in by_hook.items() if cap.startswith(k)), None)
            if rec is None or platform in rec.views:
                continue
            used.add(id(post))
            if post.get("url"):
                ledger.note_post(rec.clip_id, platform, post["url"])
            if isinstance(post.get("views"), int):
                ledger.note_views(rec.clip_id, platform, post["views"])
                found[platform] = found.get(platform, 0) + 1
                say(f"  {platform:<10} {post['views']:>8,}  {rec.hook[:60]}")

    # Rumble / YouTube links the uploader already wrote down.
    try:
        hashes = json.load(open(os.path.join(ROOT, "uploaded_hashes.json"), encoding="utf-8"))
    except (OSError, ValueError):
        hashes = {}
    asked = 0
    for entry in hashes.values():
        if not isinstance(entry, dict):
            continue
        rec = ledger.by_path(entry.get("filename", ""))
        if rec is None:
            continue
        for platform, url in (entry.get("results") or {}).items():
            if not str(url).startswith("http"):
                continue
            ledger.note_post(rec.clip_id, platform, url)
            if platform == "rumble":
                # Rumble answers yt-dlp with 403 (Cloudflare) - the link is
                # still recorded, the count cannot be read this way.
                continue
            fresh = time.time() - (rec.checked or 0) < 12 * 3600 and platform in rec.views
            if fresh or asked >= ytdlp_limit:
                continue
            asked += 1
            count = views_for(url)
            if isinstance(count, int):
                ledger.note_views(rec.clip_id, platform, count)
                found[platform] = found.get(platform, 0) + 1
                say(f"  {platform:<10} {count:>8,}  {rec.hook[:60]}")
    ledger.save()
    return found
