"""Music guard: take songs out of the YouTube copy of a stream.

YouTube's Content ID matches songs playing in the BACKGROUND of a stream -
a car radio in GTA, a song on stream between scenes - even quietly, even
under talking. One match can block the whole VOD in some countries.

What this does, on the YouTube copy only (Rumble and clips never see it):

  1. LISTEN  - an AudioSet tagger (PANNs Cnn14, GPU) scores every 2 s of
               the stream for "Music" and "Speech".
  2. SPLIT   - each stretch with music is run through Demucs (htdemucs,
               GPU), which separates voices from the beat.
  3. REBUILD - while someone is talking, only the voice track is kept, so
               the talking stays and the song goes. Music with nobody
               talking is muted. Everything else is untouched, bit for bit
               the same audio as before.

Songs WITH singing are the weak spot: the singer is a voice too, so a bit of
it can stay under the talking. Instrumentals and beats come out clean.

Any failure returns the input path unchanged - this must never cost an
upload.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
import time
import urllib.request

import numpy as np

PANNS_DIR = os.path.join(os.path.expanduser("~"), "panns_data")
LABELS_URL = ("http://storage.googleapis.com/us_audioset/youtube_corpus/"
              "v1/csv/class_labels_indices.csv")
CKPT_URL = ("https://zenodo.org/records/3987831/files/"
            "Cnn14_mAP%3D0.431.pth?download=1")
CKPT_NAME = "Cnn14_mAP=0.431.pth"
CKPT_MIN_BYTES = 300_000_000

TAG_SR = 32000
WIN_S = 2.0
MUSIC_ON = 0.30      # music score that starts a span
MUSIC_KEEP = 0.18    # a span keeps going while the score stays above this
SPEECH_ON = 0.30     # talking in this window -> keep the voice track
MIN_SPAN_S = 4.0     # shorter music blips are not worth touching
MERGE_GAP_S = 6.0    # two music stretches this close become one
PAD_S = 1.0          # start a little early, end a little late
FADE_S = 0.15        # crossfades, so nothing clicks
AUDIO_BITRATE = "192k"
CHUNK_S = 300.0      # Demucs works on this many seconds at a time
SILENT_DB = -60.0    # quieter than this is silence, not music
UNDER_MIN_DB = -45.0 # a song under talking must be at least this loud
UNDER_ON = 0.45      # ...and clearly music once the voices are gone
# A car engine with the voices removed is a drone the tagger can call
# "music" (it says didgeridoo). GTA is full of cars, so under the talking
# these have to lose clearly to music before it counts.
ENGINE_LABELS = ("Vehicle", "Car", "Engine", "Didgeridoo", "Motor vehicle (road)",
                 "Race car, auto racing", "Accelerating, revving, vroom")


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------

def _download(url: str, dest: str, say, min_bytes: int = 1) -> None:
    if os.path.isfile(dest) and os.path.getsize(dest) >= min_bytes:
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    say(f"[MusicGuard] Downloading {os.path.basename(dest)} (one time)...")
    with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            f.write(block)
    if os.path.getsize(part) < min_bytes:
        raise RuntimeError(f"download of {url} came back too small")
    os.replace(part, dest)


def ensure_models(say=print) -> str:
    """Fetch the tagger's labels and weights into ~/panns_data. Returns the
    checkpoint path. panns_inference tries to do this itself with wget,
    which Windows does not have."""
    _download(LABELS_URL, os.path.join(PANNS_DIR, "class_labels_indices.csv"),
              say)
    ckpt = os.path.join(PANNS_DIR, CKPT_NAME)
    _download(CKPT_URL, ckpt, say, CKPT_MIN_BYTES)
    return ckpt


def _label_index() -> dict:
    out = {}
    with open(os.path.join(PANNS_DIR, "class_labels_indices.csv"),
              encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out[row["display_name"]] = int(row["index"])
    return out


def _device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _free_gpu() -> None:
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Audio I/O
# --------------------------------------------------------------------------

def _probe(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", path], capture_output=True, text=True)
    return json.loads(r.stdout or "{}")


def _pcm(src: str, sr: int, ch: int, start: float = 0.0,
         dur: float = 0.0) -> subprocess.Popen:
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if dur > 0:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-map", "0:a:0", "-vn", "-ac", str(ch), "-ar", str(sr),
            "-f", "s16le", "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)


# --------------------------------------------------------------------------
# 1. Listen: where is the music, where is the talking
# --------------------------------------------------------------------------

def scan(src: str, say=print, device: str = "", deep: bool = True) -> dict:
    """Per WIN_S seconds of `src`: music / speech / singing scores, plus -
    when `deep` - a music score for what is left after the voices are taken
    out (Demucs accompaniment, loudness-normalised). That second score is
    what finds a song playing quietly UNDER talking: with the voices gone
    the song is the only thing left, however low it was mixed."""
    import torch
    import torchaudio.functional as AF

    ckpt = ensure_models(say)
    from panns_inference.models import Cnn14

    device = device or _device()
    tagger = Cnn14(sample_rate=TAG_SR, window_size=1024, hop_size=320,
                   mel_bins=64, fmin=50, fmax=14000, classes_num=527)
    state = torch.load(ckpt, map_location="cpu", weights_only=False)
    tagger.load_state_dict(state["model"])
    tagger.to(device).eval()
    idx = _label_index()
    mu, sp, sg = idx["Music"], idx["Speech"], idx["Singing"]
    engine = [idx[n] for n in ENGINE_LABELS if n in idx]
    sep = None
    if deep:
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
        sep = get_model("htdemucs")
        sep.to(device).eval()
        keep = [i for i, n in enumerate(sep.sources) if n != "vocals"]

    win = int(TAG_SR * WIN_S)
    msr = 44100
    chunk = int(CHUNK_S * msr)
    floor = 10 ** (SILENT_DB / 20)

    def tag(mono44: "torch.Tensor", normalise: bool):
        x = AF.resample(mono44, msr, TAG_SR).numpy()
        rem = len(x) % win
        if rem:
            x = np.pad(x, (0, win - rem))
        x = x.reshape(-1, win)
        rms = np.sqrt((x ** 2).mean(1) + 1e-12)
        if normalise:
            x = x / np.maximum(rms, floor)[:, None] * 0.1
        out = []
        for i in range(0, len(x), 64):
            with torch.no_grad():
                o = tagger(torch.from_numpy(x[i:i + 64].astype(np.float32))
                           .to(device), None)["clipwise_output"]
            out.append(o.float().cpu().numpy())
        return np.concatenate(out), 20 * np.log10(rms)

    keys = ("music", "speech", "singing", "level", "under", "under_level",
            "under_engine")
    scores = {k: [] for k in keys}
    proc = _pcm(src, msr, 2)
    t0 = time.time()
    try:
        while True:
            buf = proc.stdout.read(chunk * 4)
            if not buf:
                break
            mix = torch.from_numpy(np.frombuffer(buf, np.int16).reshape(-1, 2)
                                   .T.astype(np.float32) / 32768.0)
            o, level = tag(mix.mean(0), False)
            music = np.where(level >= SILENT_DB, o[:, mu], 0.0)
            scores["music"].extend(music.tolist())
            scores["speech"].extend(o[:, sp].tolist())
            scores["singing"].extend(o[:, sg].tolist())
            scores["level"].extend(level.tolist())
            if sep is not None:
                ref = mix.mean(0)
                mean, std = ref.mean(), ref.std() + 1e-8
                with torch.no_grad():
                    stems = apply_model(sep, ((mix - mean) / std)[None],
                                        device=device, split=True,
                                        overlap=0.25, progress=False)[0]
                acc = (stems[keep] * std).sum(0).mean(0).cpu()
                ao, alevel = tag(acc, True)
                scores["under"].extend(ao[:, mu].tolist())
                scores["under_level"].extend(alevel.tolist())
                scores["under_engine"].extend(ao[:, engine].max(1).tolist())
    finally:
        proc.stdout.close()
        proc.wait()
        del tagger, sep
        _free_gpu()
    n = len(scores["music"])
    say(f"[MusicGuard] Listened to {n * WIN_S / 60:.0f} min of audio in "
        f"{time.time() - t0:.0f}s{' (deep)' if deep else ''}.")
    return {k: np.asarray(v, dtype=np.float32) for k, v in scores.items()
            if len(v) == n}


def music_score(scores: dict) -> np.ndarray:
    """One music score per window: the plain one, or - where the song is
    audible under the talking - the score of what is left without voices."""
    m = scores["music"].copy()
    if "under" in scores:
        u = scores["under"]
        ok = ((scores["under_level"] >= UNDER_MIN_DB) & (u >= UNDER_ON))
        if "under_engine" in scores:
            ok &= scores["under_engine"] < 0.5 * u
        m = np.maximum(m, np.where(ok, u, 0.0))
    return m


def _smooth(x: np.ndarray) -> np.ndarray:
    if len(x) < 3:
        return x
    padded = np.pad(x, 1, mode="edge")
    return np.median(np.stack([padded[:-2], padded[1:-1], padded[2:]]),
                     axis=0)


def find_spans(scores: dict, duration: float) -> list:
    """[(start_s, end_s)] stretches that have music in them."""
    m = _smooth(music_score(scores))
    raw, i, n = [], 0, len(m)
    while i < n:
        if m[i] >= MUSIC_ON:
            j = i
            while j + 1 < n and m[j + 1] >= MUSIC_KEEP:
                j += 1
            raw.append([i * WIN_S, (j + 1) * WIN_S])
            i = j + 1
        else:
            i += 1
    merged = []
    for s, e in raw:
        if merged and s - merged[-1][1] <= MERGE_GAP_S:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    spans = []
    for s, e in merged:
        if e - s < MIN_SPAN_S:
            continue
        s, e = max(0.0, s - PAD_S), min(duration, e + PAD_S)
        if spans and s <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e])
    return [(round(s, 2), round(e, 2)) for s, e in spans]


# --------------------------------------------------------------------------
# 2. Split: the voice track for each music stretch
# --------------------------------------------------------------------------

OVERLAP_S = 2.0


def _ramp_mean(x: np.ndarray, width: int) -> np.ndarray:
    """Moving average via cumulative sums - O(n) however wide."""
    if width <= 1 or len(x) == 0:
        return x
    c = np.cumsum(np.pad(x.astype(np.float64), (width // 2, width - width // 2)))
    return ((c[width:] - c[:-width]) / width)[:len(x)].astype(np.float32)


def _voice(model, src: str, s: float, e: float, device: str):
    """Demucs vocals for [s, e) of src at the model's rate, (2, n) float."""
    import torch
    from demucs.apply import apply_model

    msr = model.samplerate
    vi = model.sources.index("vocals")
    proc = _pcm(src, msr, 2, start=s, dur=e - s)
    raw = proc.communicate()[0]
    mix = np.frombuffer(raw, np.int16).reshape(-1, 2).T.astype(np.float32)
    mix /= 32768.0
    total = mix.shape[1]
    out = np.zeros_like(mix)
    weight = np.zeros(total, dtype=np.float32)
    step, ov = int(CHUNK_S * msr), int(OVERLAP_S * msr)
    pos = 0
    while pos < total:
        end = min(total, pos + step + ov)
        piece = torch.from_numpy(mix[:, pos:end].copy())
        ref = piece.mean(0)
        mean, std = ref.mean(), ref.std() + 1e-8
        with torch.no_grad():
            sep = apply_model(model, ((piece - mean) / std)[None],
                              device=device, split=True, overlap=0.25,
                              progress=False)[0]
        voc = (sep[vi] * std + mean).cpu().numpy()
        n = voc.shape[1]
        w = np.ones(n, dtype=np.float32)
        if pos > 0:
            k = min(ov, n)
            w[:k] = np.linspace(0, 1, k, dtype=np.float32)
        if end < total:
            k = min(ov, n)
            w[n - k:] = np.minimum(w[n - k:],
                                   np.linspace(1, 0, k, dtype=np.float32))
        out[:, pos:pos + n] += voc * w
        weight[pos:pos + n] += w
        if end >= total:
            break
        pos += step
    out /= np.maximum(weight, 1e-6)
    return out


