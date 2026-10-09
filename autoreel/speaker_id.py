"""
Who is talking: Stackswopo, or the person he is talking to.

WHY
---
Everything on stream - his mic, GTA voice chat, a Monkey App call - is
one mixed audio track, so the transcript has words and times but not
whose words. For Monkey App that is the difference between a problem and
nothing: "I'm 15" from the stranger is the worst flag there is, while an
insult FROM him AT a stranger is what YouTube calls harassment, and the
reverse mostly is not. For GTA it is which lines are his.

HOW
---
A speaker-embedding model (WeSpeaker ResNet34, VoxCeleb, 25 MB, ONNX on
the CPU - nothing else installed) turns every 1.5 s of speech into a
voice fingerprint. Stackswopo's voice is the one that keeps coming back:
he is on mic the whole stream, while the people he talks to change. His
voiceprint is saved after the first stream (models/speaker/), so a
Monkey stream - where strangers talk a lot - starts from his real voice
and only adapts to tonight's mic, instead of guessing.

LIMITS
------
Two people talking over each other, a very quiet call, or a loud game
make a window "" (not sure) rather than a guess. Safety checks never rely
on the label alone: it adds who said it and how serious it is; it does
not clear a flag.

    python -m autoreel.speaker_id learn  "<a GTA stream>"    save his voiceprint
    python -m autoreel.speaker_id check  "<video>" [--start S --end E]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(ROOT, "models", "speaker")
MODEL_PATH = os.path.join(MODEL_DIR, "voxceleb_resnet34_LM.onnx")
MODEL_URL = ("https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM/"
             "resolve/main/voxceleb_resnet34_LM.onnx")
MODEL_MIN_BYTES = 20_000_000
PRINT_PATH = os.path.join(MODEL_DIR, "stackswopo_voice.npy")
RATE = 16000
WIN_S = 1.5
MIN_WIN_S = 0.8
QUIET_RMS = 120.0          # int16 RMS below this is not speech worth judging
STREAMER, OTHER, UNSURE = "Stackswopo", "other person", ""


def ensure_model(say=print) -> str:
    if os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) >= MODEL_MIN_BYTES:
        return MODEL_PATH
    import urllib.request

    os.makedirs(MODEL_DIR, exist_ok=True)
    say("[Voices] Downloading the voice model (25 MB, once)...")
    tmp = MODEL_PATH + ".part"
    urllib.request.urlretrieve(MODEL_URL, tmp)
    if os.path.getsize(tmp) < MODEL_MIN_BYTES:
        os.remove(tmp)
        raise RuntimeError("voice model download was cut short")
    os.replace(tmp, MODEL_PATH)
    return MODEL_PATH


class Embedder:
    """1.5 s of 16 kHz int16 audio -> a unit-length 256-number voiceprint."""

    def __init__(self, path: str = "", threads: int = 2):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads      # leave the CPU to the stream
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(path or ensure_model(), opts,
                                            providers=["CPUExecutionProvider"])

    def __call__(self, pcm: np.ndarray) -> np.ndarray:
        import torch
        import torchaudio.compliance.kaldi as kaldi

        wave = torch.from_numpy(pcm.astype(np.float32)).unsqueeze(0)
        feats = kaldi.fbank(wave, num_mel_bins=80, frame_length=25, frame_shift=10,
                            dither=0.0, sample_frequency=RATE, window_type="hamming",
                            use_energy=False)
        feats = (feats - feats.mean(dim=0)).numpy()[None].astype(np.float32)
        emb = self.session.run(None, {"feats": feats})[0][0]
        return emb / (np.linalg.norm(emb) or 1.0)


def read_pcm(source: str, raw_path: str) -> np.ndarray:
    """The whole audio track as 16 kHz mono int16, memory-mapped from disk."""
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", source, "-map", "0:a:0", "-ac", "1",
                    "-ar", str(RATE), "-f", "s16le", raw_path], check=True,
                   capture_output=True, timeout=3600)
    return np.memmap(raw_path, dtype=np.int16, mode="r")


def windows(segments: Sequence[dict], win: float = WIN_S) -> List[Tuple[float, float]]:
    """1.5 s pieces of every transcript sentence (the tail joins the last)."""
    out = []
    for seg in segments:
        s, e = float(seg.get("start", 0)), float(seg.get("end", 0))
        if e - s < MIN_WIN_S:
            continue
        at = s
        while e - at >= win + MIN_WIN_S:
            out.append((at, at + win))
            at += win
        out.append((at, e))
    return out


def otsu(values: np.ndarray, bins: int = 64) -> float:
    """The split that best separates two piles of similarity scores."""
    values = np.asarray(values, dtype=float)
    if values.size < 4:
        return 0.4
    hist, edges = np.histogram(values, bins=bins)
    mids = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mids) / np.maximum(w0, 1)
    m1 = (np.sum(hist * mids) - np.cumsum(hist * mids)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(mids[int(np.argmax(between[:-1]))])


def find_streamer(embs: np.ndarray, prior: Optional[np.ndarray] = None,
                  rounds: int = 5) -> Tuple[np.ndarray, float]:
    """(his voiceprint for this recording, the similarity that counts as him).

    Without a saved print: start from everyone, keep the half most like
    the middle, repeat - the one voice present all stream wins. With one:
    start from it and only adapt to tonight's mic."""
    X = embs / np.maximum(np.linalg.norm(embs, axis=1, keepdims=True), 1e-9)
    c = prior if prior is not None else X.mean(axis=0)
    c = c / (np.linalg.norm(c) or 1.0)
    for i in range(rounds):
        sims = X @ c
        if prior is None and i < 2:
            keep = sims >= np.median(sims)
        else:
            keep = sims >= np.clip(otsu(sims), 0.25, 0.6)
        if keep.sum() < 3:
            break
        c = X[keep].mean(axis=0)
        c = c / (np.linalg.norm(c) or 1.0)
    return c, float(np.clip(otsu(X @ c), 0.25, 0.6))


