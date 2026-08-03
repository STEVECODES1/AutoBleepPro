# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.3.0] - 2026-08-03

### Added
- **Command-line interface** (`cli.py`) — the whole pipeline without the
  GUI, for servers, cron jobs and batch scripts. Progress on stderr,
  output paths on stdout, exit 0/non-zero. It imports `bleep_engine` only;
  customtkinter is never loaded. `START_CLI_HELP.bat` prints its usage.
- **SRT + transcript export.** `words_to_srt()` and `words_to_txt()` in the
  engine, plus `bleeps_to_srt()` for a "censored words only" subtitle track
  that shows each detection's reason. All three accept either a
  `transcribe_words()` result or a flat word list. In the GUI: *Save
  transcript (.txt)* / *Save captions (.srt)* buttons after analysis
  (available even when nothing was flagged), an *Also export SRT on bleep*
  checkbox that writes `*_CLEAN.srt` beside the video, and *Export SRT in
  batch*. On the CLI: `--srt` / `--txt`, and `--no-bleep-export` to produce
  transcripts without re-encoding video.
- **Detection sensitivity, 0-100** (`--sensitivity`, or the slider on the
  Single Video tab; default 70). A plain rule gate, not a score — the
  number selects which families of rules may fire:
  - **0-30** real profanity, leet decodes, symbol bypasses and custom words
    only. No minced oaths, no mishears, no context inference.
  - **31-70** adds minced oaths ("fudge"), Whisper mishears ("duck"), and
    context-only candidates when a trigger points at that word's own target
    ("son of a" + "beach").
  - **71-100** additionally accepts context-only candidates on *any* nearby
    profanity signal, and widens the trigger window.
  A bare context-only word ("beach", "truck") with no surrounding signal
  stays clean at every setting — aggressive is not indiscriminate.
- **Custom beep sample.** Browse for a `.wav` in the GUI or pass
  `--beep-wav`; the sample is looped or trimmed to each word's exact
  length. A missing or unreadable file falls back to the generated tone
  with a single warning rather than failing the export.
- **Drag and drop** (optional). Drop a video onto the window to load it, or
  a folder to set up a batch run. Needs `tkinterdnd2`, which is deliberately
  not a hard requirement — without it Browse works as before and the window
  says `pip install tkinterdnd2 for drag-and-drop`.
- `engine.process_video()` / `ProcessOptions` / `ProcessResult` — the
  one-file pipeline now lives in the engine, so the GUI's batch tab and the
  CLI run identical code instead of two parallel implementations.
- 85 more tests (176 total): sensitivity gating and its band boundaries,
  SRT/TXT structure, and censored-audio length sanity.

### Changed
- **Muting is now the default censoring method**, not the beep. Both
  options are still on the radio group, and the silence path writes true
  digital silence (`AudioSegment.silent`) over the region — no tone. The
  batch tab shows which one is active: *"Uses same Method as Single Video
  tab (currently: Silence)."*
- `extract_audio()` (with its moviepy fallback) and `render_video()` moved
  from the GUI into the engine so the CLI can reuse them.
- The Single Video tab scrolls, now that it carries more settings.

### Removed
- The *Fuzzy matching* checkbox added in 2.2.1. Sensitivity ≤30 is the same
  escape hatch and is finer-grained, so keeping both would have been two
  controls for one decision. `check_word(..., fuzzy=False)` still works and
  simply clamps into the low band.

## [2.2.1] - 2026-08-03

Maintenance release. No new user-facing features beyond one opt-out
checkbox — this is the pass that makes v2.2's detection work actually
usable on long videos and in batch mode.

### Added
- `bleep_engine.py` — all detection, audio, model and path logic, split out
  of the GUI class. Imports `torch`, `pydub`, `whisper`/`stable-ts` and
  `moviepy` defensively, so the engine (and its tests) run on a machine
  with none of the ML or video stack installed.
- `tests/test_detection.py` — 91 detection/span/path tests. No GPU, no
  ffmpeg, no model download; transcription results are plain dicts.
- `.github/workflows/ci.yml` — pytest on 3.11 plus a byte-compile job.
  Installing the pinned GPU stack is best-effort and cannot fail the build.
