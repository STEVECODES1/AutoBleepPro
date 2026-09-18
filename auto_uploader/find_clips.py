import subprocess
import os
import json
import shutil
from pathlib import Path

# ============================================================
# AUTO FIND CLIPS
# TikTok + X + Facebook
# Searches Stackswopo / Monkey / GTA RP
# Sorts by highest traffic/views
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

# Firefox/browser cookies:
# Change this to your exported cookies.txt if needed.
COOKIES = BASE_DIR / "tiktok_cookies.txt"

# Temporary download location
DOWNLOAD_DIR = BASE_DIR / "downloaded_clips"

# AutoBleep uploader watch folder
WATCH_FOLDER = BASE_DIR / "watch_folder"

# yt-dlp archive
ARCHIVE = DOWNLOAD_DIR / "archive.txt"

# Maximum number of clips to download per run
MAX_CLIPS = 15

# No 50K minimum.
# Set to 0 so high-traffic clips are ranked rather than filtered.
MIN_VIEWS = 0

# Search terms
SEARCH_TERMS = [
    "stackswopo",
    "stackswopo monkey",
    "stackswopo monkey app",
    "stackswopo gta",
    "stackswopo gta rp",
    "stackswopo gta roleplay",
    "gta rp",
]

# Known TikTok accounts
TIKTOK_SOURCES = [
    "https://www.tiktok.com/@stackswopos",
    "https://www.tiktok.com/@imstackswopo",
    "https://www.tiktok.com/@clippedstackswopo",
]

# ============================================================
# SETUP
# ============================================================

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
WATCH_FOLDER.mkdir(parents=True, exist_ok=True)

print("=" * 65)
print("AUTO FIND CLIPS")
print("TikTok + X + Facebook")
print("Highest-traffic clips first")
print("=" * 65)

if not COOKIES.exists():
    print()
    print("[WARNING] Cookie file not found:")
    print(f"         {COOKIES}")
    print()
    print("The search may only work on pages that are publicly accessible.")
    print()

# ============================================================
# HELPERS
# ============================================================

def run_command(args):
    print()
    print("→", " ".join(str(x) for x in args))

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if result.stdout:
        print(result.stdout)

    if result.stderr:
        print(result.stderr)

    return result


def get_cookie_args():
    if COOKIES.exists():
        return ["--cookies", str(COOKIES)]
    return []


def extract_view_count(data):
    """
    Try several yt-dlp fields because different platforms
    expose traffic differently.
    """

    possible = [
        data.get("view_count"),
        data.get("views"),
        data.get("play_count"),
        data.get("viewCount"),
    ]

    for value in possible:
        try:
            if value is not None:
                return int(value)
        except (ValueError, TypeError):
            pass

    return 0


def add_video(results, data, source, search_term=None):
    video_id = data.get("id")

    if not video_id:
        return

    views = extract_view_count(data)

    if views < MIN_VIEWS:
        return

    url = (
        data.get("webpage_url")
        or data.get("original_url")
        or data.get("url")
    )

    if not url:
        return

    results.append({
        "id": str(video_id),
        "url": url,
        "title": data.get("title") or "Untitled",
        "uploader": data.get("uploader") or data.get("channel") or "Unknown",
        "views": views,
        "source": source,
        "search": search_term or "",
    })


# ============================================================
# FIND VIDEOS
# ============================================================

results = []

# ------------------------------------------------------------
# 1. TikTok accounts
# ------------------------------------------------------------

for source_url in TIKTOK_SOURCES:

    print()
    print(f"[TikTok] Account: {source_url}")

    args = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end",
        "50",
    ]

    args += get_cookie_args()
    args.append(source_url)

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if result.returncode != 0:
        print("[TikTok] Could not read account.")
        continue

    try:
        data = json.loads(result.stdout)

        entries = data.get("entries") or []

        for entry in entries:

            if not entry:
                continue

            # Flat playlist sometimes doesn't contain views.
            # Fetch metadata for individual videos later.
            video_url = (
                entry.get("webpage_url")
                or entry.get("url")
            )

            if not video_url:
                continue

            results.append({
                "id": str(entry.get("id", "")),
                "url": video_url,
                "title": entry.get("title") or "Untitled",
                "uploader": entry.get("uploader") or "Unknown",
                "views": extract_view_count(entry),
                "source": "TikTok",
                "search": source_url,
            })

    except Exception as e:
        print(f"[TikTok] Parse error: {e}")


