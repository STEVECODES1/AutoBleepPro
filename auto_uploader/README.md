# Auto-Upload System (YouTube + Rumble)

Watches a folder for new stream recordings and uploads them to YouTube and
Rumble automatically, with your title/description format pre-filled.

**What's built:** folder watching, YouTube upload (official OAuth2 API,
resumable), Rumble upload (browser automation — see the note below), title
prefixed with `date + "Stackswopo Stream"`, description templating,
duplicate detection (by content hash), retry with exponential backoff,
desktop notifications, per-platform logs, dry-run mode, and a CLI.

**What's not built (yet):** the web dashboard, email/Discord
notifications, upload scheduling windows, YouTube quota tracking, and
video compression. Those were in the original wishlist but cut to keep
this shippable and reliable first — say the word if you want any of them
added next.

## 1. Install dependencies

```bash
cd auto_uploader
pip install -r requirements.txt
playwright install chromium
```

## 2. Get YouTube OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a new project (or pick an existing one).
2. Go to **APIs & Services → Library**, search for **YouTube Data API v3**, click it, and click **Enable**.
3. Go to **APIs & Services → OAuth consent screen**. Choose **External**, fill in the required fields (app name, your email). Under **Test users**, add your own Google account email (this keeps the app in "testing" mode, which is fine for personal use).
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**. Application type: **Desktop app**. Give it any name.
5. Click **Download JSON** on the credential you just created. Rename the downloaded file to `client_secrets.json` and put it in this `auto_uploader/` folder.
6. The first time you run an upload, a browser window will open asking you to log into the Google account that owns `@StackswopoGames` and grant access. After that, a `youtube_token.json` file is saved here and you won't be asked again (it auto-refreshes).

## 3. Set up Rumble authentication

Rumble has no public API for regular creators, so uploads are done via
browser automation (Playwright drives the real upload page). Two ways to
authenticate:

### Recommended: attach to a Chrome window you're already logged into

This avoids storing your Rumble password anywhere and skips fighting
login-form selectors entirely - the uploader just reuses a browser window
you logged into normally.

1. Close all Chrome windows first (a running Chrome instance without the debug flag will conflict).
2. Launch Chrome with remote debugging enabled:
   ```
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\RumbleChromeProfile"
   ```
3. In that Chrome window, log into Rumble normally (handle 2FA there if prompted). Leave the window open.
4. In `config.json`, set `"rumble": { "cdp_url": "http://localhost:9222", ... }`.
5. Run the uploader as usual - it'll attach to that window instead of launching its own and logging in.

You need to leave that Chrome window open and logged in for as long as you want uploads to work. Re-run step 2 (same `--user-data-dir`) any time you restart your PC - it'll remember the login.

### Fallback: username/password

If you'd rather not deal with the Chrome debug-port setup:

1. Copy `.env.example` to `.env`.
2. Fill in `RUMBLE_USERNAME` and `RUMBLE_PASSWORD` with your Rumble login.
3. Leave `cdp_url` as `null` in `config.json`.
4. If your account has 2FA enabled, the first run will pause and ask you to type the code into the terminal when Rumble prompts for it.

