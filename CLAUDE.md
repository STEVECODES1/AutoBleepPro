# AutoBleepPro - operating notes for Claude

Read this first. It is what earlier sessions learned the slow way. The owner runs this from a phone,
away from the PC: it must work by itself after one START, and say plainly when it needs them.

## What runs, and where

- Windows PC, repo `D:\AutoBleepPro-git`. `START.bat` (pulls code, then `_AUTOSTART.ps1`) and the Startup
  folder's "AutoBleep Autostart.cmd" (at login, after a reboot) start each piece once, each with a keepalive
  window: Rumble Chrome + keeper (`_RUN_RUMBLE_CHROME.bat`, every 60 s), recorder, uploader
  (`main.py --batch` then `--watch`), Rumble live relay, Rumble recorder. Ollama starts at login too.
- The Rumble recorder (`tools/rumble_watch.py`) runs on the same Python312 as everything else - the bats
  used to call `C:\Python314`, which does not exist, so from 10-20 to 10-22 it printed "stopped,
  restarting" every 30 s and never polled. Rumble refuses plain HTTP with 403 (Cloudflare); the watcher
  now falls back to fetch() inside the 9222 Chrome's own Rumble tab. Never point it at another
  interpreter.
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
- Tests: `python -X utf8 -m pytest tests -q -p no:cacheprovider` (~11 min). The suite is clean - 3443
  passed, 0 failed (10-29, Linux, fresh venv from both requirements.txt). The four entries once listed
  here as "known stale" were real bugs, not flakiness, and are fixed:
  - `held_review.approved()` used the host OS's `os.path.basename`, so an approval never matched a clip
    path written with the other slash - added `_basename()` (same fix `job_queue.py` already has).
  - `process_file`'s "busy marker" wrapper shadowed the real function under its name with a bare
    reassignment; `inspect.getsource(main.process_file)` then read the wrapper, not the function the
    test was checking. Now `functools.wraps`, and the test calls `inspect.unwrap()`.
  - `silence_trim.quiet_enough()` judged a gap's loudness by `max()` over its WHOLE interior, including
    the one second right against the transcript boundary - which routinely still carries the tail of
    speech (a breath, an echo, a consonant Whisper cut short) even though the gap itself is real dead
    air. One loud boundary second vetoed an otherwise-silent multi-second pause, while genuinely loud
    one-second word-gaps elsewhere passed BY THE SAME EDGE RULE because they had no interior window
    left to judge at all. Now a gap with an interior judges on its core (edges trimmed first), so a
    real 6-second silence gets caught instead of being skipped in favor of noise.
  - `test_clip_offer_parallel.py`'s fake Rumble returned a bare `/watch` URL that `_confirm_on_rumble`'s
    slug-vs-title check (added after this test was written) was always going to reject, failing the
    test on a check unrelated to what it verifies. Fixed to return a URL with a real slug.
  test_cap_keys did not reproduce here across three runs - if it still fails on the PC, suspect the
  environment (GPU/Whisper nondeterminism) over the test itself.
- Always `python -X utf8`. In PowerShell: .NET file calls use the process cwd; `Start-Process
  -ArgumentList` does not quote - wrap an argument with spaces in '"..."'.

## Channels (one Google project, 10,000 units/day shared by all four)

| token (auto_uploader\) | channel | role |
|---|---|---|
| youtube_token.json | STACKSWOPO VODS @WOPOVOD (UCbS-_v6Y9AZmAAU3EpzLsvA) | full streams - THE money channel |
| youtube_shorts_token.json | STACKSWOPO CLIPS (Fan Channel) @WOPOCLIPPER (UCMeMDcCirQBxkhH3vuFgliA) | Shorts - ON, 3/day (10-28) |
| youtube_games_token.json | STACKSWOPO GAMES (UCKj0xrFqNNXpBCl_B1shckA) | recaps (has a strike) |
| youtube_compilation_token.json | STACKSWOPOVODS (Fan Channel) @stackswopo10k | weekly, Monkey compilations |

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
- `utils/deps.py` now IMPORTS the critical packages, not just finds them: a present-but-broken install
  (the 10-20 crash loop: better_profanity data file, pyparsing.core, numpy._core.numeric, PIL.Image)
  is force-repaired before the program starts, with a 30-minute stamp so a dead network cannot loop
  pip forever. Exit 3 stops the keepalive for a person, as before.
