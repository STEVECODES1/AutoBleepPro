"""Find (and fix) garbled emoji text on the Facebook Page and Instagram.

    python tools/fix_meta_mojibake.py            dry run: list what is garbled
    python tools/fix_meta_mojibake.py --apply    fix what the Graph API allows

Facebook video descriptions and Page post messages can be edited through
the Graph API. Instagram captions mostly cannot (the API only edits a few
fields) - those are listed so they can be edited in the app.
Tokens come from auto_uploader/.env and are never printed.
"""
import json
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from unmojibake import fix  # noqa: E402

GRAPH = "https://graph.facebook.com/v19.0"
LOG = os.path.join(ROOT, "auto_uploader", "logs", "meta_mojibake_fix.jsonl")
SINCE = int(time.mktime(time.strptime("2026-10-01", "%Y-%m-%d")))


def env():
    out = {}
    with open(os.path.join(ROOT, "auto_uploader", ".env"), encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _err(resp):
    try:
        return json.dumps(resp.json().get("error", {}).get("message", ""))[:200]
    except ValueError:
        return f"HTTP {resp.status_code}"


def pages(path, token, fields):
    url, params, out = f"{GRAPH}/{path}", {"access_token": token, "fields": fields,
                                            "limit": 100, "since": SINCE}, []
    while url:
        try:
            r = requests.get(url, params=params, timeout=60)
        except requests.RequestException as exc:
            print(f"  {path}: network error {type(exc).__name__}")
            break
        if r.status_code != 200:
            print(f"  {path}: {_err(r)}")
            break
        data = r.json()
        out += data.get("data", [])
        url, params = data.get("paging", {}).get("next"), None
    return out


def post(obj_id, token, field, value):
    try:
        r = requests.post(f"{GRAPH}/{obj_id}", data={"access_token": token, field: value}, timeout=60)
    except requests.RequestException as exc:
        return False, type(exc).__name__
    return r.status_code == 200, ("" if r.status_code == 200 else _err(r))


def main():
    apply = "--apply" in sys.argv
    cfg = env()
    jobs = []
    fb_tok, fb_id = cfg.get("FB_PAGE_TOKEN"), cfg.get("FB_PAGE_ID")
    ig_tok, ig_id = cfg.get("IG_PAGE_TOKEN") or fb_tok, cfg.get("IG_BUSINESS_ACCOUNT_ID")
    if fb_tok and fb_id:
        for v in pages(f"{fb_id}/videos", fb_tok, "id,description,created_time"):
            jobs.append(("fb video", v["id"], fb_tok, "description", v.get("description", ""), v.get("created_time")))
        for p in pages(f"{fb_id}/posts", fb_tok, "id,message,created_time"):
            jobs.append(("fb post", p["id"], fb_tok, "message", p.get("message", ""), p.get("created_time")))
    if ig_tok and ig_id:
        for m in pages(f"{ig_id}/media", ig_tok, "id,caption,permalink,timestamp"):
            jobs.append(("ig", m["id"], ig_tok, "caption", m.get("caption", ""), m.get("permalink")))
    bad = [j for j in jobs if fix(j[4]) != j[4]]
    print(f"checked {len(jobs)} (fb videos/posts + ig media since 10-01), garbled: {len(bad)}")
    for kind, obj, tok, field, text, when in bad:
        line = f"  {kind} {obj} {when}"
        if apply:
            ok, why = post(obj, tok, field, fix(text))
            line += "  FIXED" if ok else f"  not fixed: {why}"
            if ok:
                with open(LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"at": time.time(), "kind": kind, "id": obj,
                                        "old": text}, ensure_ascii=False) + "\n")
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
