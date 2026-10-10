"""
Clips that cannot post yet are KEPT, not dropped.

THE BUG THIS EXISTS TO FIX
--------------------------
Ten clips come out of a stream within a few minutes of each other, and
Instagram is spaced at one post every twenty-five minutes. The first clip
posted. The other nine asked the guard, were told "posted 2 min ago,
minimum is 25", and were thrown away - the function printed the reason and
returned False, and nothing ever looked at that clip again. A whole day's
output reduced to one Reel, with the spacing rule doing exactly what it
was told and the clips vanishing anyway.

Spacing is a WHEN, not a NO. So a clip the guard defers is written to the
job queue with its caption, and drained later when the wait has passed.
The queue already knew how to say this - `block()` records a guard refusal
without consuming a retry, and carries the time to come back - it just was
never wired to anything.

WHAT IS NOT QUEUED
------------------
Only timing gets deferred. A platform that is disabled, manual-only,
missing credentials, or sitting behind an open circuit breaker has no
retry time to wait for, and queueing it would build a pile of clips that
can never go out and would then all fire at once the moment it was fixed.
Those are skipped with the guard's own reason, exactly as before.
"""

from __future__ import annotations

import re
import json
import os
import sys
from typing import Optional

# auto_uploader/ is the import root for publishers/ and publish_guard.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Platforms that can carry a video clip. Reddit and X take links, which
# is a different path (announce_to_platforms) with a different cadence.
# YouTube is last on purpose. It is the strictest destination here about
# volume and repetition, and a channel is far harder to get back than a
# post is to delete - so it posts only after the others have, and only
# once its own guard, cap and spacing allow it.
# The Postproxy routes FIRST, then the per-platform publishers.
#
# Order matters: a Postproxy route runs first so that what it reaches can
# be skipped below rather than posted twice. The direct instagram and
# facebook publishers are then the fallback for a clip Postproxy did not
# get there (that account not connected on Postproxy, or a failure).
#
# postproxy_youtube sits with YouTube at the end, for the reason above.
CLIP_PLATFORMS = ("postproxy_instagram", "postproxy_tiktok",
                  "postproxy_facebook", "postproxy_x",
                  "instagram", "facebook",
                  "tiktok", "zernio_twitter", "zernio_tiktok", "buffer_x",
                  "buffer_tiktok", "postproxy_youtube", "youtube_shorts")

# Each Postproxy route posts to one platform, under the name the rest of
# this file uses for it. Its caption, tags, promo line and audio rules
# are that platform's - config.json's "tiktok" block is what TikTok gets,
# whichever route carries it.
ROUTE_BASE = {
    "postproxy_instagram": "instagram",
    "postproxy_tiktok": "tiktok",
    "postproxy_facebook": "facebook",
    "postproxy_x": "x",
    "postproxy_youtube": "youtube_shorts",
}

# Routes whose result says which platforms they reached. What each one
# actually reached is read back from its own result (platforms_reached)
# and skipped for every route and publisher after it.
FAN_OUT_ROUTES = tuple(ROUTE_BASE)

# Routes that upload to YouTube. They wait while YouTube is refusing
# uploads on the main channel (see youtube_hold_s).
YOUTUBE_ROUTES = ("youtube_shorts", "postproxy_youtube")


# Direct routes that post AS another platform: same caption rules, same
# audio rules (config.json's "x" block), one account. Not fan-out routes.
ALIAS_BASE = {"buffer_x": "x", "buffer_tiktok": "tiktok"}


def base_platform(platform: str) -> str:
    """The platform a route posts to: postproxy_tiktok -> tiktok."""
    return ROUTE_BASE.get(platform) or ALIAS_BASE.get(platform, platform)


# The account each direct (non-Postproxy) publisher posts to.
DIRECT_BASE = {"zernio_tiktok": "tiktok", "zernio_twitter": "x",
               "buffer_x": "x", "buffer_tiktok": "tiktok"}


# Platforms whose CAPTION text goes through the profanity filter. Rumble
# is deliberately absent - it is the uncensored channel, and the titles
# there are the line actually spoken, which is the point.
CLEAN_TEXT_PLATFORMS = ("instagram", "facebook", "tiktok", "x",
                        "youtube_shorts", "zernio_twitter", "zernio_tiktok")

# Where ordinary swearing stays in the caption, as spoken. X allows it;
# slurs are still taken out there - X's hateful conduct rules cover them,
# and the account is what is at risk. The audio follows the same split
# through the x block's censor_uploads ("slurs").
SWEARING_OK = ("x",)


def _slurs_only_checker():
    """The compliance list cut down to hate speech, or None."""
    try:
        from autoreel.compliance import ComplianceEngine

        return ComplianceEngine(only_categories=("hate_speech",))
    except Exception:
        return None


# A blocked clip is worth keeping for about a day. Past that the stream it
# came from is stale and posting it is worse than not.
MAX_DEFERRED_AGE_S = 36 * 3600


def _journal(config: dict, status: str, platform: str, clip_path: str,
             detail: str = "") -> None:
    """One line in logs/clips.log. Never raises, never blocks a post."""
    try:
        from utils.clip_log import record

        record((config or {}).get("logs_folder", "logs"), platform, status,
               os.path.splitext(os.path.basename(clip_path))[0], detail)
    except Exception:
        pass


def _queue(posting: dict):
    from job_queue import JobQueue

    return JobQueue(path=(posting or {}).get("queue_path") or "./clip_jobs.json")


def _record_reached(queue, job_id: str, reached: set) -> None:
    """Keep, on the job itself, which platforms a fan-out post reached -
    so a later pass over the same clip knows exactly what not to post."""
    job = queue.get(job_id)
    if job is None:
        return
    job.extra = dict(job.extra or {}, reached=sorted(reached))
    queue._save()


def _reach_path(posting: dict) -> str:
    queue_path = (posting or {}).get("queue_path") or "./clip_jobs.json"
    return os.path.join(os.path.dirname(os.path.abspath(queue_path)),
                        "fanout_reach.json")


def remembered_reach(posting: dict, route: str) -> set:
    """Which platforms this fan-out route reached the last time it posted.

    a fan-out route posts to whatever is connected on its side, which this
    project's config cannot see - so the only honest answer to "what will
    it cover when its wait is up" is what it covered last time.
    """
    try:
        with open(_reach_path(posting), encoding="utf-8") as handle:
            return set(json.load(handle).get(route) or ())
    except (OSError, ValueError, AttributeError):
        return set()


def _remember_reach(posting: dict, route: str, reached: set) -> None:
    path = _reach_path(posting)
    try:
        with open(path, encoding="utf-8") as handle:
            known = json.load(handle)
    except (OSError, ValueError):
        known = {}
    if not isinstance(known, dict):
        known = {}
    known[route] = sorted(reached)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(known, handle, indent=2)
    except OSError:
        pass


# Where a clip waiting in the queue is kept. The watch-folder copy does
# not survive: once Rumble has it, cleanup moves it to uploaded/ and
# keep_uploaded_videos deletes all but the newest few. A clip queued for
# Instagram 45 minutes later was then "no longer on disk" and dropped -
# which is why each stream reached the other platforms with ONE clip.
HELD_FOLDER = "clip_queue_files"


