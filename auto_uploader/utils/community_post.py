"""
utils/community_post.py - a post on wopovod's Posts tab for every new
full stream.

The owner asked for one each time (10-18, pointing at a "COME CLICK" post
with the video attached under it). YouTube has no API for posts, so this
goes through the signed-in Chrome on the CDP port - the same window the
Rumble uploads use. It only ever ADDS a post: its own tab, the text, the
video through YouTube's own picker, Post, tab closed.

One post per video (logs/community_posts.json), never two.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

WOPOVOD_POSTS = "https://www.youtube.com/channel/UCbS-_v6Y9AZmAAU3EpzLsvA/posts"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(_ROOT, "logs", "community_posts.json")
# The owner's own style ("COME CLICK"), a few ways, so the tab does not
# read like one line pasted every day.
LINES = (
    "NEW FULL STREAM \U0001F525 {title} - COME CLICK \U0001F447",
    "{title} \U0001F525 The full stream is up. COME CLICK \U0001F447",
    "COME CLICK \U0001F525 Full stream: {title}",
)
_LOCK = threading.Lock()


def video_id(url: str) -> str:
    """The 11-character id from a watch/short/live link, or the id itself."""
    url = (url or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    found = re.search(r"(?:v=|youtu\.be/|/shorts/|/live/)([A-Za-z0-9_-]{11})", url)
    return found.group(1) if found else ""


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def already_posted(vid: str, ledger: str = "") -> bool:
    return bool(vid) and vid in _load(ledger or LEDGER)


def _note(vid: str, entry: dict, ledger: str = "") -> None:
    path = ledger or LEDGER
    with _LOCK:
        data = _load(path)
        data[vid] = entry
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, ensure_ascii=False)
        os.replace(path + ".tmp", path)


def post_text(title: str, vid: str = "") -> str:
    """The post's line. The same one for a video on every try."""
    from utils import text_guard

    title = re.sub(r"\s+", " ", (title or "").strip()) or "Stackswopo"
    line = LINES[sum(map(ord, vid or title)) % len(LINES)]
    return text_guard.text(line.replace("{title}", title), "post")


def _picker_frame(page, wait_s: float = 20.0):
    """YouTube's "Add an existing YouTube video" is a Google Picker iframe."""
    end = time.time() + wait_s
    while time.time() < end:
        frame = next((f for f in page.frames
                      if "docs.google.com/picker" in (f.url or "")), None)
        if frame is not None:
            return frame
        page.wait_for_timeout(500)
    return None


def _attach(page, vid: str, title: str, say=print) -> bool:
    page.click("button[aria-label='Add an existing YouTube video']:visible",
               timeout=15_000)
    picker = _picker_frame(page)
    if picker is None:
        say("[Post] The video picker did not open.")
        return False
    # The channel's own uploads are listed newest first: the stream's name
    # finds it. A brand-new upload not listed yet is found by its link.
    option = picker.locator("[role=option]", has_text=title).first
    try:
        if not title:
            raise LookupError("no title")
        option.wait_for(timeout=15_000)
    except Exception:
        search = picker.locator("input[aria-label*='paste URL']").first
        search.wait_for(timeout=15_000)
        search.fill(f"https://www.youtube.com/watch?v={vid}")
        search.press("Enter")
        option = picker.locator("[role=option]").first
        option.wait_for(timeout=20_000)
    option.click()
    picker.get_by_role("button", name="Insert").click(timeout=15_000)
    page.wait_for_timeout(2500)
    shown = page.evaluate(
        "() => { const d = document.querySelector('ytd-backstage-post-dialog-renderer');"
        " return d ? d.innerText : ''; }") or ""
    if "Now playing" not in shown:
        say("[Post] The video did not attach to the post.")
        return False
    return True


def post(video_url: str, title: str, *, cdp_url: str = "http://localhost:9222",
         posts_url: str = WOPOVOD_POSTS, ledger: str = "", dry_run: bool = False,
         say=print) -> str:
    """Make the post. Returns its link, "dry run", "already" or "" (failed).
    Never raises."""
    vid = video_id(video_url)
    if not vid:
        say("[Post] No YouTube video id - no channel post.")
        return ""
    if already_posted(vid, ledger):
        say(f"[Post] {vid} already has its channel post.")
        return "already"
    text = post_text(title, vid)
    try:
        from playwright.sync_api import sync_playwright

        from utils.chrome_cdp import ensure_chrome

        ready, detail = ensure_chrome(cdp_url)
        if not ready:
            say(f"[Post] Chrome is not up ({detail}) - no channel post this time.")
            return ""
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url, timeout=45_000)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            try:
                return _make(page, vid, title, text, posts_url, ledger, dry_run, say)
            finally:
                try:
                    page.close()
                except Exception:
                    pass
    except Exception as exc:
        say(f"[Post] Could not make the channel post ({str(exc).splitlines()[0][:160]}).")
        return ""


def _post_of(page, vid):
    """The channel's post that carries this video, if there is one."""
    return page.locator(
        f"ytd-backstage-post-thread-renderer:has(a[href*='{vid}'])").first


