"""
AutoBleep Pro - Automatic Video Profanity Bleeper
Detects and bleeps profanity automatically using AI
Created step-by-step with Claude
"""

import customtkinter as ctk
from tkinter import filedialog, messagebox
import whisper
import torch
from better_profanity import profanity
from pydub import AudioSegment
from pydub.generators import Sine
from moviepy import VideoFileClip, AudioFileClip
import os
import re
import threading
import numpy as np
from datetime import timedelta

# Initialize profanity detector
profanity.load_censor_words()

# Use all CPU cores for local (non-GPU) work; torch defaults to this already,
# but setting it explicitly avoids surprises if something upstream lowers it.
torch.set_num_threads(os.cpu_count() or 1)


def detect_device():
    """Pick the fastest available device: NVIDIA GPU (CUDA) if present, else CPU."""
    if torch.cuda.is_available():
        return "cuda", torch.cuda.get_device_name(0)
    return "cpu", f"{os.cpu_count()} CPU cores"


class AutoBleepPro:
    def __init__(self):
        self.window = ctk.CTk()
        self.window.title("AutoBleep Pro - Automatic Profanity Bleeper")
        self.window.geometry("1000x800")
        
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        self.video_path = None
        self.output_path = None
        self.profane_words = []
        self.transcript = None
        
        self.setup_ui()
    
    def setup_ui(self):
        """Create the beautiful user interface"""
        # Header
        header_frame = ctk.CTkFrame(self.window, fg_color="transparent")
        header_frame.pack(pady=20, padx=20, fill="x")
        
        title = ctk.CTkLabel(
            header_frame,
            text="🔇 AutoBleep Pro",
            font=("Arial", 36, "bold")
        )
        title.pack()
        
        subtitle = ctk.CTkLabel(
            header_frame,
            text="Automatic AI-Powered Profanity Bleeping",
            font=("Arial", 14),
            text_color="gray"
        )
        subtitle.pack(pady=5)
        
        # Main content area
        content_frame = ctk.CTkFrame(self.window)
        content_frame.pack(pady=10, padx=20, fill="both", expand=True)
        
        # Step 1: Video Selection
        step1_frame = ctk.CTkFrame(content_frame)
        step1_frame.pack(pady=10, padx=15, fill="x")
        
        ctk.CTkLabel(
            step1_frame,
            text="📹 Step 1: Select Your Video",
            font=("Arial", 16, "bold")
        ).pack(anchor="w", padx=15, pady=10)
        
        file_select_frame = ctk.CTkFrame(step1_frame, fg_color="transparent")
        file_select_frame.pack(fill="x", padx=15, pady=5)
        
        self.file_label = ctk.CTkLabel(
            file_select_frame,
            text="No video selected",
            font=("Arial", 12),
            anchor="w"
        )
        self.file_label.pack(side="left", fill="x", expand=True, padx=10)
        
        self.select_btn = ctk.CTkButton(
            file_select_frame,
            text="Browse Video",
            command=self.select_video,
            width=150,
            height=35
        )
        self.select_btn.pack(side="right", padx=10, pady=10)
        
        # Step 2: Settings
        step2_frame = ctk.CTkFrame(content_frame)
        step2_frame.pack(pady=10, padx=15, fill="x")
        
        ctk.CTkLabel(
            step2_frame,
            text="⚙️ Step 2: Bleeping Settings",
            font=("Arial", 16, "bold")
        ).pack(anchor="w", padx=15, pady=10)
        
        settings_inner = ctk.CTkFrame(step2_frame, fg_color="transparent")
        settings_inner.pack(fill="x", padx=30, pady=5)
        
        # Bleep method selection
        ctk.CTkLabel(
            settings_inner,
            text="Censoring Method:",
            font=("Arial", 12)
        ).pack(anchor="w", pady=5)
        
        self.bleep_method = ctk.StringVar(value="beep")
        
        methods_frame = ctk.CTkFrame(settings_inner, fg_color="transparent")
        methods_frame.pack(anchor="w", pady=5)
        
        ctk.CTkRadioButton(
            methods_frame,
            text="🔊 Beep Sound (Professional)",
            variable=self.bleep_method,
            value="beep"
        ).pack(side="left", padx=10)
        
        ctk.CTkRadioButton(
            methods_frame,
            text="🔇 Silence (Mute)",
            variable=self.bleep_method,
            value="silence"
        ).pack(side="left", padx=10)
        
        # AI Model selection
        ctk.CTkLabel(
            settings_inner,
            text="AI Transcription Model:",
            font=("Arial", 12)
        ).pack(anchor="w", pady=(15, 5))
        
        self.model_var = ctk.StringVar(value="base")
        model_menu = ctk.CTkOptionMenu(
            settings_inner,
            values=["tiny (fastest)", "base (recommended)", "small (accurate)"],
            variable=self.model_var,
            width=200
        )
        model_menu.pack(anchor="w", pady=5)
        
        # Custom words
        ctk.CTkLabel(
            settings_inner,
            text="Additional words to bleep (optional, comma-separated):",
            font=("Arial", 12)
        ).pack(anchor="w", pady=(15, 5))
        
        self.custom_words_var = ctk.StringVar()
        self.custom_words_entry = ctk.CTkEntry(
            settings_inner,
            textvariable=self.custom_words_var,
            width=400,
            placeholder_text="e.g., brand name, competitor, etc."
        )
        self.custom_words_entry.pack(anchor="w", pady=5)
        
        # Step 3: Process Button
        step3_frame = ctk.CTkFrame(content_frame)
        step3_frame.pack(pady=10, padx=15, fill="x")
        
        ctk.CTkLabel(
            step3_frame,
            text="🚀 Step 3: Process Video",
            font=("Arial", 16, "bold")
        ).pack(anchor="w", padx=15, pady=10)
        
        self.process_btn = ctk.CTkButton(
            step3_frame,
            text="🎬 Analyze & Bleep Video Automatically",
            command=self.start_processing,
            height=50,
            font=("Arial", 16, "bold"),
            fg_color="#2B7A0B",
            hover_color="#1F5A08",
            state="disabled"
        )
        self.process_btn.pack(pady=15, padx=30, fill="x")
        
        # Progress section
        progress_frame = ctk.CTkFrame(content_frame)
        progress_frame.pack(pady=10, padx=15, fill="both", expand=True)
        
        ctk.CTkLabel(
            progress_frame,
            text="📊 Progress & Results",
            font=("Arial", 16, "bold")
        ).pack(anchor="w", padx=15, pady=10)
        
        # Progress bar
        self.progress = ctk.CTkProgressBar(progress_frame, width=900)
        self.progress.pack(pady=10, padx=30)
        self.progress.set(0)
        
        # Status label
        self.status_label = ctk.CTkLabel(
            progress_frame,
            text="Ready to process video",
            font=("Arial", 13)
        )
        self.status_label.pack(pady=5)
        
        # Results text area
        self.results_text = ctk.CTkTextbox(
            progress_frame,
            width=900,
            height=200,
            font=("Consolas", 11)
        )
        self.results_text.pack(pady=10, padx=30, fill="both", expand=True)
        self.results_text.insert("1.0", "Waiting for video...\n\nSelect a video to begin!")
    
    def select_video(self):
        """Handle video file selection"""
        file_path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=[
                ("Video Files", "*.mp4 *.avi *.mov *.mkv *.flv *.wmv"),
                ("All Files", "*.*")
            ]
        )
        
        if file_path:
            self.video_path = file_path
            filename = os.path.basename(file_path)
            self.file_label.configure(text=f"✅ {filename}")
            self.process_btn.configure(state="normal")
            self.results_text.delete("1.0", "end")
            self.results_text.insert("1.0", f"Video loaded: {filename}\n\nReady to process!\n\nClick the green button to start.")
    
    def start_processing(self):
        """Start the automatic bleeping process"""
        # Disable button during processing
        self.process_btn.configure(state="disabled")
        
        # Run in separate thread to keep UI responsive
        thread = threading.Thread(target=self.process_video)
        thread.daemon = True
        thread.start()
    
    def process_video(self):
        """Main processing pipeline - this does all the magic!"""
        try:
            device, device_label = detect_device()
            self.device_info = f"{device.upper()} ({device_label})"
            self.update_status(f"🔄 Step 1/5: Loading AI model on {self.device_info}...")
            self.progress.set(0.1)

            # Extract model name
            model_name = self.model_var.get().split()[0]  # "tiny", "base", or "small"
            model = whisper.load_model(model_name, device=device)
            
            self.update_status("🎵 Step 2/5: Extracting audio from video...")
            self.progress.set(0.2)
            
            # Load video and extract audio
            video = VideoFileClip(self.video_path)
            audio_path = self.video_path + "_temp_audio.wav"
            video.audio.write_audiofile(audio_path, logger=None)
            
            self.update_status("🤖 Step 3/5: AI transcription (detecting words with timestamps)...")
            self.progress.set(0.3)
            
            # Transcribe with WORD-LEVEL timestamps (this is key!)
            result = model.transcribe(audio_path, word_timestamps=True)
            
            self.update_status("🔍 Step 4/5: Finding profanity...")
            self.progress.set(0.6)
            
            # Get custom words
            custom_words = []
            if self.custom_words_var.get():
                custom_words = [w.strip().lower() for w in self.custom_words_var.get().split(',')]
            
            # Find profane WORDS (not segments!)
            self.profane_words = []
            for segment in result['segments']:
                if 'words' in segment:
                    for word_info in segment['words']:
                        word = word_info['word'].strip().lower()
                        # Whisper attaches punctuation and contractions/possessives
                        # (e.g. "word," or "word's") directly to tokens, which stops
                        # exact-match profanity checks from firing. Strip both so a
                        # flagged word is still caught with trailing punctuation or
                        # a 's/'ll/'d/'m/'re/'ve/'t suffix attached.
                        stripped = word.strip('.,!?;:"()[]{}-')
                        core = re.sub(r"'(s|ll|d|m|re|ve|t)$", "", stripped)

                        # Check if word is profane
                        is_profane = (
                            profanity.contains_profanity(word)
                            or profanity.contains_profanity(stripped)
                            or profanity.contains_profanity(core)
                        )
                        is_custom = any(
                            custom in word or custom in stripped or custom in core
                            for custom in custom_words if custom
                        )

                        if is_profane or is_custom:
                            self.profane_words.append({
                                'word': word_info['word'],
                                'start': word_info['start'],
                                'end': word_info['end'],
                                'reason': 'Profanity' if is_profane else 'Custom word'
                            })
            
            # Display results
            self.display_found_words()
            
            if len(self.profane_words) == 0:
                self.update_status("✅ No profanity found! Your video is clean.")
                self.progress.set(1.0)
                os.remove(audio_path)
                video.close()
                messagebox.showinfo("Clean Video", "No profanity detected!\nYour video doesn't need bleeping.")
                self.process_btn.configure(state="normal")
                return
            
            self.update_status(f"🔇 Step 5/5: Bleeping {len(self.profane_words)} words...")
            self.progress.set(0.7)
            
            # Load audio as pydub AudioSegment for precise editing
            audio_segment = AudioSegment.from_wav(audio_path)
            
            # Generate beep sound (1000 Hz tone)
            beep = Sine(1000).to_audio_segment(duration=100)  # 100ms beep
            
            # Process each profane word
            for i, word_data in enumerate(self.profane_words):
                start_ms = int(word_data['start'] * 1000)
                end_ms = int(word_data['end'] * 1000)
                duration_ms = end_ms - start_ms
                
                if self.bleep_method.get() == "beep":
                    # Create beep to match word duration
                    word_beep = beep * (duration_ms // 100 + 1)
                    word_beep = word_beep[:duration_ms]
                    # Replace audio segment with beep
                    audio_segment = audio_segment[:start_ms] + word_beep + audio_segment[end_ms:]
                else:
                    # Silence method - replace with quiet audio
                    silence = AudioSegment.silent(duration=duration_ms)
                    audio_segment = audio_segment[:start_ms] + silence + audio_segment[end_ms:]
                
                # Update progress
                progress = 0.7 + (0.2 * (i + 1) / len(self.profane_words))
                self.progress.set(progress)
            
            # Export cleaned audio
            cleaned_audio_path = self.video_path + "_cleaned_audio.wav"
            audio_segment.export(cleaned_audio_path, format="wav")
            
            self.update_status("🎬 Final step: Creating clean video...")
            self.progress.set(0.95)
            
            # Generate output filename
            base_name = os.path.splitext(self.video_path)[0]
            self.output_path = f"{base_name}_CLEAN.mp4"
            
            # Replace video's audio with cleaned audio using MoviePy's method
            cleaned_audio = AudioFileClip(cleaned_audio_path)
            final_video = video.with_audio(cleaned_audio)
            
            # Export final video
            final_video.write_videofile(
                self.output_path,
                codec='libx264',
                audio_codec='aac',
                logger=None
            )
            
            # Cleanup
            video.close()
            cleaned_audio.close()
            final_video.close()
            os.remove(audio_path)
            os.remove(cleaned_audio_path)
            
            self.update_status("✅ SUCCESS! Video cleaned and saved!")
            self.progress.set(1.0)
            
            # Show success message
            messagebox.showinfo(
                "Success!",
                f"Video successfully cleaned!\n\n"
                f"Bleeped {len(self.profane_words)} words\n\n"
                f"Saved to:\n{self.output_path}"
            )
            
            # Re-enable button
            self.process_btn.configure(state="normal")
            
        except Exception as e:
            self.update_status(f"❌ Error: {str(e)}")
            messagebox.showerror("Error", f"Processing failed:\n\n{str(e)}")
            self.process_btn.configure(state="normal")
    
    def display_found_words(self):
        """Display the profane words that were found"""
        self.results_text.delete("1.0", "end")

        device_line = f"⚡ Processed on: {getattr(self, 'device_info', 'unknown')}\n\n"

        if not self.profane_words:
            self.results_text.insert("1.0", device_line + "✅ No profanity detected!\n\nYour video is clean.")
            return

        output = device_line
        output += f"🔍 Found {len(self.profane_words)} profane words to bleep:\n\n"
        output += "=" * 70 + "\n"
        
        for i, word_data in enumerate(self.profane_words, 1):
            timestamp = self.format_timestamp(word_data['start'])
            output += f"{i}. [{timestamp}] '{word_data['word']}' - {word_data['reason']}\n"
        
        output += "=" * 70 + "\n"
        output += f"\n✅ Processing will bleep all {len(self.profane_words)} words automatically!\n"
        
        self.results_text.insert("1.0", output)
    
    def format_timestamp(self, seconds):
        """Convert seconds to MM:SS format"""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"
    
    def update_status(self, message):
        """Update status label (thread-safe)"""
        self.window.after(0, lambda: self.status_label.configure(text=message))
    
    def run(self):
        """Start the application"""
        self.window.mainloop()


if __name__ == "__main__":
    print("=" * 60)
    print("  AutoBleep Pro - Automatic Profanity Bleeper")
    print("  Starting application...")
    print("=" * 60)
    
    app = AutoBleepPro()
    app.run()
