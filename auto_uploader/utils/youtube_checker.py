"""
Checks the channel's existing uploads for a video already covering a given
date, so backfilling an old VOD folder doesn't re-upload something that
was already manually uploaded in the past.

Fetches the channel's upload list ONCE per run (1 quota unit per page via
playlistItems, vs. 100 units per call for search.list) and matches
locally - cheap even against a channel with hundreds of videos, and safe
to call once per --batch run rather than once per file.
"""

import calendar
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ExistingVideo:
    title: str
    video_id: str
    url: str


def fetch_existing_videos(youtube_service) -> list:
    """Returns every video already on the authenticated channel that is
    really there - see is_really_up."""
    channels_response = youtube_service.channels().list(part="contentDetails", mine=True).execute()
    items = channels_response.get("items", [])
    if not items:
        return []
    uploads_playlist_id = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    videos = []
    published = {}
    page_token = None
    while True:
        response = youtube_service.playlistItems().list(
            part="snippet", playlistId=uploads_playlist_id, maxResults=50, pageToken=page_token
        ).execute()
        for item in response.get("items", []):
            snippet = item["snippet"]
            video_id = snippet["resourceId"]["videoId"]
            published[video_id] = snippet.get("publishedAt", "")
            videos.append(ExistingVideo(
                title=snippet["title"], video_id=video_id, url=f"https://www.youtube.com/watch?v={video_id}"
            ))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    try:
        states = _states(youtube_service, [v.video_id for v in videos])
    except Exception as exc:
        # The old behaviour, not a failure: better a skipped re-upload
        # than a run that cannot start.
        print(f"[YouTube] Could not read upload states ({exc}) - counting "
              f"every video on the channel as uploaded.")
        return videos

    kept, dropped = [], []
    for video in videos:
        upload, processing = states.get(video.video_id, ("", ""))
        if is_really_up(upload, processing, published.get(video.video_id, "")):
            kept.append(video)
        else:
            dropped.append(video)
    if dropped:
        print(f"[YouTube] Not counting {len(dropped)} upload(s) that never "
              f"finished (failed, rejected or stuck) - e.g. "
              f"{dropped[0].title!r}. Their streams are uploaded again.")
    return kept


def _states(youtube_service, video_ids: list) -> dict:
    """{video_id: (uploadStatus, processingStatus)}, 50 ids a call - one
    quota unit each."""
    states = {}
    for start in range(0, len(video_ids), 50):
        chunk = video_ids[start:start + 50]
        response = youtube_service.videos().list(
            part="status,processingDetails", id=",".join(chunk),
            maxResults=50).execute()
        for item in response.get("items", []):
            states[item["id"]] = (
                (item.get("status") or {}).get("uploadStatus", ""),
                (item.get("processingDetails") or {}).get("processingStatus", ""))
    return states


# An upload YouTube accepted but has not processed in this long is not
# coming - Studio shows it as "Processing will begin shortly" for days.
_STUCK_AFTER_S = 24 * 3600


def is_really_up(upload_status: str, processing_status: str,
                 published_at: str = "", now: Optional[float] = None) -> bool:
    """Whether a video in the uploads playlist actually counts as uploaded.

    The playlist lists EVERY upload attempt: failed, rejected, and ones
    stuck before processing. It was read as "this stream is on YouTube",
    so a stream whose upload had stalled - "halfa mill" 9/21/26, Pending
    in Studio - was skipped on every later run, Rumble got it, and the
    source was retired as fully uploaded. It never reached YouTube.
    """
    upload = (upload_status or "").lower()
    processing = (processing_status or "").lower()
    if upload in ("failed", "rejected", "deleted"):
        return False
    if processing in ("failed", "terminated"):
        return False
    if upload in ("processed", ""):
        return True
    # "uploaded": received, not processed yet. Still processing now is
    # fine - a stream this tool put up an hour ago looks exactly like
    # this. Days later, it is stuck.
    if not published_at:
        return True
    try:
        when = datetime.strptime(published_at[:19], "%Y-%m-%dT%H:%M:%S")
        age = (now if now is not None else time.time()) - \
            calendar.timegm(when.timetuple())
    except ValueError:
        return True
    return age < _STUCK_AFTER_S


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


def _normalize(text: str) -> str:
    """Lowercase, letters+digits only, so punctuation and title-style
    differences ('*!howl*' vs '"!howl"') don't defeat the comparison."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# Below this, a title fragment is too generic to trust as an identity
# match ("no", "gg"), and a false match silently cancels an upload.
_MIN_TITLE_KEY = 3


def find_same_date_videos(existing_videos: list, target_date: datetime) -> list:
    """Every existing video whose title carries `target_date`."""
    variants = _date_variants(target_date)
    return [v for v in existing_videos
            if any(variant in v.title for variant in variants)]


def find_existing_video(existing_videos: list, target_date: datetime,
                        stream_title: Optional[str] = None) -> Optional[ExistingVideo]:
    """The existing upload of this same stream, or None.

    Date alone is not enough. It was originally, because backfilling an
    archive means one stream per date and this channel's title style has
    changed over the years (old: '*Title* - 05/08/26 - Stackswopo FULL YT
    Stream', new: '"Title" 5/8/26 Stackswopo Stream') while the date was
    always present. But the moment two streams go up on the SAME date, a
    date-only match makes the second one look like the first and it gets
    silently skipped - the upload never happens and the log points at the
    wrong video.

    So when `stream_title` is known, the title has to match as well. The
    comparison is loose (normalised substring) so it still spans both
    title eras: '!howl' matches '*!howl* - 03/20/26 - ...'.
    """
    same_date = find_same_date_videos(existing_videos, target_date)
    if not same_date:
        return None

    # No title to compare (callers that only know the date) - old behaviour.
    key = _normalize(stream_title) if stream_title else ""
    if not key or len(key) < _MIN_TITLE_KEY:
        return same_date[0] if stream_title is None else None

    for video in same_date:
        if key in _normalize(video.title):
            return video
    return None
