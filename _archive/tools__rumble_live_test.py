"""Push 70 s of a test pattern to the Rumble room WITHOUT going live, to check the
RTMP leg and that Studio sees the connection. Never presses Start stream."""
import subprocess, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rumble_live as rl

cfg = rl.env()
key = cfg["RUMBLE_STREAM_KEY"]
cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-re",
       "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=60",
       "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
       "-t", "70", "-c:v", "h264_nvenc", "-preset", "p4", "-tune", "ll", "-rc", "cbr",
       "-b:v", rl.BITRATE, "-maxrate", rl.BITRATE, "-bufsize", rl.BITRATE, "-pix_fmt", "yuv420p",
       "-force_key_frames", "expr:gte(t,n_forced*2)", "-c:a", "aac", "-b:a", "160k",
       "-f", "flv", cfg["RUMBLE_RTMP_URL"].rstrip("/") + "/" + key]
r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
print("exit", r.returncode, (r.stderr or "").replace(key, "<key>")[-800:])
