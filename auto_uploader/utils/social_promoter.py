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


# ═════════════════════════════════════════════════════════════════════════
# One switch for every channel
# ═════════════════════════════════════════════════════════════════════════

# Announcing lives in two config blocks - `features.social_promoter` for
# Discord/Reddit and `posting` for the guarded public accounts - which is
# right for configuring them individually and wrong for the question
# "announce everywhere". These functions answer that question in one call.

# Platforms that cannot be automated, and why. These stay manual no matter
# what the switch says, because the blocker is not a setting.
CANNOT_AUTOMATE = {
    "instagram":
        "Instagram has no link or text post. Every Content Publishing call "
        "needs a rendered clip on public hosting, so there is nothing to "
        "announce with until clips are hosted.",
    "facebook_group":
        "Facebook Groups have no publishing route that permits this, so "
        "group posts stay manual on purpose.",
}


def enable_all_announcements(features: dict, posting: dict) -> tuple:
    """Turn announcing on everywhere it can actually run.

    Returns (features, posting, notes) - copies, so config on disk is
    untouched - plus a line for every channel that stays manual and the
    reason it does. A switch that silently left three platforms off would
    be worse than no switch: the whole point is knowing where the post
    went.
    """
    features = dict(features or {})
    posting = dict(posting or {})
    notes = []

    features["enabled"] = True
    features["discord"] = True

    if not features.get("reddit_subreddit"):
        notes.append("reddit: needs features.social_promoter.reddit_subreddit "
                     "set to the subreddit to post in - there is nowhere to "
                     "post it otherwise.")
    # Reddit goes through the guard now, so the older unguarded praw path
    # must stay off or the same link is announced twice - once outside the
    # daily cap and the spacing, which is exactly the pattern Reddit's
    # spam filters act on.
    features["reddit"] = False if posting else bool(features.get("reddit_subreddit"))

    # X is left to the guarded path below when `posting` exists, so the
    # same announcement cannot go out twice - once outside the cap.
    features["twitter"] = False if posting else features.get("twitter", False)

    if not posting:
        notes.append("facebook/instagram/x: no `posting` block in config, so "
                     "only Discord and Reddit can be announced to.")
        return features, posting, notes

    posting["enabled"] = True
    platforms = {}
    for name, settings in (posting.get("platforms") or {}).items():
        settings = dict(settings or {})
        if name in CANNOT_AUTOMATE:
            # Still announced - by a person, from the queued text. Forcing
            # `enabled` here would only produce failed posts and trip the
            # circuit breaker.
            settings["enabled"] = False
            settings["manual_approval_only"] = True
            notes.append(f"{name}: stays manual - {CANNOT_AUTOMATE[name]}")
        else:
            settings["enabled"] = True
            settings["manual_approval_only"] = False
        platforms[name] = settings
    posting["platforms"] = platforms
    return features, posting, notes


def disable_all_announcements(features: dict, posting: dict) -> tuple:
    """Turn every announcement off in one call - Discord, Reddit, and the
    guarded public accounts - without editing config on disk.

    The kill switch file stops posting too, but it is a file that has to be
    remembered and removed. This is for one run.
    """
    features = dict(features or {})
    posting = dict(posting or {})
    features["enabled"] = False
    if posting:
        posting["enabled"] = False
    return features, posting, ["Announcements are off for this run."]


def _missing_credentials(platform: str, config: dict) -> list:
    """.env variables this platform cannot post without, or [].

    Checked BEFORE attempting a post so an unconfigured platform is
    skipped rather than counted as a failure. Import is local and
    defensive: a missing posting_status module must not stop announcing.
    """
    try:
        from utils.posting_status import missing_env
    except Exception:
        return []
    account = ""
    if platform == "reddit":
        account = str(((config or {}).get("features", {})
                       .get("social_promoter", {}) or {}).get("reddit_account", ""))
    try:
        return list(missing_env(platform, account))
    except Exception:
        return []


def _publisher_for(platform: str, config: dict):
    """The publisher object for a guarded platform, or None if untyped."""
    if platform == "facebook":
        from publishers.facebook import FacebookPublisher
        return FacebookPublisher(config)
    if platform == "instagram":
        from publishers.instagram import InstagramPublisher
        return InstagramPublisher(config)
    if platform == "reddit":
        from publishers.reddit import RedditPublisher
        return RedditPublisher(config)
    if platform == "youtube_shorts":
        from publishers.youtube_shorts import YouTubeShortsPublisher
        return YouTubeShortsPublisher(config)
    if platform.startswith("zernio"):
        from publishers.zernio import ZernioPublisher
        return ZernioPublisher(config, platform)
    return None


