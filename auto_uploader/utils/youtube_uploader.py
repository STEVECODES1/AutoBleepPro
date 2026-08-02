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

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


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
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
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

        media = MediaFileUpload(video_path, chunksize=8 * 1024 * 1024, resumable=True)
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
