"""
AutoBleep Pro v2.3.0 - Automatic Video Profanity Bleeper (GUI)
==============================================================
All detection/audio/model/pipeline logic lives in `bleep_engine.py`. This
module is the customtkinter GUI and its worker threads - nothing else.
The CLI (`cli.py`) drives the same engine functions.

Threading contract
------------------
Tk is not thread-safe. Two rules, enforced throughout:

1. Worker threads never read a Tk variable. Every setting is snapshotted
   into an immutable `engine.ProcessOptions` on the main thread before the
   thread starts.
2. Worker threads never touch a widget directly - they go through
   `self._on_main(...)`, which queues onto the Tk event loop.

Run with:  python autobleep_pro.py
"""

from __future__ import annotations

import os
import queue
import threading

import customtkinter as ctk
from tkinter import filedialog, messagebox

from pydub import AudioSegment

import bleep_engine as engine
from bleep_engine import (
    DEFAULT_SENSITIVITY,
    METHOD_BEEP,
    METHOD_SILENCE,
    SPEED_MODE,
    ModelCache,
    ProcessOptions,
    apply_bleeps,
    build_output_path,
    configure_threads,
    extract_audio,
    find_profanity_v2,
    new_temp_wav,
    process_video,
    render_video,
    safe_remove,
    sensitivity_band,
    sidecar_path,
    transcribe_words,
    validate_beep_wav,
    words_to_srt,
    words_to_txt,
)

APP_VERSION = "2.3.0"

configure_threads()

# ── Optional drag-and-drop support ───────────────────────────────────────────
# tkinterdnd2 is deliberately NOT a hard requirement: without it the Browse
# button still works and the UI just says so.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_IMPORTED = True
except Exception:  # ImportError, or a broken tkdnd install
    DND_FILES = None
    TkinterDnD = None
    _DND_IMPORTED = False


def _make_root() -> tuple[ctk.CTk, bool]:
    """Return (root window, drag-and-drop-enabled).

    tkinterdnd2 needs its own Tk subclass, so it's mixed into CTk. The Tcl
    `tkdnd` package can be missing even when the Python module imports, so
    this falls back to a plain CTk on any failure.
    """
    if _DND_IMPORTED:
        try:
            class _DndRoot(ctk.CTk, TkinterDnD.DnDWrapper):  # type: ignore[misc]
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.TkdndVersion = TkinterDnD._require(self)

            return _DndRoot(), True
        except Exception as exc:  # pragma: no cover - environment dependent
            print(f"[AutoBleep] drag-and-drop unavailable ({exc}); using Browse only.")
    return ctk.CTk(), False


# ── Picker options ───────────────────────────────────────────────────────────