def _link_of(thread, posts_url: str) -> str:
    try:
        href = thread.locator("a[href*='/post/']").first.get_attribute(
            "href", timeout=5_000) or ""
    except Exception:
        href = ""
    if not href:
        return posts_url
    return href if href.startswith("http") else f"https://www.youtube.com{href}"


def _make(page, vid, title, text, posts_url, ledger, dry_run, say) -> str:
    page.goto(posts_url, wait_until="domcontentloaded", timeout=60_000)
    try:
        page.wait_for_selector("#placeholder-area", timeout=30_000)
    except Exception:
        say("[Post] No post box on the channel page - Chrome is not signed "
            "in as the channel.")
        return ""
    page.wait_for_timeout(3_000)
    earlier = _post_of(page, vid)
    if earlier.count():
        # Up from an earlier try whose confirmation never came: never twice.
        link = _link_of(earlier, posts_url)
        _note(vid, {"at": time.time(), "post": link, "text": text}, ledger)
        say(f"[Post] Already on the channel: {link}")
        return link
    page.click("#placeholder-area")
    box = page.locator("#contenteditable-root:visible").first
    box.wait_for(timeout=15_000)
    box.click()
    page.keyboard.type(text, delay=12)
    if not _attach(page, vid, title, say):
        return ""
    written = box.inner_text(timeout=5_000).strip()
    if text[:20].lower() not in written.lower():
        say(f"[Post] The text did not stay in the post box ({written[:40]!r}).")
        return ""
    if dry_run:
        say(f"[Post] Dry run - ready to post: {text}")
        return "dry run"
    page.locator("ytd-backstage-post-dialog-renderer "
                 "button[aria-label='Post']:visible").first.click(timeout=15_000)
    thread = _post_of(page, vid)
    try:
        thread.wait_for(timeout=45_000)
    except Exception:
        page.reload(wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(8_000)
        thread = _post_of(page, vid)
        if not thread.count():
            say("[Post] Clicked Post, but the post is not on the channel.")
            return ""
    link = _link_of(thread, posts_url)
    _note(vid, {"at": time.time(), "post": link, "text": text}, ledger)
    say(f"[Post] Posted on the channel: {link}")
    return link


def processed(vid: str):
    """True once YouTube has finished processing the video, False while it
    is still working, None if it failed or is gone.

    10-18: the first post was clicked while the 3-hour stream was still
    processing (5 hours after upload) and YouTube published nothing - a
    post can only carry a finished video."""
    try:
        from utils.youtube_uploader import YouTubeUploader

        svc = YouTubeUploader(os.path.join(_ROOT, "client_secrets.json"),
                              os.path.join(_ROOT, "youtube_token.json")).get_service()
        items = svc.videos().list(part="processingDetails,status", id=vid).execute().get("items") or []
    except Exception:
        return False
    if not items:
        return None
    status = items[0].get("status", {}) or {}
    if status.get("uploadStatus") in ("failed", "rejected", "deleted"):
        return None
    details = items[0].get("processingDetails", {}) or {}
    if details.get("processingStatus") in ("failed", "terminated"):
        return None
    return (status.get("uploadStatus") == "processed"
            or details.get("processingStatus") == "succeeded")


def wait_until_processed(vid: str, max_hours: float = 12, every_s: float = 600,
                         say=print) -> bool:
    """Wait for YouTube to finish the video (one 1-unit read per check)."""
    end = time.time() + max_hours * 3600
    while True:
        state = processed(vid)
        if state:
            return True
        if state is None:
            say(f"[Post] {vid} failed processing or is gone - no channel post.")
            return False
        if time.time() >= end:
            say(f"[Post] {vid} still processing after {max_hours:g}h - no channel post.")
            return False
        time.sleep(every_s)


TOOL = os.path.join(os.path.dirname(_ROOT), "tools", "community_post.py")
LOG = os.path.join(_ROOT, "logs", "community_post.log")


def post_later(cfg, video_url: str, title: str, wait_s: float = 90,
               tries: int = 3, say=print) -> bool:
    """After a new full stream lands on wopovod: post it from a process
    of its own (tools/community_post.py), so neither the upload path nor
    a restart of the uploader can hold it up or lose it.

    Off with youtube.channel_posts = false in config.json. Never from the
    test suite - a test's made-up stream must not reach the real channel."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    vid = video_id(video_url)
    if not vid or already_posted(vid):
        return False
    youtube = getattr(cfg, "youtube", None)
    if youtube is not None and getattr(youtube, "channel_posts", True) is False:
        return False
    rumble = getattr(cfg, "rumble", None)
    cdp_url = getattr(rumble, "cdp_url", "") or "http://localhost:9222"
    import subprocess
    import sys

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as log:
        subprocess.Popen(
            [sys.executable, "-X", "utf8", TOOL, video_url, title or "",
             "--wait", str(wait_s), "--tries", str(tries), "--cdp", cdp_url],
            stdout=log, stderr=subprocess.STDOUT, cwd=os.path.dirname(_ROOT),
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    say("[Post] A post for this stream goes on wopovod's Posts tab in a "
        "couple of minutes.")
    return True
