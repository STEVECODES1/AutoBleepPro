"""Record Stackswopo when he goes live on RUMBLE (stackswopo10k).

Why: since 10-13 he streams on his own Rumble channel, and the recorder only
watched his two YouTube channels. "TALK TO ME REAL QUICK", "THE ONE N ONLY"
and "exposing" all went live on Rumble with nothing recording them.

How:
  * every POLL_S, reads the channel page (plain HTTPS - no login, no
    Chrome); its video grid is JSON and says live: true / livestream_status
    2 for a stream that is on air,
  * records it with yt-dlp from Rumble's DVR playlist, so a late start
    still gets the beginning; if yt-dlp drops while he is still live it
    starts again into the next part,
  * writes a heartbeat (.heartbeat/Stackswopo rumble live.json) like the
    recorder does, and pings Discord when it starts and when it delivers,
  * when he ends: joins the parts, and if they cover less than 95% of the
    finished stream, downloads Rumble's own replay instead,
  * delivers "<stream title>.ts" to auto_uploader/watch_folder - the
    uploader takes it from there (YouTube censored, BinScripts Rumble
    uncut, clips, recap) exactly as it did for THE ONE N ONLY.

An already-running yt-dlp on the same stream (started by hand) is adopted,
not duplicated.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADER = os.path.join(ROOT, "auto_uploader")
STAGING = r"C:\AutoBleep\recording"
HEARTBEAT = os.path.join(STAGING, ".heartbeat", "Stackswopo rumble live.json")
WATCH = os.path.join(UPLOADER, "watch_folder")
LEDGER = os.path.join(UPLOADER, "logs", "rumble_watch.json")
LOG = os.path.join(UPLOADER, "logs", "rumble_watch.log")
CHANNELS = ["stackswopo10k"]
# The interpreter running this file. It was hardcoded to C:\Python314,
# which no longer exists on this machine, so every stream this recorder
# tried to start died before yt-dlp ran.
PY = sys.executable
POLL_S = 60
END_CONFIRM_S = 180          # off air this long (and yt-dlp gone) = ended
VOD_WAIT_S = 1800            # how long to wait for Rumble's replay to appear
FULL_SHARE = 0.95
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def env(name):
    try:
        with open(os.path.join(UPLOADER, ".env"), encoding="utf-8") as f:
            for line in f:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return os.environ.get(name, "")


def discord(text):
    hook = env("DISCORD_WEBHOOK_URL")
    if not hook:
        return
    try:
        urllib.request.urlopen(urllib.request.Request(
            hook, data=json.dumps({"content": text}).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "AutoBleep"}), timeout=15)
    except Exception:
        pass


def cdp_page_html(url):
    """The page's HTML fetched from inside the signed-in Rumble Chrome.

    Rumble answers a plain request with HTTP 403 (Cloudflare) since
    10-19, which is why the recorder went blind. The Chrome on 9222 is
    already logged in and has a Rumble tab open; fetch() from that tab's
    own origin carries the cookies and the browser fingerprint, so the
    page comes back. No navigation when a Rumble tab exists, so the
    owner's browser does not flash a new tab every minute.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        log(f"CDP fallback unavailable ({type(exc).__name__})")
        return ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            ctx = browser.contexts[0]
            page = next((pg for pg in ctx.pages
                         if "rumble.com" in (pg.url or "")), None)
            opened = False
            if page is None:
                page = ctx.new_page()
                page.goto("https://rumble.com/", wait_until="domcontentloaded",
                          timeout=60000)
                opened = True
            try:
                return page.evaluate(
                    "async (u) => { const r = await fetch(u, {credentials:'include'});"
                    " return r.ok ? await r.text() : ''; }", url) or ""
            finally:
                if opened:
                    page.close()
    except Exception as exc:
        log(f"CDP fetch failed ({type(exc).__name__}: {str(exc)[:120]})")
        return ""