def label_windows(sims: np.ndarray, threshold: float, rms: np.ndarray,
                  margin: float = 0.05) -> List[str]:
    """Close to the line either way, or too quiet, is UNSURE - not a guess."""
    out = []
    for s, loud in zip(sims, rms):
        if loud < QUIET_RMS or abs(s - threshold) < margin:
            out.append(UNSURE)
        else:
            out.append(STREAMER if s > threshold else OTHER)
    return out


def who_said(spans: Sequence[Tuple[float, float, str]], start: float, end: float,
             share: float = 0.65) -> str:
    """The speaker of start..end from labelled windows: one name if it has
    most of the sure time, "both" if two do, UNSURE otherwise."""
    time: Dict[str, float] = {}
    for s, e, who in spans:
        overlap = min(e, end) - max(s, start)
        if overlap > 0 and who:
            time[who] = time.get(who, 0.0) + overlap
    total = sum(time.values())
    if total <= 0:
        return UNSURE
    best = max(time, key=time.get)
    if time[best] / total >= share:
        return best
    return "both"


def load_print() -> Optional[np.ndarray]:
    try:
        v = np.load(PRINT_PATH)
        return v / (np.linalg.norm(v) or 1.0)
    except (OSError, ValueError):
        return None


def save_print(new: np.ndarray, old: Optional[np.ndarray], say=print) -> bool:
    """Keep his voiceprint current, slowly. A voice that does not match the
    saved one is not him (or not a normal stream) and is not saved."""
    if old is not None:
        match = float(new @ old)
        if match < 0.6:
            say(f"[Voices] Tonight's main voice does not match the saved Stackswopo "
                f"voice (similarity {match:.2f}) - not updating it.")
            return False
        new = 0.8 * old + 0.2 * new
    os.makedirs(MODEL_DIR, exist_ok=True)
    np.save(PRINT_PATH, new / (np.linalg.norm(new) or 1.0))
    return True


