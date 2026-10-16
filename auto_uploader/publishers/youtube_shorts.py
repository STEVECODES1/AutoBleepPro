"""
Posts a finished clip to a YouTube channel as a Short.

WHY A SEPARATE TOKEN
--------------------
A YouTube OAuth token is bound to the CHANNEL that was picked during the
consent screen, not to the Google account. The token this project
already has belongs to the VOD channel that full streams go to, and
uploading a Short with it would put the Short there. So this has its own
token file, made by signing in again and choosing the Shorts channel.
The client_secrets.json is shared - that identifies the app, not the
channel.

WHAT MAKES IT A SHORT
---------------------
Nothing in the API. YouTube decides after the fact: vertical, and 3
minutes or under. Every clip this pipeline makes is 1080x1920 and capped
at 60 seconds, so they qualify without anything special being sent. The
#Shorts tag is not required any more and is added to the description
only because it is still how the channel page groups them.

ON YOUTUBE'S RULES
------------------
YouTube is stricter than the other destinations here about volume and
about repetition, and a channel is much harder to get back than a post
is to delete. Two things follow from that, and both are deliberate:

  * it is OFF until switched on, and posts PRIVATE until changed, so the
    first batch is reviewed by a person rather than discovered by the
    channel's audience;
  * the daily cap and the spacing live in PublishGuard with everything
    else, and they are set low. Twenty clips from one VOD posted in an
    afternoon is what "repetitious content" means in the policy, whoever
    made them.

The clips are the channel owner's own footage, which is the part that
matters most - this is not reposting.
"""

from __future__ import annotations

import re
import os
from typing import Optional

from .errors import NotConfigured

# YouTube counts anything at or under three minutes as a Short. This
# pipeline caps clips at 60s, so this is a guard against a mis-set config
# rather than an expected limit.
MAX_SHORT_SECONDS = 180

# Where config.json lives. Paths in it like "./youtube_shorts_token.json"
# mean "next to the config", not "next to whatever directory the user
# happened to be standing in" - running from the repo root instead of
# auto_uploader/ made --setup-shorts report a missing client_secrets.json
# that was sitting right there, and would have made ready() answer "not
# signed in" for a token that existed.
_CONFIG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_SECRETS = os.path.join(_CONFIG_DIR, "client_secrets.json")


def _anchored(path) -> str:
    """An absolute path for a config value, or "" if it was blank."""
    text = str(path or "").strip()
    if not text:
        return ""
    if os.path.isabs(text):
        return text
    # normpath so a "./" prefix does not survive into the path this
    # prints back at the user after sign-in.
    return os.path.normpath(os.path.join(_CONFIG_DIR, text))


# Appended to the description. Not required by YouTube any more - it
# classifies by aspect and length - but it is still how the channel page
# and search group them.
SHORTS_TAG = "#Shorts"

# The decoration a caption carries and a TITLE should not.
#
# A caption's first line is the spoken line plus the emoji and the
# channel tag - fine in a description, and on a title it reads as a bot:
# "Gumball a** animations 🤣🤣🤣💀💀💀#stackswopo". The line is the title;
# the rest belongs in the description, which is where it already goes.
#
# The tail is a run of tags and emoji separated by nothing but space. A
# word anywhere in it means the run has not started yet: "yo 🤣 that was
# crazy" is a whole sentence, and an earlier version of this cut it to
# "yo" because it allowed word characters after the first emoji.
_DECORATION = r"(?:#\w+|[\U0001F000-\U0001FAFF☀-➿️‍])"
_TITLE_TAIL = re.compile(rf"(?:\s*{_DECORATION}\s*)*\s*$")


def _bare_line(text: str) -> str:
    """A caption's first line with its trailing emoji and tags removed.

    Only the TAIL. An emoji in the middle of what somebody said is part
    of the sentence, and a title that is nothing but emoji keeps them -
    stripping it to empty would be worse than leaving it.
    """
    trimmed = _TITLE_TAIL.sub("", text or "").strip(" -,;:·|")
    return trimmed or (text or "").strip()


