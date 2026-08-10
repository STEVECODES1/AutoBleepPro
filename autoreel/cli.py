"""Command-line entry point for AutoReel.

Usage
-----
    # Full pipeline (censor + clip + upload)
    python -m autoreel.cli path/to/stream.mp4 --output-dir out/

    # Fast mode: censor + clip only, skip all uploading
    python -m autoreel.cli path/to/stream.mp4 --skip-upload
"""

import argparse
import os
import sys

from .pipeline import AutoReelPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoreel",
        description="AI Video Post-Production Supervisor: censors audio, "
                    "cuts vertical clips, and optionally posts to YouTube / Rumble.",
    )

    # ── Input / output ──────────────────────────────────────────────
    parser.add_argument("input",
                        help="Path to the full-length source video.")
    parser.add_argument("--output-dir", default="autoreel_output",
                        help="Where clips and reports are written. (default: autoreel_output)")

    # ── Transcription ─────────────────────────────────────────────
    parser.add_argument("--model", default="base",
                        choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper model size. 'base' is fast; 'medium'/'large' are more accurate.")
    parser.add_argument("--device", default="auto",
                        choices=["auto", "cpu", "cuda"],
                        help="Compute device for transcription. 'auto' uses GPU if available.")

    # ── Censoring ───────────────────────────────────────────────
    parser.add_argument("--bleep-method", default="beep",
                        choices=["beep", "silence"],
                        help="Replace flagged words with a beep tone or silence. (default: beep)")
    parser.add_argument("--custom-words", default="",
                        help="Comma-separated extra words to censor on top of the built-in list.")
    parser.add_argument("--no-censor", action="store_true",
                        help="Detect flagged words but leave audio untouched (clips still cut).")

    # ── Clip settings ────────────────────────────────────────────
    parser.add_argument("--num-clips", type=int, default=3,
                        help="Number of highlight clips to cut. (default: 3)")
    parser.add_argument("--clip-min", type=float, default=15.0,
                        help="Minimum clip length in seconds. (default: 15)")
    parser.add_argument("--clip-max", type=float, default=60.0,
                        help="Maximum clip length in seconds. (default: 60)")
    parser.add_argument("--no-face-tracking", action="store_true",
                        help="Use a static center crop instead of tracking a detected face.")

    # ── Upload control ───────────────────────────────────────────
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip all uploading (YouTube and Rumble). "
             "Censor the audio, cut the clips, and stop — ready for manual posting.",
    )
    parser.add_argument(
        "--skip-youtube",
        action="store_true",
        help="Cut and upload to Rumble only; skip YouTube.",
    )
    parser.add_argument(
        "--skip-rumble",
        action="store_true",
        help="Cut and upload to YouTube only; skip Rumble.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not os.path.isfile(args.input):
        parser.error(f"Input video not found: {args.input}")

    custom_words = tuple(w.strip() for w in args.custom_words.split(",") if w.strip())

    # Resolve upload flags: --skip-upload overrides the individual ones.
    upload_youtube = not (args.skip_upload or args.skip_youtube)
    upload_rumble  = not (args.skip_upload or args.skip_rumble)

    if args.skip_upload:
        print("[AutoReel] Upload skipped — clips will be saved locally only.")
    elif args.skip_youtube and args.skip_rumble:
        print("[AutoReel] Both platforms skipped — clips saved locally only.")
        upload_youtube = False
        upload_rumble  = False

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
        face_tracking=not args.no_face_tracking,
        upload_youtube=upload_youtube,
        upload_rumble=upload_rumble,
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