- **Fuzzy matching** checkbox (default **on**, matching v2.2 behaviour).
  Turning it off restricts detection to real profanity, symbol bypasses and
  custom words. This matters most in the Batch tab, which has no review
  step to untick false positives like "shoot", "duck" or "behind".
- Multi-word custom entries now work. The GUI has always advertised
  `rival brand, competitor name` in its placeholder, but a phrase can never
  match a single token, so anything containing a space silently did
  nothing. Phrases are now matched against the trailing context window.
- Output files no longer overwrite silently: `_CLEAN.mp4`, then
  `_CLEAN_1.mp4`, `_CLEAN_2.mp4`, …
- Batch mode skips files it previously produced, so re-running a folder no
  longer generates `x_CLEAN_CLEAN.mp4`.

### Fixed
- **Bleeping corrupted the audio track, not just slowly.** The splice
  (`seg = seg[:s] + bleep + seg[e:]`) inserted `max(end - start, 50)` ms
  while removing only `end - start` ms. Every word shorter than 50 ms
  therefore *lengthened* the track: audio drifted out of sync with the
  video, and — because the splice mutated the timeline it was still
  indexing into — every later bleep landed progressively further from its
  word. Measured on a 10-minute clip: three sub-50 ms hits added 91 ms of
  drift. Replacements are now always exactly as long as the span they
  replace, so total duration is preserved bit-for-bit, and audio outside a
  bleeped span is byte-identical to the source.
- **O(n²) audio rebuild.** That same line rebuilt the whole `AudioSegment`
  once per word. The track is now assembled in a single pass over raw PCM.
  400 bleeps on 10 minutes of audio: **27.0 s → 0.10 s**.
- **Overlapping bleep spans produced duplicated audio.** Spans are now
  sorted, clamped to the track, and merged before anything is rebuilt.
  Sub-50 ms spans are widened around their centre instead of by pushing the
  end outward, so a bleep stays centred on the word it is covering.
- **Worker threads drove Tkinter directly.** `_export_video` and
  `_run_batch` called `self.bleep_method.get()`, `self.beep_preset.get()`
  and friends from inside their loops — reading a Tk variable off the main
  thread. Every setting is now snapshotted into an immutable `Settings` on
  the main thread before the worker starts.
- **UI updates from worker threads were silently dropped.** `window.after()`
  is itself a Tcl call and raises `RuntimeError: main thread is not in main
  loop` whenever the main thread isn't inside `mainloop()`. Workers now
  hand callbacks to a `queue.Queue` that the main thread drains, and the
  poll is cancelled on close so Tk no longer prints `invalid command name`
  on exit.
- **Temp files collided.** `video_path + "__temp_audio.wav"` breaks on
  read-only input folders, collides when two videos share a stem, and
  leaves debris next to the user's footage. Replaced with `tempfile`;
  tracked and removed in `finally`, and on window close so quitting
  mid-review doesn't leak a WAV.
- **Uninitialised attributes.** `_audio_path_for_export` and
  `_video_path_for_export` were created mid-run, so any earlier failure
  raised `AttributeError` instead of the real error.
- **`_confirm_and_export` zipped two lists that could differ in length**,
  which would bleep the wrong timestamps. It now refuses and asks for a
  re-analysis.
- **The model was reloaded on every run** — seconds to minutes each time.
  `ModelCache` keeps one loaded, keyed on `(model_name, compute_pref)`, and
  releases the old one (with `torch.cuda.empty_cache()`) before loading a
  different one so switching models can't stack two copies on the GPU.
- **`transcribe_words` crashed on the fallback path.** It branched on the
  global `SPEED_MODE`, but `load_model_speed()` can fall back to
  openai-whisper *even when stable-ts imported fine* — and the two return
  completely different shapes, so the fallback hit
  `AttributeError: 'dict' object has no attribute 'segments'`. The loaded
  backend is now tracked on the returned `ModelBundle` and branched on.
- **`f***` was never detected.** The trailing-mask regex required a word
  boundary after the symbols, which never exists at end-of-token. Also
  bounded the bypass check to ≤3 surviving letters, so ordinary words
  adjacent to punctuation (`bold**`) aren't flagged.
- **`f*ck` / `sh*t` were never detected.** The leet decoder mapped `*` to
  the empty string only, producing `fck`. Ambiguous leet characters now
  expand to every plausible letter (`@` → a *and* u, so `f@ck` decodes to
  `fuck`, not just `fack`), bounded to 256 candidates per token.