def _held_dir(posting: dict) -> str:
    queue_path = (posting or {}).get("queue_path") or "./clip_jobs.json"
    return os.path.join(os.path.dirname(os.path.abspath(queue_path)),
                        HELD_FOLDER)


def _hold(posting: dict, video_path: str) -> str:
    """A copy of the clip the queue can rely on; the original path if a
    copy cannot be made."""
    import shutil

    folder = _held_dir(posting)
    target = os.path.join(folder, os.path.basename(video_path))
    if os.path.abspath(video_path) == os.path.abspath(target):
        return target
    try:
        os.makedirs(folder, exist_ok=True)
        if not (os.path.isfile(target)
                and os.path.getsize(target) == os.path.getsize(video_path)):
            shutil.copy2(video_path, target)
        return target
    except OSError:
        return video_path


def _hold_for_later(queue, job_id: str, posting: dict,
                    video_path: str) -> None:
    """Point a job that will run later at a held copy of its clip."""
    job = queue.get(job_id)
    if job is None:
        return
    held = _hold(posting, video_path)
    if held != job.clip_path:
        job.clip_path = held
        queue._save()


def _release_held(queue, posting: dict) -> None:
    """Delete held copies no waiting job needs any more."""
    from job_queue import ACTIVE_STATES

    folder = _held_dir(posting)
    if not os.path.isdir(folder):
        return
    wanted = {os.path.abspath(job.clip_path)
              for job in queue.list_jobs(ACTIVE_STATES)}
    for name in os.listdir(folder):
        path = os.path.abspath(os.path.join(folder, name))
        if path not in wanted:
            try:
                os.remove(path)
            except OSError:
                pass


def _find_moved(job, config: dict) -> str:
    """Where a queued clip's file went, if it was moved rather than
    deleted (watch folder -> uploaded/)."""
    name = os.path.basename(job.clip_path)
    for folder in (config or {}).get("note_folders", ()) or ():
        candidate = os.path.join(folder, name) if folder else ""
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def _publisher(platform: str, config: dict):
    from utils.social_promoter import _publisher_for

    return _publisher_for(platform, config or {})


# Lines that were removed from the shipped caption template and must not
# come back from an old config.json. Matched loosely because the live
# file has them dressed in emoji.
#
# "LINK IN BIO" is the one that mattered: there IS no link in the bio for
# these clips, the two channels are named on the next two lines, and it
# is the single most recognisable mark of an automated repost account.
_DEAD_TEMPLATE_LINES = (
    "link in bio",
    "monkey vids + full stream",
)


def clean_template(template: str) -> str:
    """A caption template with the lines nobody wants any more taken out.

    Whole lines, not substrings: cutting a phrase out of the middle
    leaves a sentence that reads worse than the one it replaced.
    """
    kept = [line for line in str(template or "").splitlines()
            if not any(dead in line.lower() for dead in _DEAD_TEMPLATE_LINES)]
    # Collapse the blank line the removal leaves behind, so the caption
    # does not open with a gap where the slogan used to be.
    cleaned = "\n".join(kept)
    while "\n\n\n" in cleaned:
        cleaned = cleaned.replace("\n\n\n", "\n\n")
    return cleaned.strip("\n")


def _subject_note(video_path: str, folders=()) -> str:
    """What the clip IS, written beside it when it was cut.

    The stream's title and the framing profile - "monkey", "gta" - which
    are known at cut time and gone by post time. Without this a Monkey
    clip called "Stackswopo Love Yall - Clip 02" can only be given the
    generic tags, because nothing in its name says what is in it.
    """
    stem = os.path.splitext(video_path or "")[0]
    # The temp 9:16 copy is "_vertical_<clip>.mp4"; the note belongs to
    # the clip, not to the copy.
    plain = os.path.join(os.path.dirname(stem),
                         re.sub(r"^_?vertical[_\s]+", "",
                                os.path.basename(stem), flags=re.I))
    roots = [stem, plain]
    for folder in folders or ():
        if folder and os.path.isdir(folder):
            roots.append(os.path.join(folder, os.path.basename(plain)))
    for candidate in [root + "_subject.txt" for root in roots]:
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                found = handle.read().strip()
            if found:
                return found
        except OSError:
            continue
    return ""


def _with_tags(caption: str, tags, platform: str) -> str:
    """Tags after the line, where the platform takes them.

    The model is told not to write its own - hashtags_for picks them from
    the CLIP and sizes them per platform, and a model inventing tags
    alongside that produced twenty on a post that may carry two.
    """
    from utils.social_promoter import TAG_LIMITS

    if not tags or TAG_LIMITS.get(platform, 0) <= 0:
        return caption
    # hashtags_for returns ONE STRING - "#stackswopo #funnymoments" - and
    # iterating a string yields characters, so this posted
    #     # #s #t #a #c #k #s #w #o #p #o # # #f #u #n #n #y
    # on every Instagram caption for a day. A sequence of tags is
    # accepted too, because callers reasonably expect either.
    if isinstance(tags, str):
        line = tags.strip()
    else:
        line = " ".join(str(t) if str(t).startswith("#") else f"#{t}"
                        for t in tags if str(t).strip())
    return f"{caption}\n\n{line}" if line else caption


def _model_caption(platform: str, video_path: str, headline: str,
                   config: dict) -> str:
    """This platform's caption, written for it. "" when none can be had.

    Cached beside the clip: a clip is offered to each platform hours
    apart, and writing them per drain would be one API call per platform
    after all - which is the thing writing them together avoids.
    """
    if not (config or {}).get("model_captions", True):
        return ""
    try:
        from autoreel.llm_captions import (PLATFORM_BRIEFS, cached, remember,
                                           write_captions)
    except Exception:
        return ""
    # X's brief was written for the old Zernio route; it is X's all the
    # same.
    platform = {"x": "zernio_twitter"}.get(platform, platform)
    if platform not in PLATFORM_BRIEFS:
        return ""

    have = cached(video_path)
    if platform in have:
        return have[platform]

    folders = tuple((config or {}).get("note_folders", ()) or ())
    spoken = ""
    try:
        from utils.social_promoter import spoken_line

        spoken = spoken_line(video_path, folders) or ""
    except Exception:
        spoken = ""

    # What is actually SAID in the clip, from its own transcript. The
    # model used to get only the headline - twice - and when the headline
    # was the filename it wrote about the filename.
    said = clip_transcript(video_path, config)
    # And what is on SCREEN: stills from across the clip, so the caption
    # is written from the video and the audio together.
    seen = clip_frames(video_path)
    if not (spoken or said):
        # Nothing about the clip itself to go on. A caption invented from
        # the stream's name is worse than the plain template.
        return ""

    # The same provider choice as the clip picks (clips.llm_provider).
    # Without it the captions asked Gemini first however the channel was
    # set up, and sat through its 503 retries on every clip.
    clips = (config or {}).get("clips", {}) or {}
    written = write_captions(spoken or "", said or spoken,
                             sorted(PLATFORM_BRIEFS),
                             provider=str(clips.get("llm_provider", "")
                                          or ""),
                             model=str(clips.get("llm_model", "") or ""),
                             frames=seen)
    if not written:
        return ""
    have.update(written)
    remember(video_path, have)
    return have.get(platform, "")


