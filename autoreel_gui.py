"""
AutoReel GUI - AI Video Post-Production Supervisor
Transcribes, censors, and cuts vertical highlight clips from long-form footage.
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
import os
import sys
import threading
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from autoreel.pipeline import AutoReelPipeline

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class AutoReelGUI:
    def __init__(self):
        self.window = ctk.CTk()
        self.window.title("AutoReel - AI Video Post-Production Supervisor")
        self.window.geometry("1000x900")

        self.video_path = None
        self.output_dir = os.path.join(os.getcwd(), "autoreel_output")
        self.last_output_dir = None

        self.setup_ui()

    def setup_ui(self):
        header_frame = ctk.CTkFrame(self.window, fg_color="transparent")
        header_frame.pack(pady=20, padx=20, fill="x")

        ctk.CTkLabel(
            header_frame, text="🎬 AutoReel", font=("Arial", 36, "bold")
        ).pack()
        ctk.CTkLabel(
            header_frame,
            text="Transcribe, Censor, and Cut Vertical Highlight Clips",
            font=("Arial", 14),
            text_color="gray",
        ).pack(pady=5)

        content_frame = ctk.CTkFrame(self.window)
        content_frame.pack(pady=10, padx=20, fill="both", expand=True)

        self._build_step1(content_frame)
        self._build_step2(content_frame)
        self._build_step3(content_frame)
        self._build_progress(content_frame)

    def _build_step1(self, parent):
        step1 = ctk.CTkFrame(parent)
        step1.pack(pady=10, padx=15, fill="x")

        ctk.CTkLabel(
            step1, text="📹 Step 1: Select Your Video", font=("Arial", 16, "bold")
        ).pack(anchor="w", padx=15, pady=10)

        file_row = ctk.CTkFrame(step1, fg_color="transparent")
        file_row.pack(fill="x", padx=15, pady=5)

        self.file_label = ctk.CTkLabel(
            file_row, text="No video selected", font=("Arial", 12), anchor="w"
        )
        self.file_label.pack(side="left", fill="x", expand=True, padx=10)

        ctk.CTkButton(
            file_row, text="Browse Video", command=self.select_video, width=150, height=35
        ).pack(side="right", padx=10, pady=10)

        out_row = ctk.CTkFrame(step1, fg_color="transparent")
        out_row.pack(fill="x", padx=15, pady=5)

        self.output_dir_label = ctk.CTkLabel(
            out_row, text=f"Output folder: {self.output_dir}", font=("Arial", 12), anchor="w"
        )
        self.output_dir_label.pack(side="left", fill="x", expand=True, padx=10)

        ctk.CTkButton(
            out_row, text="Change Folder", command=self.select_output_dir, width=150, height=35
        ).pack(side="right", padx=10, pady=10)

    def _build_step2(self, parent):
        step2 = ctk.CTkFrame(parent)
        step2.pack(pady=10, padx=15, fill="x")

        ctk.CTkLabel(
            step2, text="⚙️ Step 2: Settings", font=("Arial", 16, "bold")
        ).pack(anchor="w", padx=15, pady=10)

        inner = ctk.CTkFrame(step2, fg_color="transparent")
        inner.pack(fill="x", padx=30, pady=5)

        # Row: model + device
        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x", pady=5)

        ctk.CTkLabel(row1, text="AI Transcription Model:", font=("Arial", 12)).pack(
            side="left", padx=(0, 10)
        )
        self.model_var = ctk.StringVar(value="base (recommended)")
        ctk.CTkOptionMenu(
            row1,
            values=["tiny (fastest)", "base (recommended)", "small (accurate)", "medium (more accurate)", "large (best)"],
            variable=self.model_var,
            width=220,
        ).pack(side="left", padx=(0, 30))

        ctk.CTkLabel(row1, text="Device:", font=("Arial", 12)).pack(side="left", padx=(0, 10))
        self.device_var = ctk.StringVar(value="auto (recommended)")
        ctk.CTkOptionMenu(
            row1,
            values=["auto (recommended)", "cpu", "cuda"],
            variable=self.device_var,
            width=180,
        ).pack(side="left")

        # Row: bleep method
        ctk.CTkLabel(inner, text="Censoring Method:", font=("Arial", 12)).pack(
            anchor="w", pady=(15, 5)
        )
        self.bleep_method = ctk.StringVar(value="beep")
        methods_frame = ctk.CTkFrame(inner, fg_color="transparent")
        methods_frame.pack(anchor="w", pady=5)
        ctk.CTkRadioButton(
            methods_frame, text="🔊 Beep Sound", variable=self.bleep_method, value="beep"
        ).pack(side="left", padx=10)
        ctk.CTkRadioButton(
            methods_frame, text="🔇 Silence", variable=self.bleep_method, value="silence"
        ).pack(side="left", padx=10)

        # Row: toggles
        toggles_frame = ctk.CTkFrame(inner, fg_color="transparent")
        toggles_frame.pack(anchor="w", pady=(15, 5))

        self.censor_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            toggles_frame,
            text="Censor flagged words (uncheck to only report, leaving audio untouched)",
            variable=self.censor_var,
        ).pack(anchor="w", pady=5)

        self.face_tracking_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            toggles_frame,
            text="Smart face-tracking crop (follows a detected face instead of a fixed center-crop)",
            variable=self.face_tracking_var,
        ).pack(anchor="w", pady=5)

        # Row: num clips + clip length
        row3 = ctk.CTkFrame(inner, fg_color="transparent")
        row3.pack(fill="x", pady=(15, 5))

        ctk.CTkLabel(row3, text="Number of clips:", font=("Arial", 12)).pack(
            side="left", padx=(0, 10)
        )
        self.num_clips_var = ctk.StringVar(value="3")
        ctk.CTkEntry(row3, textvariable=self.num_clips_var, width=60).pack(
            side="left", padx=(0, 30)
        )

        ctk.CTkLabel(row3, text="Clip length (sec):", font=("Arial", 12)).pack(
            side="left", padx=(0, 10)
        )
        self.clip_min_var = ctk.StringVar(value="15")
        ctk.CTkEntry(row3, textvariable=self.clip_min_var, width=60).pack(side="left")
        ctk.CTkLabel(row3, text="to").pack(side="left", padx=5)
        self.clip_max_var = ctk.StringVar(value="60")
        ctk.CTkEntry(row3, textvariable=self.clip_max_var, width=60).pack(side="left")

        # Custom words
        ctk.CTkLabel(
            inner, text="Additional words to censor (optional, comma-separated):", font=("Arial", 12)
        ).pack(anchor="w", pady=(15, 5))
        self.custom_words_var = ctk.StringVar()
        ctk.CTkEntry(
            inner, textvariable=self.custom_words_var, width=400,
            placeholder_text="e.g., brand name, competitor, etc."
        ).pack(anchor="w", pady=5)

    def _build_step3(self, parent):
        step3 = ctk.CTkFrame(parent)
        step3.pack(pady=10, padx=15, fill="x")

        ctk.CTkLabel(
            step3, text="🚀 Step 3: Generate Reels", font=("Arial", 16, "bold")
        ).pack(anchor="w", padx=15, pady=10)

        self.process_btn = ctk.CTkButton(
            step3,
            text="🎬 Transcribe, Censor & Cut Clips",
            command=self.start_processing,
            height=50,
            font=("Arial", 16, "bold"),
            fg_color="#2B7A0B",
            hover_color="#1F5A08",
            state="disabled",
        )
        self.process_btn.pack(pady=15, padx=30, fill="x")

    def _build_progress(self, parent):
        progress_frame = ctk.CTkFrame(parent)
        progress_frame.pack(pady=10, padx=15, fill="both", expand=True)

        ctk.CTkLabel(
            progress_frame, text="📊 Progress & Results", font=("Arial", 16, "bold")
        ).pack(anchor="w", padx=15, pady=10)

        self.progress = ctk.CTkProgressBar(progress_frame, width=900, mode="indeterminate")
        self.progress.pack(pady=10, padx=30)

        self.status_label = ctk.CTkLabel(
            progress_frame, text="Ready to process video", font=("Arial", 13)
        )
        self.status_label.pack(pady=5)

        self.results_text = ctk.CTkTextbox(
            progress_frame, width=900, height=250, font=("Consolas", 11)
        )
        self.results_text.pack(pady=10, padx=30, fill="both", expand=True)
        self.results_text.insert("1.0", "Waiting for video...\n\nSelect a video to begin!")

        self.open_folder_btn = ctk.CTkButton(
            progress_frame,
            text="📂 Open Output Folder",
            command=self.open_output_folder,
            width=200,
            state="disabled",
        )
        self.open_folder_btn.pack(pady=10)

    def select_video(self):
        file_path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=[
                ("Video Files", "*.mp4 *.avi *.mov *.mkv *.flv *.wmv *.ts"),
                ("All Files", "*.*"),
            ],
        )
        if file_path:
            self.video_path = file_path
            filename = os.path.basename(file_path)
            self.file_label.configure(text=f"✅ {filename}")
            self.process_btn.configure(state="normal")
            self.results_text.delete("1.0", "end")
            self.results_text.insert(
                "1.0", f"Video loaded: {filename}\n\nReady to process!\n\nClick the green button to start."
            )

    def select_output_dir(self):
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.output_dir = folder
            self.output_dir_label.configure(text=f"Output folder: {self.output_dir}")

    def open_output_folder(self):
        if self.last_output_dir and os.path.isdir(self.last_output_dir):
            if sys.platform == "win32":
                os.startfile(self.last_output_dir)
            elif sys.platform == "darwin":
                subprocess.run(["open", self.last_output_dir])
            else:
                subprocess.run(["xdg-open", self.last_output_dir])

    def start_processing(self):
        self.process_btn.configure(state="disabled")
        self.open_folder_btn.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start()

        thread = threading.Thread(target=self.process_video)
        thread.daemon = True
        thread.start()

    def process_video(self):
        try:
            model_name = self.model_var.get().split()[0]
            device_choice = self.device_var.get().split()[0]
            device = None if device_choice == "auto" else device_choice

            custom_words = tuple(
                w.strip() for w in self.custom_words_var.get().split(",") if w.strip()
            )

            try:
                num_clips = int(self.num_clips_var.get())
            except ValueError:
                num_clips = 3
            try:
                clip_min = float(self.clip_min_var.get())
                clip_max = float(self.clip_max_var.get())
            except ValueError:
                clip_min, clip_max = 15.0, 60.0

            self.update_status("🔄 Starting AutoReel pipeline (this can take a while for long streams)...")

            pipeline = AutoReelPipeline(
                output_dir=self.output_dir,
                model_name=model_name,
                bleep_method=self.bleep_method.get(),
                custom_words=custom_words,
                num_clips=num_clips,
                clip_min_duration=clip_min,
                clip_max_duration=clip_max,
                device=device,
                censor_profanity=self.censor_var.get(),
                face_tracking=self.face_tracking_var.get(),
            )

            report = pipeline.run(self.video_path)

            report_path = os.path.join(self.output_dir, "supervisor_report.md")
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report.to_markdown())

            self.last_output_dir = self.output_dir
            self.display_report(report)

            self.update_status("✅ SUCCESS! Reels generated.")
            self.window.after(0, lambda: self.progress.stop())
            self.window.after(0, lambda: self.progress.set(1.0))
            self.window.after(0, lambda: self.open_folder_btn.configure(state="normal"))

            messagebox.showinfo(
                "Success!",
                f"Generated {len(report.clip_paths)} clip(s).\n\n"
                f"Flagged {len(report.violations)} word(s).\n\n"
                f"Saved to:\n{self.output_dir}",
            )
            self.process_btn.configure(state="normal")

        except Exception as e:
            self.window.after(0, lambda: self.progress.stop())
            self.update_status(f"❌ Error: {str(e)}")
            messagebox.showerror("Error", f"Processing failed:\n\n{str(e)}")
            self.process_btn.configure(state="normal")

    def display_report(self, report):
        markdown = report.to_markdown()
        self.window.after(0, lambda: self._set_results_text(markdown))

    def _set_results_text(self, text):
        self.results_text.delete("1.0", "end")
        self.results_text.insert("1.0", text)

    def update_status(self, message):
        self.window.after(0, lambda: self.status_label.configure(text=message))

    def run(self):
        self.window.mainloop()


if __name__ == "__main__":
    print("=" * 60)
    print("  AutoReel - AI Video Post-Production Supervisor")
    print("  Starting application...")
    print("=" * 60)

    app = AutoReelGUI()
    app.run()
