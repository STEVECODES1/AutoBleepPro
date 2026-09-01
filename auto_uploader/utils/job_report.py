"""One structured Discord post when a video has finished going out.

WHY THIS EXISTS
---------------
There were already two things posting to Discord and neither answered
the question anyone actually asks. `social_promoter.announce_upload` is
an ANNOUNCEMENT - it is written for followers and says "new video is
up", once, for a fresh upload. `record_stream` pings the same webhook
when a recording comes up short. Between them, nothing ever said what a
finished job actually did: which platforms took it, which refused,
which were skipped because the clip was already there, how long it
took, what got bleeped.

So the only way to know whether a night's work landed was to read two
console windows, and those windows scroll. A run that half-failed at
3am looked exactly like a run that worked.

This is the receipt. One post per finished video, every destination on
it, links where there are links and the reason where there are not.

WHAT MAKES IT A RECEIPT RATHER THAN AN ANNOUNCEMENT
---------------------------------------------------
It reports FAILURES as loudly as successes, so it is not something to
point followers at - it goes to the operator's own channel. It also
fires whether or not anything was newly uploaded, because "everything
was already posted, nothing to do" is a real and useful answer that the
announcement path deliberately stays silent about.

IT CANNOT BREAK A RUN
---------------------
Every entry point swallows its own errors and returns False. The video
is already published by the time this runs; a webhook that 404s must
never be the reason the pipeline reports failure.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

# Discord rejects an oversized payload with a 400 and the post silently
# does not appear, which is the worst way for this to fail - it is a
# reporting tool, so a report nobody receives is indistinguishable from
# a run that never happened. Everything below is trimmed to these.
MAX_TITLE = 256
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FIELDS = 25
MAX_DESCRIPTION = 4096

# Green, amber, red. Read at a glance from a phone notification, which
# is the whole point of sending it anywhere.
COLOUR_OK = 0x2ECC71
COLOUR_PARTIAL = 0xE67E22
COLOUR_FAILED = 0xE74C3C

_TIMEOUT = 15

# How a platform's recorded outcome reads. The uploader stores a URL on
# success and a "FAILED: ..." or "skipped: ..." string otherwise - see
# process_file - so the string itself is the status and there is no
# second source of truth to disagree with.
POSTED, SKIPPED, FAILED, PENDING = "posted", "skipped", "failed", "pending"

_MARK = {POSTED: "✅", SKIPPED: "⏭️",
         FAILED: "❌", PENDING: "⏳"}

# Shown instead of the raw key, because "zernio_tiktok" is an
# implementation detail and this is read by a person.
PLATFORM_NAMES = {
    "youtube": "YouTube",
    "rumble": "Rumble",
    "youtube_shorts": "YouTube Shorts",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "zernio_twitter": "X",
    "zernio_tiktok": "TikTok",
}


def webhook_url(explicit: str = "") -> str:
    """The receipt webhook, falling back to the general Discord one.

    A separate DISCORD_JOB_WEBHOOK_URL is supported because this is an
    operator's receipt and the other one may point at a public channel
    where "Rumble FAILED" is not something to broadcast. Falling back
    rather than requiring it keeps this working for anyone who has only
    ever set the one webhook.
    """
    return (str(explicit or "").strip()
            or os.environ.get("DISCORD_JOB_WEBHOOK_URL", "").strip()
            or os.environ.get("DISCORD_WEBHOOK_URL", "").strip())


def is_link(value: str) -> bool:
    return str(value or "").strip().lower().startswith(("http://", "https://"))


def classify(outcome: str) -> str:
    """What a recorded outcome string means."""
    text = str(outcome or "").strip()
    if not text:
        return PENDING
    if is_link(text):
        return POSTED
    lowered = text.lower()
    if lowered.startswith("failed") or "error" in lowered:
        return FAILED
    if lowered.startswith(("skip", "already")):
        return SKIPPED
    # Zernio answers a publishNow post without always returning a URL.
    # That is a success with no link, not an unknown - see
    # ZernioPublisher.post_clip.
    if "posted" in lowered:
        return POSTED
    return PENDING


def pretty(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform.replace("_", " ").title())


@dataclass
class JobReport:
    """Everything one finished video did, in one object."""

    title: str
    filename: str = ""
    # platform -> URL, "FAILED: ...", "skipped: ..." or ""
    destinations: dict = field(default_factory=dict)
    # Clips cut from this video, if it was a full stream.
    clips_made: int = 0
    clips_folder: str = ""
    # "13 slurs muted", or "" when nothing was censored.
    censor_note: str = ""
    seconds: float = 0.0
    is_clip: bool = False

    def statuses(self) -> dict:
        return {name: classify(value)
                for name, value in (self.destinations or {}).items()}

    def landed(self) -> int:
        return sum(1 for s in self.statuses().values() if s == POSTED)

    def failed(self) -> int:
        return sum(1 for s in self.statuses().values() if s == FAILED)

    def colour(self) -> int:
        if self.failed() and not self.landed():
            return COLOUR_FAILED
        if self.failed():
            return COLOUR_PARTIAL
        return COLOUR_OK

    def headline(self) -> str:
        """The one line that has to survive being read on a lock screen."""
        landed, failed = self.landed(), self.failed()
        total = len(self.destinations or {})
        if not total:
            return "Nothing was attempted."
        if failed and not landed:
            return f"Nothing landed - {failed} of {total} failed."
        if failed:
            return f"{landed} of {total} landed, {failed} failed."
        return f"All {landed} landed." if landed else "Nothing new to post."


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _line(platform: str, outcome: str) -> str:
    """One destination, as a single readable line."""
    status = classify(outcome)
    mark = _MARK.get(status, "")
    name = pretty(platform)
    if status == POSTED and is_link(outcome):
        return f"{mark} **{name}** - {outcome}"
    if status == POSTED:
        return f"{mark} **{name}** - posted"
    if status == PENDING:
        return f"{mark} **{name}** - not attempted"
    # The recorded reason, which is the useful half of a failure.
    reason = str(outcome or "").strip()
    reason = reason.split(":", 1)[1].strip() if ":" in reason else reason
    return f"{mark} **{name}** - {_clip(reason, 160) or status}"


def build_embed(report: JobReport) -> dict:
    """The Discord embed for one finished job."""
    lines = [_line(name, value)
             for name, value in sorted((report.destinations or {}).items())]
    description = "\n".join(lines) or "No destinations were configured."

    fields = []
    if report.clips_made:
        fields.append({
            "name": "Clips cut",
            "value": _clip(f"{report.clips_made} from this stream"
                           + (f"\n`{report.clips_folder}`"
                              if report.clips_folder else ""),
                           MAX_FIELD_VALUE),
            "inline": True})
    if report.censor_note:
        fields.append({"name": "Audio",
                       "value": _clip(report.censor_note, MAX_FIELD_VALUE),
                       "inline": True})
    if report.seconds:
        minutes, seconds = divmod(int(report.seconds), 60)
        fields.append({
            "name": "Took",
            "value": f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s",
            "inline": True})

    embed = {
        "title": _clip(report.title or report.filename or "Untitled",
                       MAX_TITLE),
        "description": _clip(description, MAX_DESCRIPTION),
        "color": report.colour(),
        "fields": fields[:MAX_FIELDS],
        "footer": {"text": _clip(
            ("Clip" if report.is_clip else "Stream")
            + (f" • {report.filename}" if report.filename else ""),
            MAX_FIELD_VALUE)},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
    }
    return embed


def payload_for(report: JobReport) -> dict:
    return {"username": "AutoBleep",
            "content": report.headline(),
            "embeds": [build_embed(report)]}


def send(report: JobReport, webhook: str = "", post=None) -> bool:
    """Post the receipt. False on anything at all going wrong.

    `post` is injectable so the tests never touch the network.
    """
    url = webhook_url(webhook)
    if not url:
        return False
    body = json.dumps(payload_for(report)).encode("utf-8")
    if post is not None:
        try:
            post(url, body)
            return True
        except Exception:
            return False
    request = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "AutoBleep"})
    try:
        urllib.request.urlopen(request, timeout=_TIMEOUT)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError):
        return False


def report_job(cfg, report: JobReport, say=print) -> bool:
    """Send it if it is switched on. Never raises.

    Reads features.job_report, defaulting ON: somebody who has set a
    Discord webhook at all wants to know what happened, and the cost of
    an unwanted post is one message they can turn off.
    """
    try:
        features = dict(getattr(cfg, "features", {}) or {})
        setting = features.get("job_report", {})
        if isinstance(setting, dict):
            enabled = bool(setting.get("enabled", True))
            explicit = str(setting.get("webhook_url", "") or "")
        else:
            enabled, explicit = bool(setting), ""
        if not enabled:
            return False
        if not webhook_url(explicit):
            return False
        sent = send(report, explicit)
        if sent:
            say(f"[Report] Posted the receipt to Discord: {report.headline()}")
        return sent
    except Exception:
        # A receipt is the least important thing here and must never be
        # the reason a published video reports failure.
        return False
