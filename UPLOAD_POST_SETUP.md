# Multi-Platform Publishing Setup

How the social publishers are wired, and what to fill in to go live.

## Install

```bash
pip install -e .
pip install requests          # required — the REST route
pip install upload-post       # optional SDK; the REST route works without it
```

`requests` is the only hard dependency. If the `upload-post` SDK is missing the
publisher logs a note and uses the direct REST call instead — same API.

---

## Upload-Post — the primary bridge (one key, 22+ platforms)

<https://upload-post.com> publishes to TikTok, Instagram, YouTube, Facebook,
X/Twitter, LinkedIn, Pinterest, Reddit, Threads, Bluesky, Discord, Telegram and
Google Business in **one** multipart POST. No per-platform OAuth, no scraping.

**Verified against the live API:**

- `POST https://api.upload-post.com/api/upload`
- Header: `Authorization: Apikey <UPLOAD_POST_API_KEY>`
- Fields: `video` (file), `title`, `user`, and **repeated** `platform[]` fields
- Optional: `description`, `tags[]`, plus per-platform overrides such as
  `instagram_title`, `facebook_title`, `tiktok_title`, `x_title`,
  `youtube_title`, `pinterest_title`, `threads_title`
- `platforms=a,b,c` (comma-joined) is the shape used by `/analyze-shorts`,
  **not** by `/upload`. Using it here silently posts nowhere.

### Setup

1. Sign up at <https://upload-post.com> and copy the API key from the dashboard.
2. **Create a profile** at <https://app.upload-post.com/manage-users> — that
   username is the `UPLOAD_POST_USER` value. The API rejects any username not
   tied to a profile with
   `{"success":false,"message":"Username not associated with any profile"}`.
3. Connect every social account you want on that profile.
4. Fill in `auto_uploader/.env`:

```bash
UPLOAD_POST_API_KEY=<your key>
UPLOAD_POST_USER=<your profile username>
```

Check what the API currently sees:

```bash
curl https://api.upload-post.com/api/uploadposts/users \
  -H "Authorization: Apikey $UPLOAD_POST_API_KEY"
```

Returns `{"success":true,"profiles":[...],"limit":N,"plan":"..."}` — an empty
`profiles` array means step 2 has not been done yet.

### Platform names

`x` maps to `twitter`; `youtube_shorts` maps to `youtube`. See
`PLATFORM_ALIASES` in `publishers/upload_post.py`.

---

## Per-platform fallbacks

Used when Upload-Post is disabled or fails.

### Instagram — instagrapi (free, unofficial)

```bash
INSTA_USERNAME=...
INSTA_PASSWORD=...
INSTA_SESSION_FILE=.instagrapi_session.json
```

Password login, session persisted to disk, direct Reel upload. Falls back to the
Meta Graph API path when instagrapi is not installed.

### TikTok — two free routes

```bash
# Route 1: OAuth (needs a free app at developers.tiktok.com)
TIKTOK_CLIENT_KEY=...
TIKTOK_CLIENT_SECRET=...
TIKTOK_REDIRECT_URI=http://localhost:8080/callback
TIKTOK_ACCESS_TOKEN=...

# Route 2: browser fallback
TIKTOK_USERNAME=...
TIKTOK_PASSWORD=...
```

### Facebook — Graph API

```bash
FB_PAGE_TOKEN=...
FB_PAGE_ID=...
```

### X/Twitter — the free tier is read-only

```bash
PUTER_API_KEY=...            # free key from puter.com/developers
X_BASIC_BEARER_TOKEN=...     # optional, $200/mo tier, for real posting
```

Without `X_BASIC_BEARER_TOKEN` the clip is hosted on Puter and the exact
copy-paste post text is printed to the log.

---

## Test

Dry run — no credentials needed, nothing is published:

```bash
python -c "
from auto_uploader.publishers.upload_post import UploadPostPublisher
print(UploadPostPublisher({}).post_clip('some_clip.mp4', caption='Test', dry_run=True))
"
```

Expected:

```
{'instagram': 'dry-run', 'tiktok': 'dry-run', 'youtube_shorts': 'dry-run', 'facebook': 'dry-run'}
```

---

## Configuration

Platforms are enabled in `auto_uploader/config.json` (gitignored). The tracked
template is `config.example.json`.

```json
"upload_post": { "enabled": true, "daily_cap": 10, "min_minutes_between": 120 }
```

When `upload_post` is enabled it is the **primary** clip path; the per-platform
publishers are fallbacks.

---

## Architecture

```
stream -> transcribe -> censor -> clip -> announce_upload()
                                            |
                          upload_post enabled?
                             |            |
                            yes           no
                             |            |
                   ONE API CALL      per-platform:
                   -> all platforms  instagram_free / facebook_free /
                                     tiktok_free / x_webhost
```

All publishers return `{platform: status}` so the guard can track caps, spacing
and the circuit breaker.

## Zernio

`zernio_twitter` and `zernio_tiktok` are **disabled** — scraping is replaced by
Upload-Post and the free per-platform publishers above. Re-enable only if you
have a specific reason to use Zernio directly.
