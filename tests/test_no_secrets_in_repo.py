"""No credentials in the repo, and no way for one to arrive unnoticed.

WHY THIS EXISTS
---------------
The ignore list guarded `auto_uploader/youtube_token.json`. The uploader
actually writes `auto_uploader/youtube_shorts_token.json`. That one-word
near-miss sat untracked-but-not-ignored for weeks, holding a
`client_secret` and a `refresh_token`, and a single `git add -A` would
have published lasting access to the channel.

Nothing was watching for it. `git status` showed it every day and every
day it looked like the other 20 untracked files nobody had looked at
either. A file being visible is not a control.

So this file is the control, and it covers the three ways a credential
reaches a public repo:

  1. It is committed with the code            -> test_no_tracked_file_...
  2. It sits untracked, waiting for add -A    -> test_no_untracked_file_...
  3. The ignore list does not actually cover
     the path the code writes                 -> test_the_known_secret_...

The third is the one that failed. `test_the_known_secret_paths_are_ignored`
names the real filenames, not the ones someone meant to write, so a
rename in the uploaders that this file has not heard about shows up as a
failure rather than as silence.

WHAT THIS IS NOT
----------------
Not a scanner for every possible secret. The patterns below are the
high-precision ones - fixed prefixes and lengths that a program emits and
a person does not type by accident. A generic `password=` match was
rejected on purpose: it fires on documentation, on test fixtures and on
this file, and a test that cries wolf gets deleted.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fixed-prefix, fixed-length shapes. High precision on purpose: these are
# machine-generated, so a match is a real credential and not prose.
_CREDENTIAL_PATTERNS = [
    # OpenAI / DeepSeek / generic "sk-" keys
    (r"sk-[A-Za-z0-9]{32,}", "an sk- API key"),
    # xKiro - the 'xt' is the giveaway
    (r"sk-xt-[a-f0-9]{32,}", "an xKiro API key"),
    # Anthropic
    (r"sk-ant-api\d{2}-[A-Za-z0-9_\-]{40,}", "an Anthropic API key"),
    # OpenRouter
    (r"sk-or-v1-[a-f0-9]{40,}", "an OpenRouter API key"),
    # Groq
    (r"gsk_[A-Za-z0-9]{40,}", "a Groq API key"),
    # Cerebras
    (r"csk-[A-Za-z0-9]{40,}", "a Cerebras API key"),
    # Google API keys are exactly 39 chars after AIza
    (r"AIza[0-9A-Za-z_\-]{35}", "a Google API key"),
    # Meta / Facebook long-lived page and app tokens
    (r"EAA[A-Za-z0-9]{60,}", "a Meta access token"),
    # GitHub
    (r"gh[pousr]_[A-Za-z0-9]{36,}", "a GitHub token"),
    # Slack
    (r"xox[baprs]-[A-Za-z0-9\-]{10,}", "a Slack token"),
    # AWS
    (r"AKIA[0-9A-Z]{16}", "an AWS access key id"),
    # Zernio, which this project stores
    (r"sk_[a-f0-9]{60,}", "a Zernio API key"),
    # Discord webhooks are credentials too - anyone with the URL can post
    (r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_\-]{50,}",
     "a Discord webhook URL"),
    # A JWT is three base64url segments; the project's Upload-Post key is one
    (r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}",
     "a JWT"),
    # Private keys and the .pem/.key files that carry them
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
]

# Filenames that are credentials or a live session regardless of content.
# Checked against the ignore list by name, because these are the paths the
# code actually writes and a near-miss here is how the last one got out.
_SECRET_PATHS = [
    "auto_uploader/.env",
    "auto_uploader/.env.backup",
    "auto_uploader/client_secrets.json",
    # The pair that matters: the uploader writes the _shorts one.
    "auto_uploader/youtube_token.json",
    "auto_uploader/youtube_shorts_token.json",
    "auto_uploader/uploaded_hashes.json",
    "auto_uploader/posting_state.json",
    "auto_uploader/config.json",
    "cookies.txt",
    "auto_uploader/cookies.txt",
    "browser_profile/Default/Cookies",
    "auto_uploader/browser_profile/Default/Cookies",
]

# Extensions/names that are never source, so an untracked one is worth
# failing on by name alone.
_UNTRACKED_NAME_PATTERNS = [
    (r"(^|/)\.env(\.|$)", "an environment file"),
    (r".*_token\.json$", "an OAuth token file"),
    (r".*cookies?\.txt$", "a cookie export"),
    (r".*\.(pem|key|p12|pfx)$", "a key file"),
    (r"(^|/)id_(rsa|ed25519|ecdsa)$", "an SSH private key"),
    (r".*\.backup$", "a backup - .env backups carry every credential"),
]

# Nothing is allowlisted. The fixtures in the last test are assembled by
# concatenation ("-----BEGIN RSA " + "PRIVATE KEY-----") rather than
# written out, so this file contains no credential-shaped literal and
# needs no exemption from its own scan. Keep it that way: an allowlist
# that grows is a test that stopped working.


def _git(*args: str) -> str:
    """stdout of a git command, or "" if git cannot answer."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=_REPO, capture_output=True, text=True,
            timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _have_git() -> bool:
    return bool(_git("rev-parse", "--git-dir"))


