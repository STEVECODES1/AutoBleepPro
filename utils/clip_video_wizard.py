"""
utils/clip_video_wizard.py — Upload-Post bridge for AutoBleepPro.

One API key → publish a clip to TikTok, Instagram, YouTube, Facebook,
X/Twitter and more in a single multipart POST.  This is the "find a way"
multi-platform upload the project was missing: no per-platform OAuth, no
scraping, no Zernio, no $200/mo X token.

Usage
    python utils/clip_video_wizard.py --video clip.mp4 --title "My Clip"
        → uploads to every connected platform on your Upload-Post profile

    python utils/clip_video_wizard.py --video clip.mp4 --title "My Clip"
        --platforms tiktok,instagram,facebook,x,youtube
        → upload to a specific subset

Setup
    1. Create an account at https://upload-post.com
    2. Connect your social accounts on the dashboard (TikTok, Instagram,
       YouTube, Facebook, X, …)
    3. Copy your API key and profile username into .env:
         UPLOAD_POST_API_KEY=...
         UPLOAD_POST_USER=...
    4. Run the script.

Free plan: 10 uploads/month.  Paid plans scale from there.

References
    - https://docs.upload-post.com
    - https://docs.upload-post.com/llm.txt
    - https://github.com/Upload-Post/n8n-nodes-upload-post
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List, Optional

# Make the uploader package importable from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

try:
    from upload_post import UploadPostClient
except ImportError:
    print("ERROR: 'upload-post' is not installed.")
    print("       pip install upload-post")
    sys.exit(1)

UPLOAD_POST_API = "https://api.upload-post.com/api/upload"
_STATUS_PATH = "/api/uploadposts/status"
_POLL_INTERVAL = 10
_POLL_TIMEOUT = 600

PLATFORM_ALIASES = {
    "instagram": "instagram",
    "facebook": "facebook",
    "tiktok": "tiktok",
    "x": "twitter",
    "youtube_shorts": "youtube",
    "youtube": "youtube",
    "threads": "threads",
    "pinterest": "pinterest",
    "reddit": "reddit",
    "linkedin": "linkedin",
    "bluesky": "bluesky",
    "discord": "discord",
    "telegram": "telegram",
}

_ALL_PLATFORMS = sorted(PLATFORM_ALIASES.keys())


def _banner():
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║         Upload-Post — Multi-Platform Clip Publisher          ║")
    print("║  TikTok · Instagram · YouTube · Facebook · X · LinkedIn …   ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload a clip to every connected social platform "
                    "via Upload-Post.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --video clip.mp4 --title "Funny moment"
      → upload to all connected platforms

  %(prog)s --video clip.mp4 --title "Funny moment" \\
      --platforms tiktok,instagram,facebook,x
      → upload to a specific subset

  %(prog)s --video clip.mp4 --title "Funny moment" --dry-run
      → preview only, no upload

Setup:
  1. Sign up at https://upload-post.com
  2. Connect your social accounts on the dashboard
  3. Add to .env:
       UPLOAD_POST_API_KEY=<your-api-key>
       UPLOAD_POST_USER=<your-username>
  Free plan: 10 uploads/month.
""",
    )
    parser.add_argument("--video", "-v", required=True,
                        help="Path to the video file to upload")
    parser.add_argument("--title", "-t", default="Clip",
                        help="Title for the upload (used across platforms)")
    parser.add_argument("--description", "-d", default="",
                        help="Description / caption body")
    parser.add_argument("--platforms", "-p", default="",
                        help="Comma-separated subset of platforms, e.g. "
                             "tiktok,instagram,facebook,x,youtube")
    parser.add_argument("--user", "-u", default=None,
                        help="Upload-Post profile username "
                             "(defaults to UPLOAD_POST_USER env var)")
    parser.add_argument("--api-key", default=None,
                        help="Upload-Post API key "
                             "(defaults to UPLOAD_POST_API_KEY env var)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview the upload without sending it")
    parser.add_argument("--async-upload", action="store_true",
                        help="Upload asynchronously (recommended for "
                             "videos longer than a few seconds)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show the full API response")
    return parser.parse_args()


