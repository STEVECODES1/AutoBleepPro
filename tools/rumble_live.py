"""Relay Stackswopo's YouTube live to the BinScripts Rumble channel, live.

Why: the full stream used to reach Rumble about three hours after the
stream ended - a 9 GB file over an 11 Mbps upload. A live relay has it
on Rumble the moment the stream ends.

How:
  * waits for the recorder's heartbeat to say "recording" (it knows the
    YouTube video id),
  * pulls the live HLS feed with yt-dlp -g and pushes it to Rumble Studio's
    Direct RTMP room with ffmpeg (NVENC, capped at 4.5 Mbps CBR so the
    clips still have upload room),
  * in the logged-in Rumble Chrome (port 9222) sets the title and
    description and presses "Start stream",
  * restarts ffmpeg straight away if the feed drops while still live,
  * presses "End Stream" when the recorder says the stream is over,
  * writes logs/rumble_live.json - main.py reads it and skips the 9 GB
    Rumble upload ONLY when the live covered the whole stream.

The stream key is read from auto_uploader/.env (RUMBLE_STREAM_KEY) and is
never printed or logged.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADER = os.path.join(ROOT, "auto_uploader")
LEDGER = os.path.join(UPLOADER, "logs", "rumble_live.json")
FFLOG = os.path.join(UPLOADER, "logs", "rumble_live.log")
HEARTBEAT = r"C:\AutoBleep\recording\.heartbeat\Stackswopo.json"
ROOM = "https://studio.rumble.com/studio/c5ab9397-d670-4307-9249-6b6615bf1819"
CDP = "http://localhost:9222"
PY = sys.executable

BITRATE = "4500k"
END_GRACE_S = 150          # recorder not recording this long = stream over
NO_FEED_GIVE_UP_S = 600    # never got a feed for this id = not the live one
STALE_HEARTBEAT_S = 240
POLL_S = 15

DESCRIPTION = (
    "Stackswopo LIVE - uncut and uncensored, as it happens.\n\n"
    "YouTube, full streams: https://www.youtube.com/@wopovod\n"
    "YouTube Shorts: https://www.youtube.com/@wopoclipper\n"
    "TikTok: https://www.tiktok.com/@wopocl1ps\n"
    "Instagram: https://www.instagram.com/stackswopomanz/\n"
    "X: https://x.com/WOPOCLIPS\n"
    "Monkey App videos: https://www.youtube.com/@BinScript\n"
    "Stackswopo live on YouTube: https://www.youtube.com/@stackswopo_\n\n"
    "#StacksWopo #GTARP #GTA5 #Roleplay #Live"
)


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with open(FFLOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def env():
    out = {}
    with open(os.path.join(UPLOADER, ".env"), encoding="utf-8") as f:
        for line in f:
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def heartbeat():
    try:
        with open(HEARTBEAT, encoding="utf-8") as f:
            hb = json.load(f)
    except (OSError, ValueError):
        return None
    if time.time() - hb.get("time", 0) > STALE_HEARTBEAT_S:
        return None
    return hb


def recording_id():
    hb = heartbeat()
    if hb and hb.get("state") == "recording" and hb.get("video_id"):
        return hb["video_id"]
    return ""


def ledger_load():
    try:
        with open(LEDGER, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ledger_save(data):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, LEDGER)


def live_title(video_id):
    try:
        raw = subprocess.run([PY, "-m", "yt_dlp", "--print", "title", "--no-warnings",
                              f"https://www.youtube.com/watch?v={video_id}"],
                             capture_output=True, text=True, timeout=90).stdout.strip().splitlines()
        raw = raw[-1] if raw else ""
    except Exception:
        raw = ""
    title = re.sub(r"\s+\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", "", raw).strip() or "LIVE"
    full = f"Stackswopo - {title} - {time.strftime('%m-%d-%y')} (LIVE UNCUT)"
    return full[:100]


def hls_url(video_id):
    r = subprocess.run([PY, "-m", "yt_dlp", "-g", "--no-warnings", "-f", "301/96/300/95/b",
                        f"https://www.youtube.com/watch?v={video_id}"],
                       capture_output=True, text=True, timeout=90)
    urls = [u for u in r.stdout.split() if u.startswith("http")]
    return urls[0] if urls else ""


class Push:
    """One ffmpeg pushing the feed to Rumble. Key never logged."""

    def __init__(self, src, target, key):
        self.key = key
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
               "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
               "-i", src,
               "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr",
               "-b:v", BITRATE, "-maxrate", BITRATE, "-bufsize", BITRATE,
               "-profile:v", "high", "-pix_fmt", "yuv420p",
               "-force_key_frames", "expr:gte(t,n_forced*2)",
               "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
               "-f", "flv", target.rstrip("/") + "/" + key]
        self.started = time.time()
        self.proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for line in self.proc.stderr:
            line = line.rstrip().replace(self.key, "<key>")
            if line:
                log(f"[ffmpeg] {line[:300]}")

    def alive(self):
        return self.proc.poll() is None

    def stop(self):
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        return time.time() - self.started


class Studio:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(CDP)
        ctx = self.browser.contexts[0]
        self.page = next((p for p in ctx.pages if "/studio/" in p.url), None) or ctx.new_page()
        if ROOM not in self.page.url:
            self.page.goto(ROOM, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(4000)

    def _button(self, pattern):
        return self.page.get_by_role("button", name=re.compile(pattern, re.I)).first

    def details(self, title):
        pg = self.page
        name = pg.locator("input[placeholder='Give your stream a name']").first
        desc = pg.locator("textarea[placeholder='Tell the audience more about your stream']").first
        name.fill(title)
        desc.fill(DESCRIPTION)
        save = self._button(r"^Save Changes$")
        if save.count() and save.is_enabled():
            save.click()
            pg.wait_for_timeout(2500)

    def start(self, wait_s=180):
        btn = self._button(r"^Start stream$")
        deadline = time.time() + wait_s
        while time.time() < deadline:
            if btn.count() and btn.is_enabled():
                break
            self.page.wait_for_timeout(3000)
        else:
            return False
        btn.click()
        self.page.wait_for_timeout(3000)
        self._confirm(r"^(Start stream|Go live|Start|Confirm|Yes)$")
        self.page.wait_for_timeout(5000)
        return self._button(r"End Stream").count() > 0

    def end(self):
        btn = self._button(r"End Stream")
        if not btn.count():
            return True
        btn.click()
        self.page.wait_for_timeout(2500)
        self._confirm(r"^(End Stream|End|Confirm|Yes)$")
        self.page.wait_for_timeout(5000)
        return self._button(r"End Stream").count() == 0

    def _confirm(self, pattern):
        dialog = self.page.locator("[role=dialog], [role=alertdialog]").last
        if dialog.count():
            b = dialog.get_by_role("button", name=re.compile(pattern, re.I)).first
            if b.count():
                b.click()

    def close(self):
        try:
            self.pw.stop()
        except Exception:
            pass


def relay(video_id, cfg):
    title = live_title(video_id)
    entry = {"video_id": video_id, "title": title, "started": time.time(),
             "ok": False, "pushed_s": 0.0, "restarts": 0, "went_live": False}
    data = ledger_load()
    data[video_id] = entry
    ledger_save(data)
    log(f"Stackswopo is live ({video_id}) - relaying to Rumble as: {title}")

    push, pushed, studio, off_since = None, 0.0, None, None
    try:
        while True:
            live_id = recording_id()
            if live_id == video_id:
                off_since = None
            else:
                off_since = off_since or time.time()
                if time.time() - off_since > END_GRACE_S:
                    break
            if (push is None or not push.alive()) and live_id == video_id:
                if push is not None:
                    pushed += push.stop()
                    entry["restarts"] += 1
                    log("Feed dropped - restarting the push")
                src = hls_url(video_id)
                if src:
                    push = Push(src, cfg["RUMBLE_RTMP_URL"], cfg["RUMBLE_STREAM_KEY"])
                else:
                    push = None
                    if not entry["went_live"] and time.time() - entry["started"] > NO_FEED_GIVE_UP_S:
                        # Usually the heartbeat still naming the LAST stream's
                        # video for a moment - that one is over and has no
                        # feed. Stop so the real new id can be picked up.
                        log(f"No live feed for {video_id} after "
                            f"{NO_FEED_GIVE_UP_S // 60} min - giving up on this id")
                        entry["no_feed"] = True
                        break
                    log("No live feed URL yet - trying again")
            if push is not None and not entry["went_live"]:
                try:
                    studio = studio or Studio()
                    studio.details(title)
                    entry["went_live"] = studio.start()
                    log("Rumble live STARTED" if entry["went_live"]
                        else "Rumble never offered Start stream - will retry")
                except Exception as exc:
                    log(f"Studio error: {exc}")
                    if studio:
                        studio.close()
                    studio = None
                ledger_save({**ledger_load(), video_id: entry})
            time.sleep(POLL_S)
    finally:
        if push is not None:
            pushed += push.stop()
        if entry["went_live"]:
            try:
                studio = studio or Studio()
                log("Rumble live ENDED" if studio.end() else "Could not press End Stream")
            except Exception as exc:
                log(f"Studio error ending: {exc}")
        if studio:
            studio.close()
        entry["pushed_s"] = round(pushed)
        entry["ended"] = time.time()
        # main.py still checks pushed_s against the real file length.
        entry["ok"] = bool(entry["went_live"] and entry["restarts"] <= 20)
        ledger_save({**ledger_load(), video_id: entry})
        log(f"Relay finished: live={entry['went_live']} pushed={entry['pushed_s']}s "
            f"restarts={entry['restarts']}")


def main():
    cfg = env()
    if not cfg.get("RUMBLE_STREAM_KEY") or not cfg.get("RUMBLE_RTMP_URL"):
        log("RUMBLE_STREAM_KEY / RUMBLE_RTMP_URL missing in .env - nothing to do")
        return 2
    log("Rumble live relay waiting for Stackswopo to go live")
    while True:
        vid = recording_id()
        if vid and vid not in ledger_load():
            relay(vid, cfg)
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