def channel_items(slug):
    """The channel's video grid, newest first ([] when it cannot be read)."""
    url = f"https://rumble.com/user/{slug}"
    req = urllib.request.Request(url,
                                 headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")
    except Exception as exc:
        log(f"could not read the {slug} page ({type(exc).__name__}) - trying the Rumble Chrome")
        html = cdp_page_html(url)
        if not html:
            return []
        log(f"read the {slug} page through the Rumble Chrome ({len(html)} chars)")
    m = re.search(r"<rum-videos-grid>\s*<script type=\"application/json\">\s*(\{.*?\})\s*</script>",
                  html, flags=re.S)
    if not m:
        return []
    try:
        return [i for i in json.loads(m.group(1)).get("items", []) if i.get("object_type") == "video"]
    except ValueError:
        return []


def on_air(items):
    return [i for i in items if i.get("live") and i.get("livestream_status") == 2]


def heartbeat(state, item=None, nbytes=0):
    os.makedirs(os.path.dirname(HEARTBEAT), exist_ok=True)
    data = {"time": time.time(), "pid": os.getpid(), "name": "Stackswopo rumble live",
            "url": (item or {}).get("url", f"https://rumble.com/user/{CHANNELS[0]}"),
            "state": state, "bytes": nbytes, "video_id": (item or {}).get("permalink_id", "")}
    try:
        with open(HEARTBEAT, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def ledger():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_ledger(data):
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def safe(title):
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(title or "")).strip().rstrip(".")
    return t[:120] or "Stackswopo stream"


def existing_ytdlp(permalink):
    """A yt-dlp already downloading this stream: (pid, output path) or None."""
    try:
        import psutil
    except ImportError:
        return None
    for p in psutil.process_iter(["pid", "cmdline"]):
        cmd = p.info.get("cmdline") or []
        if any(permalink in str(c) for c in cmd) and any("yt_dlp" in str(c) for c in cmd):
            out = ""
            if "-o" in cmd:
                out = cmd[cmd.index("-o") + 1].strip('"')
            return p.info["pid"], out
    return None


def pid_alive(pid):
    try:
        import psutil
        return psutil.pid_exists(pid) and psutil.Process(pid).status() != "zombie"
    except Exception:
        return False


def dvr_variant(item):
    """The best-quality DVR chunklist of a live item, or "".

    The channel page already gives the stream's master playlist
    (live-hls-dvr/.../playlist.m3u8) - no embed JSON, which Rumble started
    answering 403 to yt-dlp mid-stream on 10-15. ffmpeg pointed at the
    master fetches EVERY variant (1080, 720, 360 - three times the
    download), so the 1080 chunklist is picked out of it here.
    """
    from urllib.parse import urljoin

    master = ""
    for v in item.get("videos") or []:
        if str(v.get("type")) == "hls" and "m3u8" in str(v.get("url", "")):
            master = v["url"]
            break
    if not master:
        return ""
    try:
        resp = urllib.request.urlopen(urllib.request.Request(master, headers={"User-Agent": UA}), timeout=30)
        text, base = resp.read().decode("utf-8", "replace"), resp.geturl()
    except Exception:
        return ""
    best, best_bw, lines = "", -1, text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            m = re.search(r"BANDWIDTH=(\d+)", line)
            uri = next((l.strip() for l in lines[i + 1:] if l.strip() and not l.startswith("#")), "")
            if uri and m and int(m.group(1)) > best_bw:
                best, best_bw = urljoin(base, uri), int(m.group(1))
    return best or (base if "#EXTINF" in text else "")


def ffmpeg_on(out):
    try:
        import psutil
    except ImportError:
        return None
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if "ffmpeg" in str(p.info.get("name", "")).lower() and \
                any(out in str(c) for c in (p.info.get("cmdline") or [])):
            return p.info["pid"]
    return None


def start_fromstart(item):
    """ffmpeg from the FIRST segment of the DVR: the whole stream, however
    late this noticed it (on 10-15 it was 23 minutes in and still got it
    all, catching up at ~40x). (pid, path) or (None, path)."""
    out = os.path.join(STAGING, f"Stackswopo rumble {item['permalink_id']} fromstart.ts")
    # The live clipper (utils/live_clipper) clips this file while it grows
    # and names the clips' stream from this sidecar.
    began = _started_at(item) or time.time()
    try:
        with open(os.path.splitext(out)[0] + ".txt", "w", encoding="utf-8") as f:
            f.write(f"{item.get('title') or 'Stackswopo stream'} "
                    f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(began))}\n")
    except OSError:
        pass
    pid = ffmpeg_on(out)
    if pid:
        return pid, out
    variant = dvr_variant(item)
    if not variant:
        log("no DVR playlist on the channel page - live-edge recording only")
        return None, out
    err = open(out.replace(".ts", ".log"), "ab")
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-user_agent", UA,
                             "-live_start_index", "0", "-reconnect", "1",
                             "-reconnect_on_network_error", "1", "-reconnect_delay_max", "10",
                             "-i", variant, "-c", "copy", "-f", "mpegts", out],
                            stdout=err, stderr=err,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.pid, out


