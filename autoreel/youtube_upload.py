"""
YouTube uploader for BinScripts clips.

Handles title formatting, description templating and tag generation so
every Short lands looking like it came from a real channel, not a script.

WHAT THIS DOES
--------------
1.  Builds a clean, search-friendly title from the clip's hook line.
2.  Builds a professional multi-section description (intro + links +
    hashtags) that YouTube surfaces in search.
3.  Generates relevant tags from the clip transcript so the algorithm
    has signal to work with.
4.  Uploads the rendered .mp4 as a YouTube Short (< 60 s, vertical).

SETUP
-----
You need a Google OAuth 2.0 client secret file (``client_secrets.json``)
in the project root. The first run opens a browser to authorise; after
that a token is cached in ``token.json``.

Get credentials at:
https://console.cloud.google.com/apis/credentials
Enable the YouTube Data API v3 for the project.
"""

from __future__ import annotations

import os
import re
import textwrap
from typing import Optional

try:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    _YT_AVAILABLE = True
except ImportError:
    _YT_AVAILABLE = False

# ── Channel identity ────────────────────────────────────────────────────────

CHANNEL_HANDLE  = "@BinScript"
CHANNEL_RUMBLE  = "rumble.com/c/BinScripts"
CHANNEL_TAGLINE = "Raw stream moments, no filter."

# Base tags always attached to every upload.
BASE_TAGS = [
    "BinScripts",
    "BinScript",
    "stream highlights",
    "gaming clips",
    "funny moments",
    "Shorts",
    "gaming shorts",
    "live stream clips",
    "Stackswopo",
]

# YouTube category ID 20 = Gaming.
DEFAULT_CATEGORY = "20"

# OAuth scopes needed to upload.
_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
_TOKEN_FILE   = "token.json"
_SECRETS_FILE = "client_secrets.json"


# ── Title formatting ───────────────────────────────────────────────────────

# YouTube title hard limit is 100 chars. We target shorter so the full
# title is visible in feed cards without truncation.
_TITLE_MAX = 80

# Filler words that weaken titles; strip them from the hook before using
# it as a title so the result is punchy and search-friendly.
_TITLE_FILLER = re.compile(
    r"\b(um+|uh+|like|you know|i mean|basically|literally|actually|ok+ay?)\b",
    re.IGNORECASE,
)


def _clean_hook(raw: str) -> str:
    """Turn a raw transcript hook into a clean title fragment."""
    text = _TITLE_FILLER.sub("", raw)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,.-")
    # Sentence-case: first char upper, rest as-is.
    return text[:1].upper() + text[1:] if text else ""


def build_title(hook: str, clip_index: int = 0, stream_name: str = "") -> str:
    """Return a YouTube-ready title <= 80 characters.

    Format (with hook):   {Hook} 🔥 #Shorts
    Format (no hook):     {Stream Name} - Clip {N} 🔥 #Shorts
    Both always end with the #Shorts tag so YouTube classifies it.
    """
    suffix = " 🔥 #Shorts"
    if hook:
        fragment = _clean_hook(hook)
        candidate = f"{fragment}{suffix}"
        if len(candidate) <= _TITLE_MAX:
            return candidate
        # Truncate hook to fit, break at a word boundary.
        budget = _TITLE_MAX - len(suffix) - 3  # 3 for "..."
        fragment = fragment[:budget].rsplit(" ", 1)[0] + "..."
        return f"{fragment}{suffix}"

    # Fallback: use stream / channel name + clip number.
    base = stream_name or CHANNEL_HANDLE
    if clip_index:
        return f"{base} - Clip {clip_index:02d}{suffix}"
    return f"{base}{suffix}"


# ── Description formatting ─────────────────────────────────────────────────

def build_description(hook: str = "", stream_name: str = "",
                      extra_tags: Optional[list] = None) -> str:
    """Return a multi-section YouTube description.

    Sections
    --------
    1. One-line hook (what the clip is).
    2. Subscribe nudge + channel links.
    3. Hashtag block for discovery.

    YouTube shows the first 2-3 lines before the "Show more" fold, so the
    hook and subscribe ask go first.
    """
    hook_line = _clean_hook(hook) if hook else (stream_name or "")

    hashtags = _build_hashtags(extra_tags)

    desc = textwrap.dedent(f"""\
        {hook_line}

        🔔 Subscribe for daily stream clips → {CHANNEL_HANDLE}
        📺 Watch more on Rumble → {CHANNEL_RUMBLE}

        {CHANNEL_TAGLINE}

        {hashtags}
    """).strip()
    return desc


