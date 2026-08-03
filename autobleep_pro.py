"""
AutoBleep Pro v2.2.1 - Automatic Video Profanity Bleeper (GUI)
==============================================================
All detection/audio/model logic lives in `bleep_engine.py`. This module is
the customtkinter GUI and its worker threads - nothing else.

Threading contract
------------------
Tk is not thread-safe. Two rules, enforced throughout:

1. Worker threads never read a Tk variable. Every setting is snapshotted
   into an immutable `Settings` on the main thread before the thread
   starts (v2.2 called `self.bleep_method.get()` etc. from inside the
   worker loops).
2. Worker threads never touch a widget directly - they go through
   `self._on_main(...)`, which marshals onto the Tk event loop.

Run with:  python autobleep_pro.py
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
from dataclasses import dataclass, field

import customtkinter as ctk
from tkinter import filedialog, messagebox

from pydub import AudioSegment
from moviepy import VideoFileClip, AudioFileClip

import bleep_engine as engine
from bleep_engine import (
    SPEED_MODE,
    ModelCache,
    apply_bleeps,
    build_output_path,
    configure_threads,
    extract_audio_fast,
    find_profanity_v2,
    is_generated_output,
    safe_remove,
    transcribe_words,
)

APP_VERSION = "2.2.1"

configure_threads()

# ── Picker options ───────────────────────────────────────────────────────────

MODEL_MAP = {
    "tiny   — max speed (less accurate)": "tiny",
    "base   — recommended (balanced)": "base",
    "small  — more accurate (slower)": "small",
    "medium — best accuracy": "medium",
    "turbo  — fast large model (GPU recommended)": "turbo",
}

COMPUTE_MAP = {
    "Auto (GPU=float16, CPU=int8)": "auto",
    "int8 — fastest / least RAM": "int8",
    "float16 — best GPU speed": "float16",
    "float32 — max compatibility": "float32",
}

ENCODE_PRESETS = {
    "ultrafast — fastest export": "ultrafast",
    "fast — good balance": "fast",
    "medium — default quality": "medium",
    "slow — best compression": "slow",
}

BEEP_PRESETS = {
    "Classic TV Bleep (1000 Hz)": 1000,
    "High Pitch (1500 Hz)": 1500,
    "Low Buzz (400 Hz)": 400,
    "Air Horn (600 Hz)": 600,
}

VIDEO_FILETYPES = [
    ("Video Files", "*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.m4v *.webm *.ts"),
    ("All Files", "*.*"),
]


@dataclass(frozen=True)
class Settings:
    """An immutable snapshot of the control panel, taken on the main thread."""
    model_name: str
    compute_pref: str
    encode_preset: str
    bleep_method: str
    beep_freq: int
    fuzzy: bool
    output_dir: str | None
    custom_words: tuple[str, ...] = field(default=())


def _new_temp_wav() -> str:
    """A unique temp .wav path.

    v2.2 used `video_path + "__temp_audio.wav"`, which collides whenever
    two videos share a stem, breaks on read-only input folders, and leaves
    debris next to the user's footage.
    """
    fd, path = tempfile.mkstemp(prefix="autobleep_", suffix=".wav")
    os.close(fd)
    return path


class AutoBleepPro:
    # How often the main thread checks for work queued by worker threads.
    _UI_POLL_MS = 40

    def __init__(self):
        self.window = ctk.CTk()
        self.window.title(f"AutoBleep Pro v{APP_VERSION} — Smart Detection ⚡")
        self.window.geometry("1080x960")
        self.window.minsize(800, 700)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.video_paths: list[str] = []
        self.output_dir: str | None = None
        self.profane_words: list[dict] = []
        self.word_vars: list[ctk.BooleanVar] = []
        self.device_info: str = "unknown"

        # v2.2 assigned these mid-run and read them elsewhere, so any error
        # before the assignment raised AttributeError instead of the real
        # problem.
        self._audio_path_for_export: str | None = None
        self._video_path_for_export: str | None = None
        self._batch_input_dir: str | None = None
        self._batch_output_dir: str | None = None
        self._busy = False
        self._temp_files: set[str] = set()

        self._model_cache = ModelCache()

        self._ui_queue: queue.Queue = queue.Queue()
        self._closing = False
        self._poll_id: str | None = None

        self._setup_ui()
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self._drain_ui_queue()   # starts the main-thread poll loop

    # ── Thread marshalling ───────────────────────────────────────────────────

    def _on_main(self, fn, *args, **kwargs) -> None:
        """Queue `fn` to run on the Tk event loop. Safe from any thread.

        Deliberately NOT `window.after(0, ...)`: `after()` is itself a Tcl
        call that registers a command on the interpreter, so calling it
        from a worker is unsafe and raises outright
        ("RuntimeError: main thread is not in main loop") whenever the main
        thread isn't sitting inside mainloop() - during shutdown, during a
        modal dialog, or under a test harness driving update(). v2.2 used
        that pattern everywhere, so those updates were silently lost.

        `queue.Queue.put` touches no Tcl at all; `_drain_ui_queue` runs the
        callbacks on the main thread.
        """
        self._ui_queue.put((fn, args, kwargs))

    def _drain_ui_queue(self) -> None:
        """Main thread only: run whatever the workers queued up."""
        try:
            while True:
                fn, args, kwargs = self._ui_queue.get_nowait()
                try:
                    fn(*args, **kwargs)
                except Exception as exc:  # a broken UI update must not kill the poller
                    print(f"[AutoBleep] UI update failed: {exc}")
        except queue.Empty:
            pass
        if not self._closing:
            self._poll_id = self.window.after(self._UI_POLL_MS, self._drain_ui_queue)

    def _update_status(self, msg: str, progress: float | None = None) -> None:
        def _apply():
            self.status_label.configure(text=msg)
            if progress is not None:
                self.progress.set(max(0.0, min(1.0, progress)))
        self._on_main(_apply)

    def _batch_log_write(self, line: str) -> None:
        def _apply():
            self.batch_log.insert("end", line + "\n")
            self.batch_log.see("end")
        self._on_main(_apply)

    def _show_error(self, title: str, message: str) -> None:
        self._on_main(messagebox.showerror, title, message)

    def _set_buttons(self, *, process=None, confirm=None, batch=None) -> None:
        def _apply():
            if process is not None:
                self.process_btn.configure(state="normal" if process else "disabled")
            if confirm is not None:
                self.confirm_btn.configure(state="normal" if confirm else "disabled")
            if batch is not None:
                self.batch_btn.configure(state="normal" if batch else "disabled")
        self._on_main(_apply)

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self):
        hdr = ctk.CTkFrame(self.window, fg_color="transparent")
        hdr.pack(pady=(18, 4), padx=20, fill="x")
        ctk.CTkLabel(hdr, text="🔇 AutoBleep Pro",
                     font=("Arial", 36, "bold")).pack()
        speed_label = (
            f"⚡ v{APP_VERSION} — Smart Detection  •  faster-whisper + stable-ts"
            if SPEED_MODE else
            f"v{APP_VERSION} — Smart Detection  •  openai-whisper "
            "(install stable-ts[fw] for 4x speed)")
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

        # Default ON = v2.2 behaviour. The escape hatch matters most in
        # batch mode, which has no review step to untick false positives.
        self.fuzzy_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            inner,
            text="Fuzzy matching — also flag minced oaths (fudge, shoot, dang) "
                 "and likely mishears (duck, shirt)",
            variable=self.fuzzy_var, font=("Arial", 11),
        ).pack(anchor="w", pady=(14, 2))

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
        ctk.CTkLabel(bs, text="Uses same Method / Model / Compute / Encode / Beep / Fuzzy / "
                             "Custom Words as the Single Video tab.",
                     font=("Arial", 11), text_color="gray").pack(anchor="w", padx=28, pady=(0, 10))

        self.batch_btn = ctk.CTkButton(
            parent, text="⚡ Process All Videos in Folder", command=self._start_batch,
            height=48, font=("Arial", 15, "bold"),
            fg_color="#7a2bb0", hover_color="#591e80", state="disabled")
        self.batch_btn.pack(pady=14, padx=24, fill="x")

        self.batch_log = ctk.CTkTextbox(parent, font=("Consolas", 11), height=240)
        self.batch_log.pack(pady=4, padx=14, fill="both", expand=True)
        self.batch_log.insert("1.0", "Batch log will appear here...\n")

    # ── File pickers (main thread only) ──────────────────────────────────────

    def _pick_single_video(self):
        path = filedialog.askopenfilename(title="Select Video File",
                                          filetypes=VIDEO_FILETYPES)
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

    # ── Settings snapshot ────────────────────────────────────────────────────

    def _snapshot_settings(self) -> Settings:
        """Read every Tk variable ONCE, on the main thread."""
        raw = self.custom_words_var.get()
        return Settings(
            model_name=MODEL_MAP[self.model_var.get()],
            compute_pref=COMPUTE_MAP[self.compute_var.get()],
            encode_preset=ENCODE_PRESETS[self.encode_var.get()],
            bleep_method=self.bleep_method.get(),
            beep_freq=BEEP_PRESETS[self.beep_preset.get()],
            fuzzy=bool(self.fuzzy_var.get()),
            output_dir=self.output_dir,
            custom_words=tuple(w.strip().lower() for w in raw.split(",") if w.strip()),
        )

    # ── Shared worker helpers ────────────────────────────────────────────────

    def _track_temp(self, path: str) -> str:
        self._temp_files.add(path)
        return path

    def _drop_temp(self, *paths: str | None) -> None:
        safe_remove(*paths)
        for p in paths:
            self._temp_files.discard(p)

    def _extract_audio(self, video_path: str, wav_path: str) -> None:
        """ffmpeg first, moviepy as fallback. Raises with a readable message."""
        if extract_audio_fast(video_path, wav_path):
            return
        clip = None
        try:
            clip = VideoFileClip(video_path)
            if clip.audio is None:
                raise RuntimeError(
                    f"{os.path.basename(video_path)} has no audio track — "
                    "nothing to bleep.")
            clip.audio.write_audiofile(wav_path, logger=None)
        finally:
            if clip is not None:
                try:
                    clip.close()
                except Exception:
                    pass

    def _render(self, video_path: str, cleaned_audio_path: str,
                out_path: str, encode_preset: str) -> None:
        """Mux the cleaned audio back onto the video. Always closes its clips."""
        video = audio = final = None
        try:
            video = VideoFileClip(video_path)
            audio = AudioFileClip(cleaned_audio_path)
            final = video.with_audio(audio)
            final.write_videofile(out_path, codec="libx264", audio_codec="aac",
                                  preset=encode_preset, threads=os.cpu_count() or 4,
                                  logger=None)
        finally:
            for clip in (final, audio, video):
                if clip is not None:
                    try:
                        clip.close()
                    except Exception:
                        pass

    # ── Single video: analyze ────────────────────────────────────────────────

    def _start_single(self):
        if self._busy or not self.video_paths:
            return
        self._busy = True
        settings = self._snapshot_settings()          # main thread
        self._set_buttons(process=False, confirm=False)
        threading.Thread(target=self._analyze_video,
                         args=(self.video_paths[0], settings), daemon=True).start()

    def _analyze_video(self, video_path: str, settings: Settings):
        audio_path = self._track_temp(_new_temp_wav())
        try:
            self._update_status("[1/3] Loading AI model…", 0.08)
            bundle = self._model_cache.get(settings.model_name, settings.compute_pref)
            self.device_info = bundle.label

            self._update_status(f"[2/3] Extracting audio ({bundle.label})…", 0.22)
            self._extract_audio(video_path, audio_path)

            self._update_status("[3/3] AI transcription + smart word detection…", 0.42)
            result = transcribe_words(bundle, audio_path)
            found = find_profanity_v2(result, settings.custom_words, fuzzy=settings.fuzzy)

            self.profane_words = found
            self._audio_path_for_export = audio_path
            self._video_path_for_export = video_path
            self._on_main(self._populate_review_panel)
        except Exception as exc:
            err_msg = str(exc) or exc.__class__.__name__
            self._drop_temp(audio_path)
            self._audio_path_for_export = None
            self._busy = False
            self._update_status(f"❌ Error: {err_msg}", 0)
            self._show_error("Error", err_msg)
            self._set_buttons(process=True)

    def _populate_review_panel(self):
        """Main thread only - rebuilds the checkbox list."""
        for w in self.review_scroll.winfo_children():
            w.destroy()
        self.word_vars = []

        if not self.profane_words:
            ctk.CTkLabel(self.review_scroll,
                         text="✅ No profanity detected — your video is clean!",
                         font=("Arial", 13), text_color="#6daa45").pack(pady=10)
            self._update_status("No profanity found.", 1.0)
            self._drop_temp(self._audio_path_for_export)
            self._audio_path_for_export = None
            self._busy = False
            self.process_btn.configure(state="normal")
            return

        ctk.CTkLabel(self.review_scroll,
                     text=f"Found {len(self.profane_words)} word(s) — "
                          "uncheck any you want to KEEP:",
                     font=("Arial", 12, "bold")).pack(anchor="w", padx=4, pady=(4, 8))
        for word_data in self.profane_words:
            var = ctk.BooleanVar(value=True)
            self.word_vars.append(var)
            label = (f"  [{self._fmt_ts(word_data['start'])}]  "
                     f"'{word_data['word'].strip()}'  — {word_data['reason']}")
            ctk.CTkCheckBox(self.review_scroll, text=label, variable=var,
                            font=("Consolas", 11)).pack(anchor="w", padx=8, pady=2)

        self.confirm_btn.configure(state="normal")
        self._update_status(
            f"Found {len(self.profane_words)} word(s). Review & confirm below.", 0.65)

    # ── Single video: export ─────────────────────────────────────────────────

    def _confirm_and_export(self):
        # Never zip two lists that drifted out of sync - that would bleep
        # the wrong timestamps rather than fail loudly.
        if len(self.profane_words) != len(self.word_vars):
            messagebox.showerror(
                "Out of sync",
                "The detected-word list and the review checkboxes no longer "
                "match. Re-run the analysis before exporting.")
            return
        if not self._audio_path_for_export or not self._video_path_for_export:
            messagebox.showerror("Nothing to export",
                                 "Run an analysis first.")
            return

        selected = [wd for wd, var in zip(self.profane_words, self.word_vars) if var.get()]
        if not selected:
            self._drop_temp(self._audio_path_for_export)
            self._audio_path_for_export = None
            self._busy = False
            self.process_btn.configure(state="normal")
            self.confirm_btn.configure(state="disabled")
            messagebox.showinfo("Nothing to bleep",
                                "All words were unchecked. No changes made.")
            return

        settings = self._snapshot_settings()          # main thread
        self.confirm_btn.configure(state="disabled")
        threading.Thread(
            target=self._export_video,
            args=(self._video_path_for_export, self._audio_path_for_export,
                  selected, settings),
            daemon=True).start()

    def _export_video(self, video_path: str, audio_path: str,
                      words_to_bleep: list[dict], settings: Settings):
        cleaned_audio_path = self._track_temp(_new_temp_wav())
        try:
            n = len(words_to_bleep)
            self._update_status(f"Bleeping {n} word(s)…", 0.70)

            audio_seg = AudioSegment.from_wav(audio_path)
            cleaned = apply_bleeps(
                audio_seg, words_to_bleep,
                method=settings.bleep_method, freq_hz=settings.beep_freq,
                progress=lambda done, total: self._update_status(
                    f"Bleeping {done}/{total}…", 0.70 + 0.20 * done / max(total, 1)),
            )
            cleaned.export(cleaned_audio_path, format="wav")

            self._update_status(
                f"Creating final video [preset={settings.encode_preset}]…", 0.92)
            out_path = build_output_path(video_path, settings.output_dir)
            self._render(video_path, cleaned_audio_path, out_path, settings.encode_preset)

            self._update_status("✅ Done! Video saved.", 1.0)
            self._on_main(messagebox.showinfo, "Success! ✅",
                          f"Bleeped {n} word(s)\n\nSaved to:\n{out_path}")
            self._set_buttons(process=True)
        except Exception as exc:
            err_msg = str(exc) or exc.__class__.__name__
            self._update_status(f"❌ Export error: {err_msg}", 0)
            self._show_error("Export Error", err_msg)
            self._set_buttons(confirm=True)
        finally:
            self._drop_temp(audio_path, cleaned_audio_path)
            self._audio_path_for_export = None
            self._busy = False

    # ── Batch ────────────────────────────────────────────────────────────────

    def _start_batch(self):
        if self._busy or not self._batch_input_dir:
            return
        self._busy = True
        settings = self._snapshot_settings()          # main thread
        in_dir, out_dir = self._batch_input_dir, self._batch_output_dir
        self.batch_btn.configure(state="disabled")
        self.batch_log.delete("1.0", "end")
        threading.Thread(target=self._run_batch,
                         args=(in_dir, out_dir, settings), daemon=True).start()

    def _finish_batch(self, message: str, progress: float | None = None):
        self._update_status(message, progress)
        self._set_buttons(batch=True)
        self._busy = False

    def _run_batch(self, in_dir: str, out_dir: str | None, settings: Settings):
        try:
            names = sorted(os.listdir(in_dir))
        except OSError as exc:
            self._batch_log_write(f"❌ Cannot read folder: {exc}")
            self._finish_batch("❌ Batch failed.", 0)
            return

        files = [f for f in names
                 if os.path.splitext(f)[1].lower() in engine.VIDEO_EXTS
                 and not is_generated_output(f)]
        if not files:
            self._batch_log_write("No video files found (already-cleaned "
                                  "'_CLEAN' files are skipped).")
            self._finish_batch("Nothing to do.", 0)
            return

        self._batch_log_write(f"Found {len(files)} video(s).\n{'─' * 50}")
        try:
            bundle = self._model_cache.get(settings.model_name, settings.compute_pref)
        except Exception as exc:
            self._batch_log_write(f"❌ Failed to load AI model: {exc}")
            self._finish_batch("❌ Batch failed.", 0)
            return
        self._batch_log_write(f"Loaded: {bundle.label}\n{'─' * 50}")

        ok = failed = 0
        for idx, fname in enumerate(files, 1):
            video_path = os.path.join(in_dir, fname)
            self._batch_log_write(f"\n[{idx}/{len(files)}] {fname}")
            self._update_status(f"Batch: {fname} ({idx}/{len(files)})…",
                                (idx - 1) / len(files))

            audio_path = self._track_temp(_new_temp_wav())
            cleaned_audio_path = self._track_temp(_new_temp_wav())
            try:
                self._extract_audio(video_path, audio_path)
                result = transcribe_words(bundle, audio_path)
                found = find_profanity_v2(result, settings.custom_words,
                                          fuzzy=settings.fuzzy)
                if not found:
                    self._batch_log_write("  → Clean.")
                    ok += 1
                    continue

                self._batch_log_write(f"  → {len(found)} word(s). Bleeping...")
                cleaned = apply_bleeps(
                    AudioSegment.from_wav(audio_path), found,
                    method=settings.bleep_method, freq_hz=settings.beep_freq)
                cleaned.export(cleaned_audio_path, format="wav")

                out_path = build_output_path(video_path, out_dir)
                self._render(video_path, cleaned_audio_path, out_path,
                             settings.encode_preset)
                self._batch_log_write(f"  → Saved: {os.path.basename(out_path)}")
                ok += 1
            except Exception as exc:
                failed += 1
                self._batch_log_write(f"  ❌ Error: {exc}")
            finally:
                self._drop_temp(audio_path, cleaned_audio_path)

        self._batch_log_write(f"\n{'─' * 50}\n✅ Done — {ok} succeeded, {failed} failed.")
        self._finish_batch("✅ Batch complete!", 1.0)

    # ── Helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_ts(seconds: float) -> str:
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

    def _on_close(self):
        """Don't leave temp WAVs behind if the user quits mid-review."""
        self._closing = True
        # Cancel the poll before destroying the window, or Tk complains
        # ('invalid command name "..._drain_ui_queue"') when the pending
        # callback fires against a dead interpreter.
        if self._poll_id is not None:
            try:
                self.window.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        self._drop_temp(*list(self._temp_files), self._audio_path_for_export)
        self._model_cache.release()
        self.window.destroy()

    def run(self):
        self.window.mainloop()


def main() -> None:
    print("=" * 60)
    print(f"  AutoBleep Pro v{APP_VERSION} — Smart Detection ⚡")
    print(f"  Speed mode: "
          f"{'faster-whisper + stable-ts' if SPEED_MODE else 'openai-whisper (fallback)'}")
    print("=" * 60)
    AutoBleepPro().run()


if __name__ == "__main__":
    main()
