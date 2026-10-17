"""Clips held back for slurs, sent to Discord so a person can watch and decide.

A clip with a severe slur (or full of the everyday one) is held from
YouTube, Instagram, TikTok, Facebook and X; Rumble still gets it. Holding
is the safe default, but some held clips are worth posting, and the only
way to know is to watch. So each held clip goes to the Discord webhook
once, as a small preview that plays right in Discord, with its number:

    python -X utf8 tools/held_review.py --send          new held clips -> Discord
    python -X utf8 tools/held_review.py --post 3 7      post #3 and #7 after all
    python -X utf8 tools/held_review.py --list

--post lifts the slur hold for that clip only and retries its held jobs.
Holds for kids or a gambling site on screen are NOT lifted: those are the
ones that get a channel struck, whoever approves them.
"""
import json
import os
import subprocess
import tempfile
import time
import urllib.request
import uuid

UPLOADER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(UPLOADER, "logs", "held_review.json")
APPROVED = os.path.join(UPLOADER, "logs", "held_approved.json")
DISCORD_LIMIT = 9_500_000          # free servers take 10 MB per file
# Held clips are kept here: the working copy is cleaned up after the stream,
# and a clip approved a day later still has to be postable. 14 days.
KEEP = os.path.join(UPLOADER, "logs", "held_clips")
KEEP_DAYS = 14


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def approved(video_path: str) -> bool:
    """Has a person cleared this clip's slur hold? (clip_queue asks this.)"""
    return os.path.basename(video_path or "") in set(_read(APPROVED, []))


def _queue():
    from job_queue import JobQueue
    return JobQueue(path=os.path.join(UPLOADER, "clip_jobs.json"))


def held_clips(queue=None):
    """{clip basename: {"path", "caption", "why", "jobs", "rumble"}} - slur holds
    whose file is still on disk."""
    queue = queue or _queue()
    out = {}
    jobs = queue.list_jobs()
    for job in jobs:
        err = str(getattr(job, "last_error", "") or "")
        if job.state != "failed" or not err.startswith("held:"):
            continue
        if "slur" not in err:          # kids / gambling stay held
            continue
        if not os.path.isfile(job.clip_path):
            continue
        name = os.path.basename(job.clip_path)
        entry = out.setdefault(name, {"path": job.clip_path, "caption": job.caption or "",
                                      "why": err[len("held:"):].strip(), "jobs": [], "rumble": ""})
        entry["jobs"].append(job.id)
    for job in jobs:
        name = os.path.basename(job.clip_path or "")
        if name in out and "rumble" in job.platform and job.state == "done" and job.result_url:
            out[name]["rumble"] = job.result_url
    return out


def _preview(path):
    """A small mp4 that plays inside Discord, or "" if it cannot be made."""
    out = os.path.join(tempfile.gettempdir(), f"held_{uuid.uuid4().hex[:8]}.mp4")
    for kbps in (700, 380, 220):
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", path, "-vf", "scale=480:-2",
               "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k",
               "-maxrate", f"{int(kbps * 1.3)}k", "-bufsize", f"{kbps * 2}k",
               "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", out]
        try:
            subprocess.run(cmd, check=True, timeout=300)
        except Exception:
            return ""
        if os.path.getsize(out) <= DISCORD_LIMIT:
            return out
    return ""


def _post(webhook, text, file_path=""):
    boundary = uuid.uuid4().hex
    parts = [(f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
              f"Content-Type: application/json\r\n\r\n").encode()
             + json.dumps({"content": text[:1990]}).encode() + b"\r\n"]
    if file_path:
        with open(file_path, "rb") as f:
            data = f.read()
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; '
                      f'filename="held_clip.mp4"\r\nContent-Type: video/mp4\r\n\r\n').encode()
                     + data + b"\r\n")
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(webhook, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "AutoUploader"})
    urllib.request.urlopen(req, timeout=120).read()


