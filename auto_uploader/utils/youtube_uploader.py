"""
YouTube uploader via the official YouTube Data API v3 (OAuth2, resumable
upload). This is a real, documented Google API - unlike Rumble, there's
nothing reverse-engineered here.

First run opens a browser for you to grant access; after that, the
refresh token in `youtube_token.json` keeps you logged in automatically.
"""

import os
from typing import Callable, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    # channels.list/playlistItems.list (used by youtube_checker.py's
    # existing-video dedup check) need read access, which upload alone
    # doesn't grant - without this, that check 403s with
    # "insufficient authentication scopes" and silently skips itself.
    "https://www.googleapis.com/auth/youtube.readonly",
    # commentThreads.insert - the pinned-comment-with-a-link that every
    # creator leaves under their own video. Upload and readonly do not
    # cover writing a comment, and without this it 403s with
    # "insufficient authentication scopes".
    #
    # Adding a scope INVALIDATES the existing token: Google issues a token
    # for the scopes that were asked for, and it does not grow one later.
    # --setup-youtube re-authorises in place; see needs_reauth().
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def needs_reauth(token_path: str) -> bool:
    """True when the saved token predates a scope this code now needs.

    A token missing a scope fails at the moment it is used, which for a
    comment is after the video is already live - the upload works, the
    comment 403s, and the reason is three layers down inside a Google
    exception. Asked up front instead.
    """
    import json

    try:
        with open(token_path, "r", encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        return False
    have = set(saved.get("scopes") or [])
    return bool(have) and not set(SCOPES) <= have


def _video_id(value: str) -> str:
    """The id out of a watch URL, a youtu.be link, a Short, or a bare id."""
    import re as _re

    text = str(value or "").strip()
    if not text:
        return ""
    found = _re.search(
        r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})", text)
    if found:
        return found.group(1)
    return text if _re.fullmatch(r"[A-Za-z0-9_-]{11}", text) else ""


class YouTubeUploader:
    def __init__(self, client_secrets_path: str, token_path: str):
        self.client_secrets_path = client_secrets_path
        self.token_path = token_path
        self._service = None

    def _get_credentials(self) -> Credentials:
        creds = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)

        if not creds or not creds.valid:
            refreshed = False
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    refreshed = True
                except Exception as exc:
                    # A refresh token is not forever. Google revokes it
                    # when the account password changes, when the app is
                    # removed from the account's third-party access, and
                    # automatically after six months unused - and an
                    # unverified app's tokens expire in seven days.
                    #
                    # Unhandled, this came out as a raw
                    # "invalid_grant: Token has been expired or revoked"
                    # and stopped --batch dead with no way forward
                    # printed. Falling through to the browser flow is
                    # right at a keyboard and wrong in --watch, which
                    # would hang on it forever with nobody to answer.
                    # So: say the command, and let a person run it.
                    if "invalid_grant" in str(exc) or "revoked" in str(exc):
                        raise RuntimeError(
                            "YouTube sign-in has expired.\n"
                            "         python main.py --setup-youtube\n"
                            "         (a browser opens; sign in with the "
                            "account that OWNS the channel, and pick the "
                            "VOD channel rather than the Shorts one)\n"
                            "\n"
                            "         IF THIS KEEPS HAPPENING WEEKLY: the "
                            "Google Cloud app is still in Testing mode, "
                            "and a testing-mode app's refresh tokens are "
                            "killed after SEVEN DAYS by design. No amount "
                            "of signing in again changes that. Fix it once "
                            "at\n"
                            "         https://console.cloud.google.com/auth/audience\n"
                            "         -> Publish app. It stays unverified, "
                            "which only means the consent screen shows a "
                            "warning to click past; the tokens stop "
                            "expiring.") from exc
                    raise
            if not refreshed:
                if not os.path.exists(self.client_secrets_path):
                    raise FileNotFoundError(
                        f"YouTube client_secrets.json not found at {self.client_secrets_path}. "
                        "See README.md for how to get this from Google Cloud Console."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_path, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(self.token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

        return creds

    def _client(self):
        if self._service is None:
            self._service = build("youtube", "v3", credentials=self._get_credentials())
        return self._service

    def get_service(self):
        """Public accessor for the raw googleapiclient service object, so
        other modules (e.g. youtube_checker.py) can make their own API
        calls without duplicating the OAuth/credential-caching logic."""
        return self._client()

    def upload(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: list,
        privacy: str = "public",
        category_id: str = "20",
        made_for_kids: bool = False,
        thumbnail_path: Optional[str] = None,
        chunk_mb: float = 8,
        playlist_id: Optional[str] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> str:
        """Uploads `video_path`, returns the resulting watch URL."""
        service = self._client()

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": made_for_kids,
            },
        }

        # Resumable with an explicit chunk size. Bigger chunks mean fewer
        # HTTP round-trips on a fast line; smaller ones resume with less
        # lost work after a drop. 8 MB is the conservative default.
        chunk_bytes = max(256 * 1024, int(chunk_mb * 1024 * 1024))
        media = MediaFileUpload(video_path, chunksize=chunk_bytes, resumable=True)
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        try:
            while response is None:
                status, response = request.next_chunk()
                if status and progress_callback:
                    progress_callback(int(status.progress() * 100))
        except HttpError as exc:
            raise RuntimeError(f"YouTube upload failed: {exc}") from exc

        video_id = response["id"]

        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                service.thumbnails().set(
                    videoId=video_id, media_body=MediaFileUpload(thumbnail_path)
                ).execute()
            except HttpError as exc:
                # Thumbnail failure shouldn't fail the whole upload - the video is live either way.
                print(f"[YouTube] Warning: thumbnail upload failed: {exc}")

        if playlist_id:
            try:
                service.playlistItems().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "playlistId": playlist_id,
                            "resourceId": {"kind": "youtube#video", "videoId": video_id},
                        }
                    },
                ).execute()
            except HttpError as exc:
                print(f"[YouTube] Warning: failed to add video to playlist: {exc}")

        return f"https://www.youtube.com/watch?v={video_id}"

    def comment(self, video_url_or_id: str, text: str) -> bool:
        """Leave a comment on one of your own videos. True if it posted.

        The pinned-link comment every creator leaves under their own
        upload - "full stream on Rumble: ..." - which is the only route a
        viewer has to another platform from inside a Short.

        Never raises and never fails an upload. The video is live either
        way, and a missing comment is worth far less than a run that
        stopped after publishing.

        PINNING is not in the API - only posting. Pin it by hand once and
        YouTube keeps it there; the comment itself is what has to be
        automatic.
        """
        video_id = _video_id(video_url_or_id)
        if not video_id or not str(text or "").strip():
            return False
        try:
            service = self._client()
            service.commentThreads().insert(
                part="snippet",
                body={"snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {"textOriginal": str(text).strip()}},
                }},
            ).execute()
        except HttpError as exc:
            if "insufficientPermissions" in str(exc) or "403" in str(exc):
                print("[YouTube] Cannot comment - the saved token was issued "
                      "before this needed comment access. Re-authorise:")
                print("          python main.py --setup-youtube")
            else:
                print(f"[YouTube] Comment failed: {exc}")
            return False
        except Exception as exc:
            print(f"[YouTube] Comment failed: {exc}")
            return False
        print("[YouTube] Left a comment with the link.")
        return True