# Where to send people, per destination.
#
# Stackswopo's clips do numbers on TikTok in other people's hands - the
# reposts run 45K to 200K likes - and none of those posts sends anyone to
# the Rumble channel. A clip that travels without a destination is
# somebody else's traffic.
#
# TikTok does not make a caption link clickable, so this is written to be
# read and typed, not tapped. Kept out of X by default: 280 characters is
# the whole budget there and a URL eats a fifth of it, and X demotes posts
# carrying an external link.
#
# {rumble} is the configured rumble.channel_url. An empty promo line, or
# a channel_url nobody has set, adds nothing at all.
# What each destination gets when config.json says nothing.
#
# auto_uploader/config.json is GITIGNORED - it is the live file, with the
# credentials paths and the per-machine folders in it - so a block added
# to the shipped template never reaches a machine that already has one.
# Every setting that only exists in the template is a setting that works
# here and nowhere real.
#
# So the default lives in code. A config that wants something else says
# so and wins; a config that says nothing still gets a working pointer.
DEFAULT_PROMOS = {
    "tiktok": "Full uncensored streams on Rumble: {rumble}",
    "twitter": "",
}


def promo_line(platform: str, config: dict) -> str:
    """The 'where to find more' line for this destination, or ''."""
    settings = (config or {}).get("zernio", {}) or {}
    promos = settings.get("promote", {})
    if not isinstance(promos, dict):
        promos = {}
    key = platform[len("zernio_"):] if platform.startswith("zernio_") else platform
    if key in promos:
        template = str(promos.get(key) or "").strip()
    else:
        template = str(DEFAULT_PROMOS.get(key, "") or "").strip()
    if not template:
        return ""
    channel = str(((config or {}).get("rumble", {}) or {}).get(
        "channel_url", "") or "").strip()
    if "{rumble}" in template and not channel:
        # Naming a channel nobody configured would post the literal
        # "{rumble}" under every clip.
        return ""
    return template.replace("{rumble}", channel).strip()


def with_promo(caption: str, platform: str, config: dict,
               limit: int = 0) -> str:
    """Append the promo line if it fits, and if it is not already there.

    Never truncates the caption to make room: the line that was written
    for the clip earns the post, and the pointer is the extra.
    """
    line = promo_line(platform, config)
    if not line:
        return caption
    if line.lower() in (caption or "").lower():
        return caption
    joined = f"{caption.rstrip()}\n\n{line}" if caption.strip() else line
    if limit and len(joined) > int(limit):
        return caption
    return joined


def caption_for(platform: str, video_path: str, fallback: str,
                config: dict) -> str:
    """The caption this platform posts with.

    Instagram has a studied template that matches how the account already
    writes; Facebook falls back to it rather than inventing a second
    voice, because the same clip reading two different ways across two
    Pages is what looks automated.
    """
    from utils.social_promoter import build_caption, clip_title, hashtags_for

    # A Postproxy route writes exactly what its platform would.
    platform = base_platform(platform)

    # Every zernio_* destination shares one config block: they are one
    # service with several accounts, and a per-destination lookup would
    # find nothing and quietly fall through to Instagram's template.
    key = "zernio" if platform.startswith("zernio") else platform
    settings = (config or {}).get(key, {}) or {}
    template = settings.get("caption_template", "")
    if not template:
        template = ((config or {}).get("instagram", {}) or {}).get(
            "caption_template", "")
    # config.json is not tracked, so it is whatever it was the day it was
    # written - and a line deleted from the shipped template stays in the
    # live one forever. "LINK IN BIO" was removed here and kept posting
    # for days because nothing rewrote the file anyone actually runs.
    #
    # Cleaning the template on the way past means a dead line dies
    # everywhere at once, without asking anybody to go and edit JSON.
    template = clean_template(template)
    # Tags are picked from the CLIP and sized to the platform: a Monkey
    # clip must not be tagged #gtarp, and the count that helps on
    # Instagram gets a post demoted on X.
    # Where a clip's notes might be when they are not beside the file
    # being posted - see _clip_config's note_folders.
    folders = tuple((config or {}).get("note_folders", ()) or ())
    headline = clip_title(video_path, folders)
    # Matched against the headline AND the filename, because the headline
    # is usually the line spoken in the clip and people do not announce
    # what app they are on. "Stackswopo Love Yall - Clip 03" says nothing
    # a tag can be picked from, while the VOD it was cut out of is called
    # "monkey_n_gamble_howl" - which says exactly what it is. Using only
    # the headline meant the specific tags almost never fired and every
    # clip went out with the generic fillers.
    subject = f"{headline} {os.path.basename(video_path or '')} " \
              f"{_subject_note(video_path, folders)}"
    tags = hashtags_for(subject, platform,
                        settings.get("max_hashtags"))
    # A line written for THIS platform, if one can be had. One template
    # posted to four platforms is the most visible mark of an automated
    # account, and it is what every ranking is tuned to find - X demotes
    # a dozen hashtags and cuts off at 280, Instagram rewards them,
    # Facebook reads either as spam, a Short wants a title.
    #
    # Falls straight back to the template on anything: a caption that
    # reads a bit generic is a bad post, and no caption is no post.
    written = _model_caption(platform, video_path, headline, config)
    if written:
        caption = _with_tags(written, tags, platform)
    else:
        caption = build_caption(template, video_path, tags=tags,
                                folders=folders) or fallback

    # Instagram and YouTube apply their rules to the TEXT as well as the
    # video. Rumble is not in this set on purpose: that channel is the
    # uncensored one, and running the filter over it would flatten the
    # exact thing its audience is there for.
    if platform in CLEAN_TEXT_PLATFORMS:
        from autoreel.safe_text import clean_lines

        caption = clean_lines(caption, _slurs_only_checker()
                              if platform in SWEARING_OK else None)

    # Facebook makes a link in a caption clickable: the full stream this
    # clip was cut from, when it is up, in place of the channel pointer
    # (utils/stream_links). Instagram, TikTok and X do not, or demote a
    # post that has one, so they keep their own line.
    if platform == "facebook":
        try:
            from utils.stream_links import link_for_clip

            full = link_for_clip(video_path, "facebook", folders)
        except Exception:
            full = ""
        if full:
            return f"{caption.rstrip()}\n\n▶ Full stream: {full}"

    # Last, so the pointer survives the cleaner and the tag builder and
    # is measured against what actually gets posted.
    return with_promo(caption, platform, config,
                      limit=_char_limit(platform))


def _char_limit(platform: str) -> int:
    """What this destination will keep, 0 for no limit worth enforcing."""
    if not platform.startswith("zernio"):
        return 0
    try:
        from publishers.zernio import CHAR_LIMITS, DESTINATIONS
    except Exception:
        return 0
    return int(CHAR_LIMITS.get(DESTINATIONS.get(platform, ""), 0) or 0)


