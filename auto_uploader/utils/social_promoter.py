"""
Posts an announcement after a successful (real, non-skipped) upload.

Discord works out of the box with just a webhook URL in .env - it's a
plain HTTP POST, no extra dependency. Twitter/X (tweepy) and Reddit
(praw) are optional: if their flag is on but the library or credentials
are missing, the promoter says so and moves on rather than failing the
upload flow. Announcing is always best-effort - a failed post must never
mark an upload as failed.
"""

import json
import os
import urllib.request


def _post_discord(webhook_url: str, message: str) -> None:
    payload = json.dumps({"content": message}).encode("utf-8")
    request = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "AutoUploader"},
    )
    urllib.request.urlopen(request, timeout=15)


def _post_twitter(message: str) -> None:
    import tweepy  # optional dependency

    client = tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_SECRET"],
    )
    client.create_tweet(text=message[:280])


REDDIT_FIELDS = ("CLIENT_ID", "CLIENT_SECRET", "USERNAME", "PASSWORD")


def reddit_env_names(field: str, account: str = "") -> list:
    """Env var names to try for one credential field, in priority order.

    Reddit posting is expected to run on a DIFFERENT account from the one
    the rest of the project may have configured, so credentials are looked
    up per named account rather than from one fixed set. `account="2"`
    finds REDDIT_CLIENT_ID_2; `account="ALT"` finds either
    REDDIT_CLIENT_ID_ALT or REDDIT_ALT_CLIENT_ID, because both layouts
    read naturally and guessing wrong just means an auth failure later.

    An empty account is the primary REDDIT_* set.
    """
    account = (account or "").strip().strip("_")
    if not account:
        return [f"REDDIT_{field}"]
    return [f"REDDIT_{field}_{account}", f"REDDIT_{account}_{field}"]


def reddit_credentials(account: str = "") -> dict:
    """One Reddit account's credentials, read from the environment.

    Raises KeyError naming the variable it looked for, so a half-filled
    .env fails with something actionable instead of an auth error later.
    """
    creds = {}
    for field in REDDIT_FIELDS:
        names = reddit_env_names(field, account)
        value = ""
        for name in names:
            value = os.environ.get(name, "").strip()
            if value:
                break
        if not value:
            who = f"the '{account}' account" if account else "Reddit"
            raise KeyError(
                f"{names[0]} is not set in .env - needed to post to {who}.")
        creds[field.lower()] = value
    return creds


def reddit_credentials_missing(account: str = "") -> list:
    """Which credential variables are absent. Empty list = ready."""
    missing = []
    for field in REDDIT_FIELDS:
        names = reddit_env_names(field, account)
        if not any(os.environ.get(n, "").strip() for n in names):
            missing.append(names[0])
    return missing


def _post_reddit(subreddit: str, title: str, url: str,
                 account: str = "") -> None:
    import praw  # optional dependency

    creds = reddit_credentials(account)
    reddit = praw.Reddit(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        username=creds["username"],
        password=creds["password"],
        # Reddit asks that the user agent identify the app and the account
        # it acts for; a shared/blank one is itself a spam signal.
        user_agent=f"AutoUploader/1.0 (by u/{creds['username']})",
    )
    reddit.subreddit(subreddit).submit(title=title, url=url)


def build_message(title: str, new_uploads: dict) -> str:
    lines = [f"🎬 New upload: {title}"]
    if new_uploads.get("youtube"):
        lines.append(f"▶️ YouTube: {new_uploads['youtube']}")
    if new_uploads.get("rumble"):
        lines.append(f"🟢 Rumble: {new_uploads['rumble']}")
    return "\n".join(lines)


def announce_upload(features: dict, title: str, new_uploads: dict) -> list:
    """Announce `new_uploads` ({platform: url}, only things uploaded THIS
    run - never pre-existing skips). Returns the channels that posted."""
    if not features.get("enabled") or not new_uploads:
        return []

    message = build_message(title, new_uploads)
    posted = []

    if features.get("discord", True):
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if not webhook:
            print("[Social] Discord enabled but DISCORD_WEBHOOK_URL not set in .env - skipping.")
        else:
            try:
                _post_discord(webhook, message)
                posted.append("discord")
                print("[Social] Posted to Discord.")
            except Exception as exc:
                print(f"[Social] WARNING: Discord post failed: {exc}")

    if features.get("twitter", False):
        try:
            _post_twitter(message)
            posted.append("twitter")
            print("[Social] Posted to Twitter/X.")
        except ImportError:
            print("[Social] Twitter enabled but tweepy not installed (pip install tweepy) - skipping.")
        except KeyError as exc:
            print(f"[Social] Twitter enabled but {exc} not set in .env - skipping.")
        except Exception as exc:
            print(f"[Social] WARNING: Twitter post failed: {exc}")

    if features.get("reddit", False):
        subreddit = features.get("reddit_subreddit", "")
        # Which Reddit account to act as. Config-driven so a different
        # account can be used without editing code - see reddit_env_names.
        account = features.get("reddit_account", "")
        primary_url = new_uploads.get("youtube") or new_uploads.get("rumble", "")
        if not subreddit:
            print("[Social] Reddit enabled but reddit_subreddit not set in config - skipping.")
        else:
            try:
                _post_reddit(subreddit, title, primary_url, account)
                posted.append("reddit")
                print(f"[Social] Posted to r/{subreddit}.")
            except ImportError:
                print("[Social] Reddit enabled but praw not installed (pip install praw) - skipping.")
            except KeyError as exc:
                print(f"[Social] Reddit enabled but {exc} not set in .env - skipping.")
            except Exception as exc:
                print(f"[Social] WARNING: Reddit post failed: {exc}")

    return posted
