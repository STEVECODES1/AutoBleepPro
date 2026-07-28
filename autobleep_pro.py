"""
AutoBleep Pro v2.1 - Automatic Video Profanity Bleeper
=====================================================
SPEED STACK:
  - faster-whisper  → ~4x faster transcription vs openai-whisper
  - stable-ts       → accurate word-level timestamps on faster-whisper
  - int8 / float16  → compute mode selection (less RAM, faster)
  - ffmpeg extract  → fast 16kHz mono WAV extraction
  - libx264 presets → ultrafast / fast encode options
  - Turbo model     → added to model picker

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
import numpy as np
from datetime import timedelta

# ── Try importing speed stack; fall back to openai-whisper if not installed ──
try:
    import stable_whisper
    SPEED_MODE = True
except ImportError:
    import whisper as openai_whisper
    SPEED_MODE = False
    print("[AutoBleep] stable-ts not found — using openai-whisper. "
          "Run: pip install stable-ts[fw] for 4x speed.")

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


def detect_device():
    """Pick the fastest available device: NVIDIA GPU (CUDA) if present, else CPU."""
    if torch.cuda.is_available():
        return "cuda", torch.cuda.get_device_name(0)
    return "cpu", f"{os.cpu_count()} CPU cores"


def make_beep(duration_ms: int, freq_hz: int, _cache: dict = {}) -> AudioSegment:
    """Return a beep of the given duration, caching by (duration, freq) key."""
    key = (duration_ms, freq_hz)
    if key not in _cache:
        base = Sine(freq_hz).to_audio_segment(duration=100)
        repeated = base * (duration_ms // 100 + 1)
        _cache[key] = repeated[:duration_ms]
    return _cache[key]


def safe_remove(*paths):
    """Delete temp files, silently ignoring missing-file errors."""
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def extract_audio_fast(video_path: str, wav_path: str) -> bool:
    """
    Fast audio extraction using ffmpeg CLI (16kHz mono WAV).
    Returns True on success, False if ffmpeg not available.
    """
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", video_path,
                "-ac", "1",         # mono
                "-ar", "16000",     # 16kHz (Whisper native rate)
                "-vn",              # no video
                wav_path
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def load_model_speed(model_name: str, compute_pref: str):
    """
    Load stable-ts (faster-whisper backend) with chosen compute type.
    Falls back to openai-whisper if stable-ts unavailable.
    Returns (model, device_str, mode_label).
    """
    device, dev_label = detect_device()

    if SPEED_MODE:
        if compute_pref == "auto":
            compute_type = "float16" if device == "cuda" else "int8"
        else:
            compute_type = compute_pref
        try:
            model = stable_whisper.load_faster_whisper(
                model_name, device=device, compute_type=compute_type
            )
            mode = f"faster-whisper [{compute_type}] on {device.upper()} ({dev_label})"
            return model, device, mode
        except Exception as e:
            print(f"[AutoBleep] faster-whisper load failed ({e}), trying standard stable-ts...")
            try:
                model = stable_whisper.load_model(model_name, device=device)
                mode = f"stable-ts on {device.upper()} ({dev_label})"
                return model, device, mode
            except Exception as e2:
                print(f"[AutoBleep] stable-ts also failed ({e2}), falling back to openai-whisper")

    # Fallback: openai-whisper
    model = openai_whisper.load_model(model_name, device=device)
    mode = f"openai-whisper on {device.upper()} ({dev_label})"
    return model, device, mode


def transcribe_words(model, audio_path: str, speed_mode: bool) -> dict:
    """
    Transcribe audio and return result in standard Whisper dict format
    with word-level timestamps.
    """
    if speed_mode:
        # stable-ts returns a WhisperResult object
        result = model.transcribe(audio_path, word_timestamps=True)
        segments = []
        for seg in result.segments:
            words = []
            for w in (seg.words or []):
                words.append({
                    "word": w.word,
                    "start": float(w.start),
                    "end": float(w.end),
                })
            segments.append({"words": words, "text": seg.text})
        return {"segments": segments}
    else:
        # openai-whisper returns a plain dict
        return model.transcribe(audio_path, word_timestamps=True)


class AutoBleepPro:
    def __init__(self):
        self.window = ctk.CTk()
        self.window.title(
            "AutoBleep Pro v2.1 — Speed Edition ⚡")
        self.window.geometry("1080x960")
        self.window.minsize(800, 700)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # ── State ─────────────────────────────────────────────────────────────
        self.video_paths: list[str] = []
        self.output_dir: str | None = None
        self.profane_words: list[dict] = []
        self.word_vars: list[ctk.BooleanVar] = []
        self.device_info = "unknown"
        self._batch_input_dir: str | None = None
        self._batch_output_dir: str | None = None

        self._setup_ui()

    # ─────────────────────────────────────────────────────────────────────────
    # UI SETUP
    # ─────────────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        # ── Header ────────────────────────────────────────────────────────────
        hdr = ctk.CTkFrame(self.window, fg_color="transparent")
        hdr.pack(pady=(18, 4), padx=20, fill="x")

        ctk.CTkLabel(
            hdr, text="🔇 AutoBleep Pro",
            font=("Arial", 36, "bold")).pack()

        speed_label = ("⚡ Speed Edition v2.1  •  faster-whisper + stable-ts"
                       if SPEED_MODE
                       else "v2.1  •  openai-whisper (install stable-ts[fw] for 4x speed)")
        ctk.CTkLabel(
            hdr, text=speed_label,
            font=("Arial", 13),
            text_color="#4f98a3" if SPEED_MODE else "gray"
        ).pack(pady=2)

        # ── Tab view ──────────────────────────────────────────────────────────
        self.tabs = ctk.CTkTabview(self.window, height=580)
        self.tabs.pack(pady=6, padx=20, fill="both", expand=True)
        self.tabs.add("Single Video")
        self.tabs.add("Batch Folder")

        self._build_single_tab(self.tabs.tab("Single Video"))
        self._build_batch_tab(self.tabs.tab("Batch Folder"))

        # ── Bottom status bar ─────────────────────────────────────────────────
        bot = ctk.CTkFrame(self.window)
        bot.pack(pady=(4, 12), padx=20, fill="x")

        self.progress = ctk.CTkProgressBar(bot)
        self.progress.pack(pady=(10, 4), padx=20, fill="x")
        self.progress.set(0)

        self.status_label = ctk.CTkLabel(
            bot, text="Ready — select a video to begin",
            font=("Arial", 13))
        self.status_label.pack(pady=(0, 8))

    # ── Single Video tab ──────────────────────────────────────────────────────

    def _build_single_tab(self, parent):
        # Step 1 — Video selection
        s1 = ctk.CTkFrame(parent)
        s1.pack(pady=8, padx=10, fill="x")
        ctk.CTkLabel(s1, text="📹 Step 1: Select Video",
                     font=("Arial", 15, "bold")).pack(anchor="w", padx=14, pady=8)

        row = ctk.CTkFrame(s1, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=4)

        self.file_label = ctk.CTkLabel(
            row, text="No video selected",
            font=("Arial", 12), anchor="w")
        self.file_label.pack(side="left", fill="x", expand=True, padx=6)

        ctk.CTkButton(row, text="Browse Video",
                      command=self._pick_single_video,
                      width=150, height=34).pack(side="right", padx=6)

        # Step 2 — Settings
        s2 = ctk.CTkFrame(parent)
        s2.pack(pady=8, padx=10, fill="x")
        ctk.CTkLabel(s2, text="⚙️ Step 2: Settings",
                     font=("Arial", 15, "bold")).pack(anchor="w", padx=14, pady=8)

        inner = ctk.CTkFrame(s2, fg_color="transparent")
        inner.pack(fill="x", padx=28, pady=4)

        # Censor method
        ctk.CTkLabel(inner, text="Censoring Method:",
                     font=("Arial", 12)).pack(anchor="w", pady=(4, 2))
        self.bleep_method = ctk.StringVar(value="beep")
        mf = ctk.CTkFrame(inner, fg_color="transparent")
        mf.pack(anchor="w")
        ctk.CTkRadioButton(mf, text="🔊 Beep Sound",
                           variable=self.bleep_method, value="beep").pack(side="left", padx=8)
        ctk.CTkRadioButton(mf, text="🔇 Silence (Mute)",
                           variable=self.bleep_method, value="silence").pack(side="left", padx=8)

        # Beep preset
        ctk.CTkLabel(inner, text="Beep Sound Preset:",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.beep_preset = ctk.StringVar(value=list(BEEP_PRESETS.keys())[0])
        ctk.CTkOptionMenu(inner, values=list(BEEP_PRESETS.keys()),
                          variable=self.beep_preset, width=280).pack(anchor="w")

        # AI model
        ctk.CTkLabel(inner, text="AI Transcription Model:",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.model_var = ctk.StringVar(value=list(MODEL_MAP.keys())[1])  # base default
        ctk.CTkOptionMenu(inner, values=list(MODEL_MAP.keys()),
                          variable=self.model_var, width=320).pack(anchor="w")

        # ⚡ Compute type (v2.1 new)
        ctk.CTkLabel(inner, text="⚡ Compute Mode (Speed vs Accuracy):",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.compute_var = ctk.StringVar(value=list(COMPUTE_MAP.keys())[0])  # auto
        ctk.CTkOptionMenu(inner, values=list(COMPUTE_MAP.keys()),
                          variable=self.compute_var, width=320).pack(anchor="w")

        # ⚡ Encode preset (v2.1 new)
        ctk.CTkLabel(inner, text="⚡ Video Export Speed:",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.encode_var = ctk.StringVar(value=list(ENCODE_PRESETS.keys())[1])  # fast
        ctk.CTkOptionMenu(inner, values=list(ENCODE_PRESETS.keys()),
                          variable=self.encode_var, width=320).pack(anchor="w")

        # Custom words
        ctk.CTkLabel(inner, text="Extra words to bleep (comma-separated, optional):",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.custom_words_var = ctk.StringVar()
        ctk.CTkEntry(inner, textvariable=self.custom_words_var, width=420,
                     placeholder_text="e.g., rival brand, competitor name").pack(anchor="w")

        # Output directory
        ctk.CTkLabel(inner, text="Output Folder (optional):",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        out_row = ctk.CTkFrame(inner, fg_color="transparent")
        out_row.pack(anchor="w", fill="x")
        self.out_label = ctk.CTkLabel(out_row, text="Same folder as input",
                                      font=("Arial", 11), text_color="gray", anchor="w")
        self.out_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(out_row, text="Choose Folder",
                      command=self._pick_output_dir,
                      width=140, height=30).pack(side="right", padx=4)

        # Step 3 — Process
        s3 = ctk.CTkFrame(parent)
        s3.pack(pady=8, padx=10, fill="x")
        ctk.CTkLabel(s3, text="🚀 Step 3: Process",
                     font=("Arial", 15, "bold")).pack(anchor="w", padx=14, pady=8)

        self.process_btn = ctk.CTkButton(
            s3,
            text="🎬 Analyze & Bleep Video",
            command=self._start_single,
            height=48, font=("Arial", 15, "bold"),
            fg_color="#2B7A0B", hover_color="#1F5A08",
            state="disabled"
        )
        self.process_btn.pack(pady=10, padx=24, fill="x")

        # Word review panel
        wr = ctk.CTkFrame(parent)
        wr.pack(pady=8, padx=10, fill="both", expand=True)
        ctk.CTkLabel(wr, text="🔍 Word Review — uncheck words you DON'T want bleeped:",
                     font=("Arial", 13, "bold")).pack(anchor="w", padx=14, pady=(8, 4))

        self.review_scroll = ctk.CTkScrollableFrame(wr, height=130)
        self.review_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        self.review_placeholder = ctk.CTkLabel(
            self.review_scroll,
            text="Detected words will appear here after analysis.",
            font=("Arial", 11), text_color="gray")
        self.review_placeholder.pack(padx=8, pady=8)

        self.confirm_btn = ctk.CTkButton(
            wr, text="✅ Confirm & Export Clean Video",
            command=self._confirm_and_export,
            height=40, font=("Arial", 13, "bold"),
            fg_color="#1a6faf", hover_color="#14527f",
            state="disabled"
        )
        self.confirm_btn.pack(pady=(0, 10), padx=24, fill="x")

    # ── Batch tab ─────────────────────────────────────────────────────────────

    def _build_batch_tab(self, parent):
        ctk.CTkLabel(
            parent,
            text="Process an entire folder of videos automatically — no review step.",
            font=("Arial", 13), text_color="gray").pack(pady=(14, 4))

        bf = ctk.CTkFrame(parent)
        bf.pack(pady=8, padx=10, fill="x")

        ctk.CTkLabel(bf, text="📁 Input Folder:",
                     font=("Arial", 13, "bold")).pack(anchor="w", padx=14, pady=(10, 2))
        brow = ctk.CTkFrame(bf, fg_color="transparent")
        brow.pack(fill="x", padx=14, pady=4)
        self.batch_dir_label = ctk.CTkLabel(
            brow, text="No folder selected", font=("Arial", 11),
            text_color="gray", anchor="w")
        self.batch_dir_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(brow, text="Browse Folder",
                      command=self._pick_batch_folder,
                      width=150, height=34).pack(side="right", padx=6)

        ctk.CTkLabel(bf, text="📤 Output Folder:",
                     font=("Arial", 13, "bold")).pack(anchor="w", padx=14, pady=(10, 2))
        orow = ctk.CTkFrame(bf, fg_color="transparent")
        orow.pack(fill="x", padx=14, pady=4)
        self.batch_out_label = ctk.CTkLabel(
            orow, text="Same as input folder", font=("Arial", 11),
            text_color="gray", anchor="w")
        self.batch_out_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(orow, text="Choose Folder",
                      command=self._pick_batch_output,
                      width=150, height=34).pack(side="right", padx=6)

        # Settings info
        bs = ctk.CTkFrame(parent)
        bs.pack(pady=8, padx=10, fill="x")
        ctk.CTkLabel(bs, text="⚙️ Batch Settings",
                     font=("Arial", 14, "bold")).pack(anchor="w", padx=14, pady=8)
        ctk.CTkLabel(bs,
                     text="Uses same Method / Model / Compute Mode / "
                          "Encode Speed / Beep Preset / Custom Words "
                          "as the Single Video tab.",
                     font=("Arial", 11), text_color="gray"
                     ).pack(anchor="w", padx=28, pady=(0, 10))

        self.batch_btn = ctk.CTkButton(
            parent,
            text="⚡ Process All Videos in Folder",
            command=self._start_batch,
            height=48, font=("Arial", 15, "bold"),
            fg_color="#7a2bb0", hover_color="#591e80",
            state="disabled"
        )
        self.batch_btn.pack(pady=14, padx=24, fill="x")

        self.batch_log = ctk.CTkTextbox(parent, font=("Consolas", 11), height=240)
        self.batch_log.pack(pady=4, padx=14, fill="both", expand=True)
        self.batch_log.insert("1.0", "Batch log will appear here...\n")

    # ─────────────────────────────────────────────────────────────────────────
    # FILE / FOLDER PICKERS
    # ─────────────────────────────────────────────────────────────────────────

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

    # ─────────────────────────────────────────────────────────────────────────
    # SINGLE VIDEO — ANALYZE PHASE
    # ─────────────────────────────────────────────────────────────────────────

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

            self._update_status(
                f"[2/3] Extracting audio ({mode_label})…", 0.22)

            # Try fast ffmpeg extract first, fall back to moviepy
            if not extract_audio_fast(video_path, audio_path):
                video = VideoFileClip(video_path)
                video.audio.write_audiofile(audio_path, logger=None)
                video.close()

            self._update_status("[3/3] AI transcription with word timestamps…", 0.42)
            result = transcribe_words(model, audio_path, SPEED_MODE)

            custom_words = self._get_custom_words()
            found = self._find_profanity(result, custom_words)

            self.profane_words = found
            self._audio_path_for_export = audio_path
            self._video_path_for_export = video_path

            self.window.after(0, self._populate_review_panel)

        except Exception as exc:
            safe_remove(audio_path)
            self._update_status(f"❌ Error: {exc}", 0)
            self.window.after(0, lambda: [
                messagebox.showerror("Error", str(exc)),
                self.process_btn.configure(state="normal")
            ])

    # ─────────────────────────────────────────────────────────────────────────
    # WORD REVIEW PANEL
    # ─────────────────────────────────────────────────────────────────────────

    def _populate_review_panel(self):
        for w in self.review_scroll.winfo_children():
            w.destroy()
        self.word_vars.clear()

        if not self.profane_words:
            ctk.CTkLabel(
                self.review_scroll,
                text="✅ No profanity detected — your video is clean!",
                font=("Arial", 13), text_color="#6daa45").pack(pady=10)
            self._update_status("No profanity found.", 1.0)
            self.process_btn.configure(state="normal")
            safe_remove(self._audio_path_for_export)
            return

        ctk.CTkLabel(
            self.review_scroll,
            text=f"Found {len(self.profane_words)} word(s) — uncheck any you want to KEEP:",
            font=("Arial", 12, "bold")).pack(anchor="w", padx=4, pady=(4, 8))

        for word_data in self.profane_words:
            var = ctk.BooleanVar(value=True)
            self.word_vars.append(var)
            ts = self._fmt_ts(word_data["start"])
            label = f"  [{ts}]  '{word_data['word']}'  — {word_data['reason']}"
            cb = ctk.CTkCheckBox(self.review_scroll, text=label,
                                 variable=var, font=("Consolas", 11))
            cb.pack(anchor="w", padx=8, pady=2)

        self.confirm_btn.configure(state="normal")
        self._update_status(
            f"Found {len(self.profane_words)} word(s). Review & confirm below.", 0.65)

    # ─────────────────────────────────────────────────────────────────────────
    # SINGLE VIDEO — EXPORT PHASE
    # ─────────────────────────────────────────────────────────────────────────

    def _confirm_and_export(self):
        selected = [
            wd for wd, var in zip(self.profane_words, self.word_vars)
            if var.get()
        ]
        if not selected:
            messagebox.showinfo(
                "Nothing to bleep",
                "All words were unchecked. No changes made.")
            return
        self.confirm_btn.configure(state="disabled")
        threading.Thread(
            target=self._export_video,
            args=(self._video_path_for_export,
                  self._audio_path_for_export,
                  selected,
                  self.output_dir),
            daemon=True
        ).start()

    def _export_video(self, video_path, audio_path, words_to_bleep, out_dir):
        cleaned_audio_path = video_path + "__cleaned_audio.wav"
        try:
            freq = BEEP_PRESETS[self.beep_preset.get()]
            encode_preset = ENCODE_PRESETS[self.encode_var.get()]

            self._update_status(
                f"Bleeping {len(words_to_bleep)} word(s)…", 0.70)

            audio_seg = AudioSegment.from_wav(audio_path)

            for i, wd in enumerate(words_to_bleep):
                s_ms = int(wd["start"] * 1000)
                e_ms = int(wd["end"] * 1000)
                dur = max(e_ms - s_ms, 50)

                bleep_seg = (make_beep(dur, freq)
                             if self.bleep_method.get() == "beep"
                             else AudioSegment.silent(duration=dur))

                audio_seg = audio_seg[:s_ms] + bleep_seg + audio_seg[e_ms:]
                self._update_status(
                    f"Bleeping word {i+1}/{len(words_to_bleep)}…",
                    0.70 + 0.20 * (i + 1) / len(words_to_bleep)
                )

            audio_seg.export(cleaned_audio_path, format="wav")

            self._update_status(
                f"Creating final video [preset={encode_preset}]…", 0.92)
            out_path = self._build_output_path(video_path, out_dir)

            video = VideoFileClip(video_path)
            clean_audio = AudioFileClip(cleaned_audio_path)
            final = video.with_audio(clean_audio)
            final.write_videofile(
                out_path,
                codec="libx264",
                audio_codec="aac",
                preset=encode_preset,          # ⚡ v2.1: user-chosen encode speed
                threads=os.cpu_count() or 4,   # ⚡ v2.1: all CPU cores
                logger=None
            )
            video.close()
            clean_audio.close()
            final.close()

            self._update_status("✅ Done! Video saved.", 1.0)
            self.window.after(0, lambda: [
                messagebox.showinfo(
                    "Success! ✅",
                    f"Bleeped {len(words_to_bleep)} word(s)\n\nSaved to:\n{out_path}"
                ),
                self.process_btn.configure(state="normal")
            ])

        except Exception as exc:
            self._update_status(f"❌ Export error: {exc}", 0)
            self.window.after(0, lambda: [
                messagebox.showerror("Export Error", str(exc)),
                self.confirm_btn.configure(state="normal")
            ])
        finally:
            safe_remove(audio_path, cleaned_audio_path)

    # ─────────────────────────────────────────────────────────────────────────
    # BATCH PROCESSING
    # ─────────────────────────────────────────────────────────────────────────

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
            self._batch_log_write("No video files found in selected folder.")
            self.window.after(0, lambda: self.batch_btn.configure(state="normal"))
            return

        self._batch_log_write(
            f"Found {len(files)} video(s). Starting...\n{'─'*50}")

        model_name = MODEL_MAP[self.model_var.get()]
        compute_pref = COMPUTE_MAP[self.compute_var.get()]
        encode_preset = ENCODE_PRESETS[self.encode_var.get()]
        custom_words = self._get_custom_words()
        freq = BEEP_PRESETS[self.beep_preset.get()]

        self._batch_log_write(f"Model: {model_name} | Compute: {compute_pref} "
                               f"| Encode: {encode_preset}")

        model, device, mode_label = load_model_speed(model_name, compute_pref)
        self._batch_log_write(f"Loaded: {mode_label}\n{'─'*50}")

        for idx, fname in enumerate(files, 1):
            video_path = os.path.join(in_dir, fname)
            self._batch_log_write(f"\n[{idx}/{len(files)}] {fname}")
            self._update_status(
                f"Batch: {fname} ({idx}/{len(files)})…",
                (idx - 1) / len(files))

            audio_path = video_path + "__temp_audio.wav"
            cleaned_audio_path = video_path + "__cleaned_audio.wav"
            try:
                if not extract_audio_fast(video_path, audio_path):
                    video = VideoFileClip(video_path)
                    video.audio.write_audiofile(audio_path, logger=None)
                    video.close()

                result = transcribe_words(model, audio_path, SPEED_MODE)
                found = self._find_profanity(result, custom_words)

                if not found:
                    self._batch_log_write("  → Clean — no bleeping needed.")
                    safe_remove(audio_path)
                    continue

                self._batch_log_write(
                    f"  → {len(found)} word(s) found. Bleeping...")

                audio_seg = AudioSegment.from_wav(audio_path)
                for wd in found:
                    s_ms = int(wd["start"] * 1000)
                    e_ms = int(wd["end"] * 1000)
                    dur = max(e_ms - s_ms, 50)
                    bleep_seg = (make_beep(dur, freq)
                                 if self.bleep_method.get() == "beep"
                                 else AudioSegment.silent(duration=dur))
                    audio_seg = audio_seg[:s_ms] + bleep_seg + audio_seg[e_ms:]

                audio_seg.export(cleaned_audio_path, format="wav")
                out_path = self._build_output_path(video_path, out_dir)

                video2 = VideoFileClip(video_path)
                cl_audio = AudioFileClip(cleaned_audio_path)
                final = video2.with_audio(cl_audio)
                final.write_videofile(
                    out_path,
                    codec="libx264",
                    audio_codec="aac",
                    preset=encode_preset,
                    threads=os.cpu_count() or 4,
                    logger=None
                )
                video2.close()
                cl_audio.close()
                final.close()

                self._batch_log_write(
                    f"  → Saved: {os.path.basename(out_path)}")

            except Exception as exc:
                self._batch_log_write(f"  ❌ Error: {exc}")
            finally:
                safe_remove(audio_path, cleaned_audio_path)

        self._update_status("✅ Batch complete!", 1.0)
        self._batch_log_write(f"\n{'─'*50}\n✅ All done!")
        self.window.after(0, lambda: self.batch_btn.configure(state="normal"))

    # ─────────────────────────────────────────────────────────────────────────
    # SHARED HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _find_profanity(self, result: dict, custom_words: list[str]) -> list[dict]:
        found = []
        for segment in result.get("segments", []):
            for word_info in segment.get("words", []):
                word = word_info["word"].strip().lower()
                stripped = word.strip('.,!?;:\"()[]{}-')
                core = re.sub(r"'(s|ll|d|m|re|ve|t)$", "", stripped)

                is_profane = any(
                    profanity.contains_profanity(v)
                    for v in (word, stripped, core)
                )
                is_custom = any(
                    c and (c in word or c in stripped or c in core)
                    for c in custom_words
                )

                if is_profane or is_custom:
                    found.append({
                        "word": word_info["word"],
                        "start": word_info["start"],
                        "end": word_info["end"],
                        "reason": "Profanity" if is_profane else "Custom word",
                    })
        return found

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
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m:02d}:{s:02d}"

    def _update_status(self, msg: str, progress: float | None = None):
        def _apply():
            self.status_label.configure(text=msg)
            if progress is not None:
                self.progress.set(max(0.0, min(1.0, progress)))
        self.window.after(0, _apply)

    def _batch_log_write(self, line: str):
        self.window.after(
            0, lambda: [
                self.batch_log.insert("end", line + "\n"),
                self.batch_log.see("end")
            ]
        )

    # ─────────────────────────────────────────────────────────────────────────
    # LAUNCH
    # ─────────────────────────────────────────────────────────────────────────

    def run(self):
        self.window.mainloop()


if __name__ == "__main__":
    print("=" * 60)
    print("  AutoBleep Pro v2.1 — Speed Edition ⚡")
    print(f"  Speed mode: {'faster-whisper + stable-ts' if SPEED_MODE else 'openai-whisper (fallback)'}")
    print("=" * 60)
    AutoBleepPro().run()
