"""Sign the BinScripts YouTube channel in (11-07): opens Google's consent
page in the browser; pick the account, then the BinScripts channel, then
Allow. Saves auto_uploader/youtube_binscripts_token.json."""
import os
import sys

UP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auto_uploader")
sys.path.insert(0, UP)
os.chdir(UP)
from utils.youtube_uploader import YouTubeUploader  # noqa: E402

token = os.path.join(UP, "youtube_binscripts_token.json")
svc = YouTubeUploader(os.path.join(UP, "client_secrets.json"), token).get_service()
me = svc.channels().list(part="snippet", mine=True).execute().get("items", [])
print("Signed in as:", [c["snippet"]["title"] for c in me])