# Platforms whose CLIP AUDIO gets bleeped before it is posted.
#
# Only where a strike is the cost of being wrong. YouTube demonetises and
# age-restricts over spoken language and a channel is far harder to get
# back than a post is to delete. Instagram does not, which is why it is
# absent - censoring a clip for it removes the moment and buys nothing.
# Rumble is the uncensored channel by design.
#
# The TEXT of a caption is cleaned for more platforms than this; see
# CLEAN_TEXT_PLATFORMS. Cleaning words on screen is free, and re-encoding
# audio is not.
# "all"   - every flagged word, which is what YouTube's rules need.
# "slurs" - ONLY the hate-speech category, leaving ordinary swearing.
# False   - nothing.
#
# Instagram removed a clip under HATEFUL CONDUCT while the same account's
# ordinary swearing broke nothing. Those are different policies and they
# need different answers: bleeping every swear for Instagram would
# flatten the voice the channel is there for and buy nothing, while
# leaving a slur in is how an account gets taken away rather than a post
# deleted.
#
# Rumble is absent on purpose. It is the uncensored channel, that is the
# whole point of the split, and its audience is there for exactly what
# the other platforms will not take.
# What each platform actually bleeps.
#
# Every one of these is "slurs" now. Shorts and TikTok were "all", which
# muted ordinary swearing too - and on a channel whose speech is mostly
# ordinary swearing that is a mute every few seconds. One real clip
# logged 13 hate_speech hits and another 7 profanity hits; under "all"
# every one of those became a hole in the audio. The clips came back
# unwatchable, which is worse for reach than the language ever was.
#
# The risk this trades away is real and worth naming: YouTube can
# demonetise over language and TikTok's For You eligibility discourages
# it. Neither BANS it - slurs are the line that actually costs a
# channel, and that line is still held here.
CENSOR_AUDIO_DEFAULTS = {
    # Shorts earn from the Shorts ad pool on the same guidelines.
    "youtube_shorts": "ad_safe",
    "instagram": "slurs",
    "facebook": "slurs",
    "zernio_twitter": "slurs",
    "zernio_tiktok": "slurs",
    # X through Postproxy (postproxy_x). The Postproxy routes take their
    # platform's entry here - see base_platform.
    "x": "slurs",
    # tiktok (standalone, via tiktok_free/tiktok_api_client) is not wired
    # through the clip queue today, so this is kept only as the default a
    # caller reaches for by name when wiring it up. "slurs" here matches
    # the rest of the short-form table rather than letting a future call
    # site inherit False and ship the original to TikTok uncensored.
    "tiktok": "slurs",
}

# Which compliance categories each mode bleeps. Empty tuple = all.
# "all" is every category the compliance engine knows; "slurs" is
# hate speech only. Public because the full-VOD upload path in main.py
# resolves the same setting - a stream and a clip cut from that stream
# disagreeing about what counts would be indefensible.
# "ad_safe" - YouTube, heavily guarded: every category and every swear
# word, slurs included, EXCEPT the mild words YouTube's advertiser-
# friendly guidelines allow (compliance.MILD_WORDS). Anyone who wants it
# uncensored has Rumble.
CENSOR_SCOPES = {"all": (), "slurs": ("hate_speech",), "ad_safe": ()}


def scope_allow(mode: str) -> tuple:
    """Words a scope leaves alone even though a list flags them."""
    if str(mode or "").strip().lower() != "ad_safe":
        return ()
    from autoreel.compliance import MILD_WORDS

    return MILD_WORDS

# The old private name, kept because tests and callers import it.
_CENSOR_SCOPES = CENSOR_SCOPES


def scope_categories(mode: str) -> tuple:
    """Categories to bleep for a scope name. Unknown names bleep all.

    Falling back to "all" on a typo is deliberate: a misspelled scope
    should over-censor and be noticed, not silently publish a slur.
    """
    return CENSOR_SCOPES.get(str(mode or "").strip().lower(), ())


def _hold_if_slurs(platform: str, video_path: str, config: dict) -> None:
    """Raise HeldBack when this clip must not go to a social platform."""
    held = _slur_heavy(video_path, config)
    if not held:
        return
    from publishers.errors import HeldBack

    print(f"[Clips] {platform}: held back - {held}. Rumble still gets it; "
          f"post it by hand if you judge it fine.")
    raise HeldBack(f"held: {held}")


def _censored_clip(platform: str, video_path: str, config: dict) -> tuple:
    """(path to post, temp path to delete) for this platform's rules.

    Clips are cut from the RAW stream - every call site passes the
    original video, not the censored copy - and until now nothing
    bleeped them afterwards either. So a Short went to YouTube carrying
    whatever was actually said, on a channel where that is a strike.

    Returns the original and "" whenever censoring is off, unavailable,
    or finds nothing, so this can never be the reason a clip fails.
    """
    platform = base_platform(platform)
    # Checked HERE, right before each post, and not only when the clip
    # is first offered: a fresh clip has no transcript of its own until
    # its censor pass below writes one, so the check at offer time saw
    # nothing and passed it - and the queue's later post never looked.
    # That is how "18 48 Live 02 - Clip 01" (a hard-R and an f-slur,
    # muted) reached Shorts, Instagram, Facebook, X and TikTok on 10-09.
    _hold_if_slurs(platform, video_path, config)
    settings = (config or {}).get(platform, {}) or {}
    wanted = settings.get("censor_uploads")
    if wanted is None:
        wanted = CENSOR_AUDIO_DEFAULTS.get(platform, False)
    if not wanted:
        return video_path, ""
    # True is the old spelling of "all", kept working because it is what
    # any config.json already in the wild says.
    mode = "all" if wanted is True else str(wanted)
    scope = _CENSOR_SCOPES.get(mode, ())
    allow = scope_allow(mode)

    general = (config or {}).get("general", {}) or {}
    try:
        from utils.censor import censor_video

        result = censor_video(
            video_path, general.get("censored_folder") or "censored",
            # large-v3-turbo, not base: the posting queue is not always
            # handed the "general" block, and base on shouted gameplay
            # made up strings of swears ("fuck fuck fuck ...") that then
            # got muted - a Short went out with its audio mostly gone.
            # A clip is under a minute; turbo on the GPU is seconds.
            model_name=general.get("censor_model") or "large-v3-turbo",
            bleep_method=general.get("censor_bleep_method", "silence"),
            custom_words=tuple(general.get("censor_custom_words", ()) or ()),
            device=general.get("censor_device") or None,
            padding_ms=int(general.get("censor_padding_ms", 250)),
            # False - must match utils/config.py's GeneralConfig default
            # exactly. This read a raw dict straight off config.json
            # instead of the typed AppConfig, and defaulted to True here
            # while config.py had already been fixed to default to
            # False - so a config.json missing the key (any file
            # written before the setting existed, which is every one
            # already on disk, since config.json is gitignored and
            # never gets this fix through a pull) got word-level mutes
            # on the FULL STREAM and whole-SENTENCE mutes on every CLIP
            # cut from it, from the exact same source audio. Reported
            # back in those words: "the original audio sounds fine and
            # caption fine" (the typed-config path, already correct)
            # against clips that were not (this path, still wrong).
            mute_whole_segment=bool(
                general.get("censor_mute_whole_segment", False)),
            only_categories=scope,
            allow_words=allow)
    except Exception as exc:
        # A clip that cannot be censored must not go out UNcensored to a
        # platform that asked for it - that is the one failure worth
        # losing the post over.
        print(f"[Clips] {platform}: could not censor the clip ({exc}) - "
              f"not posting it there.")
        return "", ""

    # The censor pass has just written the clip's transcript.
    try:
        _hold_if_slurs(platform, video_path, config)
    except Exception:
        made = getattr(result, "output_path", "") or ""
        if made and made != video_path:
            try:
                os.remove(made)
            except OSError:
                pass
        raise
    made = getattr(result, "output_path", "") or video_path
    if made != video_path:
        count = getattr(result, "violation_count", 0)
        print(f"[Clips] {platform}: bleeped {count} word(s) before posting.")
        return made, made
    return video_path, ""


