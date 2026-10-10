"""Make every mute land on the actual sound, then listen again.

Two passes on top of ComplianceEngine.mute_spans, both of which can only ADD
muting - never remove any:

snap_to_sound
    Whisper's word timings are 100-300 ms out, and mute_spans has to stop
    at the neighbouring word, so in fast talk the first or last syllable of
    a swear or slur could stay audible. This reads the audio itself: from
    each flagged word it walks outwards while the sound is still as loud as
    the word, and mutes up to where it actually drops away (at most
    SNAP_REACH_MS past Whisper's edges).

second_listen
    Transcribes the CLEANED audio again - the whole file when it is short,
    a few seconds around every mute when it is a long stream - and mutes
    anything that is still recognisable as a flagged word.
"""
import os
import subprocess
import tempfile
import wave

FRAME_MS = 10
SNAP_REACH_MS = 250          # furthest a mute grows past Whisper's word edge
QUIET_RATIO = 0.35           # "the word has ended" = this much quieter than it
QUIET_RUN = 3                # ...for this many 10 ms frames in a row
GUARD_MS = 30                # kept past the point the sound drops away
FULL_LISTEN_MAX_S = 30 * 60  # re-transcribe the whole file up to this length
WINDOW_S = 3.0               # otherwise this much either side of each mute
GAP_S = 1.0                  # silence between windows when they are joined
MIN_NEW_MS = 30              # a re-heard word must add this much to count


def merge(spans):
    out = []
    for s, e in sorted((int(s), int(e)) for s, e in spans if e > s):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _samples(wav_path):
    import numpy as np

    with wave.open(wav_path, "rb") as wf:
        rate, width, chans = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError("expected 16-bit audio")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if chans > 1:
        data = data.reshape(-1, chans).mean(axis=1)
    return data, rate


def frame_levels(wav_path):
    """RMS level of every 10 ms frame."""
    import numpy as np

    data, rate = _samples(wav_path)
    hop = max(1, rate * FRAME_MS // 1000)
    usable = len(data) // hop * hop
    frames = data[:usable].reshape(-1, hop)
    return np.sqrt((frames ** 2).mean(axis=1) + 1e-12)


def snap_word(levels, start_s, end_s):
    """(start_ms, end_ms) covering the word's actual sound."""
    import numpy as np

    n = len(levels)
    a = max(0, min(n - 1, int(start_s * 1000 / FRAME_MS)))
    b = max(a + 1, min(n, int(end_s * 1000 / FRAME_MS) + 1))
    loud = float(np.percentile(levels[a:b], 80)) if b > a else 0.0
    if loud <= 1e-4:
        return int(start_s * 1000), int(end_s * 1000)
    quiet = loud * QUIET_RATIO
    reach = SNAP_REACH_MS // FRAME_MS

    def walk(i, step):
        run = 0
        for k in range(reach):
            j = i + step * k
            if j < 0 or j >= n:
                return j - step * run
            run = run + 1 if levels[j] < quiet else 0
            if run >= QUIET_RUN:
                return j - step * (QUIET_RUN - 1)
        return i + step * reach

    left = walk(a, -1)
    right = walk(b - 1, 1)
    return (max(0, left * FRAME_MS - GUARD_MS),
            min(n * FRAME_MS, (right + 1) * FRAME_MS + GUARD_MS))


def snap_to_sound(wav_path, spans, violations):
    """spans plus, for each violation, the span its sound really covers."""
    try:
        levels = frame_levels(wav_path)
    except Exception as exc:
        print(f"[Censor] could not read the audio levels ({exc}) - padding only")
        return merge(spans)
    extra = [snap_word(levels, float(v.start), float(v.end)) for v in violations]
    return merge(list(spans) + extra)


def _to_16k(src, dst):
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst],
                       capture_output=True, text=True, timeout=60 * 60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-300:])