def send_new(say=print, limit=25):
    """Post every not-yet-sent held clip to Discord. Returns how many went."""
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        say("[Held] DISCORD_WEBHOOK_URL is not set - nothing sent.")
        return 0
    state = _read(STATE, {"next": 1, "sent": {}})
    sent = 0
    for name, info in sorted(held_clips().items(), key=lambda kv: os.path.getmtime(kv[1]["path"])):
        if name in state["sent"] or sent >= limit:
            continue
        number = state["next"]
        try:
            import shutil
            os.makedirs(KEEP, exist_ok=True)
            shutil.copy2(info["path"], os.path.join(KEEP, name))
        except OSError:
            pass
        preview = _preview(info["path"])
        line = info["caption"].strip().split("\n")[0][:180]
        text = (f"**HELD CLIP #{number}** - {line}\n"
                f"Held from YouTube/IG/TikTok/FB/X: {info['why']}\n"
                + (f"Already on Rumble: {info['rumble']}\n" if info["rumble"] else "")
                + f"Worth posting? Tell Claude **post held {number}** "
                  f"(or run `tools/held_review.py --post {number}`). Ignore it to keep it held.")
        try:
            _post(webhook, text, preview)
        except Exception as exc:
            say(f"[Held] Discord refused #{number} ({exc}).")
            continue
        finally:
            if preview:
                try:
                    os.remove(preview)
                except OSError:
                    pass
        state["sent"][name] = {"number": number, "at": time.time(), "path": info["path"]}
        state["next"] = number + 1
        _write(STATE, state)
        sent += 1
        say(f"[Held] Sent #{number}: {name}")
    return sent


def post(numbers, say=print):
    """Lift the slur hold on these clip numbers and retry their held jobs."""
    state = _read(STATE, {"next": 1, "sent": {}})
    by_number = {v["number"]: k for k, v in state["sent"].items()}
    ok = set(_read(APPROVED, []))
    import shutil

    queue = _queue()
    revived = 0
    for n in numbers:
        name = by_number.get(int(n))
        if not name:
            say(f"[Held] No held clip #{n}.")
            continue
        ok.add(name)
        _write(APPROVED, sorted(ok))       # before the retry, so the hold is lifted
        for job in queue.list_jobs():
            err = str(getattr(job, "last_error", "") or "")
            if (job.state != "failed" or os.path.basename(job.clip_path or "") != name
                    or not err.startswith("held:") or "slur" not in err):
                continue
            kept = os.path.join(KEEP, name)
            if not os.path.isfile(job.clip_path) and os.path.isfile(kept):
                os.makedirs(os.path.dirname(job.clip_path), exist_ok=True)
                shutil.copy2(kept, job.clip_path)
            if os.path.isfile(job.clip_path) and queue.retry(job.id):
                revived += 1
        say(f"[Held] #{n} cleared: {name}")
    _write(APPROVED, sorted(ok))
    return revived


_SENDING: set = set()


def send_at_hold(video_path: str, why: str) -> None:
    """Called the moment a clip is held: the clip is copied now (the
    working files are cleaned up soon after) and sent to Discord in the
    background, once per clip whichever platform held it first."""
    import shutil
    import threading

    name = os.path.basename(video_path or "")
    if not name or name in _SENDING or not os.path.isfile(video_path):
        return
    if name in _read(STATE, {"sent": {}}).get("sent", {}):
        return
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    _SENDING.add(name)
    os.makedirs(KEEP, exist_ok=True)
    for old in os.listdir(KEEP):
        try:
            if time.time() - os.path.getmtime(os.path.join(KEEP, old)) > KEEP_DAYS * 86400:
                os.remove(os.path.join(KEEP, old))
        except OSError:
            pass
    copy = os.path.join(KEEP, name)
    try:
        shutil.copy2(video_path, copy)
    except OSError:
        _SENDING.discard(name)
        return

    def run():
        preview = ""
        try:
            preview = _preview(copy)
            state = _read(STATE, {"next": 1, "sent": {}})
            number = state.get("next", 1)
            _post(webhook, f"**HELD CLIP #{number}** - {os.path.splitext(name)[0]}\n"
                           f"Held from YouTube/IG/TikTok/FB/X: {why}\n"
                           f"Worth posting? Tell Claude **post held {number}** "
                           f"(or run `tools/held_review.py --post {number}`). "
                           f"Ignore it to keep it held.", preview)
            state = _read(STATE, {"next": 1, "sent": {}})
            state.setdefault("sent", {})[name] = {"number": number, "at": time.time(),
                                                   "path": video_path}
            state["next"] = max(state.get("next", 1), number + 1)
            _write(STATE, state)
            print(f"[Held] Sent to Discord as #{number}: {name}")
        except Exception as exc:
            print(f"[Held] Could not send {name} to Discord ({exc}).")
        finally:
            for p in (preview,):
                if p:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
    threading.Thread(target=run, name="held-send", daemon=True).start()


def maybe_send_in_background(cfg=None):
    """After a stream: send any new held clips, without making anything wait."""
    import threading

    def run():
        try:
            send_new()
        except Exception as exc:
            print(f"[Held] Could not send held clips ({exc}).")
    threading.Thread(target=run, name="held-review", daemon=True).start()
