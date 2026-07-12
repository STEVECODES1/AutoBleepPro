# 🎬 AutoReel — AI Video Post-Production Supervisor

AutoReel takes a long-form "Full Stream" video and:

1. **Compliance pass** — transcribes the audio (word-level timestamps via Whisper),
   flags anything that would break YouTube's Terms of Service or "Made for
   Kids" / kid-friendly standards (profanity, drug references, violent
   phrasing, self-harm references, sexual content, plus any custom words
   you supply), and censors it (beep or silence) — the same word-level
   precision as AutoBleep Pro.
2. **Highlight detection** — scores the transcript for engaging moments
   (reactions, exclamations, keyword-dense bursts) and greedily selects a
   non-overlapping set of clip windows.
3. **Vertical clip rendering** — cuts each selected window, reframes it to
   1080×1920 (9:16) for Reels/TikTok, and burns in a caption.
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
- `clipper.py` — `ClipRenderer`: cuts, reframes to 9:16, and captions clips
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
