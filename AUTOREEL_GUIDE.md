# 🎬 AutoReel — AI Video Post-Production Supervisor

AutoReel takes a long-form "Full Stream" video and:

1. **Compliance pass** — transcribes the audio (word-level timestamps via Whisper),
   flags anything that would break YouTube's Terms of Service or "Made for
   Kids" / kid-friendly standards (profanity, drug references, violent
   phrasing, self-harm references, sexual content, plus any custom words
   you supply), and censors it (beep or silence) — the same word-level
   precision as AutoBleep Pro. You can disable censoring with `--no-censor`
   if you just want violations reported without the audio being touched.
2. **Highlight detection** — scores the transcript for engaging, clip-worthy
   moments (reactions, laughter, shouting, elongated words, exclamations,
   keyword-dense bursts) and greedily selects a non-overlapping set of clip
   windows.
3. **Vertical clip rendering** — cuts each selected window and reframes it
   to 1080×1920 (9:16) for Reels/TikTok. When a face is detected (e.g. a
   facecam layout), the crop dynamically follows it instead of a fixed
   center-crop, so it doesn't get cut out of frame. Falls back to a static
   center-crop automatically when no face is found. Disable with
   `--no-face-tracking`.
4. **Supervisor report** — a markdown summary of what was censored and
   which clips were produced, written to `supervisor_report.md`.

## Usage

```bash
python -m autoreel.cli path/to/full_stream.mp4 --output-dir autoreel_output
```

Or on Windows, double-click `START_AUTOREEL.bat` (or run it from a
terminal so you can pass a file):

```
START_AUTOREEL.bat "stream.mp4" --num-clips 5
```

### GUI

Prefer a window over the command line? Double-click `START_AUTOREEL_GUI.bat`
(same dark customtkinter interface as AutoBleep Pro). Pick a video, set the
model/device/censoring/face-tracking options and number of clips, hit
**Transcribe, Censor & Cut Clips**, and watch progress and the final
supervisor report right in the window. A **📂 Open Output Folder** button
appears once it's done.

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--output-dir` | `autoreel_output` | Where the cleaned video, clips, and report go |
| `--model` | `base` | Whisper model size (`tiny`/`base`/`small`/`medium`/`large`) |
| `--bleep-method` | `beep` | `beep` or `silence` for censored spans |
| `--custom-words` | *(none)* | Comma-separated extra words to censor |
| `--num-clips` | `3` | Number of short clips to generate |
| `--clip-min` | `15` | Minimum clip length in seconds |
| `--clip-max` | `60` | Maximum clip length in seconds |
| `--device` | `auto` | Compute device for transcription (`auto`/`cpu`/`cuda`) |
| `--no-censor` | *(off)* | Report violations but leave the audio untouched |
| `--no-face-tracking` | *(off)* | Always use a static center crop instead of following a detected face |

## Output

```
autoreel_output/
├── <name>_CLEAN.mp4          ← full video with compliance edits (only if violations were found)
├── <name>_01.mp4              ← vertical highlight clip
├── <name>_02.mp4
├── <name>_03.mp4
└── supervisor_report.md      ← what was censored + which clips were made
```

## Architecture

The pipeline is split into small, independently testable pieces under
`autoreel/`:

- `transcription.py` — wraps Whisper (word-level timestamps)
- `compliance.py` — `ComplianceEngine`: flags + censors non-compliant audio
- `highlights.py` — `HighlightScorer`: scores and selects clip-worthy windows
- `clipper.py` — `ClipRenderer`: cuts and reframes clips to 9:16
- `pipeline.py` — `AutoReelPipeline` wires the stages together and builds
  the `SupervisorReport`
- `cli.py` — command-line entry point

`compliance.py` and `highlights.py` have no hard dependency on
whisper/moviepy/pydub at import time, so their logic is covered by plain
`unittest` tests in `tests/` that run without any AI/video libraries
installed:

```bash
python -m unittest discover -s tests -v
```

## Troubleshooting

**Transcription fails with `No such file or directory: 'ffmpeg'`**
Whisper shells out to a **system** `ffmpeg` on PATH for audio decoding —
moviepy's bundled ffmpeg doesn't cover this. Install ffmpeg separately
(Windows: `winget install ffmpeg`; macOS: `brew install ffmpeg`; Linux:
your package manager) and open a new terminal so PATH picks it up. Verify
with `ffmpeg -version`.

**GPU not being used even though you have an NVIDIA card**
Run `python -c "import torch; print(torch.cuda.is_available())"`. If that
prints `False`, your installed `torch` build doesn't have CUDA support (or
your NVIDIA drivers aren't installed/up to date) — reinstall torch, or
check `nvidia-smi` works first. `--device auto` (the default) always falls
back to CPU silently rather than erroring, so this is the way to confirm
which one you actually got.