requires_git = pytest.mark.skipif(
    not _have_git(),
    reason="no git checkout to inspect (a source tarball, or git missing)")


def _read(path: str, limit: int = 2_000_000) -> str:
    """Text of a repo file, or "" for binary/oversized/missing.

    Skipping big files is deliberate: none of the shapes above live in a
    multi-megabyte file, and reading every one would make this the slowest
    test in the suite for nothing.
    """
    full = os.path.join(_REPO, path)
    try:
        if os.path.getsize(full) > limit:
            return ""
        with open(full, "rb") as handle:
            raw = handle.read()
    except OSError:
        return ""
    if b"\x00" in raw[:8192]:
        return ""
    return raw.decode("utf-8", "replace")


def _find_credentials(text: str) -> list:
    """[(description, the match)] for every credential-shaped string."""
    hits = []
    for pattern, description in _CREDENTIAL_PATTERNS:
        for match in re.finditer(pattern, text):
            hits.append((description, match.group(0)))
    return hits


# ── 1. What is already committed ─────────────────────────────────────────

@requires_git
def test_no_tracked_file_contains_a_credential():
    """The leak that happened. Every committed file is readable, so this
    is a hard guarantee rather than a heuristic."""
    offenders = []
    for path in _git("ls-files").splitlines():
        path = path.strip()
        if not path:
            continue
        for description, value in _find_credentials(_read(path)):
            offenders.append(f"{path}: {description} ({value[:12]}...)")

    assert not offenders, (
        "a credential is committed to the repository:\n  "
        + "\n  ".join(offenders)
        + "\n\nRotate it first - it is in the history now and removing the "
          "line does not remove it - then make the file ignored if it is "
          "generated.")


# ── 2. What is sitting there waiting for git add -A ──────────────────────

@requires_git
def test_no_untracked_file_looks_like_a_credential():
    """The gap. Untracked-but-not-ignored is the exact state
    youtube_shorts_token.json was in, and `git status` gave it no more
    prominence than a log file.

    Untracked files that are ordinary source are fine and expected - a
    half-written module has to be allowed. Only names that are never
    source, or contents that carry a real credential, fail here.
    """
    problems = []
    # --porcelain, not --short: the machine-readable form is stable and
    # does not change with colour or column settings.
    for line in _git("status", "--porcelain").splitlines():
        if not line.startswith("?? "):
            continue
        path = line[3:].strip().strip('"')
        if path.endswith("/"):
            continue

        for pattern, description in _UNTRACKED_NAME_PATTERNS:
            if re.search(pattern, path):
                problems.append(f"{path}: the filename is {description}")
                break

        for description, value in _find_credentials(_read(path)):
            problems.append(f"{path}: contains {description} "
                            f"({value[:12]}...)")

    assert not problems, (
        "untracked files that must not reach a commit:\n  "
        + "\n  ".join(problems)
        + "\n\nEither add the path to .gitignore if it is generated, or "
          "delete it if it is a secret that should not exist on disk.")


# ── 3. The rules the code actually depends on ────────────────────────────
#
# The one that failed. Two near-misses to guard against, and they are
# different:
#
#   * a path in this list is NOT ignored       -> a future add -A leaks it
#   * a path in this list is no longer written -> this test guards a name
#     nobody uses, which is exactly how
#     'youtube_token.json' came to sit in the
#     ignore list covering nothing
#
# The first is checked against the ignore MATCHER rather than the
# filesystem, because in CI none of these files exist - they are all
# generated. So "is it ignored" is answerable anywhere.
#
# The second is checked by looking for the name in the tracked sources,
# which is what test_every_guarded_path_is_one_the_code_still_writes does.

