"""Sign the uploader in to the channel that gets stream RECAPS
(STACKSWOPO GAMES). Opens Google's sign-in in the browser: pick the
STACKSWOPO GAMES channel when it asks which account/channel.

    python setup_games_channel.py
"""
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.path.join(HERE, "youtube_games_token.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl",
          "https://www.googleapis.com/auth/youtube.readonly"]

flow = InstalledAppFlow.from_client_secrets_file(os.path.join(HERE, "client_secrets.json"), SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", open_browser=True)
me = build("youtube", "v3", credentials=creds, cache_discovery=False).channels().list(
    part="snippet", mine=True).execute()["items"][0]["snippet"]
print("Signed in as:", me["title"], me.get("customUrl", ""))
if "games" not in (me["title"] + me.get("customUrl", "")).lower():
    print("That is not STACKSWOPO GAMES - nothing saved. Run it again and pick that channel.")
    sys.exit(1)
with open(TOKEN, "w", encoding="utf-8") as f:
    f.write(creds.to_json())
print("Saved", TOKEN)