def windows_for(spans, total_s):
    """Seconds to re-listen to: everything when short, else around each mute."""
    if total_s <= FULL_LISTEN_MAX_S:
        return [(0.0, total_s)]
    wins = []
    for s, e in spans:
        a, b = max(0.0, s / 1000 - WINDOW_S), min(total_s, e / 1000 + WINDOW_S)
        if wins and a <= wins[-1][1]:
            wins[-1] = (wins[-1][0], max(wins[-1][1], b))
        else:
            wins.append((a, b))
    return wins


def _join_windows(wav16, wins, out_path):
    """Cut the windows out of a 16 kHz mono wav and join them with silence.
    Returns [(joined_start_s, joined_end_s, source_start_s)]."""
    import numpy as np

    data, rate = _samples(wav16)
    gap = np.zeros(int(GAP_S * rate), dtype=np.float32)
    pieces, table, pos = [], [], 0.0
    for a, b in wins:
        chunk = data[int(a * rate):int(b * rate)]
        if not len(chunk):
            continue
        table.append((pos, pos + len(chunk) / rate, a))
        pieces += [chunk, gap]
        pos += (len(chunk) + len(gap)) / rate
    joined = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)
    pcm = (np.clip(joined, -1, 1) * 32767).astype(np.int16)
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
    return table


def _back_to_source(segments, table):
    """Joined-file timestamps -> source timestamps; words in the gaps dropped."""
    def where(t):
        for a, b, src in table:
            if a - 0.05 <= t <= b + 0.05:
                return src + (t - a)
        return None

    out = []
    for seg in segments or []:
        words = []
        for w in seg.get("words") or []:
            s, e = where(float(w.get("start", 0))), where(float(w.get("end", 0)))
            if s is None or e is None:
                continue
            words.append(dict(w, start=s, end=max(s, e)))
        if words:
            out.append(dict(seg, start=words[0]["start"], end=words[-1]["end"], words=words))
    return out


def _new_ms(span, spans):
    """How much of `span` is not already muted."""
    s, e = span
    left = e - s
    for a, b in spans:
        overlap = min(e, b) - max(s, a)
        if overlap > 0:
            left -= overlap
    return left


def second_listen(clean_wav, spans, engine, transcriber, total_s, source_wav16=None,
                  around=None):
    """Spans still needed after listening to the cleaned audio again.

    Returns (extra_spans, heard_words). Raises nothing: a re-listen that
    fails just means the first pass stands, and that is said out loud.
    `source_wav16` (the uncensored 16 kHz audio) is used to snap any newly
    heard word to its sound.
    """
    tmp = tempfile.mkdtemp(prefix="censor_verify_")
    wav16 = os.path.join(tmp, "clean16.wav")
    joined = os.path.join(tmp, "listen.wav")
    try:
        _to_16k(clean_wav, wav16)
        # `around`: only re-listen near these (a later round checks the
        # spots it just added); `spans` is everything already muted.
        wins = windows_for(around or spans, total_s)
        table = _join_windows(wav16, wins, joined)
        heard_s = sum(b - a for a, b, _ in table)
        result = transcriber.transcribe(joined)
        segments = _back_to_source(result.get("segments"), table)
        found = engine.scan_segments(segments)
        if not found:
            print(f"[Censor] Second listen: clean ({heard_s / 60:.1f} min re-heard).")
            return [], []
        total_ms = int(total_s * 1000)
        extra = engine.mute_spans(found, total_ms)
        if source_wav16:
            extra = snap_to_sound(source_wav16, extra, found)
        extra = [sp for sp in extra if _new_ms(sp, spans) >= MIN_NEW_MS]
        words = sorted({str(v.word).strip() for v in found})
        if extra:
            print(f"[Censor] Second listen caught {len(extra)} spot(s) that were "
                  f"still audible - muting them too.")
        else:
            print(f"[Censor] Second listen: {len(found)} hit(s), all already inside "
                  f"a mute - clean.")
        return extra, words
    except Exception as exc:
        print(f"[Censor] Second listen could not run ({type(exc).__name__}: {exc}) - "
              f"keeping the first-pass mutes.")
        return [], []
    finally:
        for p in (wav16, joined):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass
