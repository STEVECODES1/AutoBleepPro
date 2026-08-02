"""
Checks the channel's existing uploads for a video already covering a given
date, so backfilling an old VOD folder doesn't re-upload something that
was already manually uploaded in the past.

Fetches the channel's upload list ONCE per run (1 quota unit per page via
playlistItems, vs. 100 units per call for search.list) and matches
locally - cheap even against a channel with hundreds of videos, and safe
to call once per --batch run rather than once per file.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ExistingVideo:
    title: str
    video_id: str
    url: str


def fetch_existing_videos(youtube_service) -> list:
    """Returns every video already on the authenticated channel."""
    channels_response = youtube_service.channels().list(part="contentDetails", mine=True).execute()
    items = channels_response.get("items", [])
    if not items:
        return []
    uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    videos = []
    page_token = None
    while True:
        response = youtube_service.playlistItems().list(
            part="snippet", playlistId=uploads_playlist_id, maxResults=50, pageToken=page_token
        ).execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            video_id = snippet["resourceId"]["videoId"]
            videos.append(ExistingVideo(
                title=snippet["title"], video_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}"
            ))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return videos


def _date_variants(dt: datetime) -> list:
    """Every date representation this channel's titles have actually used
    (old videos: zero-padded "05/08/26"; new ones via this tool:
    non-padded "5/8/26"), so matching works against both eras of uploads."""
    yy = dt.strftime("%y")
    return [
        f"{dt.month}/{dt.day}/{yy}",
        f"{dt.month:02d}/{dt.day:02d}/{yy}",
        f"{dt.month}-{dt.day}-{yy}",
        f"{dt.month:02d}-{dt.day:02d}-{yy}",
        dt.strftime("%Y-%m-%d"),
    ]


def find_existing_video(existing_videos: list, target_date: datetime) -> Optional[ExistingVideo]:
    """Returns the first existing video whose title contains `target_date`
    in any known format, or None. Matching by date rather than by title
    text on purpose - this channel's title style has changed over time
    (old: '*Title* - 05/08/26 - Stackswopo FULL YT Stream', new: '"Title"
    5/8/26 Stackswopo Stream'), but every era has included the date.
    """
    variants = _date_variants(target_date)
    for video in existing_videos:
        if any(variant in video.title for variant in variants):
            return video
    return None