def announce_to_platforms(posting: dict, title: str, new_uploads: dict,
                          config: dict = None, dry_run: bool = False,
                          skip: tuple = ()) -> list:
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
    from publishers.errors import NotConfigured

    guard = PublishGuard(posting, posting.get("state_path"))
    message = build_message(title, new_uploads)
    posted = []

    queue_path = posting.get("manual_queue_path", "")
    platforms = list((posting.get("platforms") or {}).keys()) or [
        "facebook", "instagram", "x"]

    for platform in platforms:
        if platform == "facebook_group":
            continue   # no route at all, and nothing useful to hand a human
        if platform in skip:
            # Already handled elsewhere this run - a clip posted as a Reel
            # must not also be announced here as a link to itself.
            continue

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
        if publisher is None and platform != "x":
            # Reddit lands here: it is announced through praw on the
            # features.social_promoter path, so there is no publisher for
            # it in this one. Without this the code below called
            # post_link() on None, and the AttributeError was caught by
            # the generic handler and recorded as a failed post - three of
            # those trip the circuit breaker over a post never attempted.
            print(f"[Social] {platform}: skipped here - it has no publisher on "
                  "this path (Reddit is announced via features.social_promoter).")
            continue

        if publisher is not None and not getattr(publisher, "supports_link_posts", True):
            # Not a failure and not the guard's business: the platform has
            # no link-post endpoint at all, so recording it either way
            # would corrupt the cap history with a post that cannot happen.
            print(f"[Social] {platform}: skipped - no link-post endpoint "
                  "exists on this platform; needs a rendered clip and public "
                  "hosting to post at all")
            continue

        # Ask the publisher itself, not a central table: it owns which
        # credentials it needs. A publisher that does not answer (a test
        # double, a future platform) is treated as ready.
        ready = getattr(publisher, "ready", None)
        configured = True if ready is None else bool(ready())
        missing = _missing_credentials(platform, config) if not configured else []
        if not configured:
            # NOT a failure. The circuit breaker exists to stop hammering
            # an account that is rejecting posts; an unset .env variable
            # is a configuration problem, and counting it tripped the
            # breaker after three uploads - so filling the credentials in
            # correctly STILL left the platform blocked until someone
            # reset it by hand. That is what happened to Facebook.
            detail = (f"Set {', '.join(missing)} in .env"
                      if missing else "credentials are not usable")
            print(f"[Social] {platform}: skipped - not configured yet. "
                  f"{detail} (then: python main.py --posting-status --verify)")
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
        except NotConfigured as exc:
            # A missing scope refuses every post forever, so counting it
            # would trip the breaker and leave the platform blocked even
            # after the token is fixed. Not a failure - a setup step.
            print(f"[Social] {platform}: skipped - {exc}")
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


def clip_title(video_path: str) -> str:
    """A caption headline from the clip's filename.

    The recorder names clips "<name> <platform> clips <clip title>.mp4",
    so the interesting part is the tail - "ban that...", "who put stacks
    on slots". Stripping the prefix is what turns a filename into
    something that reads like a caption.
    """
    stem = os.path.splitext(os.path.basename(video_path or ""))[0]
    for marker in (" clips ", " twitch ", " youtube ",
                   "_clips_", "_twitch_", "_youtube_"):
        if marker in stem:
            stem = stem.split(marker, 1)[1]
    # Downloads are stored with ASCII-safe names, which means underscores
    # where the clip title had spaces. A caption should read as the title
    # did, not as the filename does.
    return " ".join(stem.replace("_", " ").split()).strip(" -.") or "Stackswopo"


def build_caption(template: str, video_path: str, title: str = "") -> str:
    """Fill the Instagram caption template. Never raises on a bad key."""
    headline = title or clip_title(video_path)
    if not template:
        return headline
    try:
        return template.format(title=headline)
    except (KeyError, IndexError):
        # A typo'd placeholder must not cost the post.
        return headline