def start_part(item, n, stamp):
    out = os.path.join(STAGING, f"Stackswopo rumble live {stamp}.part{n:02d}.ts")
    errlog = open(out.replace(".ts", ".log"), "ab")
    proc = subprocess.Popen([PY, "-m", "yt_dlp", "--hls-use-mpegts", "--retries", "infinite",
                             "--fragment-retries", "infinite", "--socket-timeout", "30",
                             "--no-progress", "--newline", "-o", out, item["url"]],
                            stdout=errlog, stderr=errlog,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return proc.pid, out


def seconds_of(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", path],
                           capture_output=True, text=True, timeout=120)
        return float(json.loads(r.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def finished_file(path):
    """yt-dlp leaves NAME.part while working and renames on a clean end."""
    if os.path.isfile(path):
        return path
    if os.path.isfile(path + ".part"):
        try:
            os.replace(path + ".part", path)
            return path
        except OSError:
            return path + ".part"
    return ""


def join(parts, out):
    parts = [p for p in parts if p and os.path.isfile(p) and os.path.getsize(p) > 0]
    if not parts:
        return ""
    if len(parts) == 1:
        os.replace(parts[0], out)
        return out
    lst = out + ".txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write("file '" + p.replace("'", "'\\''") + "'\n")
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
                        "-safe", "0", "-i", lst, "-c", "copy", out], capture_output=True)
    os.remove(lst)
    return out if r.returncode == 0 and os.path.isfile(out) else ""


def replay(item, slug, out):
    """Rumble's own finished copy, when the live recording missed some."""
    deadline = time.time() + VOD_WAIT_S
    while time.time() < deadline:
        match = [i for i in channel_items(slug) if i.get("permalink_id") == item.get("permalink_id")]
        if match and not match[0].get("live") and match[0].get("duration"):
            log(f"replay is up ({match[0]['duration'] / 3600:.2f} h) - downloading it")
            r = subprocess.run([PY, "-m", "yt_dlp", "--retries", "infinite", "--fragment-retries",
                                "infinite", "--hls-use-mpegts", "-o", out, item["url"]],
                               capture_output=True, text=True)
            got = finished_file(out)
            return got if r.returncode == 0 and got else ""
        time.sleep(POLL_S)
    return ""


def deliver(src, dest):
    """Move a finished recording into the watch folder.

    The recording is on C: and the watch folder on D:, and os.replace
    cannot cross drives - on 10-15 that failed at the last step with a
    complete 1.95 h recording sitting in staging. Copied under a name the
    uploader ignores, renamed only once complete (so it never picks up half
    a file), then the original removed.
    """
    import shutil

    partial = dest + ".copying"
    # copy2 keeps the finish time: the uploader's watcher sees an
    # already-finished file and takes it after 30 s, not 10 minutes.
    shutil.copy2(src, partial)
    os.replace(partial, dest)
    try:
        os.remove(src)
    except OSError:
        pass


def _size(path):
    for cand in (path, path + ".part"):
        if cand and os.path.isfile(cand):
            return os.path.getsize(cand)
    return 0


def _started_at(item):
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(item.get("live_streamed_on"))).timestamp()
    except Exception:
        return 0.0


GIVE_UP_AFTER_END_S = 900    # a capture still running this long after he ended is stuck


def record(item, slug):
    """Held awake for the whole stream and the hand-off: a PC that sleeps
    mid-stream loses the connection and the recording with it."""
    sys.path.insert(0, UPLOADER)
    try:
        from utils.keep_awake import KeepAwake
    except Exception:  # pragma: no cover - never blocks a recording
        return _record(item, slug)
    with KeepAwake("recording Stackswopo on Rumble"):
        return _record(item, slug)


