#!/usr/bin/env python3
"""
AutoBleep Pro - command-line interface.

Drives the same `bleep_engine` functions as the GUI, with no customtkinter
import anywhere in the path, so it runs headless (servers, cron, CI, batch
scripts).

Examples
--------
  python cli.py input.mp4 -o out_dir/
  python cli.py input_folder/ -o out_dir/ --batch
  python cli.py in.mp4 --srt --txt --sensitivity 50 --method silence
  python cli.py in.mp4 --model base --compute int8 --encode fast --beep-wav ./beep.wav
  python cli.py in.mp4 --custom-words "brand1,brand2" --no-bleep-export

Progress goes to stderr, results to stdout, so `python cli.py ... > list.txt`
captures just the output paths. Exit code is 0 on success, non-zero on
failure.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import bleep_engine as engine
from bleep_engine import (
    COMPUTE_CHOICES,
    DEFAULT_BEEP_FREQ,
    DEFAULT_METHOD,
    DEFAULT_SENSITIVITY,
    ENCODE_CHOICES,
    METHOD_BEEP,
    METHOD_SILENCE,
    MODEL_CHOICES,
    ProcessOptions,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAILED = 1


class UsageError(Exception):
    """A bad invocation. Reported to stderr, mapped to EXIT_USAGE."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Censor profanity in video files (AutoBleep Pro).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "sensitivity bands:\n"
            "   0-30   only real profanity, leet/masked words and --custom-words\n"
            "  31-70   adds minced oaths (fudge), mishears (duck) and matching context\n"
            "  71-100  also fires on weaker surrounding context\n"
        ),
    )
    parser.add_argument("input", help="Video file, or a folder of videos.")
    parser.add_argument("-o", "--output", default=None,
                        help="Output directory (default: alongside each input).")
    parser.add_argument("--batch", action="store_true",
                        help="Treat INPUT as a folder. Auto-detected for directories.")

    parser.add_argument("--method", choices=(METHOD_BEEP, METHOD_SILENCE),
                        default=DEFAULT_METHOD,
                        help=f"How to censor each word (default: {DEFAULT_METHOD}).")
    parser.add_argument("--freq", type=int, default=DEFAULT_BEEP_FREQ,
                        help=f"Beep frequency in Hz when --method beep and no "
                             f"--beep-wav (default: {DEFAULT_BEEP_FREQ}).")
    parser.add_argument("--beep-wav", default=None, metavar="PATH",
                        help="Custom .wav to use as the beep; looped/trimmed to "
                             "each word. Falls back to --freq if unreadable.")

    parser.add_argument("--model", choices=MODEL_CHOICES, default="base",
                        help="Whisper model size (default: base).")
    parser.add_argument("--compute", choices=COMPUTE_CHOICES, default="auto",
                        help="Compute type (default: auto).")
    parser.add_argument("--encode", choices=ENCODE_CHOICES, default="fast",
                        help="libx264 preset (default: fast).")

    parser.add_argument("--sensitivity", type=int, default=DEFAULT_SENSITIVITY,
                        metavar="0-100",
                        help=f"Detection sensitivity (default: {DEFAULT_SENSITIVITY}).")
    parser.add_argument("--custom-words", default="", metavar='"a,b,c"',
                        help="Extra comma-separated words/phrases to censor.")

    parser.add_argument("--trim-silence", action="store_true",
                        help="Also cut long dead air out of the export - "
                             "loading screens, menus, the gaps between bits. "
                             "Only removes stretches that have BOTH no "
                             "transcribed words AND quiet audio, so laughter "
                             "and game noise survive. Off by default.")
    parser.add_argument("--min-silence", type=float, default=2.5,
                        metavar="SECONDS",
                        help="With --trim-silence: how long a quiet stretch "
                             "must be before it is cut (default 2.5). Below "
                             "about 2s you start cutting the pauses that make "
                             "speech sound human.")
    parser.add_argument("--srt", action="store_true",
                        help="Write a full-transcript .srt beside each output.")
    parser.add_argument("--txt", action="store_true",
                        help="Write a timestamped .txt transcript beside each output.")
    parser.add_argument("--no-bleep-export", dest="bleep_export", action="store_false",
                        help="Skip video rendering; only analyse (use with --srt/--txt).")

    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress progress output on stderr.")
    parser.add_argument("--version", action="version",
                        version=f"AutoBleep Pro CLI {engine.__version__}")
    return parser


