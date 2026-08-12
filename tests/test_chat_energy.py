"""
Chat: read, counted, deleted.

The only signal in this pipeline that is not inference - a few hundred
people saying, at a timestamp, that something happened. And the one with
the strongest rule about what must NOT survive: a chat log is tens of
thousands of other people's messages, and none of it is wanted here.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoreel.chat_energy import (
    chat_bonus,
    download_args,
    rates_for_url,
    spike_over,
    _rates,
    _timestamps,
)


def test_no_chat_means_no_opinion():
    assert chat_bonus([], 0, 30) == 1.0
    assert rates_for_url("") == []


def test_a_steady_chat_boosts_nothing():
    assert chat_bonus([5] * 200, 40, 80) == 1.0


def test_the_moment_chat_went_off_is_the_one_promoted():
    rates = [5] * 200
    rates[100] = 80

    assert chat_bonus(rates, 95, 105) > 1.0
    assert chat_bonus(rates, 0, 40) == 1.0


def test_a_spike_is_relative_to_this_stream_not_a_fixed_number():
    """A channel with 400 viewers and one with 40 have different normal
    rates; the question is busier than usual for THIS stream."""
    small = [2] * 100
    small[50] = 20
    big = [40] * 100
    big[50] = 400

    assert round(spike_over(small, 45, 55)) == round(spike_over(big, 45, 55))


def test_chat_outranks_loudness_but_still_cannot_carry_a_clip():
    """Chat is an opinion, volume is a measurement - but neither selects
    a window on its own."""
    from autoreel.audio_energy import energy_bonus

    loud = [-60.0] * 100
    loud[50] = 0.0
    busy = [1] * 100
    busy[50] = 100

    assert chat_bonus(busy, 45, 55) > energy_bonus(loud, 45, 55)
    assert chat_bonus(busy, 45, 55) <= 1.5


# ── Nothing is kept ──────────────────────────────────────────────────────

def test_the_chat_log_is_deleted_before_the_call_returns(tmp_path, monkeypatch):
    """The rule this module exists to hold."""
    from autoreel import chat_energy

    leaked = {}

    def fake_run(args, **kwargs):
        stem = args[args.index("-o") + 1]
        path = stem + ".live_chat.json"
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"videoOffsetTimeMsec": "1000"}) + "\n")
        leaked["folder"] = os.path.dirname(path)

        class Done:
            returncode = 0
        return Done()

    monkeypatch.setattr(chat_energy.subprocess, "run", fake_run)

    rates = rates_for_url("https://youtu.be/abc")

    assert rates, "the chat was counted"
    assert not os.path.exists(leaked["folder"]), \
        "the chat log outlived the call that read it"


def test_only_timestamps_are_taken_from_the_log(tmp_path):
    """No message text, no usernames, no ids reach a variable."""
    path = tmp_path / "chat.json"
    with open(path, "w", encoding="utf-8") as f:
        for offset in (1000, 2000, 2500):
            f.write(json.dumps({
                "videoOffsetTimeMsec": str(offset),
                "message": "SECRET MESSAGE TEXT",
                "authorName": "SECRET USER",
            }) + "\n")

    offsets = _timestamps(str(path))

    assert offsets == [1.0, 2.0, 2.5]
    assert all(isinstance(o, float) for o in offsets)


def test_counting_produces_numbers_only():
    counts = _rates([1.0, 1.2, 1.9, 5.0])
    assert counts[1] == 3 and counts[5] == 1
    assert all(isinstance(n, int) for n in counts)


def test_a_download_that_fails_is_not_an_error(monkeypatch):
    """Twitch, Kick and Rumble do not serve chat replay this way. No chat
    is a normal answer."""
    from autoreel import chat_energy

    def explode(args, **kwargs):
        raise OSError("no yt-dlp")

    monkeypatch.setattr(chat_energy.subprocess, "run", explode)
    assert rates_for_url("https://kick.com/x") == []


def test_the_download_takes_chat_and_nothing_else():
    args = download_args("https://youtu.be/abc", "/tmp/chat")

    assert "--skip-download" in args, "it must not fetch the video"
    assert "live_chat" in args
