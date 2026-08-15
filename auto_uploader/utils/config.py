"""
Loads config.json (non-secret settings, titles, templates, folders) and
merges in secrets from .env (credentials) via python-dotenv. Keeping
secrets out of config.json means config.json is safe to commit/share.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv


@dataclass
class YouTubeConfig:
    channel: str
    privacy: str
    category_id: str
    made_for_kids: bool
    title_format: str
    description_template: str
    tags: list
    playlist_id: str
    thumbnail_path: str
    client_secrets_path: str
    token_path: str
    censor_uploads: bool
    upload_chunk_mb: float = 8


@dataclass
class RumbleConfig:
    channel: str
    privacy: str
    title_format: str
    description_template: str
    tags: list
    thumbnail_path: str
    username: str
    password: str
    login_url: str
    upload_url: str
    cdp_url: Optional[str]
    primary_category: str
    secondary_category: str
    censor_uploads: bool
    rss_url: str
    skip_if_exists: bool


@dataclass
class GeneralConfig:
    watch_folder: str
    uploaded_folder: str
    logs_folder: str
    date_style: str
    max_retries: int
    retry_delays: tuple
    ask_for_title: bool
    default_title: str
    supported_formats: tuple
    enable_desktop_notifications: bool
    dry_run_mode: bool
    stability_check_seconds: int
    duplicate_store_path: str
    censor_before_upload: bool
    censor_model: str
    censor_bleep_method: str
    censor_device: Optional[str]
    censor_custom_words: tuple
    censored_folder: str
    censor_padding_ms: int = 250
    censor_mute_whole_segment: bool = True
    # Optional; defaulted so older config.json files keep loading.
    filename_channel_prefixes: tuple = ()
    cleanup: dict = None
    speed: dict = None


@dataclass
class AppConfig:
    youtube: YouTubeConfig
    rumble: RumbleConfig
    general: GeneralConfig
    project_root: str
    features: dict = field(default_factory=dict)
    posting: dict = field(default_factory=dict)
    clips: dict = field(default_factory=dict)
    instagram: dict = field(default_factory=dict)
    # Optional. Facebook's posting limits live under `posting.platforms`;
    # this block is for how a Reel is composed - caption, framing - and
    # falls back to Instagram's so the same clip does not read two
    # different ways across two accounts.
    facebook: dict = field(default_factory=dict)
    # The SECOND YouTube channel, the one Shorts go to. Separate from
    # `youtube` because a YouTube OAuth token is bound to the channel
    # chosen on the consent screen, not to the Google account - so the
    # two channels cannot share a token however much else they share.
    youtube_shorts: dict = field(default_factory=dict)
    # Named routing for a full stream. "" is the existing behaviour.
    mode: str = ""
    modes: dict = field(default_factory=dict)


def _resolve_path(project_root: str, path: str) -> str:
    """Join `path` onto `project_root` unless `path` is already absolute
    (explicit check, not relying on os.path.join's implicit "an absolute
    component resets everything before it" behavior - this way a Windows
    drive path like "D:/videos stizz" for watch_folder is unambiguously
    left as-is instead of depending on join's platform-specific rules)."""
    resolved = path if os.path.isabs(path) else os.path.join(project_root, path)
    return os.path.normpath(resolved)


def load_config(config_path: str = "config.json", env_path: str = ".env") -> AppConfig:
    project_root = os.path.dirname(os.path.abspath(config_path)) or "."
    load_dotenv(env_path)

    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    yt = raw["youtube"]
    rb = raw["rumble"]
    gen = raw["general"]

    youtube = YouTubeConfig(
        channel=yt["channel"],
        privacy=yt.get("privacy", "public"),
        category_id=str(yt.get("category_id", "20")),
        made_for_kids=bool(yt.get("made_for_kids", False)),
        title_format=yt["title_format"],
        description_template=yt["description_template"],
        tags=list(yt.get("tags", [])),
        playlist_id=os.environ.get("YOUTUBE_PLAYLIST_ID", yt.get("playlist_id", "")),
        thumbnail_path=yt.get("thumbnail_path", ""),
        client_secrets_path=os.environ.get(
            "YOUTUBE_CLIENT_SECRETS_PATH",
            os.path.join(project_root, "client_secrets.json"),
        ),
        token_path=os.path.join(project_root, "youtube_token.json"),
        censor_uploads=bool(yt.get("censor_uploads", True)),
        upload_chunk_mb=float(yt.get("upload_chunk_mb", 8) or 8),
    )

    rumble = RumbleConfig(
        channel=rb["channel"],
        privacy=rb.get("privacy", "public"),
        title_format=rb["title_format"],
        description_template=rb["description_template"],
        tags=list(rb.get("tags", [])),
        thumbnail_path=rb.get("thumbnail_path", ""),
        username=os.environ.get("RUMBLE_USERNAME", ""),
        password=os.environ.get("RUMBLE_PASSWORD", ""),
        login_url=rb.get("login_url", "https://rumble.com/login.php"),
        upload_url=rb.get("upload_url", "https://rumble.com/upload.php"),
        cdp_url=os.environ.get("RUMBLE_CDP_URL") or rb.get("cdp_url") or None,
        primary_category=rb.get("primary_category", "Gaming"),
        secondary_category=rb.get("secondary_category", ""),
        censor_uploads=bool(rb.get("censor_uploads", False)),
        rss_url=rb.get("rss_url") or f"https://rumble.com/user/{rb['channel']}/index.xml",
        skip_if_exists=bool(rb.get("skip_if_exists", True)),
    )

    general = GeneralConfig(
        watch_folder=_resolve_path(project_root, gen.get("watch_folder", "./watch_folder")),
        uploaded_folder=_resolve_path(project_root, gen.get("uploaded_folder", "./uploaded")),
        logs_folder=_resolve_path(project_root, gen.get("logs_folder", "./logs")),
        date_style=gen.get("date_style", "M/D/YY"),
        max_retries=int(gen.get("max_retries", 3)),
        retry_delays=tuple(gen.get("retry_delays", [60, 300, 900])),
        ask_for_title=bool(gen.get("ask_for_title", True)),
        filename_channel_prefixes=tuple(gen.get("filename_channel_prefixes", []) or []),
        cleanup=dict(gen.get("cleanup", {}) or {}),
        speed=dict(gen.get("speed", {}) or {}),
        default_title=gen.get("default_title", "Gaming Stream"),
        supported_formats=tuple(
            e.lower() for e in gen.get("supported_formats", [".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".ts"])
        ),
        enable_desktop_notifications=bool(gen.get("enable_desktop_notifications", True)),
        dry_run_mode=bool(gen.get("dry_run_mode", False)),
        stability_check_seconds=int(gen.get("stability_check_seconds", 5)),
        duplicate_store_path=_resolve_path(project_root, gen.get("duplicate_store_path", "./uploaded_hashes.json")),
        censor_before_upload=bool(gen.get("censor_before_upload", True)),
        censor_model=gen.get("censor_model", "base"),
        censor_bleep_method=gen.get("censor_bleep_method", "beep"),
        censor_device=os.environ.get("CENSOR_DEVICE") or gen.get("censor_device") or None,
        censor_custom_words=tuple(gen.get("censor_custom_words", [])),
        censored_folder=_resolve_path(project_root, gen.get("censored_folder", "./censored")),
        censor_padding_ms=int(gen.get("censor_padding_ms", 250)),
        censor_mute_whole_segment=bool(gen.get("censor_mute_whole_segment", True)),
    )

    # The posting block's paths are resolved against the config file, not
    # the working directory. Left relative, running main.py from anywhere
    # else would write a FRESH posting_state.json - and a cap that reads
    # zero posts is a cap that permits a full day's burst on every run.
    posting = dict(raw.get("posting", {}))
    for key, default in (("state_path", "./posting_state.json"),
                         ("queue_path", "./clip_jobs.json"),
                         ("kill_switch_file", "./STOP_POSTING"),
                         # Where a post that only a human may make gets
                         # written down. Relative, it lands under whatever
                         # directory the run started in - which is how a
                         # queue of manual posts becomes a file nobody
                         # ever opens.
                         ("manual_queue_path", "./logs/manual_posts.txt")):
        posting[key] = _resolve_path(project_root, posting.get(key) or default)

    return AppConfig(youtube=youtube, rumble=rumble, general=general,
                     project_root=project_root,
                     features=raw.get("features", {}),
                     posting=posting,
                     clips=raw.get("clips", {}),
                     instagram=raw.get("instagram", {}),
                     facebook=raw.get("facebook", {}),
                     youtube_shorts=raw.get("youtube_shorts", {}),
                     mode=str(raw.get("mode", "") or ""),
                     modes=raw.get("modes", {}))


def validate_config(cfg: AppConfig) -> list:
    """Returns a list of human-readable problems; empty list = all good.
    Used by --test-config. Doesn't make any network calls."""
    problems = []

    if not os.path.exists(cfg.youtube.client_secrets_path):
        problems.append(
            f"YouTube client_secrets.json not found at: {cfg.youtube.client_secrets_path} "
            "(download it from Google Cloud Console - see README)"
        )
    if not cfg.rumble.cdp_url and (not cfg.rumble.username or not cfg.rumble.password):
        problems.append(
            "Neither rumble.cdp_url (config.json) nor RUMBLE_USERNAME/RUMBLE_PASSWORD (.env) are set."
        )
    if "{title}" not in cfg.youtube.title_format or "{date}" not in cfg.youtube.title_format:
        problems.append("youtube.title_format must contain both {title} and {date}")
    if "{title}" not in cfg.rumble.title_format or "{date}" not in cfg.rumble.title_format:
        problems.append("rumble.title_format must contain both {title} and {date}")

    for folder in (cfg.general.watch_folder, cfg.general.uploaded_folder, cfg.general.logs_folder):
        os.makedirs(folder, exist_ok=True)

    return problems
