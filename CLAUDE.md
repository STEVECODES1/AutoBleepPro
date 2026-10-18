# AutoBleepPro - operating notes for Claude

Read this first. It is what earlier sessions learned the slow way. The owner runs this from a phone,
away from the PC: it must work by itself after one START, and say plainly when it needs them.

## What runs, and where

- Windows PC, repo `D:\AutoBleepPro-git`. `START.bat` (pulls code, then `_AUTOSTART.ps1`) and the Startup
  folder's "AutoBleep Autostart.cmd" (at login, after a reboot) start each piece once, each with a keepalive
  window: Rumble Chrome + keeper (`_RUN_RUMBLE_CHROME.bat`, every 60 s), recorder, uploader
  (`main.py --batch` then `--watch`), Rumble live relay, Rumble recorder. Ollama starts at login too.
- The uploader picks up new code only on restart: `tools\quiet_restart.ps1` stops it ONLY when idle - no
  `logs\processing.json` naming any main.py, no in_progress clip job, no ffmpeg/python/node child, and no
  upload heartbeat (`logs\uploading.json`, touched every 15 s by every YouTube chunk / Rumble poll -
  `utils/upload_beat.py`) in the last 10 min. Never kill it any other way: 10-16 cut a Rumble upload at
  75%; 10-17 22:43 `restart_when_idle.ps1` cut the full stream's YouTube upload (see Incidents).
- Rumble uploads attach over CDP to the Chrome on port 9222 (profile `C:\RumbleChromeProfile`). That
  window IS the owner's main Chrome (Studio as wopovod, IG, FB, the Claude extension) - never start it with
  --disable-extensions. A hung attach restarts it (`rumble_uploader._heal_chrome`, at most every 30 min,
  never under another upload). Tests can never close it (conftest stubs `_kill_chrome_on_port`).
- The brain (`utils/brain.py`, every minute inside --watch) alerts once (Discord webhook + ntfy) and says
  "Fixed" later: recorder down/stalled/live-not-recording, crash loop, low disk, Rumble Chrome not
  answering 5 min, YouTube refusing uploads, a big video stuck 6 h in watch_folder with no upload moving,
  a clip post in_progress > 2 h. Daily report after 10:00: full streams + links, clips per platform,
  held/waiting/failed, disk, what needs the owner.
- Tests: `python -X utf8 -m pytest tests -q -p no:cacheprovider` (~11 min). Known stale failures (not
  regressions): test_cap_keys shorts cap, test_clip_offer_parallel overlap, test_jump_cuts silent pause,
  test_parallel_uploads (1-2, flaky), test_job_report end-of-process_file, test_stream_links /
  test_youtube_links "Full stream" vs "Full video" wording.
- Always `python -X utf8`. In PowerShell: .NET file calls use the process cwd; `Start-Process
  -ArgumentList` does not quote - wrap an argument with spaces in '"..."'.

## Channels (one Google project, 10,000 units/day shared by all four)

| token (auto_uploader\) | channel | role |
|---|---|---|
| youtube_token.json | STACKSWOPO VODS @WOPOVOD (UCbS-_v6Y9AZmAAU3EpzLsvA) | full streams - THE money channel |
| youtube_shorts_token.json | @WOPOCLIPPER | Shorts |
| youtube_games_token.json | STACKSWOPO GAMES (UCKj0xrFqNNXpBCl_B1shckA) | recaps (has a strike) |
| youtube_compilation_token.json | STACKSWOPOVODS @stackswopo10k | weekly, Monkey compilations |

Costs: videos.insert 1600, writes 50, reads 1. Rumble: BinScripts, uncensored. IG/FB via Graph API, X and
TikTok via Buffer (caps: Shorts 5, TikTok 6, IG 8, X 8 a day; FB every 80 min). Upload speed is ~3 Mbps:
a 4.4 GB full stream takes ~3 h to YouTube.

## Guards that must stay

- `utils/text_guard.py` - every title/description/caption passes through it; pinned by
  `tests/test_no_garbled_text_goes_out.py`. Titles say the stream name once
  (`templating.without_doubled_lead`); a recap with no date in its file name takes the full stream's
  (`stream_links.date_for`).
