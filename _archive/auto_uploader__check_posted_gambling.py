"""Read-only: which posted YouTube videos show a casino in their frames.
YouTube keeps 3 stills per video (hq1/hq2/hq3); each goes to the local
vision model. Changes nothing. Writes logs/posted_gambling.txt."""
import base64, os, sys, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from autoreel.gambling_check import frame_has_gambling

out = open(os.path.join(HERE, "logs", "posted_gambling.txt"), "w", encoding="utf-8")
for tok in ("youtube_shorts_token.json", "youtube_token.json", "youtube_games_token.json",
            "youtube_compilation_token.json"):
    yt = build("youtube", "v3", credentials=Credentials.from_authorized_user_file(
        os.path.join(HERE, tok)), cache_discovery=False)
    ch = yt.channels().list(part="snippet,contentDetails", mine=True).execute()["items"][0]
    up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
    items = yt.playlistItems().list(part="snippet,contentDetails", playlistId=up,
                                    maxResults=50).execute()["items"]
    flagged = 0
    for it in items:
        vid, title = it["contentDetails"]["videoId"], it["snippet"]["title"]
        for still in ("hq1", "hq2", "hq3"):
            try:
                img = urllib.request.urlopen(f"https://i.ytimg.com/vi/{vid}/{still}.jpg", timeout=20).read()
            except Exception:
                continue
            v = frame_has_gambling(base64.b64encode(img).decode())
            if v and v[0]:
                flagged += 1
                out.write(f"{ch['snippet']['title']} | https://youtu.be/{vid} | {title[:70]} | {v[1]}\n")
                out.flush()
                break
    out.write(f"== {ch['snippet']['title']}: {flagged} of {len(items)} recent videos show gambling\n")
    out.flush()
out.write("DONE\n")
