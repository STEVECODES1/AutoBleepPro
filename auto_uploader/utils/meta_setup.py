"""
Fill in the Facebook/Instagram credentials from a token you already have.

FB_PAGE_TOKEN, FB_PAGE_ID, IG_PAGE_TOKEN and IG_BUSINESS_ACCOUNT_ID are
four separate values, and clicking through Meta's Graph API Explorer to
find each one is where most people give up. Only ONE of them is actually
a secret you have to obtain: a token with pages_show_list and
pages_manage_posts. Everything else is derivable from it.

    /me/accounts                      -> Page id AND that Page's own token
    /{page}?fields=instagram_business_account  -> the IG account id

A System User token, a long-lived user token, or a Page token all work as
the starting point, so this looks for any of them under any of the names
they are commonly stored as.

Nothing is guessed and nothing is posted. This reads from Graph and
writes to .env, and it says exactly what it found before it writes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from typing import Optional

GRAPH_API = "https://graph.facebook.com/v19.0"
_TIMEOUT = 30

# Where a usable Meta token might already be sitting. Ordered by how
# specific each one is - a Page token beats a system user token, because
# it needs no exchange.
TOKEN_NAMES = (
    "FB_PAGE_TOKEN",
    "META_SYSTEM_USER_TOKEN",
    "META_ACCESS_TOKEN",
    "META_TOKEN",
    "FB_SYSTEM_USER_TOKEN",
    "FB_ACCESS_TOKEN",
    "FB_USER_TOKEN",
    "IG_PAGE_TOKEN",
)

# What this writes. Kept in one place so the CLI, the tests and the
# summary all agree on the set.
WRITES = ("FB_PAGE_TOKEN", "FB_PAGE_ID",
          "IG_PAGE_TOKEN", "IG_BUSINESS_ACCOUNT_ID")


class MetaError(RuntimeError):
    """A Graph call failed, carrying the reason Graph actually gave."""


def _get(path: str, params: dict) -> dict:
    url = f"{GRAPH_API}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Graph puts the real reason in the body, never the status line.
        # "400 Bad Request" on its own is useless; the body says whether
        # the token expired, lacks a scope, or is for the wrong app.
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            message = detail.get("error", {}).get("message", "")
        except Exception:
            message = ""
        raise MetaError(message or f"HTTP {exc.code} from {path}") from None
    except Exception as exc:
        raise MetaError(str(exc)) from None


# ── Reading and writing .env ─────────────────────────────────────────────

def read_env(path: str) -> dict:
    """Parse a .env into a dict. Missing file = {}."""
    values: dict = {}
    if not os.path.isfile(path):
        return values
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def update_env(path: str, values: dict) -> str:
    """Set these keys in .env, keeping everything else exactly as it is.

    Rewritten line by line rather than regenerated: a .env holds hand-
    written comments and credentials for half a dozen services, and
    regenerating it would quietly drop whatever this module does not know
    about. A dated backup is taken first, because this file is the one
    thing in the project that cannot be recovered from git.
    """
    backup = ""
    lines: list = []
    if os.path.isfile(path):
        backup = f"{path}.backup-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, backup)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()

    remaining = dict(values)
    out: list = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in remaining and not line.lstrip().startswith("#"):
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)

    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# Written by: python main.py --setup-meta")
        for key, value in remaining.items():
            out.append(f"{key}={value}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out).rstrip() + "\n")
    return backup


# ── Talking to Graph ─────────────────────────────────────────────────────

def find_token(env: dict) -> tuple:
    """(name, token) of the first usable-looking Meta token, or ("", "")."""
    for name in TOKEN_NAMES:
        value = (env.get(name) or os.environ.get(name) or "").strip()
        if value:
            return name, value
    return "", ""


def token_scopes(token: str) -> list:
    """Permissions this token actually carries.

    Worth checking before anything else: a token missing
    pages_manage_posts will list Pages perfectly and then fail on the
    first real post, which looks like a completely different problem.
    """
    data = _get("debug_token", {"input_token": token, "access_token": token})
    return list(data.get("data", {}).get("scopes", []) or [])


def list_pages(token: str) -> list:
    """Every Page this token can act for: [{id, name, access_token}].

    The Page's OWN token comes back in this response, which is the part
    that makes the rest automatic - it is what FB_PAGE_TOKEN wants, and
    it does not expire the way a user token does.
    """
    data = _get("me/accounts", {"access_token": token,
                                "fields": "id,name,access_token"})
    return list(data.get("data", []) or [])


def instagram_account(page_id: str, page_token: str) -> str:
    """The IG Business account id linked to this Page, or "".

    Empty is a normal answer, not an error: an IG account has to be a
    Business/Creator account AND linked to the Page in Meta Business
    Suite before it exists here at all.
    """
    data = _get(page_id, {"access_token": page_token,
                          "fields": "instagram_business_account"})
    return str((data.get("instagram_business_account") or {}).get("id", ""))


REQUIRED_SCOPES = ("pages_show_list", "pages_manage_posts")


def token_expiry(token: str) -> Optional[int]:
    """Unix seconds this token dies, 0 if it never does, None if unknown.

    Worth asking BEFORE the first post rather than finding out from a
    failed one at 3am: "Session has expired" is what a short-lived token
    looks like the next morning.
    """
    try:
        data = _get("debug_token", {"input_token": token,
                                    "access_token": token})
    except MetaError:
        return None
    info = (data or {}).get("data") or {}
    if info.get("expires_at") is None:
        return None
    try:
        return int(info["expires_at"])
    except (TypeError, ValueError):
        return None


def describe_expiry(expires_at: Optional[int]) -> str:
    """The expiry in words, for someone deciding whether to act."""
    if expires_at is None:
        return "unknown expiry"
    if expires_at == 0:
        return "never expires"
    left = expires_at - time.time()
    if left <= 0:
        return "ALREADY EXPIRED"
    if left < 3600:
        return f"expires in {left / 60:.0f} minutes"
    if left < 86400:
        return f"expires in {left / 3600:.0f} hours"
    return f"expires in {left / 86400:.0f} days"


def exchange_for_long_lived(token: str, app_id: str, app_secret: str) -> str:
    """Trade a short-lived user token for a 60-day one.

    THIS is the step that was missing, and the reason Facebook kept
    expiring overnight. A Page token inherits the lifetime of the user
    token it was read from: taken from the ~1-hour token the Graph API
    Explorer hands out, the Page token dies within the hour. Taken from a
    long-lived user token, the Page token does not expire at all.

    Needs the app id and secret, which is why they belong in .env - there
    is no way to do this exchange without them.
    """
    if not (app_id and app_secret):
        raise MetaError(
            "FB_APP_ID and FB_APP_SECRET are needed to make a token that "
            "does not expire. Both are at developers.facebook.com -> your "
            "app -> Settings -> Basic. Then: python main.py --set-env "
            "FB_APP_ID=... FB_APP_SECRET=...")
    data = _get("oauth/access_token", {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": token,
    })
    long_lived = (data or {}).get("access_token", "")
    if not long_lived:
        raise MetaError("the exchange returned no token")
    return long_lived


def resolve(token: str, page_choice: str = "") -> dict:
    """Token -> everything needed, or raise MetaError explaining what is
    missing. Returns {values, page_name, scopes, warnings}."""
    warnings: list = []

    try:
        scopes = token_scopes(token)
    except MetaError as exc:
        # Not fatal on its own - some tokens cannot introspect themselves
        # but work fine for the calls below.
        scopes, warnings = [], [f"could not read the token's scopes ({exc})"]

    missing = [s for s in REQUIRED_SCOPES if scopes and s not in scopes]
    if missing:
        warnings.append(
            f"the token is missing {', '.join(missing)} - listing Pages may "
            "work but posting will fail. Add the scope in the Meta app, then "
            "regenerate the token.")

    pages = list_pages(token)
    if not pages:
        raise MetaError(
            "the token can see no Pages. Either it lacks pages_show_list, or "
            "it is a System User token that has not been given access to the "
            "Page in Meta Business Suite (Business Settings -> System Users "
            "-> Assign Assets -> Pages).")

    page = pages[0]
    if page_choice:
        wanted = page_choice.strip().lower()
        matched = [p for p in pages
                   if wanted in str(p.get("name", "")).lower()
                   or wanted == str(p.get("id", ""))]
        if not matched:
            names = ", ".join(f"{p.get('name')} ({p.get('id')})" for p in pages)
            raise MetaError(f"no Page matching '{page_choice}'. Found: {names}")
        page = matched[0]
    elif len(pages) > 1:
        warnings.append(
            "several Pages are available and the first was used: "
            + ", ".join(f"{p.get('name')} ({p.get('id')})" for p in pages)
            + ". Re-run with --meta-page \"<name>\" to pick another.")

    page_id = str(page.get("id", ""))
    page_token = str(page.get("access_token", "")) or token
    values = {"FB_PAGE_TOKEN": page_token, "FB_PAGE_ID": page_id}

    try:
        ig_id = instagram_account(page_id, page_token)
    except MetaError as exc:
        ig_id = ""
        warnings.append(f"could not check for a linked Instagram account ({exc})")
    if ig_id:
        # Instagram publishing goes through the PAGE's token; there is no
        # separate Instagram one to fetch.
        values["IG_BUSINESS_ACCOUNT_ID"] = ig_id
        values["IG_PAGE_TOKEN"] = page_token
    else:
        warnings.append(
            "no Instagram Business account is linked to this Page, so "
            "Instagram stays unconfigured. Link it in Meta Business Suite "
            "(the IG account must be Business or Creator, not personal).")

    return {"values": values, "page_name": str(page.get("name", "")),
            "scopes": scopes, "warnings": warnings}


def setup(env_path: str, token: str = "", page_choice: str = "",
          write: bool = True) -> dict:
    """Resolve and (optionally) write.

    The user token is upgraded to a long-lived one FIRST when the app
    credentials are present, because the Page token is read from it and
    inherits its lifetime. Skipping that step is what made Facebook stop
    working every morning.
    """
    env = read_env(env_path)
    if not token:
        name, token = find_token(env)
        if not token:
            raise MetaError(
                "no Meta token found in .env. Looked for: "
                + ", ".join(TOKEN_NAMES)
                + ". Paste one with --meta-token \"<token>\", or get one from "
                "developers.facebook.com -> your app -> Graph API Explorer, "
                "with pages_show_list and pages_manage_posts.")
        result_source = name
    else:
        result_source = "--meta-token"

    exchanged = False
    app_id = env.get("FB_APP_ID", "") or os.environ.get("FB_APP_ID", "")
    app_secret = (env.get("FB_APP_SECRET", "")
                  or os.environ.get("FB_APP_SECRET", ""))
    expires_at = token_expiry(token)
    if app_id and app_secret and expires_at:
        # expires_at of 0 means it already never expires; None means the
        # token would not say, and exchanging anyway is harmless.
        try:
            token = exchange_for_long_lived(token, app_id, app_secret)
            exchanged = True
        except MetaError as exc:
            print(f"[Meta] Could not make the token long-lived: {exc}")

    result = resolve(token, page_choice)
    result["source"] = result_source
    result["exchanged"] = exchanged
    result["page_token_expiry"] = describe_expiry(
        token_expiry(result["values"].get("FB_PAGE_TOKEN", "")))
    result["backup"] = ""
    result["written"] = False
    if write:
        result["backup"] = update_env(env_path, result["values"])
        result["written"] = True
    return result


# ── Learning the account's own caption style ─────────────────────────────

def recent_captions(account_id: str, token: str, limit: int = 25) -> list:
    """The account's own recent captions, newest first.

    Its own posts, through its own credentials - the documented
    /{ig-user}/media edge. Nothing is scraped and no other account is
    read.
    """
    data = _get(f"{account_id}/media",
                {"fields": "caption,media_type,timestamp",
                 "limit": str(limit), "access_token": token})
    return [item for item in (data.get("data") or []) if item.get("caption")]


def _hashtags(caption: str) -> list:
    return re.findall(r"#\w+", caption or "")


def _emoji_run(caption: str) -> str:
    """The longest unbroken run of non-text characters on the first line.

    That run is the account's signature - "🤣🤣🤣💀💀💀" - and it is the
    part a generated caption has to reproduce to look like the others.
    """
    first = (caption or "").splitlines()[0] if caption else ""
    runs = re.findall(r"[^\w\s#@,.!?'\"()\-:/]+", first)
    return max(runs, key=len) if runs else ""


def study_captions(captions: list) -> dict:
    """What these captions have in common. Frequencies, not guesses."""
    from collections import Counter

    tags, emoji, links = Counter(), Counter(), Counter()
    for item in captions:
        caption = item.get("caption", "")
        tags.update(t.lower() for t in _hashtags(caption))
        run = _emoji_run(caption)
        if run:
            emoji[run] += 1
        for line in caption.splitlines():
            for url in re.findall(r"https?://\S+", line):
                # The label matters as much as the URL - "Link Youtube
                # monkey - <url>" is the account's own phrasing.
                label = line.split(url)[0].strip(" -–—:")
                links[(label, url)] += 1
    return {
        "sampled": len(captions),
        "hashtags": tags.most_common(6),
        "emoji": emoji.most_common(3),
        "links": links.most_common(4),
    }


def suggest_template(study: dict) -> str:
    """A caption_template built from what the account actually posts."""
    run = study["emoji"][0][0] if study["emoji"] else ""
    tags = " ".join(tag for tag, _ in study["hashtags"][:2])
    head = f"{{title}} {run}{tags}".strip()
    lines = [head]
    for (label, url), _ in study["links"]:
        lines.append("")
        lines.append(f"{label} - {url}" if label else url)
    return "\n".join(lines)