- Child-safety (`autoreel/safe_text.child_risk`): kid words AND ages under 18 ("he was 15", "15 year old",
  "8th grade"; not minutes/kills/heights/a thing's age). Recaps/weekly drop such moments; clips are held
  by title/caption AND by what they SAY (`clip_queue._spoken_child_risk`: a spoken age or abuse word holds
  everywhere but Rumble; "pedo" holds the Short only). Gambling on screen held too.
- Rumble: a kids-titled clip uploads PRIVATE; slurs in Rumble titles masked; the video stays uncut.
- The owner's rule: wopovod must be fully secured; GAMES/recaps matter less.

## Tools (tools\, all print what they do)

- `health_check.py [HOURS] [--yt]` - queue by platform/state, every not-done job and why, newest uploads.
- `community_post.py <video> "<stream>" [--dry-run]` - the wopovod Posts-tab post the uploader makes by
  itself after each new full stream (`utils/community_post.post_later`: own process, waits for YouTube to
  finish processing, retries every 20 min for 6 h, one post per video in logs/community_posts.json).
- `metadata_audit.py`, `playlist_builder.py`, `links_backfill.py`, `rumble_cta_backfill.py`,
  `make_banner.py` + `set_banner.py`, `held_review.py`, `quiet_restart.ps1`, `restart_when_idle.ps1`.
- `main.py --check-llm` - which AI providers answer right now. `main.py --forget FILE --forget-platform
  youtube` - make the ledger forget one platform's upload.

## AI models (checked 10-18)

- Picked live where safe (`autoreel/llm_highlights.resolve_model`): Gemini first (gemini-3.8-flash on
  10-18), local GPU qwen3-vl:4b (Ollama; still the right size next to Whisper on the 8 GB RTX 4060),
  NVIDIA nemotron-3-nano-omni, xKiro qwen3.8-omni-flash:free, Groq gpt-oss-120b. OpenRouter is pinned:
  google/gemma-4-31b-it:free (nex-n2.5-pro lost its free tier 10-18). Cerebras answers 402 (no credit).
  Paid (OpenAI, Anthropic claude-opus-5-5, DeepSeek) only with ALLOW_PAID_AI=1.
- Whisper large-v3-turbo (faster-whisper 1.1.1, CUDA) for censor + transcripts. yt-dlp 2026.8.19 = latest.

## Browser work

- 9222 Chrome = wopovod Studio/YouTube, IG, FB, Rumble. Brave = STACKSWOPO GAMES. Device ids change.
- 10-18 05:45 that Chrome restarted and came back signed in as "glorybinn" (ZB_Benji@icloud.com); the
  STACKSWOPO VODS account was not in Switch account any more. Posts, collabs and Studio work need it -
  only the owner can sign it in (never type passwords). The post tool switches channel when it can and
  sends one Discord note a day when it cannot.
- Collaborations (no API): Studio video edit -> Show more -> Collaboration -> Invite -> "StackswopoGames"
  -> Create link -> close popup -> Save; accept all in Brave at
  https://studio.youtube.com/channel/UCKj0xrFqNNXpBCl_B1shckA/collaboration/UCbS-_v6Y9AZmAAU3EpzLsvA .
  A daily scheduled task does this for new wopovod uploads.
- Posts tab: composer `#placeholder-area` -> `#contenteditable-root`; "Add an existing YouTube video" is a
  Google Picker iframe (docs.google.com/picker): `[role=option]` with the title -> Insert -> Post.
- Instagram caption edits only persist with real key events and a real click on Done.
- Never type passwords or card details; never hard-delete - move to `D:\AutoBleepPro-git\_to_delete`
  (gitignored; the owner empties it). Ask before spending money or accepting terms.

## Incidents worth remembering

- 10-17 22:43: a restart killed the full stream's YouTube upload 3 min in. YouTube kept a stub
  ("Processing will begin shortly", no thumbnail, Visibility Pending); the next run matched it by title
  and recorded it as done. Found 5 h later. Fix: upload_beat + restart scripts; the brain's stuck-stream
  check; the post tool alerts on "still processing after 3 h". Re-upload recipe: owner deletes the stub
  in Studio -> `main.py --forget "<file>" --forget-platform youtube` -> rename the file to carry its date
  (`<name> 10-17-26.ts`) and copy `censored\<name>_transcript*.json` + `_thumbnail.jpg` to the new name ->
  `main.py --file "<path>" --only youtube --keep-source` (not the watcher: its channel list is cached
  from startup and still held the stub). Then point old links at the new id (descriptions + the recap's
  comment). 10-17 is now H5-UgZr8OD0.
- 10-18 02:32: a test of the Chrome self-heal closed the real Chrome (now stubbed in conftest).

## Held clips and learning

- A clip held for slurs goes to Discord as a playable preview, numbered; "post held N" =
  `python -X utf8 tools/held_review.py --post N` (kids/gambling holds never lift).
- View learning runs after each recap (every 2 days); `main.py --learn` by hand.

## Open items (10-18)

- The 9222 Chrome must be signed back into STACKSWOPO VODS by the owner. A post retrier for H5-UgZr8OD0
  runs until ~22:00 10-18 (logs\community_post_today.log) and posts once it can.
- `_to_delete\` holds ~7.2 GB of leftovers (old _verify, browser profiles, rumble_redo, scratch) for the
  owner to empty. One-off scripts moved to `_archive\` (in git).
- Recaps 10-14/10-15 and the 10-16 weekly carry a "kids" caption; the owner chose to leave them public.
- The owner must do these themselves: sleep = Never (SETUP-SLEEP.bat), auto sign-in after a reboot if
  they want reboots unattended, delete the 4.6 GB backup `C:\AutoBleep\recording\Stackswopo rumble
  v7gx6ca edge.ts`.