def _record(item, slug):
    """Two captures at once, the longer one delivered:
      * from the start of the DVR (ffmpeg) - the whole stream,
      * at the live edge (yt-dlp) - the backup if the DVR one fails.
    """
    stamp = time.strftime("%Y-%m-%d %H_%M")
    full_pid, full_out = start_fromstart(item)
    adopted = existing_ytdlp(item["permalink_id"])
    if adopted:
        edge_pid, edge_out = adopted
        log(f"adopting the yt-dlp already recording {item['title']!r} (pid {edge_pid})")
    else:
        edge_pid, edge_out = start_part(item, 1, stamp)
    parts, n = [edge_out], 1
    discord(f"\U0001F534 **Recording Stackswopo on Rumble** - {item['title']} ({item['url']})")
    off_since = None
    while True:
        heartbeat("recording", item, _size(full_out) + sum(_size(p) for p in parts))
        time.sleep(60)
        live_now = any(i.get("permalink_id") == item["permalink_id"] for i in on_air(channel_items(slug)))
        if live_now:
            off_since = None
            if not pid_alive(edge_pid):
                n += 1
                log(f"live-edge recording dropped while still live - part {n}")
                parts[-1] = finished_file(parts[-1]) or parts[-1]
                edge_pid, edge_out = start_part(item, n, stamp)
                parts.append(edge_out)
            if full_pid and not pid_alive(full_pid):
                # Starts over from segment 1 into the same file: the DVR
                # still has it all, and it catches up in minutes.
                log("from-start recording dropped - starting it again")
                full_pid, full_out = start_fromstart(item)
            continue
        off_since = off_since or time.time()
        busy = [pid for pid in (edge_pid, full_pid) if pid and pid_alive(pid)]
        if busy and time.time() - off_since > GIVE_UP_AFTER_END_S:
            log("captures still running 15 min after the end - stopping them")
            for pid in busy:
                try:
                    import psutil
                    psutil.Process(pid).terminate()
                except Exception:
                    pass
            time.sleep(10)
            busy = []
        if not busy and time.time() - off_since >= END_CONFIRM_S:
            break
    ended = (off_since or time.time())
    title = safe(item.get("title"))
    edge_file = join([finished_file(p) for p in parts],
                     os.path.join(STAGING, f"Stackswopo rumble {item['permalink_id']} edge.ts"))
    full_file = finished_file(full_out) if full_out else ""
    options = [(seconds_of(f), f) for f in (full_file, edge_file) if f and os.path.isfile(f)]
    options.sort(reverse=True)
    got, best = options[0] if options else (0.0, "")
    began = _started_at(item)
    want = (ended - began) if began else 0.0
    log(f"{title}: best capture {got / 3600:.2f} h" + (f" of about {want / 3600:.2f} h" if want else ""))
    if not best or (want and got < FULL_SHARE * want) or (not want and got < 600):
        vod = os.path.join(STAGING, f"Stackswopo rumble {item['permalink_id']} replay.ts")
        better = replay(item, slug, vod)
        if better and seconds_of(better) > got:
            best, got = better, seconds_of(better)
    if not best:
        log(f"{title}: nothing to deliver")
        discord(f"\u26A0\uFE0F **Rumble recording failed** - {title}. Check logs/rumble_watch.log")
        return False
    dest = os.path.join(WATCH, f"{title}.ts")
    if os.path.exists(dest) or os.path.exists(os.path.join(UPLOADER, "uploaded", f"{title}.ts")):
        dest = os.path.join(WATCH, f"{title} {time.strftime('%m-%d-%y')}.ts")
    os.makedirs(WATCH, exist_ok=True)
    deliver(best, dest)
    log(f"delivered {os.path.basename(dest)} ({got / 3600:.2f} h) to the uploader")
    discord(f"\u2705 **Rumble stream recorded** - {title}: {got / 3600:.2f} h, handed to the uploader")
    return True

def main():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    log(f"watching Rumble: {', '.join(CHANNELS)}")
    while True:
        done = ledger()
        for slug in CHANNELS:
            for item in on_air(channel_items(slug)):
                key = item.get("permalink_id")
                # Finished ones are skipped; one with no result yet is a
                # recording this process was restarted in the middle of -
                # pick it up again (the yt-dlp it started is adopted).
                if not key or (key in done and done[key].get("ok") is not None):
                    continue
                log(f"{slug} is LIVE: {item.get('title')!r} {item.get('url')}")
                done[key] = {**done.get(key, {}), "title": item.get("title"), "ok": None,
                             "started": done.get(key, {}).get("started", time.time())}
                save_ledger(done)
                try:
                    ok = record(item, slug)
                except Exception as exc:
                    log(f"recording {key} failed: {exc}")
                    ok = False
                done = ledger()
                done[key]["ok"], done[key]["ended"] = ok, time.time()
                save_ledger(done)
        heartbeat("waiting")
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
