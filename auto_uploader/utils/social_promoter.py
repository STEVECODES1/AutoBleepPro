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
import time
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


def is_url(value: str) -> bool:
    """A real link, not a status marker.

    Rumble sometimes completes an upload without ever showing the video's
    URL, and records a human-readable marker instead. That string was
    being handed to Facebook as a link, which it rejected with "Invalid
    parameter" - correctly, since it is a sentence.
    """
    return str(value or "").strip().lower().startswith(("http://", "https://"))


def build_message(title: str, new_uploads: dict) -> str:
    """The announcement text. Only real URLs are shown as links.

    An upload that completed without reporting a URL still gets a line -
    it did happen, and saying so is useful - but not one that reads like
    a link when it is a status message.
    """
    lines = [f"🎬 New upload: {title}"]
    for platform, label in (("youtube", "▶️ YouTube"), ("rumble", "🟢 Rumble")):
        value = new_uploads.get(platform)
        if not value:
            continue
        if is_url(value):
            lines.append(f"{label}: {value}")
        else:
            lines.append(f"{label}: uploaded (check the channel for the link)")
    return "\n".join(lines)


def primary_link(new_uploads: dict) -> str:
    """The one URL an announcement points at. YouTube wins when both ran."""
    for platform in ("youtube", "rumble"):
        candidate = new_uploads.get(platform, "")
        if is_url(candidate):
            return candidate
    return ""


def manual_post_text(platform: str, title: str, new_uploads: dict) -> str:
    """The exact text to paste, for a platform we deliberately don't automate.

    Composed the same way an automated post would be, because the point is
    that posting by hand should cost a paste rather than a rewrite.
    """
    link = primary_link(new_uploads)
    if platform == "x":
        # 280 characters, and the link costs a fixed 23 whatever its
        # length, so the title is what has to give.
        budget = 280 - 23 - len("🎬 New upload: ") - 2
        headline = title if len(title) <= budget else title[:budget - 1] + "…"
        return f"🎬 New upload: {headline}\n{link}"
    return build_message(title, new_uploads)


def queue_manual_post(platform: str, title: str, new_uploads: dict,
                      queue_path: str = "", notify_discord: bool = True) -> str:
    """Park an announcement for a human to post. Returns the text.

    Platforms end up here for two different reasons and the handling is
    the same either way: either there is no compliant automated route
    (Facebook Groups), or automating it is not worth what it costs or
    risks (X's paid API, an account we would rather not get flagged).

    Not automating something should not mean losing it. The text is
    written where it can be found and pinged to Discord, so posting by
    hand is a paste rather than a rewrite from memory.
    """
    text = manual_post_text(platform, title, new_uploads)

    if queue_path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(queue_path)), exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M")
            with open(queue_path, "a", encoding="utf-8") as f:
                f.write(f"\n--- {platform} | {stamp} ---\n{text}\n")
        except OSError as exc:
            print(f"[Social] could not write the manual queue: {exc}")

    if notify_discord:
        webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        if webhook:
            try:
                _post_discord(
                    webhook,
                    f"📋 **Post this to {platform} by hand** "
                    f"(not automated on purpose):\n```\n{text}\n```")
            except Exception as exc:
                print(f"[Social] could not ping Discord: {exc}")

    print(f"[Social] {platform}: parked for manual posting -")
    for line in text.splitlines():
        print(f"           {line}")
    return text


def _publisher_for(platform: str, config: dict):
    """The publisher object for a guarded platform, or None if untyped."""
    if platform == "facebook":
        from publishers.facebook import FacebookPublisher
        return FacebookPublisher(config)
    if platform == "instagram":
        from publishers.instagram import InstagramPublisher
        return InstagramPublisher(config)
    return None