def _posted_summary(posted) -> str:
    """One readable line about a post, for the console.

    The whole reply was printed - account e-mail, internal ids and
    signed file URLs included - into a log that gets screenshotted and
    pasted into chats.
    """
    if not isinstance(posted, dict):
        return str(posted)
    lines = []
    for entry in posted.get("results") or ():
        if not isinstance(entry, dict):
            continue
        where = entry.get("platform") or "?"
        if entry.get("account"):
            where += f" ({entry['account']})"
        state = "ok" if entry.get("success") else "FAILED"
        url = entry.get("post_url") or ""
        lines.append(f"{where} {state}{' ' + url if url else ''}")
    return "; ".join(lines) or "accepted"


def publish(platform: str, video_path: str, caption: str,
            config: dict, dry_run: bool = False,
            detail: Optional[dict] = None) -> bool:
    """Actually post one clip. No guard, no queue - callers do that.

    Raises NotConfigured when the platform refuses for a reason no retry
    can fix - a missing token scope, most often.

    `detail`, when a caller passes a dict, is filled in for a fan-out
    route with `detail["covers"]` - the platforms Postproxy's OWN, polled
    status says actually succeeded (see publishers.postproxy.
    platforms_reached), not a guess. offer() reads it to decide which
    per-platform publishers are already covered; every other caller can
    safely ignore `detail` and nothing changes for them.
    """
    from publishers.errors import NotConfigured, PermanentlyRejected
    if not os.path.isfile(video_path):
        print(f"[Clips] {platform}: the clip is gone: {video_path}")
        return False

    publisher = _publisher(platform, config)
    if publisher is None:
        return False
    ready = getattr(publisher, "ready", None)
    if ready is not None and not ready():
        return False

    # YouTube takes the clip as an ordinary upload and works out that it
    # is a Short from the aspect and the length. There is no Reels
    # container to start, and no re-encode: the file this pipeline makes
    # is already 1080x1920, which is the only thing YouTube is looking
    # at.
    if hasattr(publisher, "post_clip"):
        from utils.keep_awake import KeepAwake

        upload, temporary = (video_path, "") if dry_run else \
            _censored_clip(platform, video_path, config)
        if not upload:
            return False
        try:
            # Awake for the post itself: an upload cut off by sleep is
            # a clip half-sent. Not for the queue check around it.
            with KeepAwake("posting a clip"):
                posted = publisher.post_clip(upload, caption, dry_run)
        except (NotConfigured, PermanentlyRejected):
            # Both mean "a retry cannot help", for different reasons.
            # Neither may be swallowed by the catch-all below.
            raise
        except Exception as exc:
            print(f"[Clips] {platform}: {exc}")
            return False
        finally:
            if temporary and temporary != video_path:
                try:
                    os.remove(temporary)
                except OSError:
                    pass

        # A fan-out route's ack is not the outcome. Its polled
        # per-platform results are: "posted" means at least one platform
        # genuinely succeeded, not merely that the file was accepted.
        reached: set = set()
        if platform in FAN_OUT_ROUTES:
            try:
                from publishers.postproxy import platforms_reached

                reached = platforms_reached(posted)
            except Exception:
                reached = set()
            if detail is not None:
                detail["covers"] = reached
            # Only tighten the verdict when there IS something to read a
            # verdict from - an old-shaped response (no `results` at
            # all: a dry run, an SDK reply that predates polling) falls
            # back to the previous "the ack means it worked" behaviour
            # rather than being newly, wrongly marked a failure for a
            # question this cannot yet answer.
            if isinstance(posted, dict) and posted.get("results"):
                posted = posted if reached else None

        if posted:
            print(f"[Clips] {platform}: {_posted_summary(posted)}")
            # Where it went, joined to why it was cut. This is the half
            # of the loop that makes the numbers mean anything later.
            from autoreel.memory import remember_post

            remember_post(config, video_path, platform, str(posted))
        return bool(posted)

    if not getattr(publisher, "supports_reels", False):
        return False

    if dry_run:
        print(f"[Clips] {platform}: WOULD post "
              f"{os.path.basename(video_path)} as a Reel")
        return True

    from utils.social_promoter import _vertical_copy

    settings = (config or {}).get(platform, {}) or {}

    # Censor BEFORE the re-frame, so the vertical copy is made from the
    # censored audio rather than the raw clip.
    #
    # This branch used to skip censoring entirely: _censored_clip ran only
    # for publishers with post_clip (Shorts), while Instagram and Facebook
    # go out as Reels and came straight off the raw cut. CENSOR_AUDIO_DEFAULTS
    # has said "slurs" for both of them the whole time and nothing read it
    # here, so a slur that Shorts muted went to Instagram intact - which is
    # how a Reel came down for hateful conduct.
    censored, censored_temp = _censored_clip(platform, video_path, config)
    if not censored:
        return False

    upload_path, temp = _vertical_copy(censored, settings,
                                       (config or {}).get("clips", {}) or {})
    # Facebook answers HTTP 413 to a Reel over ~100 MB (2026-10-08).
    from utils.fit_size import fit

    fitted, fit_temp = fit(upload_path)
    if fit_temp:
        if temp and temp not in (video_path, censored):
            try:
                os.remove(temp)
            except OSError:
                pass
        upload_path, temp = fitted, fit_temp
    print(f"[Clips] {platform}: uploading "
          f"{os.path.basename(video_path)} as a Reel...")
    from utils.keep_awake import KeepAwake

    try:
        with KeepAwake("posting a Reel"):
            ok = bool(publisher.post_reel_from_file(
                upload_path, caption,
                share_to_feed=bool(settings.get("share_to_feed", True))))
    except (NotConfigured, PermanentlyRejected):
        # Re-raised for the caller to treat as "not set up yet" or "this
        # video will never be accepted" rather than a failed post - see
        # publishers/errors. The catch-all below must not see either.
        raise
    except Exception as exc:
        ok = False
        print(f"[Clips] {platform}: Reel upload raised {exc}")
    finally:
        for leftover in (temp, censored_temp):
            # Never the caller's own file: _vertical_copy returns "" when
            # the clip was already 9:16, and _censored_clip returns "" when
            # there was nothing to censor.
            if leftover and leftover != video_path:
                try:
                    os.remove(leftover)
                except OSError:
                    pass
    return ok


def _clip_score(video_path: str) -> float:
    """How highly the clipper rated this clip (from its _source.json
    note), so a capped platform posts the best clips first. 0 if unknown."""
    try:
        from utils.stream_links import source_of
        return float((source_of(video_path) or {}).get("score") or 0.0)
    except Exception:
        return 0.0


