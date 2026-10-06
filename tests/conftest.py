"""Shared setup for the test suite.

config.json is deliberately NOT tracked in git - config.example.json is.
Settings are the operator's, and a `git pull` must never collide with a
switch they flipped locally. That is a real problem this project hit
three times in one night: every pull aborted with

    error: Your local changes to the following files would be
    overwritten by merge: auto_uploader/config.json

leaving the machine several builds behind while fixes sat unused.

The consequence for tests is that a fresh checkout has no config.json.
Rather than teach fourteen test files to look in two places, this makes
the file exist before anything is collected - which is also exactly what
main.py does on its first run.
"""

from __future__ import annotations

import os
import shutil

import pytest

_UPLOADER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "auto_uploader")

# Every variable that makes a live model reachable. See _no_live_llm_keys.
_LLM_KEY_NAMES = (
    "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "CEREBRAS_API_KEY", "XKIRO_API_KEY", "GROQ_API_KEY",
    # Added to llm_highlights later and never to this list, so a machine
    # with any of them in .env failed the provider-order tests.
    "NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY", "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
)

# Posting services whose key alone decides "ready". A real one in .env
# made "not ready without a key" fail on the machine that has it. A test
# that wants one sets it with monkeypatch.
_SERVICE_KEY_NAMES = (
    "ZERNIO_API_KEY", "BUFFER_API_KEY", "CLOUDINARY_URL",
    "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET",
)


def pytest_configure(config):
    live = os.path.join(_UPLOADER, "config.json")
    example = os.path.join(_UPLOADER, "config.example.json")
    if not os.path.isfile(live) and os.path.isfile(example):
        shutil.copyfile(example, live)


@pytest.fixture(autouse=True)
def _no_live_llm_keys(monkeypatch):
    """No test may reach a real model, whatever is in .env.

    utils/config.py calls load_dotenv() at import, so a developer's real
    credentials - and the runner's - sit in os.environ for the whole
    session. Anything that asks a model for something then behaves
    differently on a machine with working keys than it does on CI.

    That is not hypothetical. caption_for() asks a model to write a
    per-platform caption and falls back to the config template on any
    failure, and five caption tests assert the TEMPLATE path. They passed
    for as long as no configured provider actually answered - Gemini's
    free tier was over quota and the rest were unset or wrong - and broke
    the moment a reachable provider was added: the template assertions
    started receiving real captions instead, which is correct production
    behaviour and a broken test.

    Clearing the keys here means those tests measure the fallback they
    are named for, and the next provider anyone adds cannot silently
    change what the suite checks. A test that wants the model path sets
    its own key with monkeypatch, which still works - this only removes
    what the environment leaked in.
    """
    for name in _LLM_KEY_NAMES + _SERVICE_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _gemini_not_resting():
    """autoreel.llm_highlights leaves Gemini alone for 10 minutes after an
    overload - module state that must not leak from one test into the
    next."""
    try:
        from autoreel import llm_highlights
    except Exception:
        yield
        return
    llm_highlights._GEMINI_COOLDOWN.update(until=0.0, why="")
    yield
    llm_highlights._GEMINI_COOLDOWN.update(until=0.0, why="")


@pytest.fixture(autouse=True)
def _chrome_not_marked_down():
    """utils.rumble_uploader skips Chrome for 10 minutes after it fails to
    attach - module state, reset between tests."""
    import sys as _sys

    _sys.path.insert(0, _UPLOADER)
    try:
        from utils import rumble_uploader
    except Exception:
        yield
        return
    rumble_uploader._CHROME_DOWN.update(until=0.0, why="")
    yield
    rumble_uploader._CHROME_DOWN.update(until=0.0, why="")


@pytest.fixture(autouse=True)
def _rumble_page_not_fetched_for_real(monkeypatch, tmp_path):
    """The Rumble channel page costs ScrapingBee credits and is cached on
    disk for hours. No test may spend the credits, and none may read the
    machine's real cache - a fresh one answered a Firecrawl test with the
    real channel's 25 videos."""
    import sys as _sys

    _sys.path.insert(0, _UPLOADER)
    monkeypatch.delenv("SCRAPINGBEE_API_KEY", raising=False)
    try:
        from utils import rumble_checker
    except Exception:
        return
    monkeypatch.setattr(rumble_checker, "_CACHE_PATH",
                        str(tmp_path / "rumble_channel_cache.json"))


@pytest.fixture(autouse=True)
def _inside_posting_hours(monkeypatch):
    """publish_guard keeps the Postproxy routes to peak hours, local time.
    The suite must not pass at 3pm and fail at 3am - pin it to midday. A
    test about the window patches _local_hour itself."""
    import sys as _sys

    _sys.path.insert(0, _UPLOADER)
    try:
        import publish_guard
    except Exception:
        return
    monkeypatch.setattr(publish_guard, "_local_hour", lambda now: 12.0)