MODEL_MAP = {
    "tiny   — max speed (less accurate)": "tiny",
    "base   — recommended (balanced)": "base",
    "small  — more accurate (slower)": "small",
    "medium — accurate (slower)": "medium",
    "turbo  — fast large model (GPU recommended)": "turbo",
    "large-v3 — cleanest result (GPU strongly recommended)": "large-v3",
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

METHOD_LABELS = {METHOD_SILENCE: "Silence", METHOD_BEEP: "Beep"}

BAND_BLURB = {
    engine.BAND_LOW: "Low — only real profanity, leet & masked words, custom words",
    engine.BAND_NORMAL: "Normal — adds minced oaths, mishears & matching context",
    engine.BAND_HIGH: "High — also fires on weaker surrounding context",
}

VIDEO_FILETYPES = [
    ("Video Files", "*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.m4v *.webm *.ts"),
    ("All Files", "*.*"),
]


class AutoBleepPro:
    # How often the main thread checks for work queued by worker threads.
    _UI_POLL_MS = 40

    def __init__(self):
        self.window, self.dnd_enabled = _make_root()
        self.window.title(f"AutoBleep Pro v{APP_VERSION} ⚡")
        self.window.geometry("1080x1000")
        self.window.minsize(800, 700)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.video_paths: list[str] = []
        self.output_dir: str | None = None
        self.profane_words: list[dict] = []
        self.word_vars: list[ctk.BooleanVar] = []
        self.device_info: str = "unknown"

        self._audio_path_for_export: str | None = None
        self._video_path_for_export: str | None = None
        self._transcript: dict | None = None      # for the .srt / .txt buttons
        self._batch_input_dir: str | None = None
        self._batch_output_dir: str | None = None
        self._custom_beep_path: str | None = None
        self._warned_beep_paths: set[str] = set()
        self._busy = False
        self._temp_files: set[str] = set()

        self._model_cache = ModelCache()

        self._ui_queue: queue.Queue = queue.Queue()
        self._closing = False
        self._poll_id: str | None = None

        self._setup_ui()
        self._enable_drag_and_drop()
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self._drain_ui_queue()   # starts the main-thread poll loop

    # ── Thread marshalling ───────────────────────────────────────────────────

    def _on_main(self, fn, *args, **kwargs) -> None:
        """Queue `fn` to run on the Tk event loop. Safe from any thread.

        Deliberately NOT `window.after(0, ...)`: `after()` is itself a Tcl
        call that registers a command on the interpreter, so calling it
        from a worker raises outright ("RuntimeError: main thread is not in
        main loop") whenever the main thread isn't inside mainloop().
        `queue.Queue.put` touches no Tcl at all.
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

    def _set_buttons(self, *, process=None, confirm=None, batch=None,
                     transcript=None) -> None:
        def _apply():
            if process is not None:
                self.process_btn.configure(state="normal" if process else "disabled")
            if confirm is not None:
                self.confirm_btn.configure(state="normal" if confirm else "disabled")
            if batch is not None:
                self.batch_btn.configure(state="normal" if batch else "disabled")
            if transcript is not None:
                state = "normal" if transcript else "disabled"
                self.save_txt_btn.configure(state=state)
                self.save_srt_btn.configure(state=state)
        self._on_main(_apply)

    # ── UI construction ──────────────────────────────────────────────────────

    def _setup_ui(self):
        hdr = ctk.CTkFrame(self.window, fg_color="transparent")
        hdr.pack(pady=(18, 4), padx=20, fill="x")
        ctk.CTkLabel(hdr, text="🔇 AutoBleep Pro",
                     font=("Arial", 36, "bold")).pack()
        speed_label = (
            f"⚡ v{APP_VERSION}  •  faster-whisper + stable-ts"
            if SPEED_MODE else
            f"v{APP_VERSION}  •  openai-whisper "
            "(install stable-ts[fw] for 4x speed)")
        ctk.CTkLabel(hdr, text=speed_label, font=("Arial", 13),
                     text_color="#4f98a3" if SPEED_MODE else "gray").pack(pady=2)

        self.tabs = ctk.CTkTabview(self.window, height=600)
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
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        scroll.pack(fill="both", expand=True)

        s1 = ctk.CTkFrame(scroll)
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
        self.dnd_hint = ctk.CTkLabel(
            s1,
            text=("🖱️  …or drag a video file onto this window"
                  if self.dnd_enabled else
                  "💡 pip install tkinterdnd2 for drag-and-drop"),
            font=("Arial", 11), text_color="gray", anchor="w")
        self.dnd_hint.pack(anchor="w", padx=20, pady=(0, 8))

        s2 = ctk.CTkFrame(scroll)
        s2.pack(pady=8, padx=10, fill="x")
        ctk.CTkLabel(s2, text="⚙️ Step 2: Settings",
                     font=("Arial", 15, "bold")).pack(anchor="w", padx=14, pady=8)
        inner = ctk.CTkFrame(s2, fg_color="transparent")
        inner.pack(fill="x", padx=28, pady=4)

        ctk.CTkLabel(inner, text="Censoring Method:",
                     font=("Arial", 12)).pack(anchor="w", pady=(4, 2))
        self.bleep_method = ctk.StringVar(value=engine.DEFAULT_METHOD)
        self.bleep_method.trace_add("write", self._on_method_changed)
        mf = ctk.CTkFrame(inner, fg_color="transparent")
        mf.pack(anchor="w")
        ctk.CTkRadioButton(mf, text="🔇 Silence (Mute)", variable=self.bleep_method,
                           value=METHOD_SILENCE).pack(side="left", padx=8)
        ctk.CTkRadioButton(mf, text="🔊 Beep Sound", variable=self.bleep_method,
                           value=METHOD_BEEP).pack(side="left", padx=8)

        ctk.CTkLabel(inner, text="Beep Sound Preset (used when method = Beep):",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.beep_preset = ctk.StringVar(value=list(BEEP_PRESETS.keys())[0])
        ctk.CTkOptionMenu(inner, values=list(BEEP_PRESETS.keys()),
                          variable=self.beep_preset, width=280).pack(anchor="w")

        ctk.CTkLabel(inner, text="Custom beep file (.wav, optional):",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        beep_row = ctk.CTkFrame(inner, fg_color="transparent")
        beep_row.pack(anchor="w", fill="x")
        self.beep_file_label = ctk.CTkLabel(beep_row, text="Using preset tone",
                                            font=("Arial", 11), text_color="gray",
                                            anchor="w")
        self.beep_file_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(beep_row, text="Clear", command=self._clear_custom_beep,
                      width=70, height=30, fg_color="gray30",
                      hover_color="gray20").pack(side="right", padx=4)
        ctk.CTkButton(beep_row, text="Browse .wav", command=self._pick_custom_beep,
                      width=120, height=30).pack(side="right", padx=4)

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

        # ── Sensitivity ──────────────────────────────────────────────────
        ctk.CTkLabel(inner, text="🎚️ Detection sensitivity:",
                     font=("Arial", 12)).pack(anchor="w", pady=(14, 2))
        self.sensitivity_var = ctk.IntVar(value=DEFAULT_SENSITIVITY)
        slider_row = ctk.CTkFrame(inner, fg_color="transparent")
        slider_row.pack(anchor="w", fill="x")
        self.sensitivity_slider = ctk.CTkSlider(
            slider_row, from_=0, to=100, number_of_steps=100,
            variable=self.sensitivity_var, width=320,
            command=lambda _v: self._refresh_sensitivity_label())
        self.sensitivity_slider.pack(side="left", padx=(0, 10))
        self.sensitivity_value = ctk.CTkLabel(slider_row, text=str(DEFAULT_SENSITIVITY),
                                              font=("Consolas", 13, "bold"), width=36)
        self.sensitivity_value.pack(side="left")
        self.sensitivity_hint = ctk.CTkLabel(
            inner, text="", font=("Arial", 11), text_color="gray", anchor="w")
        self.sensitivity_hint.pack(anchor="w", pady=(2, 0))
        self._refresh_sensitivity_label()

        ctk.CTkLabel(inner, text="Extra words to bleep (comma-separated, optional):",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        self.custom_words_var = ctk.StringVar()
        ctk.CTkEntry(inner, textvariable=self.custom_words_var, width=420,
                     placeholder_text="e.g., rival brand, competitor name").pack(anchor="w")

        self.export_srt_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(inner, text="Also export SRT on bleep (writes *_CLEAN.srt)",
                        variable=self.export_srt_var,
                        font=("Arial", 11)).pack(anchor="w", pady=(12, 2))

        ctk.CTkLabel(inner, text="Output Folder (optional):",
                     font=("Arial", 12)).pack(anchor="w", pady=(12, 2))
        out_row = ctk.CTkFrame(inner, fg_color="transparent")
        out_row.pack(anchor="w", fill="x", pady=(0, 6))
        self.out_label = ctk.CTkLabel(out_row, text="Same folder as input",
                                      font=("Arial", 11), text_color="gray", anchor="w")
        self.out_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(out_row, text="Choose Folder", command=self._pick_output_dir,
                      width=140, height=30).pack(side="right", padx=4)

        s3 = ctk.CTkFrame(scroll)
        s3.pack(pady=8, padx=10, fill="x")
        ctk.CTkLabel(s3, text="🚀 Step 3: Process",
                     font=("Arial", 15, "bold")).pack(anchor="w", padx=14, pady=8)
        self.process_btn = ctk.CTkButton(
            s3, text="🎬 Analyze & Bleep Video", command=self._start_single,
            height=48, font=("Arial", 15, "bold"),
            fg_color="#2B7A0B", hover_color="#1F5A08", state="disabled")
        self.process_btn.pack(pady=10, padx=24, fill="x")

        wr = ctk.CTkFrame(scroll)
        wr.pack(pady=8, padx=10, fill="both", expand=True)
        ctk.CTkLabel(wr, text="🔍 Word Review — uncheck words you DON'T want bleeped:",
                     font=("Arial", 13, "bold")).pack(anchor="w", padx=14, pady=(8, 4))
        self.review_scroll = ctk.CTkScrollableFrame(wr, height=150)
        self.review_scroll.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        ctk.CTkLabel(self.review_scroll,
                     text="Detected words will appear here after analysis.",
                     font=("Arial", 11), text_color="gray").pack(padx=8, pady=8)

        save_row = ctk.CTkFrame(wr, fg_color="transparent")
        save_row.pack(fill="x", padx=24, pady=(0, 6))
        self.save_txt_btn = ctk.CTkButton(
            save_row, text="💾 Save transcript (.txt)", command=self._save_transcript_txt,
            height=34, font=("Arial", 12), fg_color="gray30", hover_color="gray20",
            state="disabled")
        self.save_txt_btn.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.save_srt_btn = ctk.CTkButton(
            save_row, text="💾 Save captions (.srt)", command=self._save_transcript_srt,
            height=34, font=("Arial", 12), fg_color="gray30", hover_color="gray20",
            state="disabled")
        self.save_srt_btn.pack(side="left", expand=True, fill="x", padx=(4, 0))

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
        self.batch_method_note = ctk.CTkLabel(
            bs, text="", font=("Arial", 11), text_color="gray", anchor="w")
        self.batch_method_note.pack(anchor="w", padx=28, pady=(0, 2))
        ctk.CTkLabel(bs, text="Model / Compute / Encode / Beep / Sensitivity / Custom Words "
                             "also come from the Single Video tab.",
                     font=("Arial", 11), text_color="gray").pack(anchor="w", padx=28,
                                                                 pady=(0, 6))
        self.batch_srt_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(bs, text="Export SRT in batch (full transcript beside each video)",
                        variable=self.batch_srt_var,
                        font=("Arial", 11)).pack(anchor="w", padx=28, pady=(0, 10))
        self._refresh_batch_method_note()

        self.batch_btn = ctk.CTkButton(
            parent, text="⚡ Process All Videos in Folder", command=self._start_batch,
            height=48, font=("Arial", 15, "bold"),
            fg_color="#7a2bb0", hover_color="#591e80", state="disabled")
        self.batch_btn.pack(pady=14, padx=24, fill="x")

        self.batch_log = ctk.CTkTextbox(parent, font=("Consolas", 11), height=240)
        self.batch_log.pack(pady=4, padx=14, fill="both", expand=True)
        self.batch_log.insert("1.0", "Batch log will appear here...\n")

    # ── Small UI callbacks (main thread) ─────────────────────────────────────

    def _on_method_changed(self, *_args) -> None:
        self._refresh_batch_method_note()

    def _refresh_batch_method_note(self) -> None:
        label = METHOD_LABELS.get(self.bleep_method.get(), self.bleep_method.get())
        self.batch_method_note.configure(
            text=f"Uses same Method as Single Video tab (currently: {label}).")

    def _refresh_sensitivity_label(self) -> None:
        value = engine.clamp_sensitivity(self.sensitivity_var.get())
        self.sensitivity_value.configure(text=str(value))
        self.sensitivity_hint.configure(text=BAND_BLURB[sensitivity_band(value)])

    def _enable_drag_and_drop(self) -> None:
        if not self.dnd_enabled:
            return
        try:
            self.window.drop_target_register(DND_FILES)
            self.window.dnd_bind("<<Drop>>", self._on_drop)
        except Exception as exc:  # pragma: no cover - environment dependent
            self.dnd_enabled = False
            print(f"[AutoBleep] could not register drop target ({exc}).")

    def _on_drop(self, event):
        """Accept a dropped file (single video) or folder (batch input)."""
        try:
            paths = [p for p in self.window.tk.splitlist(event.data) if p]
        except Exception:
            paths = [event.data] if event.data else []
        if not paths:
            return

        first = paths[0]
        if os.path.isdir(first):
            self._set_batch_folder(first)
            self.tabs.set("Batch Folder")
            self._update_status(f"Batch folder set from drop: {os.path.basename(first)}")
            return

        videos = [p for p in paths
                  if os.path.splitext(p)[1].lower() in engine.VIDEO_EXTS]
        if not videos:
            self._update_status("Dropped file isn't a supported video format.")
            return
        self._set_single_video(videos[0])
        if len(videos) > 1:
            self._update_status(
                f"Loaded {os.path.basename(videos[0])} "
                f"({len(videos) - 1} other file(s) ignored — use the Batch tab).")

    # ── File pickers (main thread only) ──────────────────────────────────────

    def _set_single_video(self, path: str) -> None:
        self.video_paths = [path]
        self.file_label.configure(text=f"✅ {os.path.basename(path)}")
        self.process_btn.configure(state="normal")
        self._update_status("Video loaded — click Analyze & Bleep to start.")

    def _pick_single_video(self):
        path = filedialog.askopenfilename(title="Select Video File",
                                          filetypes=VIDEO_FILETYPES)
        if path:
            self._set_single_video(path)

    def _pick_output_dir(self):
        d = filedialog.askdirectory(title="Choose Output Folder")
        if d:
            self.output_dir = d
            self.out_label.configure(text=d, text_color="white")

    def _pick_custom_beep(self):
        path = filedialog.askopenfilename(
            title="Select Beep Sound",
            filetypes=[("WAV audio", "*.wav"), ("All Files", "*.*")])
        if not path:
            return
        usable, reason = validate_beep_wav(path)
        if not usable:
            messagebox.showerror("Unusable beep file", reason)
            return
        self._custom_beep_path = path
        self.beep_file_label.configure(text=os.path.basename(path), text_color="white")
        self.bleep_method.set(METHOD_BEEP)   # a custom sample implies beep mode
        self._update_status(reason)

    def _clear_custom_beep(self):
        self._custom_beep_path = None
        self.beep_file_label.configure(text="Using preset tone", text_color="gray")
        self._update_status("Custom beep cleared — using the preset tone.")

    def _set_batch_folder(self, path: str) -> None:
        self._batch_input_dir = path
        self.batch_dir_label.configure(text=path, text_color="white")
        self.batch_btn.configure(state="normal")

    def _pick_batch_folder(self):
        d = filedialog.askdirectory(title="Select Folder of Videos")
        if d:
            self._set_batch_folder(d)

    def _pick_batch_output(self):
        d = filedialog.askdirectory(title="Select Batch Output Folder")
        if d:
            self._batch_output_dir = d
            self.batch_out_label.configure(text=d, text_color="white")

    # ── Settings snapshot ────────────────────────────────────────────────────

    def _snapshot_options(self, *, output_dir_override: str | None = None,
                          write_srt: bool = False) -> ProcessOptions:
        """Read every Tk variable ONCE, on the main thread."""
        raw = self.custom_words_var.get()

        beep_path = self._custom_beep_path
        if beep_path:
            usable, reason = validate_beep_wav(beep_path)
            if not usable:
                # One warning per bad path, then fall back to the tone.
                if beep_path not in self._warned_beep_paths:
                    self._warned_beep_paths.add(beep_path)
                    self._update_status(f"⚠️ {reason} Falling back to the preset tone.")
                beep_path = None

        return ProcessOptions(
            model_name=MODEL_MAP[self.model_var.get()],
            compute_pref=COMPUTE_MAP[self.compute_var.get()],
            encode_preset=ENCODE_PRESETS[self.encode_var.get()],
            method=self.bleep_method.get(),
            beep_freq=BEEP_PRESETS[self.beep_preset.get()],
            custom_beep_wav=beep_path,
            sensitivity=engine.clamp_sensitivity(self.sensitivity_var.get()),
            custom_words=tuple(w.strip().lower() for w in raw.split(",") if w.strip()),
            output_dir=output_dir_override if output_dir_override is not None
            else self.output_dir,
            write_srt=write_srt,
        )

    # ── Temp-file bookkeeping ────────────────────────────────────────────────

    def _track_temp(self, path: str) -> str:
        self._temp_files.add(path)
        return path

    def _drop_temp(self, *paths: str | None) -> None:
        safe_remove(*paths)
        for p in paths:
            self._temp_files.discard(p)

    # ── Single video: analyze ────────────────────────────────────────────────

    def _start_single(self):
        if self._busy or not self.video_paths:
            return
        self._busy = True
        options = self._snapshot_options()          # main thread
        self._set_buttons(process=False, confirm=False, transcript=False)
        threading.Thread(target=self._analyze_video,
                         args=(self.video_paths[0], options), daemon=True).start()

    def _analyze_video(self, video_path: str, options: ProcessOptions):
        audio_path = self._track_temp(new_temp_wav())
        try:
            self._update_status("[1/3] Loading AI model…", 0.08)
            bundle = self._model_cache.get(options.model_name, options.compute_pref)
            self.device_info = bundle.label

            self._update_status(f"[2/3] Extracting audio ({bundle.label})…", 0.22)
            extract_audio(video_path, audio_path)

            self._update_status("[3/3] AI transcription + smart word detection…", 0.42)
            transcript = transcribe_words(bundle, audio_path)
            found = find_profanity_v2(transcript, options.custom_words,
                                      sensitivity=options.sensitivity)

            self._transcript = transcript
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

        # The transcript exists regardless of whether anything was flagged.
        self.save_txt_btn.configure(state="normal")
        self.save_srt_btn.configure(state="normal")

        if not self.profane_words:
            ctk.CTkLabel(self.review_scroll,
                         text="✅ No profanity detected — your video is clean!",
                         font=("Arial", 13), text_color="#6daa45").pack(pady=10)
            self._update_status("No profanity found. Transcript is still available.", 1.0)
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

    # ── Transcript export buttons ────────────────────────────────────────────

    def _default_sidecar_dir(self) -> str:
        if self.output_dir:
            return self.output_dir
        if self._video_path_for_export:
            return os.path.dirname(self._video_path_for_export)
        return os.getcwd()

    def _save_transcript(self, extension: str, writer, what: str) -> None:
        if not self._transcript:
            messagebox.showinfo("Nothing to save", "Run an analysis first.")
            return
        stem = os.path.splitext(os.path.basename(
            self._video_path_for_export or "transcript"))[0]
        path = filedialog.asksaveasfilename(
            title=f"Save {what}",
            defaultextension=extension,
            initialfile=stem + extension,
            initialdir=self._default_sidecar_dir(),
            filetypes=[(what, f"*{extension}"), ("All Files", "*.*")])
        if not path:
            return
        try:
            writer(self._transcript, path)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return
        self._update_status(f"✅ {what} saved to {os.path.basename(path)}")

    def _save_transcript_txt(self):
        self._save_transcript(".txt", words_to_txt, "Transcript")

    def _save_transcript_srt(self):
        self._save_transcript(".srt", words_to_srt, "Captions")

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
            messagebox.showerror("Nothing to export", "Run an analysis first.")
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

        options = self._snapshot_options(write_srt=bool(self.export_srt_var.get()))
        self.confirm_btn.configure(state="disabled")
        threading.Thread(
            target=self._export_video,
            args=(self._video_path_for_export, self._audio_path_for_export,
                  selected, options, self._transcript),
            daemon=True).start()

    def _export_video(self, video_path: str, audio_path: str,
                      words_to_bleep: list[dict], options: ProcessOptions,
                      transcript: dict | None):
        cleaned_audio_path = self._track_temp(new_temp_wav())
        try:
            n = len(words_to_bleep)
            self._update_status(f"Censoring {n} word(s)…", 0.70)

            audio_seg = AudioSegment.from_wav(audio_path)
            cleaned = apply_bleeps(
                audio_seg, words_to_bleep,
                method=options.method, freq_hz=options.beep_freq,
                custom_wav=options.custom_beep_wav,
                # The whole transcript, not just the flagged words: the
                # padding is clamped to the words either side so only the
                # profanity goes, not the sentence around it.
                all_words=engine._flatten_words(transcript),
                progress=lambda done, total: self._update_status(
                    f"Censoring {done}/{total}…", 0.70 + 0.20 * done / max(total, 1)),
            )
            cleaned.export(cleaned_audio_path, format="wav")

            self._update_status(
                f"Creating final video [preset={options.encode_preset}]…", 0.92)
            out_path = build_output_path(video_path, options.output_dir)
            render_video(video_path, cleaned_audio_path, out_path, options.encode_preset)

            extra = ""
            if options.write_srt and transcript:
                srt_path = words_to_srt(transcript, sidecar_path(out_path, ".srt"))
                extra = f"\nCaptions: {os.path.basename(str(srt_path))}"

            self._update_status("✅ Done! Video saved.", 1.0)
            self._on_main(messagebox.showinfo, "Success! ✅",
                          f"Censored {n} word(s)\n\nSaved to:\n{out_path}{extra}")
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
        options = self._snapshot_options(          # main thread
            output_dir_override=self._batch_output_dir,
            write_srt=bool(self.batch_srt_var.get()))
        in_dir = self._batch_input_dir
        self.batch_btn.configure(state="disabled")
        self.batch_log.delete("1.0", "end")
        threading.Thread(target=self._run_batch,
                         args=(in_dir, options), daemon=True).start()

    def _finish_batch(self, message: str, progress: float | None = None):
        self._update_status(message, progress)
        self._set_buttons(batch=True)
        self._busy = False

    def _run_batch(self, in_dir: str, options: ProcessOptions):
        try:
            files = engine.list_videos(in_dir)
        except OSError as exc:
            self._batch_log_write(f"❌ Cannot read folder: {exc}")
            self._finish_batch("❌ Batch failed.", 0)
            return

        if not files:
            self._batch_log_write("No video files found (already-cleaned "
                                  "'_CLEAN' files are skipped).")
            self._finish_batch("Nothing to do.", 0)
            return

        self._batch_log_write(f"Found {len(files)} video(s).\n{'─' * 50}")
        try:
            bundle = self._model_cache.get(options.model_name, options.compute_pref)
        except Exception as exc:
            self._batch_log_write(f"❌ Failed to load AI model: {exc}")
            self._finish_batch("❌ Batch failed.", 0)
            return
        self._batch_log_write(
            f"Loaded: {bundle.label}\n"
            f"Method: {METHOD_LABELS.get(options.method, options.method)}  |  "
            f"Sensitivity: {options.sensitivity} "
            f"({sensitivity_band(options.sensitivity)})\n{'─' * 50}")

        ok = failed = 0
        for idx, video_path in enumerate(files, 1):
            name = os.path.basename(video_path)
            self._batch_log_write(f"\n[{idx}/{len(files)}] {name}")
            self._update_status(f"Batch: {name} ({idx}/{len(files)})…",
                                (idx - 1) / len(files))

            result = process_video(
                video_path, options, bundle,
                log=lambda msg: self._batch_log_write(f"  → {msg}"))

            if result.ok:
                ok += 1
            else:
                failed += 1
                self._batch_log_write(f"  ❌ Error: {result.error}")

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
    print(f"  AutoBleep Pro v{APP_VERSION} ⚡")
    print(f"  Speed mode: "
          f"{'faster-whisper + stable-ts' if SPEED_MODE else 'openai-whisper (fallback)'}")
    print(f"  Default method: {METHOD_LABELS[engine.DEFAULT_METHOD]}")
    print("=" * 60)
    AutoBleepPro().run()


if __name__ == "__main__":
    main()
