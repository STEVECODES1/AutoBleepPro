"""The receipt: one Discord post saying what a finished video did.

Two things already posted to Discord and neither answered the question
anyone asks. The announcer says "new video is up" for a fresh upload and
is written for followers; record_stream pings when a recording comes up
short. Nothing ever said which platforms took a video, which refused,
which were skipped, and how long it took - so the only way to know
whether a night's work landed was to read two console windows that
scroll. A run that half-failed at 3am looked exactly like one that
worked.
"""

from __future__ import annotations

import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (_REPO, os.path.join(_REPO, "auto_uploader")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from utils.job_report import (COLOUR_FAILED, COLOUR_OK,  # noqa: E402
                              COLOUR_PARTIAL, FAILED, MAX_FIELDS,
                              MAX_TITLE, POSTED, PENDING, SKIPPED,
                              JobReport, build_embed, classify, payload_for,
                              pretty, report_job, send, webhook_url)


# ── reading an outcome ───────────────────────────────────────────────

def test_a_url_is_a_success():
    assert classify("https://rumble.com/v70abc-x.html") == POSTED
    assert classify("http://youtu.be/abc") == POSTED


def test_a_failure_is_a_failure():
    assert classify("FAILED: quota exceeded") == FAILED
    assert classify("FAILED: Rumble finished but the video is not there") == FAILED


def test_a_skip_is_not_a_failure():
    """A clip already on the platform is the system working, and a
    receipt that paints it red teaches people to ignore the receipt."""
    assert classify("skipped: already posted") == SKIPPED
    assert classify("already uploaded previously") == SKIPPED


def test_a_success_with_no_link_is_still_a_success():
    """Zernio answers a publishNow post without always returning the
    platform URL - see ZernioPublisher.post_clip. The post was made."""
    assert classify("posted (Zernio returned no link)") == POSTED


def test_nothing_recorded_means_not_attempted():
    assert classify("") == PENDING
    assert classify(None) == PENDING


# ── the one line that gets read on a lock screen ─────────────────────

def test_all_landed_says_so():
    report = JobReport(title="x", destinations={
        "rumble": "https://r/1", "youtube": "https://y/1"})

    assert report.headline() == "All 2 landed."
    assert report.colour() == COLOUR_OK


def test_a_partial_run_names_both_numbers():
    report = JobReport(title="x", destinations={
        "rumble": "https://r/1", "youtube": "FAILED: nope"})

    assert report.headline() == "1 of 2 landed, 1 failed."
    assert report.colour() == COLOUR_PARTIAL


def test_a_total_failure_is_red():
    report = JobReport(title="x", destinations={
        "rumble": "FAILED: a", "youtube": "FAILED: b"})

    assert report.headline() == "Nothing landed - 2 of 2 failed."
    assert report.colour() == COLOUR_FAILED


def test_everything_already_posted_is_not_a_failure():
    """This is the case the announcer stays silent about, and it is a
    real answer worth having."""
    report = JobReport(title="x", destinations={
        "rumble": "skipped: already posted"})

    assert report.headline() == "Nothing new to post."
    assert report.colour() == COLOUR_OK


# ── what the post actually contains ──────────────────────────────────

def test_every_destination_appears_with_its_link():
    report = JobReport(title="A Stream", destinations={
        "rumble": "https://rumble.com/v70abc-x.html",
        "youtube_shorts": "FAILED: quota exceeded for today"})

    body = build_embed(report)["description"]

    assert "https://rumble.com/v70abc-x.html" in body
    assert "quota exceeded for today" in body, "the reason is the useful half"
    assert "Rumble" in body and "YouTube Shorts" in body


def test_the_platform_key_is_not_what_gets_shown():
    """"zernio_tiktok" is an implementation detail; a person reads this."""
    assert pretty("zernio_tiktok") == "TikTok"
    assert pretty("zernio_twitter") == "X"
    assert pretty("youtube_shorts") == "YouTube Shorts"


def test_the_clips_and_the_bleeping_are_reported():
    report = JobReport(title="A Stream", destinations={"rumble": "https://r/1"},
                       clips_made=3, censor_note="silenced 14 word(s)",
                       seconds=1877.0)

    names = {f["name"]: f["value"] for f in build_embed(report)["fields"]}

    assert "3 from this stream" in names["Clips cut"]
    assert names["Audio"] == "silenced 14 word(s)"
    assert names["Took"] == "31m 17s"


def test_a_run_with_nothing_to_report_still_renders():
    """An empty job must not produce a broken embed - Discord rejects
    one with an empty description and the post silently vanishes."""
    embed = build_embed(JobReport(title="", filename=""))

    assert embed["title"], "an embed with no title is rejected"
    assert embed["description"], "an embed with no description is rejected"


# ── it must fit inside what Discord accepts ──────────────────────────

def test_a_very_long_title_is_trimmed():
    """Discord rejects an oversized payload with a 400 and the post
    silently does not appear - the worst way for a reporting tool to
    fail, because it looks exactly like a run that never happened."""
    embed = build_embed(JobReport(title="x" * 900))

    assert len(embed["title"]) <= MAX_TITLE


def test_a_huge_number_of_destinations_stays_inside_the_limits():
    report = JobReport(title="x", destinations={
        f"platform_{i}": f"FAILED: reason {i} " + "y" * 300
        for i in range(40)})

    embed = build_embed(report)
    payload = json.dumps(payload_for(report))

    assert len(embed["fields"]) <= MAX_FIELDS
    assert len(embed["description"]) <= 4096
    assert len(payload) < 6000, "Discord refuses an embed bigger than this"


# ── where it is sent, and how it fails ───────────────────────────────

def test_a_dedicated_webhook_beats_the_shared_one(monkeypatch):
    """The other webhook may point at a public channel, and "Rumble
    FAILED" is not something to broadcast there."""
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://public")
    monkeypatch.setenv("DISCORD_JOB_WEBHOOK_URL", "https://private")

    assert webhook_url() == "https://private"


def test_it_falls_back_to_the_only_webhook_most_people_set(monkeypatch):
    monkeypatch.delenv("DISCORD_JOB_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://shared")

    assert webhook_url() == "https://shared"


def test_no_webhook_means_no_post_and_no_error(monkeypatch):
    monkeypatch.delenv("DISCORD_JOB_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    assert send(JobReport(title="x")) is False


def test_a_webhook_that_refuses_never_raises(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://shared")

    def explode(_url, _body):
        raise OSError("404")

    assert send(JobReport(title="x"), post=explode) is False


def test_the_receipt_can_be_switched_off():
    class Cfg:
        features = {"job_report": {"enabled": False}}

    assert report_job(Cfg(), JobReport(title="x"), say=lambda *_a: None) is False


def test_a_broken_config_cannot_take_the_run_down():
    """The video is already published by the time this runs."""
    class Cfg:
        @property
        def features(self):
            raise RuntimeError("config exploded")

    assert report_job(Cfg(), JobReport(title="x"), say=lambda *_a: None) is False


# ── it is wired into the one place that knows everything ─────────────

def test_it_is_sent_from_the_end_of_process_file():
    """Both uploads AND every clip destination have reported in by then,
    and nowhere earlier knows all of it."""
    import inspect

    import main

    body = inspect.getsource(main.process_file)
    spot = body.index("report_job(")

    assert "clip_reels" in body[spot:spot + 400], \
        "the clip destinations are not in the receipt"
    assert body.index("return results") > spot, \
        "the receipt has to be the last thing that happens"
