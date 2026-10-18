"""Read-only: recent uploads and views on each YouTube channel we post to."""
import json, os, re, statistics, sys
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

HERE = os.path.dirname(os.path.abspath(__file__))
TOKENS = ["youtube_token.json", "youtube_shorts_token.json", "youtube_games_token.json",
          "youtube_compilation_token.json"]


def secs(d):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d)
    dd, h, mi, s = (int(x or 0) for x in m.groups())
    return dd * 86400 + h * 3600 + mi * 60 + s


report = {}
for tok in TOKENS:
    path = os.path.join(HERE, tok)
    if not os.path.exists(path):
        continue
    try:
        yt = build("youtube", "v3", credentials=Credentials.from_authorized_user_file(path),
                   cache_discovery=False)
        ch = yt.channels().list(part="snippet,contentDetails,statistics", mine=True).execute()["items"][0]
        up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
        ids = [i["contentDetails"]["videoId"] for i in yt.playlistItems().list(
            part="contentDetails", playlistId=up, maxResults=50).execute()["items"]]
        vids = yt.videos().list(part="snippet,contentDetails,statistics,status",
                                id=",".join(ids)).execute()["items"]
        rows = [{"t": v["snippet"]["title"][:60], "d": v["snippet"]["publishedAt"][:10],
                 "s": secs(v["contentDetails"]["duration"]), "p": v["status"]["privacyStatus"],
                 "v": int(v["statistics"].get("viewCount", 0))} for v in vids]
        report[ch["snippet"]["title"]] = {
            "subs": ch["statistics"].get("subscriberCount"), "total_views": ch["statistics"].get("viewCount"),
            "videos": ch["statistics"].get("videoCount"), "recent": rows}
    except Exception as exc:
        report[tok] = {"error": str(exc)[:200]}

json.dump(report, open(os.path.join(HERE, "logs", "traffic_check.json"), "w", encoding="utf-8"), indent=1)
for name, r in report.items():
    if "error" in r:
        print(name, "ERROR", r["error"]); continue
    pub = [x for x in r["recent"] if x["p"] == "public"]
    shorts = [x["v"] for x in pub if x["s"] <= 180]
    longs = [x["v"] for x in pub if x["s"] > 180]
    med = lambda a: int(statistics.median(a)) if a else 0
    print(f"== {name}: {r['subs']} subs, {r['total_views']} views, {r['videos']} videos")
    print(f"   last 50: {len(pub)} public | shorts {len(shorts)} median {med(shorts)} views"
          f" | long {len(longs)} median {med(longs)} views")
    for x in sorted(pub, key=lambda x: -x["v"])[:4]:
        print(f"   top: {x['v']:>6} {x['d']} {x['s']//60}m {x['t']}")
    print(f"   newest: " + "; ".join(f"{x['d']} {x['v']}v" for x in r["recent"][:6]))