- **Context-only homophones fired on unrelated context.** Any trigger
  phrase anywhere in the window flagged the word, so "what the **beach**"
  matched via the *hell* trigger. Triggers must now point at the same
  underlying word. Added `ass` triggers, without which `bass` was
  unreachable in every code path.
- Videos with no audio track produced a bare
  `AttributeError: 'NoneType' object has no attribute 'write_audiofile'`;
  they now say so in plain English.
- moviepy clips are closed in `finally`, so a failed export no longer holds
  a file handle open (and Windows can delete the temp file).
- `safe_remove` also tolerates `PermissionError` — the common Windows case
  it was supposed to be covering but didn't.
- Batch mode left its button permanently disabled when the input folder was
  unset, and reported no success/failure counts.
- Review-panel timestamps now render hours (`1:02:05`), instead of showing
  a 2-hour video's last word as `125:30`.

### Changed
- `autobleep_pro.py` is GUI and threading only. `python autobleep_pro.py`
  and `START_AUTOBLEEP.bat` are unaffected.
- `make_beep` caches only the 100 ms base tone per frequency. The old
  module-level dict cached one segment per `(duration, frequency)` pair and
  never evicted, so a long video accumulated a distinct `AudioSegment` for
  every distinct word length.
- Beep/silence segments are resampled to match the source track before
  being spliced, rather than relying on pydub converting them on every
  concatenation.

### Removed
- Dead code: `import numpy as np` and `from datetime import timedelta` were
  unused. The `core` apostrophe-stripping regex could never match, because
  `_normalize` strips apostrophes before it runs.

### Known behaviour
- Fuzzy matching flags minced oaths and likely mishears by design, which
  means "shoot", "duck", "behind", "sugar" and "shirt" are bleeped on
  sight. That is intentional, but the Batch tab has no review step —
  untick **Fuzzy matching** for batch runs where that is not wanted.

## [2.2.0] - 2026-07-28

### Added
- Smart detection engine:
  - leet-speak decoder (`sh1t`, `f@ck`, `a$$`)
  - common homophone / minced-oath list (fudge, shoot, dang, …)
  - asterisk and symbol bypass detection (`f**k`, `s**t`, `b***h`)
  - Whisper mishear list (shirt→shit, duck→fuck, …)
  - context window, so "son of a **beach**" is caught while "beach" alone
    is not
  - deduplication, so a word matching two rules is only bleeped once

### Fixed
- `NameError` on `MISHEAR_CONTEXT_ONLY` — the set is spelled
  `MISHEARD_CONTEXT_ONLY`. Any word in the mishear list (shot, truck,
  shirt) crashed analysis, which is most videos.
- `NameError` in `_analyze_video` / `_export_video`: `exc` is unbound after
  its `except` block, so the error dialog's lambda raised instead of
  showing the message.
- openai-whisper is now always imported, not only when stable-ts is
  missing, so `load_model_speed()`'s last-resort fallback can't hit the
  same class of `NameError`.
- Restored `openai-whisper` in `requirements.txt`; the v2.1 switch to
  faster-whisper dropped it, breaking `autoreel/transcription.py`, which
  imports it directly.

## [2.1.0] - 2026-07-28

### Added
- Speed stack: faster-whisper + stable-ts for word-level timestamps
  (~4× faster than openai-whisper).
- Compute-type picker (int8 / float16 / float32, plus auto).
- ffmpeg-based 16 kHz mono audio extraction.
- libx264 encode presets (ultrafast → slow).
- Turbo model in the model picker.

## [2.0.0] - 2026-07-28

### Added
- Word-by-word review UI — untick words you don't want bleeped.
- Output folder picker.
- Batch folder processing.
- Beep sound presets (4 options).
- Medium Whisper model.
- Pinned dependency versions and MIT license.

[Unreleased]: https://github.com/STEVECODES1/AutoBleepPro/compare/v2.3.0...HEAD
[2.3.0]: https://github.com/STEVECODES1/AutoBleepPro/compare/v2.2.1...v2.3.0
[2.2.1]: https://github.com/STEVECODES1/AutoBleepPro/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/STEVECODES1/AutoBleepPro/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/STEVECODES1/AutoBleepPro/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/STEVECODES1/AutoBleepPro/releases/tag/v2.0.0