def _build_hashtags(extra: Optional[list] = None) -> str:
    """Return a hashtag line from base tags + any extra clip-specific tags."""
    tags = list(BASE_TAGS)
    if extra:
        tags = list(extra) + tags
    # Deduplicate while preserving order.
    seen: set = set()
    unique = []
    for t in tags:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)
    # Format each as #Tag (no spaces, title-cased).
    formatted = ["#" + re.sub(r"\s+", "", t.title()) for t in unique[:15]]
    return " ".join(formatted)


# ── Tag generation ────────────────────────────────────────────────────────────

# Common gaming/stream words that make poor standalone tags.
_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
    "of", "is", "it", "he", "she", "we", "i", "you", "they",
    "this", "that", "was", "be", "with", "for", "his", "her",
    "my", "just", "so", "oh", "yeah", "no", "yes", "okay", "ok",
}


def extract_tags(transcript: str, stream_name: str = "") -> list:
    """Pull meaningful keywords from the clip transcript.

    Returns up to 30 tags: base tags first, then words from the
    transcript that clear a minimum length and are not stop words.
    YouTube allows up to 500 characters total across all tags.
    """
    words = re.findall(r"[a-zA-Z']+", transcript.lower())
    seen = {t.lower() for t in BASE_TAGS}
    extra = []
    for w in words:
        clean = w.strip("'")
        if len(clean) >= 4 and clean not in _STOP_WORDS and clean not in seen:
            seen.add(clean)
            extra.append(clean)

    if stream_name:
        stream_words = re.findall(r"[a-zA-Z]+", stream_name)
        for w in stream_words:
            if w.lower() not in seen:
                seen.add(w.lower())
                extra.insert(0, w)

    tags = BASE_TAGS + extra
    # Trim to stay under YouTube's 500-character tag budget.
    result = []
    budget = 500
    for t in tags:
        if budget - len(t) - 1 < 0:
            break
        result.append(t)
        budget -= len(t) + 1  # +1 for the comma separator
    return result


# ── OAuth helpers ────────────────────────────────────────────────────────────

def _get_credentials() -> "Credentials":
    """Load or refresh OAuth 2.0 credentials, prompting once if needed."""
    creds = None
    if os.path.exists(_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(_TOKEN_FILE, _SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(_SECRETS_FILE, _SCOPES)
            creds = flow.run_local_server(port=0)
        with open(_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds


def _build_service():
    """Return an authorised YouTube Data API v3 service object."""
    return build("youtube", "v3", credentials=_get_credentials())


# ── Upload ────────────────────────────────────────────────────────────────

def upload_short(
    video_path: str,
    hook: str = "",
    transcript: str = "",
    stream_name: str = "",
    clip_index: int = 0,
    privacy: str = "public",
    category: str = DEFAULT_CATEGORY,
    dry_run: bool = False,
) -> Optional[str]:
    """Upload one clip as a YouTube Short. Returns the video ID, or None.

    Parameters
    ----------
    video_path  : Path to the rendered .mp4 file.
    hook        : The one-liner pulled from the clip (used as the title base).
    transcript  : Full text of the clip window (used to generate tags).
    stream_name : Clean stream / channel name (e.g. "Stackswopo Stream").
    clip_index  : Clip number within the session (used in fallback titles).
    privacy     : "public", "unlisted", or "private".
    category    : YouTube category ID string.  20 = Gaming.
    dry_run     : If True, print what would be uploaded but do nothing.
    """
    if not _YT_AVAILABLE:
        raise ImportError(
            "google-api-python-client and google-auth-oauthlib are required. "
            "Run: pip install google-api-python-client google-auth-oauthlib"
        )

    title       = build_title(hook, clip_index, stream_name)
    description = build_description(hook, stream_name)
    tags        = extract_tags(transcript, stream_name)

    if dry_run:
        print("[YouTube dry-run]")
        print(f"  Title      : {title}")
        print(f"  Description:\n{description}")
        print(f"  Tags       : {tags}")
        print(f"  File       : {video_path}")
        return None

    service = _build_service()

    body = {
        "snippet": {
            "title":       title,
            "description": description,
            "tags":        tags,
            "categoryId":  category,
        },
        "status": {
            "privacyStatus":           privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=4 * 1024 * 1024,  # 4 MB chunks
    )

    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"  Uploading... {pct}%", end="\r", flush=True)

    video_id = response.get("id", "")
    print(f"[YouTube] Uploaded: https://youtube.com/shorts/{video_id}")
    return video_id