def _resolve_inputs(args: argparse.Namespace) -> list[str]:
    """Expand INPUT into a list of video paths."""
    target = Path(args.input).expanduser()

    if target.is_dir():
        videos = engine.list_videos(target)
        if not videos:
            raise UsageError(f"no videos found in {target}")
        return videos

    if args.batch:
        raise UsageError(f"--batch given but {target} is not a directory")
    if not target.exists():
        raise UsageError(f"input not found: {target}")
    if target.suffix.lower() not in engine.VIDEO_EXTS:
        raise UsageError(f"{target.name} is not a supported video "
                         f"({', '.join(sorted(engine.VIDEO_EXTS))})")
    return [str(target)]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def say(msg: str) -> None:
        if not args.quiet:
            print(msg, file=sys.stderr, flush=True)

    try:
        videos = _resolve_inputs(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.output:
        try:
            os.makedirs(args.output, exist_ok=True)
        except OSError as exc:
            print(f"error: cannot create output directory: {exc}", file=sys.stderr)
            return EXIT_USAGE

    if args.beep_wav:
        usable, reason = engine.validate_beep_wav(args.beep_wav)
        say(("  " if usable else "warning: ") + reason)
        if not usable:
            say("         falling back to the generated tone.")

    if not args.bleep_export and not (args.srt or args.txt):
        print("error: --no-bleep-export leaves nothing to do; add --srt and/or --txt",
              file=sys.stderr)
        return EXIT_USAGE

    options = ProcessOptions(
        model_name=args.model,
        compute_pref=args.compute,
        encode_preset=args.encode,
        method=args.method,
        beep_freq=args.freq,
        custom_beep_wav=args.beep_wav,
        sensitivity=engine.clamp_sensitivity(args.sensitivity),
        custom_words=tuple(w.strip().lower()
                           for w in args.custom_words.split(",") if w.strip()),
        output_dir=args.output,
        write_video=args.bleep_export,
        write_srt=args.srt,
        write_txt=args.txt,
        trim_silence=args.trim_silence,
        min_silence_s=args.min_silence,
    )

    say(f"Loading model '{options.model_name}' ({options.compute_pref})…")
    try:
        bundle = engine.load_model_speed(options.model_name, options.compute_pref)
    except Exception as exc:
        print(f"error: could not load the transcription model: {exc}", file=sys.stderr)
        return EXIT_FAILED
    say(f"  {bundle.label}")
    say(f"  method={options.method}  sensitivity={options.sensitivity} "
        f"({engine.sensitivity_band(options.sensitivity)})  files={len(videos)}")

    failures = 0
    for index, video in enumerate(videos, 1):
        say(f"\n[{index}/{len(videos)}] {os.path.basename(video)}")
        result = engine.process_video(
            video, options, bundle,
            log=lambda msg: say(f"    {msg}"))

        if not result.ok:
            failures += 1
            print(f"error: {os.path.basename(video)}: {result.error}", file=sys.stderr)
            continue

        if result.trimmed_cuts:
            say(f"    Cut {result.trimmed_seconds / 60:.1f} min of dead air "
                f"from {result.trimmed_cuts} stretch(es).")

        for path in (result.output_path, result.srt_path, result.txt_path):
            if path:
                print(path, flush=True)

    say(f"\nDone — {len(videos) - failures} succeeded, {failures} failed.")
    return EXIT_OK if failures == 0 else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