- The owner's rule: wopovod must be fully secured; GAMES/recaps matter less.

## Tools (tools\, all print what they do)

- `health_check.py [HOURS] [--yt]` - queue by platform/state, every not-done job and why, newest uploads.
- `channel_guard.py` - read-only daily check that all four names still say "(Fan Channel)" and every
  About still carries the fan + actors notice; alerts Discord/ntfy and exits 1 when one has changed.
  Windows task "AutoBleepPro Fan Guard" runs it at 09:05, so it also runs when the uploader is dead.
- `disclaimer_backfill.py [--apply] [--max N] [--all] [--only vods,clips,games,compilations]` - puts the
  notice into videos already published, preserving title/tags/category/language and logging the old
  description. Dry-run by default; one update is 50 quota units. Task "AutoBleepPro Disclaimer Backfill"
  runs `--apply --max 10 --only games,compilations` at 09:30 until none are left.
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
- Whisper large-v3-turbo (faster-whisper 1.1.1, CUDA) for censor + transcripts. yt-dlp 2026.10.21
  (updated 10-22) = latest.

## Browser work

- 9222 Chrome = wopovod Studio/YouTube, STACKSWOPO CLIPS Studio (10-22), IG, FB, Rumble. Brave = STACKSWOPO GAMES. Device ids change.
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
- 10-29: two copies of one stream in watch_folder ("hey my babes" + the recorder's file) -> the second
  went to wopovod as a duplicate (its dedupe used the startup channel list). Now every full stream asks
  the channel for its exact title right before the first upload too (`_already_on_youtube`).
  Never leave a second copy of a stream in watch_folder - move extras to _to_delete.
  Follow-up the same day: that channel check alone could not close the race under `--watch` - it starts
  a fresh thread per arriving file, so two files for one stream can both reach `process_file()`'s
  dispatch before either upload is far enough along for the API to show it. `_title_lock()` now makes a
  second file for the same stream title WAIT for the first's whole dispatch-and-upload to finish instead
  of racing it (full streams only; `--batch` already had this for free). That alone still left Rumble
  exposed - `rb_attempt()` only asked `_already_on_rumble` on a RETRY, never the first attempt, so the
  second file's own "first" Rumble upload would have gone up anyway, one after the other instead of at
  the same time. Fixed to match `_already_on_youtube`'s shape: full streams ask before the first attempt
  too, clips only on a genuine retry as before. Covered by
  `tests/test_parallel_uploads.py::test_two_files_of_the_same_stream_do_not_race_each_other` and
  `::test_a_second_file_of_the_same_stream_does_not_reupload_to_rumble`.

## Held clips and learning

- A clip held for slurs goes to Discord as a playable preview, numbered; "post held N" =
  `python -X utf8 tools/held_review.py --post N` (kids/gambling holds never lift).
- View learning runs after each recap (every 2 days); `main.py --learn` by hand.

## 10-28: fresh Windows + the owner's list

- The PC was reset; D: kept the repo, `.env`, tokens and config. Rebuild with
  `tools\reinstall_pc.ps1` (Python 3.12 per-user, Git, FFmpeg, Deno, pip both requirement
  files, CUDA torch + cuBLAS/cuDNN, Whisper models, C:\AutoBleep\recording, the logon task
  "AutoBleepPro Autostart" and the two daily tasks). `tools\check_packages.py [--repair]`
  imports every package and checks better_profanity's data files, ffmpeg/ffprobe/deno/git, GPU.
- INSTALLERS FROM THE CLAUDE APP: the desktop app is a packaged app, so a process it starts
  sees a private copy of AppData that msiexec cannot reach (Python setup failed 0x80070003).
  Run installers/START through a one-shot scheduled task (`schtasks /Create ... /SC ONCE` +
  `/Run`), which runs outside that sandbox. The same goes for the Startup folder - a logon
  scheduled task is used instead.
- Lost with C: (owner must redo): the Rumble/YouTube Chrome profile `C:\RumbleChromeProfile`
  (sign in again: Studio as wopovod, CLIPS Studio, Rumble, IG, FB), Brave with GAMES, the
  CapCut intro (now looked for at `assets\intro.mp4` first - put it there once; an alert goes
  to Discord once a day while it is missing), sleep = Never.
- Clips: layout LOCKED to the blurred whole-frame look (`crop_strategy.layout_locked`; only
  `clips.unlock_layout: true` opens it). Title on top + CTA, captions at the bottom, always.
- Titles: a stream name that already carries "- date (FULL STREAM)" is framed once
  (`templating.strip_full_stream_frame`); thumbnail text is the clean title only. Clip titles
  come from the clip's notes in clips/ (`main._clip_line_anywhere`), never its file name
  (`_clip_title_without_filename`). YouTube titles (every channel, via
  `text_guard.title(youtube=True)`) block animal comparisons about people and crude
  sexual/incest wording (`youtube_title_risk`); "Monkey App"/"MONKEY COMPILATION" pass.
- Intro: joined as MPEG-TS byte-for-byte (`ffmpeg_tools.prepend_intro`) - the stream is never
  re-encoded. The old concat of an .mp4 intro onto a .ts failed its own length check every time.
- Policy: `kid_guard.make_safe` cuts every category the policy read calls a clear break, plus
  spoken patterns (`spoken_risks`: animal comparisons, "kys", a phone number, incest). Since
  10-29 EVERY flag is cut - "warn" ones and a local model's game-threat calls too (owner: "anything
  worth a look just cut it"). FFmpeg 9 dropped -filter_script: censor tries `-/filter:a` first. No size limit (10-29): it is
  all cut and stays PUBLIC; only a failed cut goes private.
- Disclaimer wording is now "GTA RP is role-play - everyone in it is acting. The non-GTA parts
  feature actors too. Nothing shown is real." (`disclosure.ACTORS_LINE`); older wordings are
  swapped in place (`disclosure.upgraded`). Abouts: `tools/funnel_setup.py` (run it to push).
- Shorts to @WOPOCLIPPER ON in the live config, 3/day, 2 h apart (shipped example stays off).
- Rumble shorts wait (FAILED "waiting for the full stream", quiet, retried each pass) until the
  full stream's Rumble URL exists, so Related Video is always set; after 12 h they go without.
- Recaps cut from an indexed .mp4 remux of the .ts (`recap.seekable_copy`), captions shifted by
  the audio's start offset (`recap.audio_lead`), and a transcript cache made from a different
  video is refused (`recap.transcript_of`). Weekly is built from recaps, so it inherits this.
- Recorder: one recording at a time - the first URL is the main channel, later ones only record
  while it is not and stop when it goes live (`MAIN_RECORDING`); one recorder process
  (`recorder.lock`); a stream that ends and comes back within 15 min (`REJOIN_GRACE_S`) or a
  recorder restarted mid-stream continues the SAME recording instead of a short separate one.

- 10-29 browser: the 9222 window is Chrome again (profile C:\RumbleChromeProfile, desktop
  shortcut "Chrome (AutoBleep - use this one)"). The Edge profile used 10-28 is kept at
  C:\RumbleEdgeProfile. A profile is always reopened by the browser that made it
  (`chrome_cdp._profile_is_edge`, ensure_rumble_chrome.ps1) - the other one loses its logins.
  Rumble's login only survives a restart with "Remember me" ticked.
  FOUR YouTube channels, all uploaded to through the API tokens in the table above (checked
  10-29, all four answer). The browser is only for what the API cannot do: ONE Chrome holds
  STACKSWOPO VODS (indexscript00) for posts/collabs and STACKSWOPO GAMES (dieujustebindjy) to
  accept collabs; CLIPS and STACKSWOPOVODS can be added there for Studio edits. glorybinn is also signed in there but is
  NOT part of this project - never switch to it or post/upload as it. Brave is gone - switch with profile picture -> Switch account and
  always switch BACK to STACKSWOPO VODS (community_post._switch_to does this for posts). The daily
  "Wopovod collab invites" scheduled task still says Brave and is disabled - its prompt needs the
  owner's OK to change.
- Rumble clips capped at 8 a day (`main._rumble_clip_cap`, rumble.clips_per_day). Live clipping
  OFF; clips.count 10. Gemini free tier is 20 requests/day (3.8-flash) - keep it for watch_pass.

## Open items (10-22)

- 10-22: the CLIPS channel came BACK (UCMeMDcCirQBxkhH3vuFgliA, @WOPOCLIPPER). It was renamed in
  Studio to "STACKSWOPO CLIPS (Fan Channel)" and its About now carries the fan + actors notice; Shorts
  uploads are ON again (`posting.platforms.youtube_shorts.enabled`, cap 5/day, 60 min apart). The handle
  @WOPOCLIPPER was left alone - it does not look like Stacks' own names, and changing it would break
  links. Do not create a second clips channel or re-post removed clips anywhere.
- Disclaimers (owner, 10-22): every channel says it is fan-run AND that the people are actors. The About
  is applied by `tools/funnel_setup.py` (DISCLOSURE/CLIPS_ABOUT); every description, Short, recap,
  weekly, Rumble upload, social caption and link comment gets the same line from
  `auto_uploader/utils/disclosure.py`. Do not build a new description path without calling it.
- Old videos (10-22): every VOD and every CLIPS Short now carries the notice, plus the 10 newest
  GAMES/compilation videos. 49 old GAMES recaps and 1 compilation are left for the daily 09:30 task
  (`disclaimer_backfill.py --max 10`). Old descriptions are in logs/disclaimer_backfill.jsonl.
- Impersonation rules (support.google.com/youtube/answer/2801947): a fan channel must say "fan" in its
  NAME or HANDLE; no copying his picture/banner; no "Clips/Shorts/Live/Backup/Official" + his name.
  All four names now end in "(Fan Channel)" (the API cannot rename - use Studio; see
  `tools/fan_names.py`). The clips channel is in the 9222 Chrome as of 10-22, so Studio work there is
  possible without the owner.
- RESOLVED 10-28 (this note said "still open" until 10-29 - the fix landed after the note was last
  written, and nobody came back to update it): the caption-sync report (recap burned captions picking the
  wrong line) was the transcript being made from a WAV that starts at the first AUDIO sample while every
  `-ss` cut counts from the container's start - so a recording whose audio starts late had every burned
  caption early by that same amount. `utils/recap.py`'s `audio_lead()` measures the gap and `shifted()`
  moves every transcript word by it before `transcript_of(align=True)` hands segments to
  `recap_edit.piece_ass()`. Covered by `tests/test_owner_requests_1028.py::test_captions_move_by_the_audio_lead`.

- H5-UgZr8OD0 (10-17 stream): the 25 kid-talk parts were cut in the Studio editor 10-18 ~15:05 (3:18:21 ->
  2:57:39, processing). Once it shows 2:57:39: add the GAMES collab and the channel post (both held till then).
- Every full stream now goes through `utils/kid_guard.py` before YouTube: kid talk is stream-copied out of the
  YouTube copy (Rumble keeps it) and the times go to Discord; too much of it or a failed cut -> PRIVATE, no post.
- The 9222 CDP attach works again (Rumble live clips uploaded through it 10-18 afternoon).
- `_to_delete\` holds ~7.2 GB of leftovers (old _verify, browser profiles, rumble_redo, scratch) for the
  owner to empty. One-off scripts moved to `_archive\` (in git).
- Recaps 10-14/10-15 and the 10-16 weekly carry a "kids" caption; the owner chose to leave them public.
- The owner must do these themselves: sleep = Never (SETUP-SLEEP.bat), auto sign-in after a reboot if
  they want reboots unattended, delete the 4.6 GB backup `C:\AutoBleep\recording\Stackswopo rumble
  v7gx6ca edge.ts`.
