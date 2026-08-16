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
CLIP_PLATFORMS = ("instagram", "facebook", "zernio_twitter",
                  "zernio_tiktok", "youtube_shorts")

# Platforms whose CAPTION text goes through the profanity filter. Rumble
# is deliberately absent - it is the uncensored channel, and the titles
# there are the line actually spoken, which is the point.
CLEAN_TEXT_PLATFORMS = ("instagram", "facebook", "youtube_shorts",
                        "zernio_twitter", "zernio_tiktok")

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


def _subject_note(video_path: str) -> str:
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
    for candidate in (stem + "_subject.txt", plain + "_subject.txt"):
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                found = handle.read().strip()
            if found:
                return found
        except OSError:
            continue
    return ""


def caption_for(platform: str, video_path: str, fallback: str,
                config: dict) -> str:
    """The caption this platform posts with.

    Instagram has a studied template that matches how the account already
    writes; Facebook falls back to it rather than inventing a second
    voice, because the same clip reading two different ways across two
    Pages is what looks automated.
    """
    from utils.social_promoter import build_caption, clip_title, hashtags_for

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
    headline = clip_title(video_path)
    # Matched against the headline AND the filename, because the headline
    # is usually the line spoken in the clip and people do not announce
    # what app they are on. "Stackswopo Love Yall - Clip 03" says nothing
    # a tag can be picked from, while the VOD it was cut out of is called
    # "monkey_n_gamble_howl" - which says exactly what it is. Using only
    # the headline meant the specific tags almost never fired and every
    # clip went out with the generic fillers.
    subject = f"{headline} {os.path.basename(video_path or '')} " \
              f"{_subject_note(video_path)}"
    tags = hashtags_for(subject, platform,
                        settings.get("max_hashtags"))
    caption = build_caption(template, video_path, tags=tags) or fallback

    # Instagram and YouTube apply their rules to the TEXT as well as the
    # video. Rumble is not in this set on purpose: that channel is the
    # uncensored one, and running the filter over it would flatten the
    # exact thing its audience is there for.
    if platform in CLEAN_TEXT_PLATFORMS:
        from autoreel.safe_text import clean_lines

        caption = clean_lines(caption)
    return caption


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
CENSOR_AUDIO_DEFAULTS = {
    "youtube_shorts": "all",
    "instagram": "slurs",
    "facebook": "slurs",
    "zernio_twitter": "slurs",
    "zernio_tiktok": "slurs",
}

# Which compliance categories each mode bleeps. Empty tuple = all.
_CENSOR_SCOPES = {"all": (), "slurs": ("hate_speech",)}


def _censored_clip(platform: str, video_path: str, config: dict) -> tuple:
    """(path to post, temp path to delete) for this platform's rules.

    Clips are cut from the RAW stream - every call site passes the
    original video, not the censored copy - and until now nothing
    bleeped them afterwards either. So a Short went to YouTube carrying
    whatever was actually said, on a channel where that is a strike.

    Returns the original and "" whenever censoring is off, unavailable,
    or finds nothing, so this can never be the reason a clip fails.
    """
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

    general = (config or {}).get("general", {}) or {}
    try:
        from utils.censor import censor_video

        result = censor_video(
            video_path, general.get("censored_folder") or "censored",
            model_name=general.get("censor_model", "base"),
            bleep_method=general.get("censor_bleep_method", "beep"),
            custom_words=tuple(general.get("censor_custom_words", ()) or ()),
            device=general.get("censor_device") or None,
            padding_ms=int(general.get("censor_padding_ms", 250)),
            mute_whole_segment=bool(
                general.get("censor_mute_whole_segment", True)),
            only_categories=scope)
    except Exception as exc:
        # A clip that cannot be censored must not go out UNcensored to a
        # platform that asked for it - that is the one failure worth
        # losing the post over.
        print(f"[Clips] {platform}: could not censor the clip ({exc}) - "
              f"not posting it there.")
        return "", ""

    made = getattr(result, "output_path", "") or video_path
    if made != video_path:
        count = getattr(result, "violation_count", 0)
        print(f"[Clips] {platform}: bleeped {count} word(s) before posting.")
        return made, made
    return video_path, ""