class YouTubeShortsPublisher:
    """Uploads one clip to the Shorts channel."""
    def __init__(self, config: dict):
        self.config = config or {}
        self.settings = dict(self.config.get("youtube_shorts", {}) or {})

    # ── configuration ────────────────────────────────────────────────

    def token_path(self) -> str:
        return _anchored(self.settings.get("token_path", ""))

    def client_secrets_path(self) -> str:
        """Shared with the VOD uploader - it identifies the app, not the
        channel."""
        configured = _anchored(self.settings.get("client_secrets_path", ""))
        if configured:
            return configured
        return _anchored((self.config.get("youtube", {}) or {}).get(
            "client_secrets_path", "")) or _DEFAULT_SECRETS

    def ready(self) -> bool:
        """True only when this channel has been signed into.

        Checked as a FILE rather than as a flag: a config that says yes
        while the token is missing sends every clip into an interactive
        OAuth prompt, and --watch has nobody at the keyboard to answer
        it.
        """
        token = self.token_path()
        return bool(token) and os.path.isfile(token)

    def _raise_if_setup(self) -> None:
        if not self.client_secrets_path():
            raise NotConfigured(
                "youtube_shorts: no client_secrets.json - the same file the "
                "VOD uploader uses works here.")
        if not self.token_path():
            raise NotConfigured(
                "youtube_shorts: token_path is not set in config.json.")
        if not os.path.isfile(self.token_path()):
            raise NotConfigured(
                f"youtube_shorts: not signed in yet. Run:\n"
                f"         python main.py --setup-shorts\n"
                f"         Sign in and pick the SHORTS channel, not the VOD "
                f"one - the token remembers whichever you choose.")

    # ── posting ──────────────────────────────────────────────────────

    def description_for(self, caption: str, video_path: str = "") -> str:
        from autoreel.safe_text import clean_lines

        caption = clean_lines(caption)
        template = str(self.settings.get("description_template", "")).strip()
        body = template.replace("[CAPTION]", caption) if template else caption
        # Right under the caption, where the "more" tap lands: the full
        # video on YouTube AND uncut on Rumble - see utils/stream_links.
        # YouTube gives Shorts no clickable link, so these are there to be
        # read and searched; Rumble is the one the channel lives on.
        try:
            from utils.stream_links import link_for_clip

            full = link_for_clip(video_path, "youtube") if video_path else ""
            uncut = link_for_clip(video_path, "rumble") if video_path else ""
        except Exception:
            full = uncut = ""
        lines = []
        if full and full not in body:
            lines.append(f"\u25b6 Full video: {full}")
        if uncut and uncut.startswith("https://rumble.com/v") and uncut not in body:
            lines.append(f"\u25b6 Uncut & uncensored on Rumble: {uncut}")
        if lines:
            head, sep, rest = body.partition("\n\n")
            body = f"{head}\n\n" + "\n".join(lines) + (f"\n\n{rest}" if sep else "")
        if SHORTS_TAG.lower() not in body.lower():
            body = f"{body}\n\n{SHORTS_TAG}".strip()
        return body

    def safe_fallback(self, video_path: str) -> str:
        """What to title a clip whose spoken line cannot be published.

        The stream it came from, not a bare "Stackswopo clip": a Short
        titled that on 10-16 told nobody anything. Only when the stream
        is unknown does the configured fallback stand."""
        try:
            from utils.stream_links import source_of

            stream = str(source_of(video_path).get("stream_title", "") or "").strip()
        except Exception:
            stream = ""
        if stream and not re.search(r"\d{4}-\d{2}-\d{2}|rumble live", stream, re.I):
            return f'Stacks went OFF on "{stream.upper()}"'[:90]
        configured = str(self.settings.get("safe_title", "") or "").strip()
        return configured or "Stacks went OFF on stream"

    def title_for(self, caption: str, video_path: str) -> str:
        """A Short's title is the first line of the caption, trimmed.

        YouTube rejects a title over 100 characters outright, and one
        that runs to the limit is truncated with an ellipsis in every
        feed it appears in.
        """
        line = (caption or "").strip().splitlines()
        title = _bare_line((line[0] if line else "").strip())
        if not title:
            title = os.path.splitext(os.path.basename(video_path))[0]
        # YouTube applies its rules to the TEXT as well as the video,
        # and does not care that the words came off the audio. The clip
        # titles this pipeline produces are the line actually spoken -
        # which is why they read like a person wrote them, and why some
        # of them cannot go on a YouTube channel as written.
        from autoreel.safe_text import clean_title

        title = clean_title(title, self.safe_fallback(video_path))
        limit = int(self.settings.get("max_title_chars", 90))
        if len(title) > limit:
            title = title[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
        return title or "Clip"

    def post_clip(self, video_path: str, caption: str,
                  dry_run: bool = False) -> Optional[str]:
        """Upload one clip. Returns the watch URL, or None on failure."""
        self._raise_if_setup()

        if not os.path.isfile(video_path):
            raise NotConfigured(f"youtube_shorts: no such file {video_path}")

        if dry_run:
            print(f"[YouTube Shorts] DRY RUN - would post "
                  f"{os.path.basename(video_path)} as "
                  f"'{self.title_for(caption, video_path)}'")
            return "dry-run"

        from utils.youtube_uploader import YouTubeUploader

        uploader = YouTubeUploader(self.client_secrets_path(),
                                   self.token_path())
        url = uploader.upload(
            video_path,
            title=self.title_for(caption, video_path),
            description=self.description_for(caption, video_path),
            tags=list(self.settings.get("tags", []) or []),
            # Private by default. A channel is much harder to get back
            # than a post is to delete, so the first batch is reviewed
            # rather than discovered by the audience.
            privacy=str(self.settings.get("privacy", "private")),
            category_id=str(self.settings.get("category_id", "20")),
            made_for_kids=bool(self.settings.get("made_for_kids", False)),
        )
        # The Rumble link as a comment: Shorts viewers ask "what does he
        # stream on" because the description hides behind a tap on
        # mobile. Best effort, always: the video is live either way.
        leave_link_comment(self.config, url, video_path)
        return url


def link_comment_for(config: dict, video_path: str = "") -> str:
    """The comment under a Short: the full video it came from, the uncut
    one on Rumble, and every full stream. "" when the feature is off
    (an empty youtube.link_comment).

    Viewers under a Short ask "where's the full video" - so the answer is
    the first line, not the channel page.
    """
    template = str((config.get("youtube", {}) or {}).get(
        "link_comment", "") or "").strip()
    rumble = str((config.get("rumble", {}) or {}).get(
        "channel_url", "") or "").strip()
    if not template or ("{rumble}" in template and not rumble):
        return ""
    full = rumble_stream = ""
    if video_path:
        try:
            from utils.stream_links import link_for_clip

            full = link_for_clip(video_path, "youtube")
            rumble_stream = link_for_clip(video_path, "rumble")
        except Exception:
            full = rumble_stream = ""
    lines = []
    if full:
        lines.append(f"\u25b6 Full video: {full}")
    lines.append(template.replace("{rumble}", rumble_stream or rumble).strip())
    lines.append("\u25b6 Every full stream: https://www.youtube.com/@wopovod")
    return "\n".join(line for line in lines if line)


def leave_link_comment(config: dict, url: str, video_path: str = "") -> bool:
    """Post the links under a Short. True if it went out.

    Never raises: a missing comment is a missing nicety, not a failed post.
    Shared by the direct publisher and the Postproxy YouTube route.

    The token and client secrets come from the publisher's own resolvers.
    Reading them raw from config.json - where client_secrets_path is unset
    and the token path is relative - made this return False silently, and
    no Short ever got its comment.
    """
    try:
        text = link_comment_for(config, video_path)
        if not text or not str(url or "").startswith("http"):
            return False
        from utils.youtube_uploader import YouTubeUploader

        publisher = YouTubeShortsPublisher(config)
        secrets, token = publisher.client_secrets_path(), publisher.token_path()
        if not secrets or not token:
            print("[YouTube Shorts] No Shorts token - link comment skipped.")
            return False
        return bool(YouTubeUploader(secrets, token).comment(url, text))
    except Exception as exc:
        print(f"[YouTube Shorts] Could not leave the link comment: {exc}")
        return False