def announce_to_platforms(posting: dict, title: str, new_uploads: dict,
                          config: dict = None, dry_run: bool = False) -> list:
    """Announce to the guarded public platforms. Returns those that posted.

    Every platform here goes through PublishGuard first - kill switch,
    per-platform enable, rolling cap, spacing, circuit breaker - because
    the guard is the only component allowed to authorise a post. A
    platform that is off, capped or breakered is skipped with the guard's
    own reason rather than silently.

    Announcements are LINK posts: they point at the YouTube/Rumble URL
    that already exists, so nothing needs hosting. A platform that cannot
    carry a link post says so and is skipped without consuming its cap.
    """
    posting = posting or {}
    config = config or {}
    link = primary_link(new_uploads)
    if not posting:
        return []
    if not link:
        print("[Social] No usable link for this upload yet - nothing announced. "
              "(The platform completed the upload but did not report a URL.)")
        return []

    from publish_guard import PublishGuard

    guard = PublishGuard(posting, posting.get("state_path"))
    message = build_message(title, new_uploads)
    posted = []

    queue_path = posting.get("manual_queue_path", "")
    platforms = list((posting.get("platforms") or {}).keys()) or [
        "facebook", "instagram", "x"]

    for platform in platforms:
        if platform == "facebook_group":
            continue   # no route at all, and nothing useful to hand a human

        decision = guard.check(platform)
        if not decision:
            # A platform held back on purpose still has an announcement
            # worth making - it just gets made by a person. Anything else
            # blocked (capped, breakered, off) is genuinely skipped.
            if guard.is_manual_only(platform):
                queue_manual_post(platform, title, new_uploads, queue_path)
            else:
                print(f"[Social] {platform}: skipped - {decision.reason}")
            continue

        publisher = _publisher_for(platform, config)
        if publisher is not None and not getattr(publisher, "supports_link_posts", True):
            # Not a failure and not the guard's business: the platform has
            # no link-post endpoint at all, so recording it either way
            # would corrupt the cap history with a post that cannot happen.
            print(f"[Social] {platform}: skipped - no link-post endpoint "
                  "exists on this platform; needs a rendered clip and public "
                  "hosting to post at all")
            continue

        if dry_run:
            print(f"[Social] {platform}: WOULD POST -> {link}")
            posted.append(platform)
            continue

        try:
            if platform == "x":
                _post_twitter(message)
                ok = True
            else:
                ok = publisher.post_link(message, link)
        except ImportError:
            print(f"[Social] {platform}: skipped - required library not "
                  "installed (pip install tweepy)")
            continue
        except Exception as exc:
            ok = False
            print(f"[Social] {platform}: post raised {exc}")

        if ok:
            # Recorded immediately: an unrecorded post is one the cap does
            # not know about, which is what permits a burst.
            guard.record_post(platform)
            posted.append(platform)
            print(f"[Social] Posted announcement to {platform}.")
        else:
            failures = guard.record_failure(platform)
            print(f"[Social] {platform}: post failed "
                  f"({failures} consecutive - see the log above for why)")

    return posted


def announce_upload(features: dict, title: str, new_uploads: dict,
                    posting: dict = None, config: dict = None,
                    dry_run: bool = False) -> list:
    """Announce `new_uploads` ({platform: url}, only things uploaded THIS
    run - never pre-existing skips). Returns the channels that posted.

    Two groups, deliberately handled differently:

    - Discord and Reddit are configured under features.social_promoter and
      keep their existing behaviour. Discord is a webhook into the user's
      own server, not a public account, so there is nothing for the guard
      to protect against.
    - Facebook, Instagram and X are public accounts and go through
      PublishGuard, configured under `posting`. Passing `posting` is what
      turns them on; without it this behaves exactly as it did before.
    """
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

    # The legacy, unguarded X path. Skipped entirely when `posting` is
    # configured, because X is a platform there too and running both would
    # post the same announcement twice - once outside the cap.
    if features.get("twitter", False) and not posting:
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

    if posting:
        posted.extend(announce_to_platforms(posting, title, new_uploads,
                                            config, dry_run))

    return posted