def publish(platform: str, video_path: str, caption: str,
            config: dict, dry_run: bool = False) -> bool:
    """Actually post one clip. No guard, no queue - callers do that.

    Raises NotConfigured when the platform refuses for a reason no retry
    can fix - a missing token scope, most often.
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
        upload, temporary = (video_path, "") if dry_run else \
            _censored_clip(platform, video_path, config)
        if not upload:
            return False
        try:
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
        if posted:
            print(f"[Clips] {platform}: {posted}")
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
    upload_path, temp = _vertical_copy(video_path, settings,
                                       (config or {}).get("clips", {}) or {})
    print(f"[Clips] {platform}: uploading "
          f"{os.path.basename(video_path)} as a Reel...")
    try:
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
        if temp:
            try:
                os.remove(temp)
            except OSError:
                pass
    return ok


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
    name = os.path.basename(video_path or "")
    return re.sub(r"^_?vertical[_\s]+", "", name, flags=re.I).lower()


def _already_posted(queue, platform: str, video_path: str):
    """This clip's job on this platform, whichever path it was queued as."""
    wanted = clip_key(video_path)
    exact = queue.find(platform, video_path)
    if exact is not None:
        return exact
    for job in queue.list_jobs():
        if job.platform == platform and clip_key(job.clip_path) == wanted:
            return job
    return None


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

    guard = PublishGuard(posting, posting.get("state_path"))
    queue = _queue(posting)

    for platform in platforms:
        already = _already_posted(queue, platform, video_path)
        if already is not None and already.state == "done":
            # This clip has been through here before - a re-run of the
            # same file must not post it a second time.
            outcome[platform] = "skipped: already posted"
            continue

        caption = caption_for(platform, video_path, fallback_caption, config)
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

        # Recorded before the attempt, so the queue is also the ledger of
        # what has been posted - which is what stops a re-run of the same
        # file posting it twice.
        job_id = queue.enqueue(platform, video_path, caption)

        if not decision:
            queue.block(job_id, decision.reason, decision.retry_after_s)
            outcome[platform] = "queued"
            print(f"[Clips] {platform}: {decision.reason} - queued, back in "
                  f"{decision.retry_after_s / 60:.0f} min.")
            _journal(config, "wait", platform, video_path,
                     f"{decision.reason} - back in "
                     f"{decision.retry_after_s / 60:.0f} min")
            continue

        try:
            ok = publish(platform, video_path, caption, config, dry_run)
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
                     "the platform will not process this video")
            continue
        if dry_run:
            queue.block(job_id, "dry run", 300)
            outcome[platform] = "posted"
            continue
        guard.record_result(platform, ok)
        if ok:
            queue.complete(job_id)
            outcome[platform] = "posted"
            print(f"[Clips] {platform}: posted a Reel.")
            _journal(config, "ok", platform, video_path, "posted")
        else:
            # Worth one more go later; the queue's attempt ceiling stops
            # it becoming a loop.
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
          dry_run: bool = False, quiet: bool = False) -> dict:
    """Post whatever the queue is now allowed to post.

    Called on every pass of the watcher, so a clip deferred at 14:02 goes
    out the moment its wait is up rather than when someone remembers.

    Returns {platform: posted_count}.
    """
    from publish_guard import PublishGuard
    from publishers.errors import NotConfigured, PermanentlyRejected

    posted: dict = {}
    if not posting:
        return posted

    queue = _queue(posting)
    guard = PublishGuard(posting, posting.get("state_path"))
    import time

    now = time.time()
    sent = 0
    while True:
        job = queue.claim()
        if job is None:
            break

        if now - (job.created_at or now) > MAX_DEFERRED_AGE_S:
            queue.abandon(job.id, "too old to be worth posting", now=now)
            _journal(config, "skip", job.platform, job.clip_path,
                     "over a day old - not worth posting now")
            if not quiet:
                print(f"[Clips] {job.platform}: dropped "
                      f"{os.path.basename(job.clip_path)} - over a day old.")
            continue

        if not os.path.isfile(job.clip_path):
            queue.abandon(job.id, "the clip is no longer on disk", now=now)
            _journal(config, "FAIL", job.platform, job.clip_path,
                     "the clip file is gone")
            continue

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

        try:
            ok = publish(job.platform, job.clip_path, caption, config,
                         dry_run)
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
                     "the platform will not process this video")
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
