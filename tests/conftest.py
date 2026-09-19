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
    for name in _LLM_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