This path automates the actual login form, which is more fragile (depends on correctly guessing Rumble's current login-page selectors) than the CDP-attach option above.

**If Rumble changes their upload page and this breaks:** run
`playwright codegen https://rumble.com/upload.php` in a terminal, log in
and click through an upload manually — codegen will print the exact
selectors Rumble is using now. Copy the relevant selector into
`utils/rumble_uploader.py` at the matching step (each one is commented).

## 4. Run it

```bash
# Test your config without uploading anything
python main.py --test-config

# Preview titles/descriptions for everything in watch_folder/ - uploads nothing
python main.py --dry-run

# Watch watch_folder/ and auto-upload new videos as they arrive
python main.py --watch

# Upload one specific file right now
python main.py --file "D:\videos\stream.mp4" --title "Le Bandit slots session"

# Process everything sitting in watch_folder/ right now, then exit
python main.py --batch

# ...or point it at some other folder, just for this run
python main.py --batch "D:\videos stizz"
```

`--watch` stays running and picks up files as you drop them in. `--batch`
processes whatever's already there and exits — that's the one to use when
you've just dropped in a couple of finished VODs. With no path after it,
`--batch` uses `general.watch_folder` from `config.json`
(`./watch_folder`, i.e. `auto_uploader\watch_folder`).

### Fully hands-off: auto-upload finished downloads

Point `general.watch_folder` at wherever your recorder/downloader writes,
then leave `python main.py --watch` running. New videos upload themselves.

`--watch` is built to run unattended, which means two things:

- **It never prompts.** The title prompt would block forever in the
  watcher's background thread with nobody at the keyboard. Titles come
  from the filename, or a `.txt` sidecar, or `default_title` — never
  `input()`. (`--batch` and `--file` still prompt as before.)
- **It ignores half-finished downloads.** `.part` / `.ytdl` files are
  skipped, and so are yt-dlp's pre-merge stream fragments
  (`Video.f140.mp4` = audio only, `Video.f299.mp4` = video only). yt-dlp
  downloads each stream *in full* and merges afterwards, so those sit
  there complete and unchanging for a while — without that guard the
  stability check would fire and upload an audio-only file.

**yt-dlp titles work automatically.** A download named
`Stackswopo - LOL  NO -YdH8jO6Vjs.mp4` becomes the title `LOL NO`: the
trailing video ID is dropped, and a leading channel name listed in
`general.filename_channel_prefixes` is stripped. Names that don't end in a
video ID are left completely alone.

Drop a finished recording into `watch_folder/`, and (unless you disable
`ask_for_title` in `config.json`) it'll ask you for the stream title in
the terminal, then upload to both platforms with:

- Title: `"<your title>" <today's date> Stackswopo Stream`
- Description: the templates in `config.json`, with `[DATE]` and
  `[STREAM TITLE]` filled in automatically.

Successfully processed files get moved to `uploaded/`. Already-uploaded
files (matched by content hash, not filename — so renaming a file won't
fool it) are skipped automatically.

### Backfilling an old folder of VODs

`--batch` also works great for a folder of old recordings that were only
ever manually uploaded to YouTube in the past:

- **Date comes from the filename when there is one** (e.g. `"'!howl' 3-20-26
  Stackswopo Stream.mp4"` → March 20, 2026), instead of always using
  today's date — so backfilled titles get the date the stream actually
  aired, not the date you happened to run the batch.
- **Before uploading, it checks your real YouTube channel** for a video
  whose title already contains that same date (covers both this
  channel's old title style, `*Title* - 05/08/26 - ...`, and the new one
  this tool generates) and skips the YouTube upload if a match is found
  - it still uploads to Rumble, since old VODs were typically only ever
  put on YouTube manually. This check runs once per `--batch`/`--watch`
  run (not once per file), so it's cheap regardless of how many videos
  are on the channel already.
- Run one or two files first with `--file` before pointing `--batch` at
  a big folder, to make sure the results look right.

## Censoring: per-platform

Profanity censoring is configured **per platform**, because the two have
different rules:

| Platform | `censor_uploads` | Why |
|---|---|---|
| YouTube | `true` | YouTube age-restricts / demonetizes over spoken profanity |
| Rumble | `false` | Uploads the original, uncensored audio |

Flip either flag in `config.json`. `general.censor_before_upload` is the
master switch - set it to `false` to disable censoring everywhere.

`general.censor_bleep_method` controls *how* words are censored:
`"silence"` (mutes them) or `"beep"` (overlays a tone).

Censoring runs **lazily**: if YouTube is skipped as already-uploaded and
Rumble isn't censoring, transcription never runs at all - which saves
many minutes per stream on a backlog run.

## Extras (config.json -> "features")

- **Health check** — `python main.py --health`: disk space, CPU/RAM,
  YouTube/Rumble reachability, and cleanup of stale temp files (old page
  dumps, leftover censored copies, `__pycache__`). Problems raise a
  desktop notification.
- **Social announcements** (`social_promoter`, off by default) — after a
  successful NEW upload (never for skipped duplicates), posts the title +
  links to Discord (just set `DISCORD_WEBHOOK_URL` in `.env` and flip
  `"enabled": true`). Twitter/X and Reddit are supported too but need
  their API keys in `.env` plus `pip install tweepy` / `praw`.
- **Content optimizer** (`content_optimizer`, on by default) — after each
  new upload, writes `<video>_optimize.md` next to the uploaded file:
  alternate title ideas, ready-to-paste YouTube chapter markers,
  high-energy timestamps for thumbnails, and ~30s Shorts windows. Reuses
  the transcript the censor pass already produced - it never runs a
  second transcription just for the report.
- **Rumble duplicate detection** (`rumble.skip_if_exists`, on by default)
  — before uploading to Rumble: local history by file hash, local history
  by title (catches re-encoded copies), then the channel RSS feed matched
  by stream date. Matches are skipped and logged with the existing URL.

## 5. Skip the title prompt

Set `"ask_for_title": false` in `config.json`'s `general` section, and
every auto-detected upload will use `"default_title"` instead of asking.
You can also drop a `.txt` file next to a video with the same name (e.g.
`stream_2026-08-02.mp4` + `stream_2026-08-02.txt`) containing just the
title on the first line — that's used automatically and skips the prompt
for that file specifically.

## 6. Customizing title/description/tags

Everything text-related lives in `config.json` — no code changes needed:

- `youtube.title_format` / `rumble.title_format` — use `{title}` and
  `{date}` as placeholders.
- `youtube.description_template` / `rumble.description_template` — use
  `[DATE]` and `[STREAM TITLE]` as placeholders.
- `youtube.tags` / `rumble.tags` — plain list of strings, no `#` needed.
- `general.date_style` — `"M/D/YY"` matches your existing channel's
  convention (e.g. `7/31/26`). Set it to a strftime format like
  `"%Y-%m-%d"` instead if you'd rather have ISO dates.

## 7. Retry / error handling

Each platform gets up to 3 retries (configurable via `general.max_retries`
and `general.retry_delays` in `config.json`) with delays of 1 min, 5 min,
then 15 min between attempts. If YouTube succeeds but Rumble fails (or
vice versa), the successful one still counts — the file is only marked
"fully done" and moved to `uploaded/` after both platforms have been
attempted, and the failure is logged and desktop-notified either way.

## 8. Logs

- `logs/youtube.log` and `logs/rumble.log` — timestamped, per-platform.
- Everything also prints to the console while running.

## Known limitations, honestly

- **Rumble automation is inherently more fragile than the YouTube half.**
  It's driving their real web page, not a stable API contract. Expect to
  occasionally need to update a selector in `rumble_uploader.py` (see
  the `playwright codegen` tip above).
- **2FA on Rumble** requires you to be present to type the code in — this
  tool won't (and shouldn't) try to bypass that.
- The web dashboard, email/Discord notifications, scheduling windows,
  quota tracking, and auto-compression from the original wishlist aren't
  built. This version is the reliable core; ask if you want any of those
  added on top.