def separate(src: str, spans: list, scores: dict, sr: int, ch: int,
             workdir: str, say=print, device: str = "") -> list:
    """For each span, the replacement audio saved as int16 .npy (n, ch):
    the voice track where someone is talking, silence where nobody is.
    Returns [(start_s, end_s, npy_path, talk_seconds)]."""
    import torch
    from demucs.pretrained import get_model

    device = device or _device()
    model = get_model("htdemucs")
    model.to(device).eval()
    speech = _smooth(scores["speech"])
    fade = int(FADE_S * sr)
    out = []
    t0 = time.time()
    try:
        for k, (s, e) in enumerate(spans, 1):
            voc = _voice(model, src, s, e, device)
            if model.samplerate != sr:
                import torchaudio.functional as AF
                voc = AF.resample(torch.from_numpy(voc), model.samplerate,
                                  sr).numpy()
            n = voc.shape[1]
            # Talking gate, one value per tagger window, eased at the edges.
            t = s + np.arange(n) / sr
            wi = np.clip((t // WIN_S).astype(np.int64), 0, len(speech) - 1)
            gate = _ramp_mean((speech[wi] >= SPEECH_ON).astype(np.float32),
                              fade)
            talk = float(gate.mean() * (e - s))
            voc = voc * gate
            if ch == 1:
                voc = voc.mean(0, keepdims=True)
            pcm = np.clip(voc.T * 32767.0, -32768, 32767).astype(np.int16)
            path = os.path.join(workdir, f"span_{k:03d}.npy")
            np.save(path, pcm)
            out.append((s, e, path, talk))
    finally:
        del model
        _free_gpu()
    say(f"[MusicGuard] Split {len(spans)} music stretch(es) in "
        f"{time.time() - t0:.0f}s.")
    return out


# --------------------------------------------------------------------------
# 3. Rebuild: stream the audio through, swap only the music stretches
# --------------------------------------------------------------------------

def rebuild(src: str, out_path: str, parts: list, sr: int, ch: int,
            offset: float, say=print) -> None:
    """Write out_path: src's video stream copied untouched, its audio
    re-encoded with each part swapped in (crossfaded at both ends)."""
    fade = max(1, int(FADE_S * sr))
    spans = []
    for s, e, path, _ in parts:
        r = np.load(path, mmap_mode="r")
        spans.append((int(round(s * sr)), r))
    log = tempfile.NamedTemporaryFile(prefix="musicguard_", suffix=".log",
                                      delete=False)
    log.close()
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", src]
    if abs(offset) > 0.01:
        cmd += ["-itsoffset", f"{offset:.3f}"]
    cmd += ["-f", "s16le", "-ar", str(sr), "-ac", str(ch),
            "-thread_queue_size", "4096", "-i", "pipe:0",
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-c:a", "aac", "-b:a", AUDIO_BITRATE, out_path]
    reader = _pcm(src, sr, ch)
    with open(log.name, "wb") as errf:
        writer = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=errf)
        pos = 0
        frames = sr * 5
        try:
            while True:
                buf = reader.stdout.read(frames * ch * 2)
                if not buf:
                    break
                a = np.frombuffer(buf, np.int16).reshape(-1, ch)
                n = len(a)
                hit = [(s0, r) for s0, r in spans
                       if s0 < pos + n and s0 + len(r) > pos]
                if hit:
                    a = a.astype(np.float32)
                    for s0, r in hit:
                        lo, hi = max(pos, s0), min(pos + n, s0 + len(r))
                        k = np.arange(lo - s0, hi - s0)
                        w = np.minimum(1.0, np.minimum(k / fade,
                                                       (len(r) - k) / fade))
                        w = w.astype(np.float32)[:, None]
                        seg = a[lo - pos:hi - pos]
                        a[lo - pos:hi - pos] = (seg * (1 - w)
                                                + np.asarray(r[k]) * w)
                    a = np.clip(a, -32768, 32767).astype(np.int16)
                writer.stdin.write(a.tobytes())
                pos += n
        finally:
            try:
                writer.stdin.close()
            except OSError:
                pass
            reader.stdout.close()
            reader.wait()
            code = writer.wait()
    if code != 0:
        with open(log.name, encoding="utf-8", errors="replace") as f:
            tail = f.read()[-600:]
        os.unlink(log.name)
        raise RuntimeError(f"ffmpeg rebuild failed: {tail}")
    os.unlink(log.name)


def _duration(info: dict) -> float:
    try:
        return float(info["format"]["duration"])
    except Exception:
        return 0.0


def _stream(info: dict, kind: str) -> dict:
    for s in info.get("streams", []):
        if s.get("codec_type") == kind:
            return s
    return {}


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def guard(src: str, out_dir: str = "", say=print, device: str = "",
          max_share: float = 0.85) -> str:
    """The path to upload: a music-free copy of src, or src itself when
    there is no music, or when anything at all goes wrong."""
    try:
        why = _not_ready_here()
        if why and not os.environ.get(_CHILD_FLAG):
            # The uploader's own Python can be a different one from the
            # one the GPU libraries are installed in (this machine runs
            # the uploader on 3.14 with a CPU-only torch, and has a 3.12
            # with CUDA torch, Demucs and PANNs). Hand the job to that
            # one instead of skipping it - or of running Demucs over
            # three hours of audio on the CPU.
            other = _other_python()
            if other:
                say(f"[MusicGuard] This Python can't run it ({why}) - "
                    f"using {other}.")
                return _guard_in(other, src, out_dir, say)
            say(f"[MusicGuard] Skipped - {why}, and no other Python here "
                f"has the GPU libraries. Install them with: python -m pip "
                f"install demucs panns_inference (and a CUDA torch).")
            return src
        return _guard(src, out_dir, say, device, max_share)
    except Exception as exc:
        say(f"[MusicGuard] Skipped - uploading the copy as it was ({exc}).")
        return src


_CHILD_FLAG = "AUTOBLEEP_MUSIC_GUARD_CHILD"
_PROBE = ("import importlib.util as u, torch;"
          "ok = all(u.find_spec(m) for m in "
          "('panns_inference', 'demucs', 'torchaudio'));"
          "print('READY' if ok and torch.cuda.is_available() else 'NO')")


def _not_ready_here() -> str:
    """'' when this interpreter can run the guard on the GPU, else why."""
    import importlib.util

    for module in ("panns_inference", "demucs", "torchaudio"):
        if importlib.util.find_spec(module) is None:
            return f"no {module}"
    try:
        import torch
        if not torch.cuda.is_available():
            return "its torch has no CUDA"
    except Exception as exc:
        return f"torch: {exc}"
    return ""


def _other_python() -> str:
    """Another Python on this machine that has the GPU libraries, or ""."""
    import sys

    seen, found = {os.path.normcase(sys.executable)}, []
    env = os.environ.get("MUSIC_GUARD_PYTHON", "")
    if env:
        found.append(env)
    try:
        listing = subprocess.run(["py", "-0p"], capture_output=True,
                                 text=True, timeout=20).stdout
        for line in listing.splitlines():
            path = line.split()[-1] if line.strip() else ""
            if path.lower().endswith("python.exe"):
                found.append(path)
    except (OSError, subprocess.TimeoutExpired):
        pass
    for path in found:
        key = os.path.normcase(path)
        if key in seen or not os.path.isfile(path):
            continue
        seen.add(key)
        try:
            answer = subprocess.run([path, "-c", _PROBE], capture_output=True,
                                    text=True, timeout=120).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        if "READY" in answer:
            return path
    return ""


def _guard_in(python: str, src: str, out_dir: str, say) -> str:
    """Run the guard in another Python; its log lines come through `say`.
    The child prints the path to upload as its last line."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    env[_CHILD_FLAG] = "1"
    cmd = [python, "-m", "autoreel.music_guard", src]
    if out_dir:
        cmd += ["--out-dir", out_dir]
    last = ""
    proc = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace")
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        if line.startswith("[MusicGuard]"):
            say(line)
        last = line
    proc.wait()
    if proc.returncode == 0 and last and os.path.isfile(last):
        return last
    say(f"[MusicGuard] Skipped - the helper Python did not finish "
        f"(exit {proc.returncode}).")
    return src


def _guard(src, out_dir, say, device, max_share) -> str:
    base = os.path.splitext(os.path.basename(src))[0]
    out_dir = out_dir or os.path.dirname(os.path.abspath(src))
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{base}.nomusic.mp4")
    report_path = out_path + ".json"
    if (os.path.isfile(out_path) and os.path.isfile(report_path)
            and os.path.getmtime(out_path) > os.path.getmtime(src)):
        say(f"[MusicGuard] Reusing the music-free copy from earlier.")
        return out_path

    info = _probe(src)
    a, v = _stream(info, "audio"), _stream(info, "video")
    if not a or not v:
        say("[MusicGuard] No audio or video stream - nothing to do.")
        return src
    duration = _duration(info)
    sr = int(a.get("sample_rate") or 48000)
    ch = 1 if int(a.get("channels") or 2) == 1 else 2
    try:
        offset = float(a.get("start_time", 0)) - float(v.get("start_time", 0))
    except (TypeError, ValueError):
        offset = 0.0

    say("[MusicGuard] Listening for songs (YouTube copy only)...")
    scores = scan(src, say, device)
    spans = find_spans(scores, duration)
    music_s = sum(e - s for s, e in spans)
    if not spans:
        say("[MusicGuard] No music found - uploading as is.")
        return src
    if duration and music_s / duration > max_share:
        say(f"[MusicGuard] Music in {music_s / duration:.0%} of the stream - "
            f"that looks like a misread, not touching it.")
        return src
    say(f"[MusicGuard] Music in {len(spans)} stretch(es), "
        f"{music_s / 60:.1f} min total.")

    workdir = tempfile.mkdtemp(prefix="musicguard_")
    tmp_out = out_path + ".part.mp4"
    try:
        parts = separate(src, spans, scores, sr, ch, workdir, say, device)
        say("[MusicGuard] Rebuilding the audio...")
        t0 = time.time()
        rebuild(src, tmp_out, parts, sr, ch, offset, say)
        got = _duration(_probe(tmp_out))
        if duration and abs(got - duration) > 2.0:
            raise RuntimeError(f"length changed {duration:.1f}s -> {got:.1f}s")
        os.replace(tmp_out, out_path)
        talk = sum(p[3] for p in parts)
        report = {
            "source": os.path.abspath(src),
            "made": time.strftime("%Y-%m-%d %H:%M:%S"),
            "spans": [{"start": s, "end": e,
                       "start_hms": time.strftime("%H:%M:%S", time.gmtime(s)),
                       "talk_s": round(p[3], 1)}
                      for (s, e), p in zip(spans, parts)],
            "music_seconds": round(music_s, 1),
            "voice_kept_seconds": round(talk, 1),
            "muted_seconds": round(music_s - talk, 1),
        }
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
        say(f"[MusicGuard] Done in {time.time() - t0:.0f}s: song removed under "
            f"{talk / 60:.1f} min of talking (voice kept), "
            f"{(music_s - talk) / 60:.1f} min of music-only muted.")
        return out_path
    finally:
        for name in os.listdir(workdir):
            try:
                os.unlink(os.path.join(workdir, name))
            except OSError:
                pass
        try:
            os.rmdir(workdir)
        except OSError:
            pass
        if os.path.isfile(tmp_out):
            try:
                os.unlink(tmp_out)
            except OSError:
                pass


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("video")
    p.add_argument("--out-dir", default="")
    p.add_argument("--scan-only", action="store_true",
                   help="only list where the music is")
    args = p.parse_args()
    if args.scan_only:
        sc = scan(args.video)
        dur = _duration(_probe(args.video))
        for s, e in find_spans(sc, dur):
            i0, i1 = int(s // WIN_S), int(e // WIN_S) + 1
            print(f"{time.strftime('%H:%M:%S', time.gmtime(s))} - "
                  f"{time.strftime('%H:%M:%S', time.gmtime(e))}  "
                  f"music {sc['music'][i0:i1].mean():.2f}  "
                  f"speech {sc['speech'][i0:i1].mean():.2f}  "
                  f"singing {sc['singing'][i0:i1].mean():.2f}")
    else:
        print(guard(args.video, args.out_dir))