def _vertical_copy(video_path: str, instagram_cfg: dict, clips_cfg: dict):
    """A full-bleed 9:16 re-frame, or (None, "") to post as-is.

    Returns (path, temp_path) - temp_path is what the caller deletes.
    """
    if not instagram_cfg.get("vertical", True):
        return video_path, ""
    try:
        from autoreel.clip_maker import make_vertical
        from autoreel.crop_strategy import resolve_crop_strategy
    except Exception:
        return video_path, ""

    # A clip out of ClipMaker is already 1080x1920. Re-framing it is a
    # second full encode that costs time and a generation of quality to
    # produce a file the same shape as the one it started from - and it
    # re-runs the crop, so a region profile would crop the crop.
    from utils.ffmpeg_tools import is_already_vertical

    if is_already_vertical(video_path):
        return video_path, ""

    strategy = resolve_crop_strategy(
        {"clips": clips_cfg or {}},
        (clips_cfg or {}).get("content_kind", "gameplay"))
    target = os.path.join(
        os.path.dirname(os.path.abspath(video_path)),
        f"_vertical_{os.path.basename(video_path)}")
    print(f"[Social] instagram: re-framing to 9:16 ({strategy} crop)...")
    made = make_vertical(video_path, target, strategy)
    if not made:
        # Letterboxed beats not posted.
        print("[Social] instagram: could not re-frame - posting as-is.")
        return video_path, ""
    return made, made


def post_clip_to_instagram(posting: dict, video_path: str, caption: str,
                           config: dict = None, dry_run: bool = False,
                           ignore_spacing: bool = False) -> bool:
    """Publish a clip to Instagram as a Reel. True if it went out.

    Instagram cannot carry a link announcement - it has no link post at
    all - so it is skipped everywhere else in this module. A CLIP is the
    one thing it can take, because a Reel is a video and the bytes can be
    uploaded directly.

    Behind the same guard as everything else: kill switch, cap, spacing,
    circuit breaker.
    """
    if not posting or not video_path or not os.path.isfile(video_path):
        return False

    from publish_guard import PublishGuard

    guard = PublishGuard(posting, posting.get("state_path"))
    allowed, reason = guard.can_post("instagram", ignore_spacing=ignore_spacing)
    if not allowed:
        print(f"[Social] instagram: {reason}")
        return False
    if ignore_spacing:
        print("[Social] instagram: posting now, ignoring the spacing rule "
              "(you asked for this one by hand). The daily cap still applies.")

    publisher = _publisher_for("instagram", config or {})
    if publisher is None or not getattr(publisher, "supports_reels", False):
        return False
    if not publisher.ready():
        print("[Social] instagram: skipped - not configured yet. Run: "
              "python main.py --setup-meta")
        return False

    if dry_run:
        print(f"[Social] instagram: WOULD post {os.path.basename(video_path)} "
              "as a Reel")
        return True

    instagram_cfg = (config or {}).get("instagram", {}) or {}
    caption = build_caption(instagram_cfg.get("caption_template", ""),
                            video_path) or caption
    upload_path, temp = _vertical_copy(video_path, instagram_cfg,
                                       (config or {}).get("clips", {}))

    print(f"[Social] instagram: uploading "
          f"{os.path.basename(video_path)} as a Reel...")
    try:
        ok = publisher.post_reel_from_file(
            upload_path, caption,
            share_to_feed=bool(instagram_cfg.get("share_to_feed", True)))
    except Exception as exc:
        ok = False
        print(f"[Social] instagram: Reel upload raised {exc}")
    finally:
        if temp:
            try:
                os.remove(temp)
            except OSError:
                pass
    guard.record_result("instagram", ok)
    print(f"[Social] instagram: {'posted a Reel' if ok else 'Reel failed'}.")
    return ok


def announce_upload(features: dict, title: str, new_uploads: dict,
                    posting: dict = None, config: dict = None,
                    dry_run: bool = False, all_uploads: dict = None,
                    clip_path: str = "", skip_platforms: tuple = ()) -> list:
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

    posted_extra = []
    if clip_path:
        # The one route Instagram has. Done first so a failure here still
        # leaves every other platform to announce normally.
        if post_clip_to_instagram(posting, clip_path,
                                  build_message(title, all_uploads or new_uploads),
                                  config, dry_run):
            posted_extra.append("instagram")

    # WHAT triggers an announcement and WHAT it says are different
    # questions. Only a real upload this run should trigger one - that is
    # what stops a re-run announcing the same video twice. But once it
    # fires, the post should name every place the stream is watchable,
    # including a platform that succeeded on an earlier run. Announcing
    # "it's on Rumble" while saying nothing about the YouTube copy that
    # already exists sends people to the smaller audience.
    announced = dict(all_uploads or {})
    announced.update(new_uploads)     # this run's URLs win on conflict

    message = build_message(title, announced)
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
        primary_url = primary_link(announced)
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
        posted.extend(announce_to_platforms(posting, title, announced,
                                            config, dry_run, skip_platforms))

    return posted + [p for p in posted_extra if p not in posted]
