"""Read-only: which videos in D:\\videos stizz are NOT on the Rumble channel yet.

Matches each file's title words (and its date) against the slugs of every
video on the channel. Writes logs/videos_stizz_check.txt. Uploads and
deletes nothing.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from utils.channel_vods import channel_video_urls

FOLDER = r"D:\videos stizz"
OUT = os.path.join(HERE, "logs", "videos_stizz_check.txt")
VIDEO = (".mp4", ".ts", ".mkv", ".mov", ".webm", ".flv")
SKIP = {"stackswopo", "stream", "kick", "full", "yt", "live", "the", "a", "to", "of", "and", "is"}

cfg = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))
channel = cfg["rumble"].get("channel_url") or os.path.dirname(cfg["rumble"].get("rss_url", ""))
def account_titles(max_pages=80):
    """Every video title on the logged-in Rumble account (all its channels),
    read from rumble.com/account/content in the Rumble Chrome. Read-only."""
    from playwright.sync_api import sync_playwright

    titles = []
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222", timeout=90000)
        page = browser.contexts[0].new_page()
        try:
            for pg in range(1, max_pages + 1):
                page.goto(f"https://rumble.com/account/content?type=all&pg={pg}", timeout=90000)
                page.wait_for_timeout(3000)
                rows = page.evaluate("""() => Array.from(document.querySelectorAll('article.video-post'))
                    .map(a => ((a.querySelector('h2.video-title a') || {}).innerText || '').trim())""")
                if not rows:
                    break
                titles += rows
        finally:
            page.close()
    return titles


urls = account_titles()
slugs = [" " + re.sub(r"[^a-z0-9]+", " ", t.lower()).strip() + " " for t in urls]


def parts(name):
    stem = os.path.splitext(name)[0]
    date = ""
    m = re.search(r"(\d{1,2})\D(\d{1,2})\D(\d{2})(?!\d)", stem)          # 3/14/26, 5-14-26
    if m:
        date = f"{int(m.group(1))}{int(m.group(2)):02d}{m.group(3)}"
    m2 = re.search(r"_(20\d{2})(\d{2})(\d{2})_", stem)                   # _20250918_
    if m2:
        date = f"{int(m2.group(2))}{m2.group(3)}{m2.group(1)[2:]}"
    words = [w for w in re.findall(r"[a-z0-9]+", stem.lower().replace("@stackswopo_", ""))
             if w not in SKIP and not w.isdigit() and len(w) > 1]
    return words, date


def on_channel(name):
    words, date = parts(name)
    for s in slugs:
        hits = sum(1 for w in words if f" {w} " in f" {s} ")
        if words and hits >= min(3, len(words)):
            return True
        if date and date in s.replace(" ", "") and hits >= 1:
            return True
    return False


files = sorted(f for f in os.listdir(FOLDER)
               if f.lower().endswith(VIDEO) and os.path.isfile(os.path.join(FOLDER, f)))
missing = [f for f in files if not on_channel(f)]
done = [f for f in files if f not in missing]
gb = lambda fs: sum(os.path.getsize(os.path.join(FOLDER, f)) for f in fs) / 1e9
with open(OUT, "w", encoding="utf-8") as o:
    o.write(f"Rumble channel has {len(urls)} videos.\n{len(files)} videos in the folder: "
            f"{len(done)} look like they are on Rumble ({gb(done):.0f} GB), "
            f"{len(missing)} do NOT ({gb(missing):.0f} GB)\n\nNOT ON RUMBLE:\n")
    o.write("\n".join(missing) + "\n")
print(open(OUT, encoding="utf-8").read()[:2500])
