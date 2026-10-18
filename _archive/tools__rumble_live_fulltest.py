"""Full test of the Rumble live relay, approved by the user 2026-10-10:
go live on BinScripts for ~60 s with a test pattern titled "Test - ignore",
then press End Stream. Uses the same Studio code the real relay uses."""
import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rumble_live as rl

SHOT = os.path.join(rl.ROOT, "_scratch_live_{}.png")
cfg = rl.env()
key = cfg["RUMBLE_STREAM_KEY"]
cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re",
       "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
       "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
       "-t", "240", "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "cbr", "-b:v", "2500k",
       "-maxrate", "2500k", "-bufsize", "2500k", "-pix_fmt", "yuv420p",
       "-force_key_frames", "expr:gte(t,n_forced*2)", "-c:a", "aac", "-b:a", "128k",
       "-f", "flv", cfg["RUMBLE_RTMP_URL"].rstrip("/") + "/" + key]
push = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
studio = None
try:
    time.sleep(15)
    studio = rl.Studio()
    studio.details("Test - ignore")
    started = studio.start(wait_s=120)
    studio.page.screenshot(path=SHOT.format("started"))
    print("STARTED:", started, flush=True)
    links = studio.page.eval_on_selector_all(
        "a[href*='rumble.com/v']", "els => els.map(e => e.href)")
    print("links:", links[:3], flush=True)
    time.sleep(60)
    ended = studio.end()
    studio.page.wait_for_timeout(3000)
    studio.page.screenshot(path=SHOT.format("ended"))
    print("ENDED:", ended, flush=True)
finally:
    push.terminate()
    if studio:
        studio.close()