def clip_key(video_path: str) -> str:
    """What makes two paths the SAME clip.

    The queue matched on the exact path string, and one clip legitimately
    has more than one: it is offered as `watch_folder/X.mp4` when it is
    already 9:16, and as `censored/_vertical_X.mp4` when a re-frame
    happened. Two paths meant two jobs meant two uploads - which is how
    "Clip 01" appears twice on the Shorts channel, byte for byte
    identical, eighteen seconds each.

    The name without its folder or the re-frame prefix is enough here:
    clip filenames already carry the stream and the index, so two clips
    sharing one are the same clip.
    """
    from utils.social_promoter import plain_clip_name

    stem, ext = os.path.splitext(os.path.basename(video_path or ""))
    # The censored copy is the same clip too - see plain_clip_name.
    return (plain_clip_name(stem) + ext).lower()


def _already_posted(queue, platform: str, video_path: str):
    """This clip's job on this platform, whichever path it was queued as.
    A job that has POSTED it wins over any other, so "already posted" is
    never hidden behind a second job still waiting for the same clip."""
    wanted = clip_key(video_path)
    same = [job for job in queue.list_jobs()
            if job.platform == platform and clip_key(job.clip_path) == wanted]
    for job in same:
        if job.state == "done":
            return job
    exact = queue.find(platform, video_path)
    if exact is not None:
        return exact
    return same[0] if same else None


# YouTube's vulgar-language policy names "a clip taken out of context"
# focused on that language as the thing that gets age-restricted, and its
# hate speech policy counts slurs whether or not they are audible - it
# says nothing about muting. A 16-second clip carrying five muted slurs
# is exactly that clip. At this many, it does not go to the short-form
# platforms at all. Set clips.max_slurs_for_social to 0 to turn it off.
DEFAULT_MAX_SLURS_FOR_SOCIAL = 3

# Held at ONE, whatever the count (see _slur_heavy).
SEVERE_SLURS = frozenset({
    "nigger", "niggers", "faggot", "faggots", "fag", "fags", "faggy",
    "tranny", "trannies", "shemale", "kike", "kikes", "spic", "spics",
    "wetback", "wetbacks", "chink", "chinks", "gook", "gooks", "coon", "coons",
    "beaner", "beaners", "raghead", "ragheads", "towelhead", "dyke", "dykes",
})


def _plain_word(word: str) -> str:
    return re.sub(r"[^a-z]", "", str(word or "").lower())


def _clip_segments(video_path: str, config: dict):
    """The clip's own word-level transcript from its censor pass, or None.

    Looked up under the CLIP's name - the file posted is often the
    censored copy, whose own transcript has the slurs muted out.
    """
    from utils.censor import words_cache_path
    from utils.social_promoter import plain_clip_name

    stem = plain_clip_name(os.path.splitext(os.path.basename(video_path))[0])
    folders = [os.path.dirname(os.path.abspath(video_path))]
    folders += list((config or {}).get("note_folders", ()) or ())
    for folder in folders:
        if not folder or not os.path.isdir(folder):
            continue
        path = words_cache_path(folder, stem)
        try:
            with open(path, encoding="utf-8") as handle:
                segments = json.load(handle).get("segments")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(segments, list):
            return segments
    return None


