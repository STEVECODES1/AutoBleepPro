"""Command-line entry point for AutoReel.

Usage:
    python -m autoreel.cli path/to/full_stream.mp4 --output-dir out/
"""

import argparse
import os
import sys

from .pipeline import AutoReelPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoreel",
        description="AI Video Post-Production Supervisor: makes a video kid-friendly "
        "and cuts short clips for Reels/TikTok.",
    )
    parser.add_argument("input", help="Path to the full-length source video.")
    parser.add_argument("--output-dir", default="autoreel_output", help="Where clips/reports are written.")
    parser.add_argument("--model", default="base", choices=["tiny", "base", "small", "medium", "large"])
    parser.add_argument("--bleep-method", default="beep", choices=["beep", "silence"])
    parser.add_argument("--custom-words", default="", help="Comma-separated extra words to censor.")
    parser.add_argument("--num-clips", type=int, default=3)
    parser.add_argument("--clip-min", type=float, default=15.0, help="Minimum clip length in seconds.")
    parser.add_argument("--clip-max", type=float, default=60.0, help="Maximum clip length in seconds.")
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Compute device for transcription. 'auto' (default) uses your GPU if available, else CPU.",
    )
    parser.add_argument(
        "--no-censor",
        action="store_true",
        help="Detect and report flagged words but leave the audio untouched (clips are still cut).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not os.path.isfile(args.input):
        parser.error(f"Input video not found: {args.input}")

    custom_words = tuple(w.strip() for w in args.custom_words.split(",") if w.strip())

    pipeline = AutoReelPipeline(
        output_dir=args.output_dir,
        model_name=args.model,
        bleep_method=args.bleep_method,
        custom_words=custom_words,
        num_clips=args.num_clips,
        clip_min_duration=args.clip_min,
        clip_max_duration=args.clip_max,
        device=None if args.device == "auto" else args.device,
        censor_profanity=not args.no_censor,
    )

    report = pipeline.run(args.input)

    report_path = os.path.join(args.output_dir, "supervisor_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report.to_markdown())

    print(report.to_markdown())
    print(f"Report written to: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
