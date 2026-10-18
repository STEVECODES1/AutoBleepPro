"""Fix mojibake description lines on posted Shorts. Usage:
python tools/fix_short_description.py <video_id>   fix one video
python tools/fix_short_description.py --all         scan recent uploads, fix all affected
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "auto_uploader"))

from utils.youtube_uploader import YouTubeUploader  # noqa: E402

SHORTS = os.path.join(ROOT, "auto_uploader", "youtube_shorts_token.json")
SECRETS = os.path.join(ROOT, "auto_uploader", "client_secrets.json")
SCAN = 50


def _runs(text):
    """cp1252-mojibake emoji runs: F0 9F lead bytes misread."""
    out = []
    i = 0
    while i < len(text):
        chunk = text[i:i + 4]
        if (len(chunk) == 4 and ord(chunk[0]) == 0xF0
                and all(ord(c) >= 0x80 for c in chunk[1:])):
            out.append(chunk)
        i += 1
    return list(dict.fromkeys(out))


def _fix(text):
    for run in _runs(text):
        try:
            text = text.replace(run, run.encode("cp1252").decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return text


def _service():
    return YouTubeUploader(SECRETS, SHORTS).get_service()


def fix_one(service, video_id):
    current = service.videos().list(
        part="snippet", id=video_id).execute()["items"][0]["snippet"]
    fixed = _fix(current.get("description", ""))
    if fixed == current.get("description", ""):
        print(f"{video_id}: clean already - {current['title'][:60]}")
        return False
    service.videos().update(
        part="snippet",
        body={"id": video_id,
              "snippet": {"title": current["title"],
                          "description": fixed,
                          "categoryId": current.get("categoryId", "20"),
                          "tags": current.get("tags", [])}}).execute()
    print(f"{video_id}: FIXED - {current['title'][:60]}")
    return True


def fix_all(service):
    mine = service.channels().list(
        part="contentDetails", mine=True).execute()["items"][0]
    playlist = mine["contentDetails"]["relatedPlaylists"]["uploads"]
    fixed, seen, token = 0, 0, None
    while seen < SCAN:
        page = service.playlistItems().list(
            part="snippet", playlistId=playlist, maxResults=50,
            pageToken=token).execute()
        for item in page.get("items", []):
            seen += 1
            if fix_one(service, item["snippet"]["resourceId"]["videoId"]):
                fixed += 1
            if seen >= SCAN:
                break
        token = page.get("nextPageToken")
        if not token:
            break
    print(f"scanned {seen}, fixed {fixed}")


def main():
    service = _service()
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        fix_all(service)
    else:
        video = sys.argv[1] if len(sys.argv) > 1 else "yO_hKzgcD3k"
        fix_one(service, video)


if __name__ == "__main__":
    main()
