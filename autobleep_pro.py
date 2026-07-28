"""
AutoBleep Pro v2.2 - Automatic Video Profanity Bleeper
=====================================================
SPEED STACK:
  - faster-whisper  → ~4x faster transcription vs openai-whisper
  - stable-ts       → accurate word-level timestamps on faster-whisper
  - int8 / float16  → compute mode selection (less RAM, faster)
  - ffmpeg extract  → fast 16kHz mono WAV extraction
  - libx264 presets → ultrafast / fast encode options
  - Turbo model     → added to model picker

WORD DETECTION v2.2:
  - Leet-speak decoder  (sh1t → shit, f@ck → fuck, etc.)
  - Common homophones   (fudge/ship/shoot/dang/crap/etc.)
  - Asterisk/symbol bypass (f**k, s**t, b***h)
  - Whisper mishear list (common transcription substitutes)
  - Context window      (bitch in "son of a" regardless of word alone)
  - Deduplication       (no double-bleep if word matches twice)
  - better-profanity    (large built-in wordlist, still used as base)

Features:
  - Word review UI  (uncheck words you don't want bleeped)
  - Output folder picker
  - Batch folder processing
  - Multiple beep sound presets
  - Custom word list
  - GPU auto-detection
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
import torch
from better_profanity import profanity
from pydub import AudioSegment
from pydub.generators import Sine
from moviepy import VideoFileClip, AudioFileClip
import os
import re
import threading
import subprocess
import unicodedata
import numpy as np
from datetime import timedelta

# ── Try importing speed stack; fall back to openai-whisper if not installed ──
try:
    import stable_whisper
    SPEED_MODE = True
except ImportError:
    SPEED_MODE = False
    print("[AutoBleep] stable-ts not found — using openai-whisper. "
          "Run: pip install stable-ts[fw] for 4x speed.")

# Always available as the last-resort fallback in load_model_speed(), even
# when stable-ts imported fine but its own load attempts fail at runtime.
import whisper as openai_whisper

# ── Profanity filter init ────────────────────────────────────────────────────
profanity.load_censor_words()

# Use all CPU cores for local (non-GPU) work
torch.set_num_threads(os.cpu_count() or 1)

# ── Model options ────────────────────────────────────────────────────────────
MODEL_MAP = {
    "tiny   — max speed (less accurate)": "tiny",
    "base   — recommended (balanced)": "base",
    "small  — more accurate (slower)": "small",
    "medium — best accuracy": "medium",
    "turbo  — fast large model (GPU recommended)": "turbo",
}

# ── Compute type options (faster-whisper / stable-ts) ────────────────────────
COMPUTE_MAP = {
    "Auto (GPU=float16, CPU=int8)": "auto",
    "int8 — fastest / least RAM": "int8",
    "float16 — best GPU speed": "float16",
    "float32 — max compatibility": "float32",
}

# ── Encode preset options ────────────────────────────────────────────────────
ENCODE_PRESETS = {
    "ultrafast — fastest export": "ultrafast",
    "fast — good balance": "fast",
    "medium — default quality": "medium",
    "slow — best compression": "slow",
}

# ── Beep preset frequencies (Hz) ─────────────────────────────────────────────
BEEP_PRESETS = {
    "Classic TV Bleep (1000 Hz)": 1000,
    "High Pitch (1500 Hz)": 1500,
    "Low Buzz (400 Hz)": 400,
    "Air Horn (600 Hz)": 600,
}

# ═══════════════════════════════════════════════════════════════════════════════
# WORD DETECTION ENGINE v2.2
# ═══════════════════════════════════════════════════════════════════════════════

LEET_MAP = str.maketrans({
    '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's',
    '6': 'g', '7': 't', '8': 'b', '9': 'g',
    '@': 'a', '$': 's', '!': 'i', '+': 't', '#': 'h',
    '*': '',
})

BYPASS_PATTERN = re.compile(
    r'\b([a-z])[*@#$!]{1,4}([a-z])\b|\b([a-z])[*@#$!]{2,}\b',
    re.IGNORECASE
)

BYPASS_STARTS = {
    'f': {'k'},
    's': {'t', 'r'},
    'b': {'h', 'd'},
    'a': {'e'},
    'c': {'t', 'k'},
    'd': {'k'},
    'p': {'s', 'y'},
    'h': {'l'},
    'n': {'r', 'a'},
    'w': {'e'},
    'j': {'z'},
}

HOMOPHONES: dict[str, list[str]] = {
    "fudge": ["fuck"],
    "frick": ["fuck"],
    "freak": ["fuck"],
    "freaking": ["fucking"],
    "frickin": ["fucking"],
    "frigging": ["fucking"],
    "effing": ["fucking"],
    "shoot": ["shit"],
    "ship": ["shit"],
    "sugar": ["shit"],
    "sheet": ["shit"],
    "dang": ["damn"],
    "darn": ["damn"],
    "dagnabbit": ["goddammit"],
    "crap": ["shit"],
    "crud": ["crap"],
    "witch": ["bitch"],
    "beach": ["bitch"],
    "rich": ["bitch"],
    "bass": ["ass"],
    "butt": ["ass"],
    "behind": ["ass"],
    "heck": ["hell"],
    "what the heck": ["what the hell"],
    "son of a gun": ["son of a bitch"],
}

CONTEXT_ONLY: set[str] = {"witch", "beach", "bass", "rich", "sheet"}

CONTEXT_TRIGGERS: list[tuple[str, str]] = [
    ("son of a", "bitch"),
    ("what the", "hell"),
    ("what the", "heck"),
    ("go to", "hell"),
    ("holy", "shit"),
    ("bull", "shit"),
    ("horse", "shit"),
    ("no", "shit"),
    ("you piece of", "shit"),
    ("mother", "fucker"),
    ("piece of", "shit"),
]

WHISPER_MISHEARDS: dict[str, str] = {
    "shirt": "shit",
    "witch": "bitch",
    "batch": "bitch",
    "ditch": "bitch",
    "rich": "bitch",
    "cluck": "fuck",
    "duck": "fuck",
    "luck": "fuck",
    "truck": "fuck",
    "stuck": "fuck",
    "shut": "shit",
    "shot": "shit",
    "ship": "shit",
    "shop": "shit",
}

MISHEARD_CONTEXT_ONLY: set[str] = {"luck", "truck", "stuck", "rich", "shot", "shop"}


def _normalize(text: str) -> str:
    text = unicodedata.normalize('NFD', text.lower())
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return re.sub(r"[^a-z0-9@$!*#+]", "", text)


def _deleet(text: str) -> str:
    return text.translate(LEET_MAP)


def _strip_affixes(word: str) -> list[str]:
    candidates = [word]
    for suffix in ('ing', 'ed', 'er', 'ers', 'in', 's', "'s", "'d", "n't"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            candidates.append(word[:-len(suffix)])
    for prefix in ('mother', 'bull', 'horse', 'cluster', 'jack', 'dumb',
                   'god', 'holy', 'un', 'out'):
        if word.startswith(prefix) and len(word) > len(prefix) + 2:
            candidates.append(word[len(prefix):])
    return candidates


def _is_bypass(raw_word: str) -> bool:
    if BYPASS_PATTERN.search(raw_word):
        first = raw_word[0].lower()
        last = re.sub(r'[^a-z]', '', raw_word.lower())[-1:]
        if first in BYPASS_STARTS and last in BYPASS_STARTS.get(first, set()):
            return True
        if len(raw_word) <= 6 and raw_word.count('*') >= 2:
            return True
    return False


def _check_word(
    raw_word: str,
    context_words: list[str],
    custom_words: list[str]
) -> tuple[bool, str]:
    if _is_bypass(raw_word):
        return True, "Symbol bypass (f**k style)"

    norm = _normalize(raw_word)
    deleet = _deleet(norm)
    stripped = re.sub(r"^[^a-z]+|[^a-z]+$", "", norm)
    core = re.sub(r"'(s|ll|d|m|re|ve|t|n't)$", "", stripped)

    if deleet != norm:
        for v in _strip_affixes(deleet):
            if profanity.contains_profanity(v):
                return True, "Leet-speak profanity"

    candidates = set()
    for base in (norm, stripped, core, deleet):
        candidates.update(_strip_affixes(base))

    for candidate in candidates:
        if candidate and profanity.contains_profanity(candidate):
            return True, "Profanity detected"

    clean_word = re.sub(r"[^a-z]", "", stripped)
    if clean_word in HOMOPHONES:
        if clean_word not in CONTEXT_ONLY:
            return True, f"Likely profanity substitute ('{clean_word}')"
        ctx_str = " ".join(context_words)
        for trigger, _ in CONTEXT_TRIGGERS:
            if trigger in ctx_str:
                return True, f"Context homophone ('{clean_word}')"

    if clean_word in WHISPER_MISHEARDS:
        if clean_word not in MISHEARD_CONTEXT_ONLY:
            return True, f"Whisper mishear of '{WHISPER_MISHEARDS[clean_word]}'"
        ctx_str = " ".join(context_words)
        if any(profanity.contains_profanity(cw) or cw in HOMOPHONES
               for cw in context_words):
            return True, f"Whisper mishear (context) of '{WHISPER_MISHEARDS[clean_word]}'"

    ctx_str = " ".join(context_words[-4:])
    for trigger, target in CONTEXT_TRIGGERS:
        if trigger in ctx_str and clean_word == target:
            return True, f"Context trigger ('{trigger} {target}')"

    for cw in custom_words:
        if cw and (cw in norm or cw in stripped or cw in core):
            return True, "Custom word"

    return False, ""


def find_profanity_v2(
    result: dict,
    custom_words: list[str]
) -> list[dict]:
    found = []
    seen_starts: set[float] = set()
    all_words: list[dict] = []

    for segment in result.get("segments", []):
        for word_info in segment.get("words", []):
            all_words.append(word_info)

    for idx, word_info in enumerate(all_words):
        raw = word_info.get("word", "")
        start = word_info.get("start", 0.0)
        end = word_info.get("end", 0.0)

        context = [
            re.sub(r"[^a-z]", "", all_words[i]["word"].strip().lower())
            for i in range(max(0, idx - 5), idx)
        ]

        is_bad, reason = _check_word(raw.strip(), context, custom_words)

        if is_bad and start not in seen_starts:
            seen_starts.add(start)
            found.append({
                "word": raw,
                "start": start,
                "end": end,
                "reason": reason,
            })

    return found


# ═══════════════════════════════════════════════════════════════════════════════
# DEVICE / MODEL / AUDIO HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def detect_device():
    if torch.cuda.is_available():
        return "cuda", torch.cuda.get_device_name(0)
    return "cpu", f"{os.cpu_count()} CPU cores"


def make_beep(duration_ms: int, freq_hz: int, _cache: dict = {}) -> AudioSegment:
    key = (duration_ms, freq_hz)
    if key not in _cache:
        base = Sine(freq_hz).to_audio_segment(duration=100)
        repeated = base * (duration_ms // 100 + 1)
        _cache[key] = repeated[:duration_ms]
    return _cache[key]


def safe_remove(*paths):
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def extract_audio_fast(video_path: str, wav_path: str) -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-ac", "1", "-ar", "16000", "-vn", wav_path],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def load_model_speed(model_name: str, compute_pref: str):
    device, dev_label = detect_device()
    if SPEED_MODE:
        compute_type = ("float16" if device == "cuda" else "int8") \
            if compute_pref == "auto" else compute_pref
        try:
            model = stable_whisper.load_faster_whisper(
                model_name, device=device, compute_type=compute_type)
            return model, device, f"faster-whisper [{compute_type}] on {device.upper()} ({dev_label})"
        except Exception as e:
            print(f"[AutoBleep] faster-whisper failed ({e}), trying stable-ts...")
            try:
                model = stable_whisper.load_model(model_name, device=device)
                return model, device, f"stable-ts on {device.upper()} ({dev_label})"
            except Exception as e2:
                print(f"[AutoBleep] stable-ts failed ({e2}), falling back...")
    model = openai_whisper.load_model(model_name, device=device)
    return model, device, f"openai-whisper on {device.upper()} ({dev_label})"


def transcribe_words(model, audio_path: str, speed_mode: bool) -> dict:
    if speed_mode:
        result = model.transcribe(audio_path, word_timestamps=True)
        segments = []
        for seg in result.segments:
            words = [{"word": w.word, "start": float(w.start), "end": float(w.end)}
                     for w in (seg.words or [])]
            segments.append({"words": words, "text": seg.text})
        return {"segments": segments}
    return model.transcribe(audio_path, word_timestamps=True)


# ═══════════════════════════════════════════════════════════════════════════════
# GUI
# ═══════════════════════════════════════════════════════════════════════════════

class AutoBleepPro:
    def __init__(self):
        self.window = ctk.CTk()
        self.window.title("AutoBleep Pro v2.2 — Smart Detection ⚡")
        self.window.geometry("1080x960")
        self.window.minsize(800, 700)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.video_paths: list[str] = []
        self.output_dir: str | None = None
        self.profane_words: list[dict] = []
        self.word_vars: list[ctk.BooleanVar] = []
        self.device_info = "unknown"
        self._batch_input_dir: str | None = None
        self._batch_output_dir: str | None = None

        self._setup_ui()

    def _setup_ui(self):
        hdr = ctk.CTkFrame(self.window, fg_color="transparent")
        hdr.pack(pady=(18, 4), padx=20, fill="x")
        ctk.CTkLabel(hdr, text="🔇 AutoBleep Pro",
                     font=("Arial", 36, "bold")).pack()
        speed_label = ("⚡ v2.2 — Smart Detection  •  faster-whisper + stable-ts"
                       if SPEED_MODE
                       else "v2.2 — Smart Detection  •  openai-whisper (install stable-ts[fw] for 4x speed)")
        ctk.CTkLabel(hdr, text=speed_label, font=("Arial", 13),
                     text_color="#4f98a3" if SPEED_MODE else "gray").pack(pady=2)

        self.tabs = ctk.CTkTabview(self.window, height=580)
        self.tabs.pack(pady=6, padx=20, fill="both", expand=True)
        self.tabs.add("Single Video")
        self.tabs.add("Batch Folder")
        self._build_single_tab(self.tabs.tab("Single Video"))
        self._build_batch_tab(self.tabs.tab("Batch Folder"))

        bot = ctk.CTkFrame(self.window)
        bot.pack(pady=(4, 12), padx=20, fill="x")
        self.progress = ctk.CTkProgressBar(bot)
        self.progress.pack(pady=(10, 4), padx=20, fill="x")
        self.progress.set(0)
        self.status_label = ctk.CTkLabel(bot, text="Ready — select a video to begin",
                                          font=("Arial", 13))
        self.status_label.pack(pady=(0, 8))

    def _build_single_tab(self, parent):
        s1 = ctk.CTkFrame(parent)
        s1.pack(pady=8, padx=10, fill="x")
        ctk.CTkLabel(s1, text="📹 Step 1: Select Video",
                     font=("Arial", 15, "bold")).pack(anchor="w", padx=14, pady=8)
        row = ctk.CTkFrame(s1, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=4)
        self.file_label = ctk.CTkLabel(row, text="No video selected",
                                        font=("Arial", 12), anchor="w")
        self.file_label.pack(side="left", fill="x", expand=True, padx=6)
        ctk.CTkButton(row, text="Browse Video", command=self._pick_single_video,
                      width=150, height=34).pack(side="right", padx=6)

        s2 = ctk.CTkFrame(parent)
        s2.pack(pady=8, padx=10, fill="x")
        ctk.CTkLabel(s2, text="⚙️ Step 2: Settings",
                     font=("Arial", 15, "bold")).pack(anchor="w", padx=14, pady=8)
        inner = ctk.CTkFrame(s2, fg_color="transparent")
        inner.pack(fill="x", padx=28, pady=4)

        ctk.CTkLabel(inner, text="Censoring Method:",
                     font=("Arial", 12)).pack(anchor="w", pady=(4, 2))
        self.bleep_method = ctk.StringVar(value="beep")
        mf = ctk.CTkFrame(inner, fg_color="transparent")
        mf.pack(anchor="w")
        ctk.CTkRadioButton(mf, text="🔊 Beep Sound",
                           variable=self.bleep_method, value="beep").pack(side="left", padx=8)
        ctk.CTkRadioButton(mf, text="🔇 Silence (Mute)",
                           variable=self.bleep_method, value="silence").pack(side="left", padx=8)

        ctk.CTkLabel(inner, text="Beep Sound Preset:",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.beep_preset = ctk.StringVar(value=list(BEEP_PRESETS.keys())[0])
        ctk.CTkOptionMenu(inner, values=list(BEEP_PRESETS.keys()),
                          variable=self.beep_preset, width=280).pack(anchor="w")

        ctk.CTkLabel(inner, text="AI Transcription Model:",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.model_var = ctk.StringVar(value=list(MODEL_MAP.keys())[1])
        ctk.CTkOptionMenu(inner, values=list(MODEL_MAP.keys()),
                          variable=self.model_var, width=320).pack(anchor="w")

        ctk.CTkLabel(inner, text="⚡ Compute Mode (Speed vs Accuracy):",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.compute_var = ctk.StringVar(value=list(COMPUTE_MAP.keys())[0])
        ctk.CTkOptionMenu(inner, values=list(COMPUTE_MAP.keys()),
                          variable=self.compute_var, width=320).pack(anchor="w")

        ctk.CTkLabel(inner, text="⚡ Video Export Speed:",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.encode_var = ctk.StringVar(value=list(ENCODE_PRESETS.keys())[1])
        ctk.CTkOptionMenu(inner, values=list(ENCODE_PRESETS.keys()),
                          variable=self.encode_var, width=320).pack(anchor="w")

        ctk.CTkLabel(inner, text="Extra words to bleep (comma-separated, optional):",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.custom_words_var = ctk.StringVar()
        ctk.CTkEntry(inner, textvariable=self.custom_words_var, width=420,
                     placeholder_text="e.g., rival brand, competitor name").pack(anchor="w")

        ctk.CTkLabel(inner, text="Output Folder (optional):",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        out_row = ctk.CTkFrame(inner, fg_color="transparent")
        out_row.pack(anchor="w", fill="x")
        self.out_label = ctk.CTkLabel(out_row, text="Same folder as input",
                                       font=("Arial", 11), text_color="gray", anchor="w")
        self.out_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(out_row, text="Choose Folder", command=self._pick_output_dir,
                      width=140, height=30).pack(side="right", padx=4)

        s3 = ctk.CTkFrame(parent)
        s3.pack(pady=8, padx=10, fill="x")
        ctk.CTkLabel(s3, text="🚀 Step 3: Process",
                     font=("Arial", 15, "bold")).pack(anchor="w", padx=14, pady=8)
        self.process_btn = ctk.CTkButton(
            s3, text="🎬 Analyze & Bleep Video", command=self._start_single,
            height=48, font=("Arial", 15, "bold"),
            fg_color="#2B7A0B", hover_color="#1F5A08", state="disabled")
        self.process_btn.pack(pady=10, padx=24, fill="x")

        wr = ctk.CTkFrame(parent)
        wr.pack(pady=8, padx=10, fill="both", expand=True)
        ctk.CTkLabel(wr, text="🔍 Word Review — uncheck words you DON'T want bleeped:",
                     font=("Arial", 13, "bold")).pack(anchor="w", padx=14, pady=(8, 4))
        self.review_scroll = ctk.CTkScrollableFrame(wr, height=130)
        self.review_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        ctk.CTkLabel(self.review_scroll,
                     text="Detected words will appear here after analysis.",
                     font=("Arial", 11), text_color="gray").pack(padx=8, pady=8)
        self.confirm_btn = ctk.CTkButton(
            wr, text="✅ Confirm & Export Clean Video", command=self._confirm_and_export,
            height=40, font=("Arial", 13, "bold"),
            fg_color="#1a6faf", hover_color="#14527f", state="disabled")
        self.confirm_btn.pack(pady=(0, 10), padx=24, fill="x")

    def _build_batch_tab(self, parent):
        ctk.CTkLabel(parent,
                     text="Process an entire folder of videos automatically — no review step.",
                     font=("Arial", 13), text_color="gray").pack(pady=(14, 4))
        bf = ctk.CTkFrame(parent)
        bf.pack(pady=8, padx=10, fill="x")

        ctk.CTkLabel(bf, text="📁 Input Folder:",
                     font=("Arial", 13, "bold")).pack(anchor="w", padx=14, pady=(10, 2))
        brow = ctk.CTkFrame(bf, fg_color="transparent")
        brow.pack(fill="x", padx=14, pady=4)
        self.batch_dir_label = ctk.CTkLabel(brow, text="No folder selected",
                                             font=("Arial", 11), text_color="gray", anchor="w")
        self.batch_dir_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(brow, text="Browse Folder", command=self._pick_batch_folder,
                      width=150, height=34).pack(side="right", padx=6)

        ctk.CTkLabel(bf, text="📤 Output Folder:",
                     font=("Arial", 13, "bold")).pack(anchor="w", padx=14, pady=(10, 2))
        orow = ctk.CTkFrame(bf, fg_color="transparent")
        orow.pack(fill="x", padx=14, pady=4)
        self.batch_out_label = ctk.CTkLabel(orow, text="Same as input folder",
                                             font=("Arial", 11), text_color="gray", anchor="w")
        self.batch_out_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(orow, text="Choose Folder", command=self._pick_batch_output,
                      width=150, height=34).pack(side="right", padx=6)

        bs = ctk.CTkFrame(parent)
        bs.pack(pady=8, padx=10, fill="x")
        ctk.CTkLabel(bs, text="⚙️ Batch Settings",
                     font=("Arial", 14, "bold")).pack(anchor="w", padx=14, pady=8)
        ctk.CTkLabel(bs, text="Uses same Method / Model / Compute / Encode / Beep / Custom Words "
                     "as the Single Video tab.",
                     font=("Arial", 11), text_color="gray").pack(anchor="w", padx=28, pady=(0, 10))

        self.batch_btn = ctk.CTkButton(
            parent, text="⚡ Process All Videos in Folder", command=self._start_batch,
            height=48, font=("Arial", 15, "bold"),
            fg_color="#7a2bb0", hover_color="#591e80", state="disabled")
        self.batch_btn.pack(pady=14, padx=24, fill="x")

        self.batch_log = ctk.CTkTextbox(parent, font=("Consolas", 11), height=240)
        self.batch_log.pack(pady=4, padx=14, fill="both", expand=True)
        self.batch_log.insert("1.0", "Batch log will appear here...\n")

    # ── File pickers ──────────────────────────────────────────────────────────

    def _pick_single_video(self):
        path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=[("Video Files", "*.mp4 *.avi *.mov *.mkv *.flv *.wmv"),
                       ("All Files", "*.*")])
        if path:
            self.video_paths = [path]
            self.file_label.configure(text=f"✅ {os.path.basename(path)}")
            self.process_btn.configure(state="normal")
            self._update_status("Video loaded — click Analyze & Bleep to start.")

    def _pick_output_dir(self):
        d = filedialog.askdirectory(title="Choose Output Folder")
        if d:
            self.output_dir = d
            self.out_label.configure(text=d, text_color="white")

    def _pick_batch_folder(self):
        d = filedialog.askdirectory(title="Select Folder of Videos")
        if d:
            self._batch_input_dir = d
            self.batch_dir_label.configure(text=d, text_color="white")
            self.batch_btn.configure(state="normal")

    def _pick_batch_output(self):
        d = filedialog.askdirectory(title="Select Batch Output Folder")
        if d:
            self._batch_output_dir = d
            self.batch_out_label.configure(text=d, text_color="white")

    # ── Single video analyze ──────────────────────────────────────────────────

    def _start_single(self):
        self.process_btn.configure(state="disabled")
        self.confirm_btn.configure(state="disabled")
        threading.Thread(target=self._analyze_video,
                         args=(self.video_paths[0],), daemon=True).start()

    def _analyze_video(self, video_path: str):
        audio_path = video_path + "__temp_audio.wav"
        try:
            model_name = MODEL_MAP[self.model_var.get()]
            compute_pref = COMPUTE_MAP[self.compute_var.get()]
            self._update_status("[1/3] Loading AI model…", 0.08)
            model, device, mode_label = load_model_speed(model_name, compute_pref)
            self.device_info = mode_label
            self._update_status(f"[2/3] Extracting audio ({mode_label})…", 0.22)
            if not extract_audio_fast(video_path, audio_path):
                video = VideoFileClip(video_path)
                video.audio.write_audiofile(audio_path, logger=None)
                video.close()
            self._update_status("[3/3] AI transcription + smart word detection…", 0.42)
            result = transcribe_words(model, audio_path, SPEED_MODE)
            custom_words = self._get_custom_words()
            found = find_profanity_v2(result, custom_words)
            self.profane_words = found
            self._audio_path_for_export = audio_path
            self._video_path_for_export = video_path
            self.window.after(0, self._populate_review_panel)
        except Exception as exc:
            # FIX: Python 3 deletes `exc` after the except block ends.
            # Capture it into a plain local variable NOW so the lambda can use it.
            err_msg = str(exc)
            safe_remove(audio_path)
            self._update_status(f"❌ Error: {err_msg}", 0)
            self.window.after(0, lambda: [
                messagebox.showerror("Error", err_msg),
                self.process_btn.configure(state="normal")
            ])

    def _populate_review_panel(self):
        for w in self.review_scroll.winfo_children():
            w.destroy()
        self.word_vars.clear()
        if not self.profane_words:
            ctk.CTkLabel(self.review_scroll,
                         text="✅ No profanity detected — your video is clean!",
                         font=("Arial", 13), text_color="#6daa45").pack(pady=10)
            self._update_status("No profanity found.", 1.0)
            self.process_btn.configure(state="normal")
            safe_remove(self._audio_path_for_export)
            return
        ctk.CTkLabel(self.review_scroll,
                     text=f"Found {len(self.profane_words)} word(s) — uncheck any you want to KEEP:",
                     font=("Arial", 12, "bold")).pack(anchor="w", padx=4, pady=(4, 8))
        for word_data in self.profane_words:
            var = ctk.BooleanVar(value=True)
            self.word_vars.append(var)
            ts = self._fmt_ts(word_data["start"])
            label = f"  [{ts}]  '{word_data['word']}'  — {word_data['reason']}"
            ctk.CTkCheckBox(self.review_scroll, text=label, variable=var,
                            font=("Consolas", 11)).pack(anchor="w", padx=8, pady=2)
        self.confirm_btn.configure(state="normal")
        self._update_status(
            f"Found {len(self.profane_words)} word(s). Review & confirm below.", 0.65)

    # ── Export ────────────────────────────────────────────────────────────────

    def _confirm_and_export(self):
        selected = [wd for wd, var in zip(self.profane_words, self.word_vars) if var.get()]
        if not selected:
            messagebox.showinfo("Nothing to bleep", "All words were unchecked. No changes made.")
            return
        self.confirm_btn.configure(state="disabled")
        threading.Thread(
            target=self._export_video,
            args=(self._video_path_for_export, self._audio_path_for_export,
                  selected, self.output_dir),
            daemon=True).start()

    def _export_video(self, video_path, audio_path, words_to_bleep, out_dir):
        cleaned_audio_path = video_path + "__cleaned_audio.wav"
        try:
            freq = BEEP_PRESETS[self.beep_preset.get()]
            encode_preset = ENCODE_PRESETS[self.encode_var.get()]
            self._update_status(f"Bleeping {len(words_to_bleep)} word(s)…", 0.70)
            audio_seg = AudioSegment.from_wav(audio_path)
            for i, wd in enumerate(words_to_bleep):
                s_ms = int(wd["start"] * 1000)
                e_ms = int(wd["end"] * 1000)
                dur = max(e_ms - s_ms, 50)
                bleep_seg = (make_beep(dur, freq) if self.bleep_method.get() == "beep"
                             else AudioSegment.silent(duration=dur))
                audio_seg = audio_seg[:s_ms] + bleep_seg + audio_seg[e_ms:]
                self._update_status(f"Bleeping word {i+1}/{len(words_to_bleep)}…",
                                     0.70 + 0.20 * (i + 1) / len(words_to_bleep))
            audio_seg.export(cleaned_audio_path, format="wav")
            self._update_status(f"Creating final video [preset={encode_preset}]…", 0.92)
            out_path = self._build_output_path(video_path, out_dir)
            video = VideoFileClip(video_path)
            clean_audio = AudioFileClip(cleaned_audio_path)
            final = video.with_audio(clean_audio)
            final.write_videofile(out_path, codec="libx264", audio_codec="aac",
                                  preset=encode_preset, threads=os.cpu_count() or 4,
                                  logger=None)
            video.close(); clean_audio.close(); final.close()
            self._update_status("✅ Done! Video saved.", 1.0)
            self.window.after(0, lambda: [
                messagebox.showinfo("Success! ✅",
                                    f"Bleeped {len(words_to_bleep)} word(s)\n\nSaved to:\n{out_path}"),
                self.process_btn.configure(state="normal")
            ])
        except Exception as exc:
            # FIX: Python 3 deletes `exc` after the except block ends.
            # Capture it into a plain local variable NOW so the lambda can use it.
            err_msg = str(exc)
            self._update_status(f"❌ Export error: {err_msg}", 0)
            self.window.after(0, lambda: [
                messagebox.showerror("Export Error", err_msg),
                self.confirm_btn.configure(state="normal")
            ])
        finally:
            safe_remove(audio_path, cleaned_audio_path)

    # ── Batch ─────────────────────────────────────────────────────────────────

    def _start_batch(self):
        self.batch_btn.configure(state="disabled")
        self.batch_log.delete("1.0", "end")
        threading.Thread(target=self._run_batch, daemon=True).start()

    def _run_batch(self):
        in_dir = self._batch_input_dir
        out_dir = getattr(self, "_batch_output_dir", None)
        if not in_dir:
            return
        exts = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv"}
        files = [f for f in os.listdir(in_dir)
                 if os.path.splitext(f)[1].lower() in exts]
        if not files:
            self._batch_log_write("No video files found.")
            self.window.after(0, lambda: self.batch_btn.configure(state="normal"))
            return
        self._batch_log_write(f"Found {len(files)} video(s).\n{'─'*50}")
        model_name = MODEL_MAP[self.model_var.get()]
        compute_pref = COMPUTE_MAP[self.compute_var.get()]
        encode_preset = ENCODE_PRESETS[self.encode_var.get()]
        custom_words = self._get_custom_words()
        freq = BEEP_PRESETS[self.beep_preset.get()]
        model, device, mode_label = load_model_speed(model_name, compute_pref)
        self._batch_log_write(f"Loaded: {mode_label}\n{'─'*50}")
        for idx, fname in enumerate(files, 1):
            video_path = os.path.join(in_dir, fname)
            self._batch_log_write(f"\n[{idx}/{len(files)}] {fname}")
            self._update_status(f"Batch: {fname} ({idx}/{len(files)})…",
                                 (idx - 1) / len(files))
            audio_path = video_path + "__temp_audio.wav"
            cleaned_audio_path = video_path + "__cleaned_audio.wav"
            try:
                if not extract_audio_fast(video_path, audio_path):
                    v = VideoFileClip(video_path)
                    v.audio.write_audiofile(audio_path, logger=None)
                    v.close()
                result = transcribe_words(model, audio_path, SPEED_MODE)
                found = find_profanity_v2(result, custom_words)
                if not found:
                    self._batch_log_write("  → Clean.")
                    safe_remove(audio_path)
                    continue
                self._batch_log_write(f"  → {len(found)} word(s). Bleeping...")
                audio_seg = AudioSegment.from_wav(audio_path)
                for wd in found:
                    s_ms = int(wd["start"] * 1000)
                    e_ms = int(wd["end"] * 1000)
                    dur = max(e_ms - s_ms, 50)
                    bleep_seg = (make_beep(dur, freq) if self.bleep_method.get() == "beep"
                                 else AudioSegment.silent(duration=dur))
                    audio_seg = audio_seg[:s_ms] + bleep_seg + audio_seg[e_ms:]
                audio_seg.export(cleaned_audio_path, format="wav")
                out_path = self._build_output_path(video_path, out_dir)
                v2 = VideoFileClip(video_path)
                ca = AudioFileClip(cleaned_audio_path)
                final = v2.with_audio(ca)
                final.write_videofile(out_path, codec="libx264", audio_codec="aac",
                                      preset=encode_preset, threads=os.cpu_count() or 4,
                                      logger=None)
                v2.close(); ca.close(); final.close()
                self._batch_log_write(f"  → Saved: {os.path.basename(out_path)}")
            except Exception as exc:
                self._batch_log_write(f"  ❌ Error: {exc}")
            finally:
                safe_remove(audio_path, cleaned_audio_path)
        self._update_status("✅ Batch complete!", 1.0)
        self._batch_log_write(f"\n{'─'*50}\n✅ All done!")
        self.window.after(0, lambda: self.batch_btn.configure(state="normal"))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_custom_words(self) -> list[str]:
        raw = self.custom_words_var.get()
        return [w.strip().lower() for w in raw.split(",") if w.strip()]

    @staticmethod
    def _build_output_path(video_path: str, out_dir: str | None) -> str:
        base, ext = os.path.splitext(video_path)
        fname = os.path.basename(base) + "_CLEAN" + ext
        folder = out_dir if out_dir else os.path.dirname(video_path)
        return os.path.join(folder, fname)

    @staticmethod
    def _fmt_ts(seconds: float) -> str:
        return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"

    def _update_status(self, msg: str, progress: float | None = None):
        def _apply():
            self.status_label.configure(text=msg)
            if progress is not None:
                self.progress.set(max(0.0, min(1.0, progress)))
        self.window.after(0, _apply)

    def _batch_log_write(self, line: str):
        self.window.after(0, lambda: [
            self.batch_log.insert("end", line + "\n"),
            self.batch_log.see("end")
        ])

    def run(self):
        self.window.mainloop()


if __name__ == "__main__":
    print("=" * 60)
    print("  AutoBleep Pro v2.2 — Smart Detection ⚡")
    print(f"  Speed mode: {'faster-whisper + stable-ts' if SPEED_MODE else 'openai-whisper (fallback)'}")
    print("=" * 60)
    AutoBleepPro().run()