def clip_frames(video_path: str, count: int = 4) -> list:
    """JPEG stills spread across the clip, or [] (no ffmpeg, unreadable
    file). Two per half: vision_frames samples a window at 25% and 70%."""
    import subprocess

    try:
        from autoreel.vision_frames import frames_for

        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=30).stdout.strip()
        length = float(out)
    except Exception:
        return []
    if length <= 1:
        return []
    halves = max(1, count // 2)
    step = length / halves
    stills = []
    for i in range(halves):
        stills += frames_for(video_path, i * step, (i + 1) * step)
    return stills[:count]


def clip_transcript(video_path: str, config: dict) -> str:
    """The words said in this clip, as one line of text, or ""."""
    segments = _clip_segments(video_path, config) or []
    parts = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text") or "").strip()
        if not text:
            words = segment.get("words") or []
            text = " ".join(str(w.get("word", "")).strip()
                            for w in words if isinstance(w, dict))
        if text:
            parts.append(text)
    return " ".join(" ".join(parts).split())


def _slur_heavy(video_path: str, config: dict) -> str:
    """Why this clip is held back from social platforms, or "".

    No transcript found means no judgement - the clip is not held.
    """
    clips = (config or {}).get("clips", {}) or {}
    limit = clips.get("max_slurs_for_social", DEFAULT_MAX_SLURS_FOR_SOCIAL)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_SLURS_FOR_SOCIAL
    if limit <= 0:
        return ""
    segments = _clip_segments(video_path, config)
    if not segments:
        return ""
    from autoreel.compliance import ComplianceEngine

    hits = ComplianceEngine(only_categories=("hate_speech",)).scan_segments(
        segments)
    # One is enough for these. Earlier Monkey channels were removed for
    # hate speech and an Instagram Reel came down for hateful conduct; a
    # muted hard-R or f-slur is still a slur in YouTube's reading. The
    # everyday "nigga" stays on the count below - muted, and held only
    # when a clip is full of it.
    severe = sorted({_plain_word(h.word) for h in hits} & SEVERE_SLURS)
    if severe:
        return "a severe slur (" + ", ".join(w[0] + "*" * (len(w) - 1) for w in severe) + ")"
    if len(hits) < limit:
        return ""
    seconds = max((float(seg.get("end", 0) or 0) for seg in segments),
                  default=0.0)
    length = f" in {seconds:.0f}s" if seconds else ""
    return f"{len(hits)} slurs{length} (limit {limit})"


def youtube_hold_s(config: dict, now: Optional[float] = None) -> float:
    """Seconds until YouTube may be tried again, 0.0 if it may be now.

    The stream uploader writes logs/youtube_blocked.json when YouTube
    refuses an upload as forbidden - an upload block on the channel,
    usually from a strike. Clips wait it out too: putting them on another
    channel while one is blocked is getting around the block, which
    YouTube's rules forbid and can cost every linked channel.
    """
    import time

    now = time.time() if now is None else now
    path = os.path.join((config or {}).get("logs_folder") or "logs",
                        "youtube_blocked.json")
    try:
        with open(path, encoding="utf-8") as handle:
            until = float(json.load(handle).get("until", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0
    return max(0.0, until - now)


def _youtube_waits(platform: str, config: dict):
    """A guard-style "not yet" for a YouTube route while YouTube is
    blocked, or None."""
    if platform not in YOUTUBE_ROUTES:
        return None
    wait = youtube_hold_s(config)
    if not wait:
        return None
    from publish_guard import Decision

    return Decision(False, "YouTube is refusing uploads on the main "
                    "channel - clips wait until it accepts them again",
                    retry_after_s=wait)


def offer(posting: dict, config: dict, video_path: str,
          fallback_caption: str = "", platforms=CLIP_PLATFORMS,
          dry_run: bool = False) -> dict:
    """Post one clip everywhere it can go; defer where it cannot yet.

    Returns {platform: "posted" | "queued" | "skipped: reason"}.
    """
    from publish_guard import PublishGuard
    from publishers.errors import NotConfigured, PermanentlyRejected

    outcome: dict = {}
    if not posting or not video_path:
        return outcome

    held = _slur_heavy(video_path, config)
    if held:
        for platform in platforms:
            outcome[platform] = f"skipped: held - {held}"
        print(f"[Clips] Held back from every social platform: {held}. "
              f"Rumble still gets it. Post it by hand if you judge it fine.")
        _journal(config, "skip", "social", video_path, f"held: {held}")
        return outcome

    guard = PublishGuard(posting, posting.get("state_path"))
    queue = _queue(posting)

    # Filled in when a Postproxy route posts this clip, so nothing after
    # it posts to the same account a second time.
    covered: set = set()
    covered_by: dict = {}
    # Platforms a connected Postproxy route has taken on for this clip -
    # posted, queued, or failed and queued to retry. The direct publisher
    # for the same account stays out of it: Postproxy failing TikTok on a
    # dropped connection sent the clip to the direct TikTok route, and the
    # direct Instagram route stopped the whole watcher on a 2FA prompt.
    owned: dict = {}

    for platform in platforms:
        if platform in covered:
            route = covered_by.get(platform, "postproxy")
            outcome[platform] = f"skipped: sent by {route}"
            print(f"[Clips] {platform}: already sent in the {route} "
                  f"call - not posting it twice.")
            continue
        if platform not in ROUTE_BASE:
            owner = owned.get(DIRECT_BASE.get(platform, platform))
            if owner:
                outcome[platform] = f"skipped: handled by {owner}"
                continue

        already = _already_posted(queue, platform, video_path)
        if already is not None and platform in ROUTE_BASE:
            owned[ROUTE_BASE[platform]] = platform
        if already is not None and already.state == "done":
            # This clip has been through here before - a re-run of the
            # same file must not post it a second time.
            outcome[platform] = "skipped: already posted"
            if platform in FAN_OUT_ROUTES:
                # And neither may anything it reached. A clip whose Rumble
                # upload failed came back through here; the fan-out route
                # was skipped as done, but Instagram and TikTok - which it
                # HAD sent the clip to - were not marked, so Instagram
                # posted it directly.
                earlier = set((already.extra or {}).get("reached")
                              or remembered_reach(posting, platform))
                earlier -= covered
                for name in earlier:
                    covered_by.setdefault(name, platform)
                covered |= earlier
                if earlier:
                    print(f"[Clips] {platform}: already sent this clip to "
                          f"{', '.join(sorted(earlier))} - not again.")
            continue

        caption = caption_for(platform, video_path, fallback_caption, config)
        decision = _youtube_waits(platform, config)
        if decision is None:
            decision = guard.check(platform)

        if not decision and decision.retry_after_s is None:
            # Not a timing problem - disabled, manual-only, killed, or
            # breakered. Nothing to wait for, so nothing to queue: a pile
            # of these would all fire at once the day it was fixed.
            outcome[platform] = f"skipped: {decision.reason}"
            print(f"[Clips] {platform}: skipped - {decision.reason}")
            _journal(config, "skip", platform, video_path, decision.reason)
            continue

        publisher = _publisher(platform, config)
        ready = getattr(publisher, "ready", None) if publisher else None
        if publisher is None or (ready is not None and not ready()):
            outcome[platform] = "skipped: not configured"
            print(f"[Clips] {platform}: skipped - not configured yet.")
            _journal(config, "skip", platform, video_path,
                     "credentials not set - see --posting-status")
            continue

        if platform in ROUTE_BASE:
            owned[ROUTE_BASE[platform]] = platform

        # Recorded before the attempt, so the queue is also the ledger of
        # what has been posted - which is what stops a re-run of the same
        # file posting it twice.
        job_id = queue.begin(platform, video_path, caption,
                             extra={"score": _clip_score(video_path)})

        if not decision:
            _hold_for_later(queue, job_id, posting, video_path)
            queue.block(job_id, decision.reason, decision.retry_after_s)
            outcome[platform] = "queued"
            print(f"[Clips] {platform}: {decision.reason} - queued, back in "
                  f"{decision.retry_after_s / 60:.0f} min.")
            if platform in FAN_OUT_ROUTES:
                # It WILL post this clip when its wait is up. Posting it
                # directly as well meant the same Reel twice - Clip 01 went
                # to Instagram directly while the fan-out route held it for
                # its spacing, then that route sent it there too.
                later = remembered_reach(posting, platform)
                if platform in ROUTE_BASE:
                    # A Postproxy route reaches exactly its platform, and
                    # it is connected (ready() said so). Known even the
                    # first time, when there is no remembered reach yet.
                    later = later | {ROUTE_BASE[platform]}
                later -= covered
                for name in later:
                    covered_by.setdefault(name, platform)
                covered |= later
                if later:
                    print(f"[Clips] {platform}: will cover "
                          f"{', '.join(sorted(later))} when it goes - "
                          f"not posting those directly.")
            _journal(config, "wait", platform, video_path,
                     f"{decision.reason} - back in "
                     f"{decision.retry_after_s / 60:.0f} min")
            continue

        detail: dict = {}
        try:
            ok = publish(platform, video_path, caption, config, dry_run,
                        detail=detail)
        except NotConfigured as exc:
            queue.block(job_id, str(exc), MAX_DEFERRED_AGE_S)
            outcome[platform] = "skipped: not configured"
            print(f"[Clips] {platform}: skipped - {exc}")
            # "wait", not "FAIL". An expired token is a CONFIGURATION
            # problem - nothing was attempted and nothing went wrong with
            # the clip - and counting it as a failure makes the report
            # read "FAIL 8" when the true answer is "one credential
            # expired, eight clips are waiting on it". That difference is
            # what tells you whether to look at the code or at a token.
            _journal(config, "wait", platform, video_path, str(exc))
            continue
        except PermanentlyRejected as exc:
            # The platform read this video and refused it. Not a failure
            # to record against the breaker either - the account is fine,
            # this one file is not.
            queue.abandon(job_id, str(exc))
            outcome[platform] = "skipped: rejected"
            print(f"[Clips] {platform}: will not accept "
                  f"{os.path.basename(video_path)} - {exc}")
            _journal(config, "FAIL", platform, video_path,
                     getattr(exc, "journal_note",
                             "the platform will not process this video"))
            continue
        if dry_run:
            queue.block(job_id, "dry run", 300)
            outcome[platform] = "posted"
            continue
        guard.record_result(platform, ok)
        if ok:
            queue.complete(job_id)
            outcome[platform] = "posted"
            if platform in FAN_OUT_ROUTES:
                # What Postproxy's OWN polled status says actually
                # succeeded (see publish()'s `detail`) - never a guess from
                # this project's config, which cannot see what is
                # connected there.
                reached = set(detail.get("covers") or ())
                for name in reached:
                    covered_by.setdefault(name, platform)
                covered |= reached
                if reached and not dry_run:
                    _remember_reach(posting, platform, reached)
                    _record_reached(queue, job_id, reached)
                shown = ", ".join(sorted(reached)) or "nothing else enabled"
                print(f"[Clips] {platform}: posted - covers {shown}.")
            else:
                print(f"[Clips] {platform}: posted a Reel.")
            _journal(config, "ok", platform, video_path, "posted")
        else:
            # Worth one more go later; the queue's attempt ceiling stops
            # it becoming a loop.
            _hold_for_later(queue, job_id, posting, video_path)
            queue.fail(job_id, "first attempt failed")
            outcome[platform] = "queued"
            print(f"[Clips] {platform}: Reel failed - queued to retry.")
            _journal(config, "FAIL", platform, video_path,
                     "upload rejected - see logs/publishers.log")
    return outcome


def _current_caption(job, config: dict) -> str:
    """This job's caption as the CURRENT code and config would write it.

    The stored one is kept as the fallback rather than as the answer: it
    is better to post yesterday's wording than to post nothing, and a
    clip whose sidecar text file has since been cleaned up would compose
    to a bare filename.
    """
    stored = getattr(job, "caption", "") or ""
    try:
        fresh = caption_for(job.platform, job.clip_path, stored, config)
    except Exception:
        # Composing a caption must never be the reason a clip does not
        # go out. Anything unexpected here falls back to what the job
        # was queued with, which is what used to be posted anyway.
        return stored
    return fresh or stored


def recaption(posting: dict, config: dict) -> list:
    """Rewrite every waiting job's stored caption with current wording.

    The drain composes captions fresh now, so this is not required for
    correctness - it exists so the backlog can be SEEN to be fixed
    instead of taken on trust, and so `--posting-status` shows what will
    actually go out rather than what was written days ago.

    Returns [(platform, clip, before, after)] for everything changed.
    """
    from job_queue import ACTIVE_STATES

    queue = _queue(posting)
    changed = []
    for job in queue.list_jobs(ACTIVE_STATES):
        before = job.caption or ""
        after = _current_caption(job, config)
        if after and after != before:
            job.caption = after
            changed.append((job.platform, job.clip_path, before, after))
    if changed:
        queue._save()
    return changed


def drain(posting: dict, config: dict, limit: int = 0,
          dry_run: bool = False, quiet: bool = False,
          recheck: bool = False) -> dict:
    """Post whatever the queue is now allowed to post.

    Called on every pass of the watcher, so a clip deferred at 14:02 goes
    out the moment its wait is up rather than when someone remembers.

    recheck: forget every stored wait and ask the guard again. A wait is
    worked out from the settings in force when the clip was blocked, so
    clips held under a 45-minute gap and a 10:00-23:00 window still sat
    for hours after both were dropped. The guard still decides - a clip
    that is genuinely not allowed yet is simply blocked again, with the
    wait the CURRENT settings give.

    Returns {platform: posted_count}.
    """
    from publish_guard import RETIRED_PLATFORMS, PublishGuard
    from publishers.errors import NotConfigured, PermanentlyRejected

    posted: dict = {}
    if not posting:
        return posted

    queue = _queue(posting)
    guard = PublishGuard(posting, posting.get("state_path"))
    import time

    now = time.time()
    if recheck:
        queue.unblock_all(now=now)
    sent = 0
    while True:
        job = queue.claim()
        if job is None:
            break

        if job.platform in RETIRED_PLATFORMS:
            # Queued before that service's subscription ended. Its code is
            # gone, so a retry could only fail - and count against nothing
            # useful.
            queue.abandon(job.id, f"{job.platform} is no longer used",
                          now=now)
            continue

        if now - (job.created_at or now) > MAX_DEFERRED_AGE_S:
            queue.abandon(job.id, "too old to be worth posting", now=now)
            _journal(config, "skip", job.platform, job.clip_path,
                     "over a day old - not worth posting now")
            if not quiet:
                print(f"[Clips] {job.platform}: dropped "
                      f"{os.path.basename(job.clip_path)} - over a day old.")
            continue

        if not os.path.isfile(job.clip_path):
            moved = _find_moved(job, config)
            if moved:
                job.clip_path = _hold(posting, moved)
                queue._save()
        if not os.path.isfile(job.clip_path):
            queue.abandon(job.id, "the clip is no longer on disk", now=now)
            _journal(config, "FAIL", job.platform, job.clip_path,
                     "the clip file is gone")
            continue

        decision = _youtube_waits(job.platform, config)
        if decision is None:
            decision = guard.check(job.platform)
        if not decision:
            # Still not allowed. Put it back with its new wait rather
            # than burning an attempt on a scheduling fact.
            queue.block(job.id, decision.reason, decision.retry_after_s)
            if not quiet:
                print(f"[Clips] {job.platform}: still waiting - {decision.reason}")
            continue

        # Written FRESH here, not taken from the job. The caption was
        # being composed at enqueue time and stored, so a job queued
        # before a wording fix kept the old text for as long as it sat
        # in the queue - and X posts one clip an hour, so a backlog kept
        # publishing pre-fix captions for hours after the fix landed.
        # Three real posts went out reading "vertical Stackswopo Love
        # Yall 20250914 204409 - Clip 03" that way, and an Instagram post
        # carried a "LINK IN BIO" line that had already been deleted from
        # the template.
        #
        # Composing it at the moment of posting means a change to the
        # template, the tags or the title logic reaches the whole backlog
        # by itself, with nothing to re-run.
        caption = _current_caption(job, config)

        detail: dict = {}
        try:
            ok = publish(job.platform, job.clip_path, caption, config,
                         dry_run, detail=detail)
        except NotConfigured as exc:
            # Held, not failed: the clip is fine, the token is not. It
            # comes back once somebody fixes the scope.
            queue.block(job.id, str(exc), MAX_DEFERRED_AGE_S)
            if not quiet:
                print(f"[Clips] {job.platform}: held - {exc}")
            continue
        except PermanentlyRejected as exc:
            # The platform looked at this video and said no. Retrying
            # against an explicit "do not retry" is not persistence.
            queue.abandon(job.id, str(exc), now=now)
            _journal(config, "FAIL", job.platform, job.clip_path,
                     getattr(exc, "journal_note",
                             "the platform will not process this video"))
            print(f"[Clips] {job.platform}: giving up on "
                  f"{os.path.basename(job.clip_path)} - {exc}")
            continue
        if dry_run:
            # Put it back with a real wait: a zero would make it eligible
            # again on the next claim and loop this forever.
            queue.block(job.id, "dry run", 300)
            sent += 1
            if limit and sent >= limit:
                break
            continue
        guard.record_result(job.platform, ok)
        if ok:
            queue.complete(job.id)
            if job.platform in FAN_OUT_ROUTES and detail.get("covers"):
                _record_reached(queue, job.id, set(detail["covers"]))
                _remember_reach(posting, job.platform, set(detail["covers"]))
            posted[job.platform] = posted.get(job.platform, 0) + 1
            print(f"[Clips] {job.platform}: posted a queued Reel "
                  f"({os.path.basename(job.clip_path)}).")
            _journal(config, "ok", job.platform, job.clip_path,
                     "posted from the queue")
        else:
            queue.fail(job.id, "Reel upload failed")
            _journal(config, "FAIL", job.platform, job.clip_path,
                     "upload rejected - see logs/publishers.log")

        sent += 1
        if limit and sent >= limit:
            break
    _release_held(queue, posting)
    return posted


def summary(posting: dict) -> str:
    """One line on what is waiting, for the status output."""
    if not posting:
        return ""
    counts = _queue(posting).counts()
    waiting = sum(counts.get(state, 0) for state in ("pending", "blocked"))
    if not waiting:
        return "No clips waiting to post."
    return (f"{waiting} clip(s) waiting to post "
            f"(done {counts.get('done', 0)}, given up on "
            f"{counts.get('failed', 0)}).")
