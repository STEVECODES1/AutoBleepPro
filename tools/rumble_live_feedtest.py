"""Feed test (NOT going live): pull a real YouTube live through the relay's
own hls_url + Push into the Rumble room, read Studio's health numbers, stop.
Never presses Start stream."""
import os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rumble_live as rl

live = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/@LofiGirl/live"
vid = subprocess.run([rl.PY, "-m", "yt_dlp", "--no-warnings", "--print", "id", live],
                     capture_output=True, text=True, timeout=90).stdout.strip().splitlines()[-1]
urls = rl.hls_url(vid)
print("feeds:", len(urls), flush=True)
cfg = rl.env()
push = rl.Push(urls, cfg["RUMBLE_RTMP_URL"], cfg["RUMBLE_STREAM_KEY"])
studio = None
try:
    time.sleep(35)
    print("ffmpeg alive:", push.alive(), flush=True)
    studio = rl.Studio()
    studio.page.wait_for_timeout(8000)
    top = studio.page.inner_text("body")[:220].replace("\n", " ")
    print("studio:", re.sub(r"\s+", " ", top), flush=True)
finally:
    print("pushed s:", round(push.stop()), flush=True)
    if studio:
        studio.close()