def _resolve_credentials(args: argparse.Namespace) -> tuple:
    api_key = (args.api_key or
               os.environ.get("UPLOAD_POST_API_KEY", "").strip())
    user = (args.user or
            os.environ.get("UPLOAD_POST_USER", "").strip())
    return api_key, user


def _resolve_platforms(args: argparse.Namespace) -> List[str]:
    if args.platforms:
        wanted = [p.strip().lower() for p in args.platforms.split(",") if p.strip()]
    else:
        wanted = list(_ALL_PLATFORMS)

    # Map project platform names to Upload-Post aliases.
    aliases = []
    for p in wanted:
        if p in PLATFORM_ALIASES:
            aliases.append(PLATFORM_ALIASES[p])
        elif p in PLATFORM_ALIASES.values():
            aliases.append(p)
        else:
            print(f"WARNING: unknown platform '{p}' — skipping")
    return aliases


def _poll_status(client: UploadPostClient, request_id: str,
                 api_key: str) -> Optional[dict]:
    """Poll the async upload status until it resolves."""
    import requests

    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"https://api.upload-post.com{_STATUS_PATH}",
                headers={"Authorization": f"Apikey {api_key}"},
                params={"request_id": request_id},
                timeout=15,
            )
            data = resp.json()
            if not data.get("success"):
                print(f"  Status: {data.get('message', 'unknown')}")
                return data
            status = data.get("status", "")
            print(f"  Status: {status}")
            if status in ("completed", "published", "success"):
                return data
            if status in ("failed", "error"):
                return data
        except Exception as exc:
            print(f"  Poll error: {exc}")
        time.sleep(_POLL_INTERVAL)
    print(f"  TIMEOUT: upload did not complete within {_POLL_TIMEOUT}s")
    return None


def main():
    _banner()
    args = _parse_args()

    api_key, user = _resolve_credentials(args)
    if not api_key:
        print("ERROR: Upload-Post API key is not set.")
        print("       Set UPLOAD_POST_API_KEY in .env, or pass --api-key")
        print()
        print("Get a free key at: https://upload-post.com")
        sys.exit(1)
    if not user:
        print("ERROR: Upload-Post user/username is not set.")
        print("       Set UPLOAD_POST_USER in .env, or pass --user")
        sys.exit(1)

    platforms = _resolve_platforms(args)
    if not platforms:
        print("ERROR: no valid platforms selected")
        sys.exit(1)

    print(f"Video:      {args.video}")
    print(f"Title:      {args.title}")
    print(f"Description: {args.description or '(none)'}")
    print(f"Platforms:  {', '.join(platforms)}")
    print(f"User:       {user}")
    print(f"Async:      {args.async_upload}")
    print()

    if not os.path.isfile(args.video):
        print(f"ERROR: video file not found: {args.video}")
        sys.exit(1)

    video_size = os.path.getsize(args.video)
    print(f"Video size: {video_size / 1e6:.1f} MB")
    print()

    if args.dry_run:
        print("[DRY RUN] Would upload to:", ", ".join(platforms))
        print("No upload was made.")
        sys.exit(0)

    print("Uploading ...")
    print("─" * 60)

    client = UploadPostClient(api_key=api_key)

    try:
        result = client.upload_video(
            video_path=args.video,
            title=args.title,
            user=user,
            platforms=platforms,
            description=args.description or None,
            async_upload=args.async_upload,
        )
    except Exception as exc:
        print(f"\nFAILED: {exc}")
        sys.exit(1)

    print()
    print("─" * 60)

    if isinstance(result, dict):
        request_id = result.get("request_id")
        if args.async_upload and request_id and not args.verbose:
            print(f"Upload initiated (request_id={request_id}). "
                  "Polling for completion ...")
            print()
            final = _poll_status(client, request_id, api_key)
            if final:
                result = final

        if args.verbose or not args.async_upload:
            print("API Response:")
            print(json.dumps(result, indent=2, default=str))
    else:
        print("Result:", result)

    # Summary line.
    print()
    print("✅ Done. Check the Upload-Post dashboard for per-platform links.")
    print("   https://upload-post.com/dashboard")


if __name__ == "__main__":
    main()
