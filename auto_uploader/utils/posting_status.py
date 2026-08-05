"""
Answers "is posting set up correctly?" without posting anything.

Two levels, deliberately separate:

- **Offline** (`report`): which credentials are present in .env, and what
  the guard would decide right now. No network at all, so it is safe to
  run any time and tells you whether the config is coherent.
- **Live** (`verify`): one READ-ONLY call per platform, asking each API
  who the token belongs to. Nothing is created, published, or deleted.

The live check exists because the failure it catches is expensive: a
token that is expired, scoped wrong, or points at the wrong Page looks
exactly like a working token until the first publish attempt, and the
first publish attempt is against a real account with real followers.
Finding out with a read is free; finding out with a write is not.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

GRAPH_API = "https://graph.facebook.com/v19.0"
_TIMEOUT = 20

OK = "ok"
MISSING = "missing"
FAILED = "failed"
SKIPPED = "skipped"

# platform -> the .env variables it cannot work without.
REQUIRED_ENV = {
    "instagram": ("IG_PAGE_TOKEN", "IG_BUSINESS_ACCOUNT_ID"),
    "facebook": ("FB_PAGE_TOKEN", "FB_PAGE_ID"),
    "x": ("TWITTER_API_KEY", "TWITTER_API_SECRET",
          "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_SECRET"),
    # reddit is resolved per named account, so it is handled separately.
}


@dataclass
class Check:
    platform: str
    state: str                  # OK | MISSING | FAILED | SKIPPED
    detail: str = ""
    identity: str = ""          # who the credentials turned out to be

    @property
    def symbol(self) -> str:
        return {OK: "OK  ", MISSING: "-- ", FAILED: "FAIL", SKIPPED: "--  "}.get(
            self.state, "?   ")


def missing_env(platform: str, reddit_account: str = "") -> list:
    """Which required variables are absent for this platform."""
    if platform == "reddit":
        from utils.social_promoter import reddit_credentials_missing
        missing = reddit_credentials_missing(reddit_account)
        if not os.environ.get("REDDIT_SUBREDDIT", "").strip():
            missing.append("REDDIT_SUBREDDIT")
        return missing
    return [name for name in REQUIRED_ENV.get(platform, ())
            if not os.environ.get(name, "").strip()]


def _graph_get(path: str, params: dict) -> dict:
    """One read-only Graph API GET. Raises on anything but success."""
    url = f"{GRAPH_API}/{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "AutoBleepPro"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Graph puts the useful part in the body, not the status line -
        # "token expired" and "missing scope" are both plain 400s.
        try:
            body = json.loads(exc.read().decode("utf-8"))
            message = body.get("error", {}).get("message", "")
        except Exception:
            message = ""
        raise RuntimeError(message or f"HTTP {exc.code}") from None


# ── Per-platform read-only checks ────────────────────────────────────────

def _check_instagram() -> Check:
    missing = missing_env("instagram")
    if missing:
        return Check("instagram", MISSING, ", ".join(missing))
    try:
        data = _graph_get(os.environ["IG_BUSINESS_ACCOUNT_ID"], {
            "fields": "username,followers_count",
            "access_token": os.environ["IG_PAGE_TOKEN"],
        })
    except Exception as exc:
        return Check("instagram", FAILED, str(exc))
    username = data.get("username", "")
    if not username:
        return Check("instagram", FAILED,
                     "token works but the ID returned no username - is "
                     "IG_BUSINESS_ACCOUNT_ID the Instagram BUSINESS account "
                     "id rather than the Page id?")
    followers = data.get("followers_count")
    detail = f"{followers} followers" if followers is not None else ""
    return Check("instagram", OK, detail, identity=f"@{username}")


def _check_facebook() -> Check:
    missing = missing_env("facebook")
    if missing:
        return Check("facebook", MISSING, ", ".join(missing))
    try:
        data = _graph_get(os.environ["FB_PAGE_ID"], {
            "fields": "name,fan_count",
            "access_token": os.environ["FB_PAGE_TOKEN"],
        })
    except Exception as exc:
        return Check("facebook", FAILED, str(exc))
    name = data.get("name", "")
    if not name:
        return Check("facebook", FAILED, "token works but the Page id "
                                         "returned no name")
    fans = data.get("fan_count")
    return Check("facebook", OK, f"{fans} followers" if fans is not None else "",
                 identity=name)


def x_credential_shape_problems() -> list:
    """Malformed X credentials, spotted without a network call.

    A 401 from X says only "authentication failed" - it never says which
    of the four values is wrong. Most of the time it is one of these,
    and all of them are visible locally.
    """
    problems = []
    values = {name: os.environ.get(name, "") for name in REQUIRED_ENV["x"]}

    for name, raw in values.items():
        value = raw.strip()
        if raw != value:
            problems.append(f"{name} has leading/trailing whitespace")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            problems.append(
                f"{name} is wrapped in quotes - .env values are literal, so "
                "the quotes become part of the credential")
        if value.lower().startswith(("your_", "paste", "<", "xxx")):
            problems.append(f"{name} still looks like a placeholder")

    # The access token is the one credential with a recognisable shape:
    # X issues it as "<numeric user id>-<secret>". Nothing else in the set
    # contains a hyphen, so a token without one is almost always the API
    # key pasted into the wrong variable - which produces a 401 that looks
    # identical to an expired token.
    access = values["TWITTER_ACCESS_TOKEN"].strip()
    if access and "-" not in access:
        problems.append(
            "TWITTER_ACCESS_TOKEN has no '-' in it. A real access token "
            "looks like '1234567890-AbCd...'; this looks like the API key "
            "pasted into the wrong variable")
    if access and access == values["TWITTER_API_KEY"].strip():
        problems.append("TWITTER_ACCESS_TOKEN and TWITTER_API_KEY are identical")
    if (values["TWITTER_ACCESS_SECRET"].strip()
            == values["TWITTER_API_SECRET"].strip()
            and values["TWITTER_API_SECRET"].strip()):
        problems.append("TWITTER_ACCESS_SECRET and TWITTER_API_SECRET are identical")
    return problems


def x_app_credentials_work() -> tuple:
    """(ok, detail) for the API key/secret pair alone.

    Asks X for an app-only bearer token, which uses ONLY the consumer
    key and secret. That splits a 401 in half: if this succeeds the app
    credentials are fine and the access token pair is the problem, which
    is the difference between "recreate the app" and "regenerate the
    tokens".
    """
    import base64

    key = os.environ.get("TWITTER_API_KEY", "").strip()
    secret = os.environ.get("TWITTER_API_SECRET", "").strip()
    if not key or not secret:
        return False, "API key/secret not set"

    basic = base64.b64encode(
        f"{urllib.parse.quote(key)}:{urllib.parse.quote(secret)}".encode()
    ).decode()
    request = urllib.request.Request(
        "https://api.twitter.com/oauth2/token",
        data=b"grant_type=client_credentials",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)
    return bool(body.get("access_token")), ""


def _x_401_guidance() -> str:
    """What to actually do about a 401, narrowed as far as we can get."""
    shape = x_credential_shape_problems()
    if shape:
        return "401 Unauthorized. " + "; ".join(shape)

    app_ok, detail = x_app_credentials_work()
    if app_ok:
        return ("401 Unauthorized, but the API key/secret ARE valid - so the "
                "problem is the ACCESS TOKEN pair. Changing an app's "
                "permission does not update tokens that already exist: go to "
                "developer.twitter.com -> your app -> Keys and tokens -> "
                "Regenerate Access Token and Secret, then update .env. "
                "Check 'User authentication settings' says Read and Write "
                "BEFORE regenerating, or the new tokens are read-only too")
    return ("401 Unauthorized, and the API key/secret are rejected too "
            f"({detail}) - so it is not just the access tokens. Confirm .env "
            "holds the API Key and Secret from the same app, and that the app "
            "is attached to a Project (X API v2 rejects standalone apps)")


def _check_x() -> Check:
    missing = missing_env("x")
    if missing:
        return Check("x", MISSING, ", ".join(missing))

    shape = x_credential_shape_problems()
    try:
        import tweepy
    except ImportError:
        return Check("x", FAILED, "credentials are set but tweepy is not "
                                  "installed - pip install tweepy")
    try:
        client = tweepy.Client(
            consumer_key=os.environ["TWITTER_API_KEY"].strip(),
            consumer_secret=os.environ["TWITTER_API_SECRET"].strip(),
            access_token=os.environ["TWITTER_ACCESS_TOKEN"].strip(),
            access_token_secret=os.environ["TWITTER_ACCESS_SECRET"].strip(),
        )
        me = client.get_me()
    except Exception as exc:
        text = str(exc)
        if "401" in text or "Unauthorized" in text:
            return Check("x", FAILED, _x_401_guidance())
        if "403" in text or "Forbidden" in text:
            return Check("x", FAILED,
                         "403 Forbidden - the credentials authenticate but "
                         "are not allowed to do this. Usually the app is "
                         "Read-only, or it is not attached to a Project. Set "
                         "Read and Write, then REGENERATE the access tokens")
        if "429" in text:
            return Check("x", FAILED, "429 rate limited - wait and retry; "
                                      "this says nothing about the tokens")
        return Check("x", FAILED, "; ".join(shape + [text]) if shape else text)

    username = getattr(getattr(me, "data", None), "username", "")
    if not username:
        return Check("x", FAILED, "get_me() returned no user - the tokens may "
                                  "be read-only; posting needs Read + Write")
    return Check("x", OK, identity=f"@{username}")


def _check_reddit(account: str = "") -> Check:
    missing = missing_env("reddit", account)
    if missing:
        return Check("reddit", MISSING, ", ".join(missing))
    try:
        import praw
    except ImportError:
        return Check("reddit", FAILED, "credentials are set but praw is not "
                                       "installed - pip install praw")
    try:
        from utils.social_promoter import reddit_credentials

        creds = reddit_credentials(account)
        reddit = praw.Reddit(
            client_id=creds["client_id"], client_secret=creds["client_secret"],
            username=creds["username"], password=creds["password"],
            user_agent=f"AutoBleepPro/2.0 (by u/{creds['username']})")
        me = reddit.user.me()
    except Exception as exc:
        return Check("reddit", FAILED, str(exc))
    return Check("reddit", OK, identity=f"u/{me}")


_CHECKS = {
    "instagram": _check_instagram,
    "facebook": _check_facebook,
    "x": _check_x,
}


def verify(platforms: Optional[list] = None, reddit_account: str = "") -> list:
    """Read-only identity check per platform. Makes network calls."""
    names = platforms or ["instagram", "facebook", "x", "reddit"]
    results = []
    for name in names:
        if name == "reddit":
            results.append(_check_reddit(reddit_account))
        elif name in _CHECKS:
            results.append(_CHECKS[name]())
        else:
            results.append(Check(name, SKIPPED, "no credential check written"))
    return results


# ── Reporting ────────────────────────────────────────────────────────────

def report(cfg_dict: dict, guard, reddit_account: str = "",
           live: bool = False) -> None:
    """Print the posting picture: config, guard verdict, credentials."""
    posting = cfg_dict.get("posting", {}) or {}
    platforms = list((posting.get("platforms") or {}).keys())

    print("\n" + "=" * 70)
    print("POSTING STATUS")
    print("=" * 70)

    engaged, why = guard.kill_switch_engaged()
    if not engaged:
        print("Master switch : ON - platforms below post when their own "
              "switch is on too")
    elif "posting.enabled" in why:
        # The shipped default, not an incident. Saying "HALTED" here reads
        # as something broke, and it hasn't.
        print("Master switch : OFF (posting.enabled is false) - this is the "
              "shipped default")
        print("                Set posting.enabled true in config.json when "
              "you are ready to post.")
    else:
        print(f"Master switch : STOPPED - {why}")
    print(f"Kill switch   : create {posting.get('kill_switch_file', './STOP_POSTING')} "
          "to stop everything, including a running --watch")

    print("\nPer platform (the guard's actual verdict right now):")
    for name, allowed, reason in guard.status(platforms):
        print(f"  {'ALLOW' if allowed else 'BLOCK'}  {name:<16} {reason}")

    print("\nCredentials in .env:")
    for name in platforms:
        if name == "facebook_group":
            print(f"  --    {name:<16} no approved API route - manual only, by design")
            continue
        missing = missing_env(name, reddit_account)
        if missing:
            print(f"  --    {name:<16} missing: {', '.join(missing)}")
        else:
            print(f"  OK    {name:<16} all variables present")

    if not live:
        print("\n(Add --verify to check the credentials against each API. "
              "Read-only: it asks who the token belongs to, posts nothing.)")
        print("=" * 70)
        return

    print("\nLive check (read-only - asks each API who you are, posts nothing):")
    for check in verify([p for p in platforms if p != "facebook_group"],
                        reddit_account):
        identity = f"  as {check.identity}" if check.identity else ""
        detail = f"  ({check.detail})" if check.detail else ""
        print(f"  {check.symbol}  {check.platform:<16}{identity}{detail}")
    print("=" * 70)