# ------------------------------------------------------------
# 2. Search engines through yt-dlp
# ------------------------------------------------------------

for term in SEARCH_TERMS:

    print()
    print(f"[Search] {term}")

    # ytsearch returns search results.
    search_url = f"ytsearch50:{term}"

    args = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end",
        "50",
    ]

    args += get_cookie_args()
    args.append(search_url)

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if result.returncode != 0:
        print("[Search] No results.")
        continue

    try:
        data = json.loads(result.stdout)

        entries = data.get("entries") or []

        for entry in entries:

            if not entry:
                continue

            url = (
                entry.get("webpage_url")
                or entry.get("original_url")
                or entry.get("url")
            )

            if not url:
                continue

            source = "Search"

            if "tiktok.com" in url:
                source = "TikTok"
            elif "x.com" in url or "twitter.com" in url:
                source = "X"
            elif "facebook.com" in url:
                source = "Facebook"

            add_video(
                results,
                {
                    **entry,
                    "webpage_url": url
                },
                source,
                term
            )

    except Exception as e:
        print(f"[Search] Parse error: {e}")


# ============================================================
# REMOVE DUPLICATES
# ============================================================

unique = {}

for video in results:

    key = video["id"] or video["url"]

    if key not in unique:
        unique[key] = video
    else:
        # Keep whichever entry has the most views
        if video["views"] > unique[key]["views"]:
            unique[key] = video

results = list(unique.values())


# ============================================================
# SORT BY TRAFFIC
# ============================================================

results.sort(
    key=lambda x: x.get("views", 0),
    reverse=True
)

print()
print("=" * 65)
print(f"DISCOVERED: {len(results)} unique videos")
print("=" * 65)

for index, video in enumerate(results[:MAX_CLIPS], start=1):

    print(
        f"{index:02d}. "
        f"{video['views']:,} views | "
        f"{video['source']} | "
        f"{video['title'][:70]}"
    )


# ============================================================
# DOWNLOAD TOP RESULTS
# ============================================================

selected = results[:MAX_CLIPS]

if not selected:
    print()
    print("Nothing was found.")
    raise SystemExit(0)

print()
print("=" * 65)
print(f"DOWNLOADING TOP {len(selected)} CLIPS")
print("=" * 65)

for index, video in enumerate(selected, start=1):

    print()
    print(
        f"[{index}/{len(selected)}] "
        f"{video['views']:,} views"
    )
    print(f"Title: {video['title']}")
    print(f"Source: {video['source']}")
    print(f"URL: {video['url']}")

    output_template = (
        str(DOWNLOAD_DIR)
        + "/%(uploader)s - %(title).80s [%(id)s].%(ext)s"
    )

    args = [
        "yt-dlp",

        "--no-overwrites",

        "--download-archive",
        str(ARCHIVE),

        "-o",
        output_template,

        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36",

        "--referer",
        "https://www.tiktok.com/",
    ]

    args += get_cookie_args()

    args.append(video["url"])

    result = run_command(args)

    if result.returncode != 0:
        print("[DOWNLOAD FAILED]")
        continue


# ============================================================
# MOVE FINISHED DOWNLOADS INTO WATCH FOLDER
# ============================================================

print()
print("=" * 65)
print("MOVING FINISHED CLIPS")
print("=" * 65)

video_extensions = {
    ".mp4",
    ".mkv",
    ".webm",
    ".mov",
    ".avi",
}

moved = 0

for file in DOWNLOAD_DIR.iterdir():

    if not file.is_file():
        continue

    if file.name == "archive.txt":
        continue

    if file.suffix.lower() not in video_extensions:
        continue

    destination = WATCH_FOLDER / file.name

    # Avoid overwriting an existing file
    if destination.exists():
        stem = destination.stem
        suffix = destination.suffix

        counter = 1

        while destination.exists():
            destination = (
                WATCH_FOLDER
                / f"{stem}_{counter}{suffix}"
            )

            counter += 1

    print(f"[MOVE] {file.name}")
    print(f"       → {destination}")

    try:
        shutil.move(str(file), str(destination))
        moved += 1
    except Exception as e:
        print(f"[MOVE ERROR] {e}")


# ============================================================
# DONE
# ============================================================

print()
print("=" * 65)
print("DONE")
print("=" * 65)

print(f"Found:      {len(results)}")
print(f"Selected:   {len(selected)}")
print(f"Moved:      {moved}")

print()
print("Finished clips are now in:")
print(WATCH_FOLDER)

print()
print("AutoBleep's uploader can now process them.")
print()