def label(source: str, segments: List[dict], learn: bool = False, say=print) -> dict:
    """Write "speaker" onto every segment and word of `source`'s transcript
    (times relative to `source`). Returns a short summary."""
    wins = windows(segments)
    if not wins:
        return {"windows": 0}
    embedder = Embedder()
    work = tempfile.mkdtemp(prefix="voices_")
    raw = os.path.join(work, "audio.s16")
    try:
        pcm = read_pcm(source, raw)
        embs, rms = [], []
        for s, e in wins:
            # A copy, not a view: a view keeps the file open and Windows
            # then refuses to delete it.
            chunk = np.array(pcm[int(s * RATE):int(e * RATE)])
            if chunk.size < MIN_WIN_S * RATE * 0.9:      # past the end of the audio
                rms.append(0.0)
                embs.append(np.zeros(256, dtype=np.float32))
                continue
            rms.append(float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))))
            embs.append(embedder(chunk))
        del pcm
    finally:
        try:
            os.remove(raw)
            os.rmdir(work)
        except OSError:
            pass
    X = np.asarray(embs)
    loud = np.asarray(rms) >= QUIET_RMS
    judged = X[loud] if loud.sum() >= 10 else X
    prior = load_print()
    centre, threshold = find_streamer(judged, prior)
    if prior is not None and float(centre @ prior) < 0.5:
        # Tonight's busiest voice drifted away from his - strangers out-
        # talking him on a call. Trust the saved print.
        say("[Voices] The main voice here is not his saved voice - going by the saved one.")
        centre = prior
        threshold = float(np.clip(otsu(judged @ prior), 0.25, 0.6))
    sims = X @ centre
    labels = label_windows(sims, threshold, np.asarray(rms))
    spans = [(s, e, who) for (s, e), who in zip(wins, labels)]
    for seg in segments:
        seg["speaker"] = who_said(spans, float(seg["start"]), float(seg["end"]))
        for w in seg.get("words") or []:
            w["speaker"] = who_said(spans, float(w["start"]), float(w["end"]), share=0.5)
    sure = [l for l in labels if l]
    share = (sum(1 for l in sure if l == STREAMER) / len(sure)) if sure else 0.0
    summary = {"windows": len(wins), "threshold": round(threshold, 3),
               "streamer_share": round(share, 3), "unsure_share": round(1 - len(sure) / len(labels), 3),
               "matches_saved": None if prior is None else round(float(centre @ prior), 3)}
    if learn:
        if share < 0.4:
            say(f"[Voices] He is only {share:.0%} of the talking here - not learning "
                "his voice from this one. Use a GTA stream.")
        else:
            summary["saved"] = save_print(centre, prior, say)
    return summary


TAGS = {STREAMER: "STACKS:", OTHER: "THEM:", "both": "BOTH:"}


def tagged(segments: Sequence[dict]) -> str:
    """'STACKS: yo what's good THEM: nothing much' - a tag where the voice
    changes. A line nobody could place after a tagged one is marked "?:"
    so it is not read as the last speaker's."""
    parts, last = [], None
    for seg in segments:
        text = " ".join(str(seg.get("text") or "").split())
        if not text:
            continue
        tag = TAGS.get(str(seg.get("speaker") or ""), "?:" if last else "")
        if tag and tag != last:
            parts.append(tag)
            last = tag
        parts.append(text)
    return " ".join(parts)


def cached_transcript(source: str, folders: Sequence[str] = ()) -> List[dict]:
    """The censor's word-level transcript of `source`, if it made one."""
    base = os.path.splitext(os.path.basename(source))[0]
    here = [os.path.dirname(source), *folders,
            os.path.join(ROOT, "auto_uploader", "censored")]
    for folder in here:
        path = os.path.join(folder, f"{base}_transcript_words.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return list(data.get("segments") if isinstance(data, dict) else data)
        except (OSError, ValueError, TypeError):
            continue
    return []


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Who is talking: Stackswopo or someone else.")
    ap.add_argument("action", choices=("learn", "check"))
    ap.add_argument("video")
    ap.add_argument("--transcript", help="a *_transcript_words.json (found by itself if left out)")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=0.0)
    args = ap.parse_args(argv)
    if args.transcript:
        with open(args.transcript, encoding="utf-8") as f:
            data = json.load(f)
        segments = list(data.get("segments") if isinstance(data, dict) else data)
    else:
        segments = cached_transcript(args.video)
    if not segments:
        print("No transcript for this video yet - it is made when the video is censored.")
        return 1
    summary = label(args.video, segments, learn=args.action == "learn")
    print(json.dumps(summary))
    if args.action == "check":
        for seg in segments:
            if seg["end"] < args.start or (args.end and seg["start"] > args.end):
                continue
            who = seg.get("speaker") or "?"
            print(f"{seg['start']:8.1f}  {who:13s}  {str(seg.get('text', '')).strip()[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