@requires_git
@pytest.mark.parametrize("path", _SECRET_PATHS)
def test_the_known_secret_paths_are_ignored(path):
    """Every credential path the project actually uses is covered.

    check-ignore answers from the pattern list, so this works in CI where
    the files themselves are absent - which is the point: the ignore rule
    is what has to be right, and it is the thing that was wrong.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=_REPO, capture_output=True, text=True)

    assert result.returncode == 0, (
        f"{path} is NOT ignored. It is a credential or a live session, so "
        f"one `git add -A` publishes it. Add a rule to .gitignore.")


# Paths in _SECRET_PATHS that no line of code writes BY NAME, so the
# find-the-writer check below cannot look for them. Each is genuinely
# covered, just not by an exact filename:
#
#   .env.backup          - covered by the '.env.*' glob; it is a file
#                          --setup-meta creates, not one the code names.
#   */browser_profile/Default/Cookies
#                        - covered by the 'browser_profile/' DIRECTORY
#                          rule. The code passes the directory as
#                          user_data_dir and Firefox writes Cookies inside
#                          it, so the filename never appears in the source.
_NOT_WRITTEN_BY_NAME = {
    "auto_uploader/.env.backup",
    "browser_profile/Default/Cookies",
    "auto_uploader/browser_profile/Default/Cookies",
}


@requires_git
def test_every_guarded_path_is_one_the_code_still_writes():
    """The near-miss itself, as a test.

    .gitignore guarded 'youtube_token.json' while the uploader wrote
    'youtube_shorts_token.json'. The rule was right; the NAME was stale,
    and nothing noticed because a stale ignore rule looks exactly like a
    working one.

    So: a path in _SECRET_PATHS that no tracked file mentions is either a
    rename nobody has caught up with, or a rule that was never needed. Both
    are worth failing on - the first is a live leak, the second is a test
    quietly measuring nothing.
    """
    tracked = [p.strip() for p in _git("ls-files").splitlines() if p.strip()]
    sources = "".join(
        _read(p) for p in tracked
        # This file names every path by design; excluding it is what makes
        # the check meaningful rather than self-satisfying.
        if p != "tests/test_no_secrets_in_repo.py"
        and p.endswith((".py", ".bat", ".json", ".md", ".cfg", ".toml",
                        ".yml", ".yaml", ".txt")))

    stale = []
    for path in _SECRET_PATHS:
        if path in _NOT_WRITTEN_BY_NAME:
            continue
        if os.path.basename(path) not in sources:
            stale.append(path)

    assert not stale, (
        "these paths are guarded by .gitignore but nothing in the code "
        "writes them, so the rule is covering a name that no longer "
        "exists:\n  " + "\n  ".join(stale)
        + "\n\nThis is how 'youtube_token.json' came to guard nothing "
          "while the real token file went uncovered. Either the writer "
          "was renamed and the ignore rule needs to follow it, or the "
          "rule is dead and should go.")


@requires_git
def test_nothing_tracked_is_named_like_a_secret():
    """A committed .env or key file is a leak even if this run cannot read
    it - and it covers the case where the content check above skipped the
    file for being binary or large."""
    offenders = []
    for path in _git("ls-files").splitlines():
        path = path.strip()
        if not path or path.endswith(".example"):
            continue
        for pattern, description in _UNTRACKED_NAME_PATTERNS:
            if re.search(pattern, path):
                offenders.append(f"{path}: the filename is {description}")
                break

    assert not offenders, (
        "committed files that are credentials by name:\n  "
        + "\n  ".join(offenders))


def test_the_youtube_shorts_token_is_covered_by_name_and_by_glob():
    """The specific near-miss, kept as its own test so a rewrite of the
    ignore list has to consciously drop it.

    .gitignore guarded 'youtube_token.json' while the uploader wrote
    'youtube_shorts_token.json'. Two rules guard it now - the exact name
    and a *_token.json glob - and this asserts the glob exists, because
    the glob is what catches the next rename.
    """
    ignore = _read(".gitignore")

    assert "auto_uploader/youtube_shorts_token.json" in ignore, \
        "the exact filename the YouTube Shorts uploader writes is unguarded"
    assert "*_token.json" in ignore, (
        "without a glob, the next *_token.json the code writes is "
        "unguarded - which is how this one got out")


def test_the_credential_patterns_match_real_keys_and_not_prose():
    """This test is only worth having if the patterns it ships with
    actually fire on credentials and stay quiet on ordinary text.

    Both directions are asserted, because a pattern list that fails closed
    on prose gets deleted and a list that fails open is decoration.
    """
    should_match = [
        ("a Groq-style key", "GROQ_API_KEY=gsk_" + "a" * 48),
        ("the Upload-Post JWT", "eyJhbGciOiJIUzI1NiJ9."
                                + "A" * 30 + "." + "B" * 30),
        ("a Meta page token", "FB_PAGE_TOKEN=EAA" + "C" * 70),
        ("a Google API key", "GEMINI_API_KEY=AIza" + "b" * 35),
        ("a Discord webhook",
         "https://discord.com/api/webhooks/123456789012345678/"
         + "Z" * 60),
        ("a private key", "-----BEGIN RSA " + "PRIVATE KEY-----"),
    ]
    for label, text in should_match:
        assert _find_credentials(text), f"the patterns miss {label}"

    should_not_match = [
        ("an empty variable", "GROQ_API_KEY="),
        ("the example file", "GROQ_API_KEY=your_key_here"),
        ("a placeholder", "ANTHROPIC_API_KEY=sk-ant-..."),
        ("prose naming the field", "the token holds client_secret and "
                                   "refresh_token"),
        ("a doc URL", "get your key at https://console.groq.com/keys"),
        ("a short test value", 'monkeypatch.setenv("GROQ_API_KEY", "q")'),
    ]
    for label, text in should_not_match:
        hits = _find_credentials(text)
        assert not hits, f"the patterns fire on {label}: {hits}"
