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
]


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
                            "YouTube sign-in has expired. Re-authorise "
                            "with:\n"
                            "         python main.py --setup-youtube\n"
                            "         (a browser opens; pick the VOD "
                            "channel, not the Shorts one)") from exc
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
