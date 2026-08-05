# Short-form clipping + cross-platform posting — architecture plan

Status: **plan + safety layer implemented. Publishers deliberately not built yet.**

## What already exists (reuse, don't rebuild)

| Capability | Where | State |
|---|---|---|
| Highlight detection | `autoreel/highlights.py` → `HighlightScorer.select_clips()` | Built, tested |
| Vertical reframing | `autoreel/face_tracking.py` → `FaceTracker` | Built, tested — **opt-in only**, see `crop_strategy.py` |
| Crop strategy | `autoreel/crop_strategy.py` | Built, tested — centre by default |
| Clip windows from transcript | `auto_uploader/utils/content_optimizer.py` → `clip_windows()` | Built |
| Transcript + word timings | `auto_uploader/utils/censor.py` (cached) | Built |
| Profanity / slur muting | `autoreel/compliance.py` | Built, tested |
| ffmpeg encode / mux | `auto_uploader/utils/ffmpeg_tools.py` | Built |
| Long-form upload + dedup + retry | `auto_uploader/main.py` | Built, in production |
| Discord / X / Reddit announce | `auto_uploader/utils/social_promoter.py` | Built (X/Reddit unconfigured) |

The clipping half is largely done. What is missing is **safe publishing**, which is
where all the account risk lives — so that is what gets built first.

## Target flow

```
stream VOD
   │
   ├─ transcript (cached, already produced by the censor pass)
   ├─ HighlightScorer.select_clips()        → candidate moments
   ├─ trim + FaceTracker vertical reframe   → 9:16 clip
   ├─ burn captions from word timings       → captioned clip
   │
   └─ JobQueue  ──PublishGuard──►  publisher
                     │
                     ├─ kill switch?        → stop everything
                     ├─ daily cap reached?  → defer to tomorrow
                     ├─ min spacing?        → defer
                     ├─ circuit breaker?    → platform disabled
                     └─ manual approval?    → park, never auto-post
```

Nothing reaches a platform without passing the guard. The guard is the only
component that is allowed to say "yes".

## Platform reality check

This drives the whole design, so it is stated plainly rather than assumed.

| Platform | Approved route | Real limit | Decision |
|---|---|---|---|
| **Instagram Reels** | Instagram Graph API — Content Publishing | **25 posts / rolling 24 h**, enforced by Meta | Automate, cap at 5/day |
| **Facebook Page** | Facebook Graph API — Page video publish | Page-level, generous | Automate, cap at 5/day |
| **X / Twitter** | X API v2 | Free tier is ~17 writes/24 h; realistically needs a paid tier | Automate, cap at 3/day |
| **Reddit** | PRAW | No hard API cap, but **subreddit spam filters and sitewide anti-spam are the real limit** | Automate, cap at 1/day, ships parked |
| **Facebook Groups** | — | Group publishing permissions were withdrawn from the Graph API | **Manual approval only, enforced in code** |

The difference between those last two rows is the whole distinction the guard
draws:

- **Facebook Groups have no approved route at all.** That is a property of the
  platform, not of any account, so the block lives in `ALWAYS_MANUAL` and no
  config flag can lift it. No group publisher will be written.
- **Reddit has a supported API and a documented way to post.** The risk there is
  per-account reputation, which is an account fact, not a platform one — so it is
  a config decision (`manual_approval_only`). It ships `true`, and can be turned
  off once a healthy account is configured. Either way it stays behind the same
  cap, spacing and circuit breaker as everything else.

  Reddit posting reads credentials for a **named account** (`reddit_account`,
  default `"2"` → `REDDIT_CLIENT_ID_2` and friends). The primary `REDDIT_*`
  variables are never consulted for it, so the state of any other Reddit account
  cannot block this integration. Reddit expects one script app per account, so
  the alternate account needs its own app registered under itself.

**There is no proxy support and none is planned.** Nothing in this design routes
around a documented rate limit — the caps are the mechanism, not an obstacle to
work around.

## Modules

### Built now

**`auto_uploader/publish_guard.py`** — the only thing that authorises a post.

- **Global kill switch**: `posting.enabled: false` in config, *or* a
  `STOP_POSTING` file anywhere the tool can see it. The file form matters — it
  stops a running `--watch` without editing config or finding the process.
- **Per-platform daily caps**, counted over a **rolling 24 h window**, not
  midnight-to-midnight, because that is how Meta actually enforces.
- **Minimum spacing** between posts per platform: bursts look automated.
- **Circuit breaker**: N consecutive failures on a platform disables it until
  manually reset. A platform failing repeatedly is the signal that something is
  wrong at the account level, and retrying into that makes it worse.
- **Manual-approval platforms**: `ALWAYS_MANUAL` platforms are never authorised
  automatically whatever the config says; per-platform `manual_approval_only`
  parks the rest for a human until deliberately switched off.
- **One implementation.** `can_post()`/`record_result()` and
  `check()`/`record_post()` are two shapes over the same checks. A second guard
  with its own copy of the rules is a second place for one of them to be missing,
  which is how a platform ends up unguarded.

**`auto_uploader/job_queue.py`** — retry-safe work tracking.

- JSON-backed with atomic writes (temp file + `os.replace`), so a crash or Ctrl+C
  mid-write cannot corrupt the queue.
- States: `pending → in_progress → done | failed | blocked | needs_approval`.
- Deduplicated on (platform, clip): re-queuing the same clip is how a clip gets
  posted twice, so it returns the existing job. Completed jobs are kept, because
  that record is the evidence a clip already went out.
- Per-job attempt counts with exponential backoff and a max-attempt ceiling, so a
  permanently broken job stops consuming quota instead of retrying forever.
- Crash recovery: `in_progress` jobs older than a lease timeout are returned to
  `pending`, so a killed process doesn't strand work.
- A guard refusal is `blocked`, not `failed`: it consumes no attempt and carries
  its own retry time, so a capped clip comes back on its own tomorrow.

**`autoreel/crop_strategy.py`** — which crop a vertical clip gets.

  **The default is centre, not faces.** The existing
  `FaceTracker` was built for talking-head footage; pointed at GTA RP it
  chases whatever NPC face wanders through frame and the crop jitters
  around the scene. Since ~95% of this channel is gameplay, the default is:

  | Strategy | When | Behaviour |
  |---|---|---|
  | `center` (default) | Gameplay | Fixed 9:16 centre crop. The HUD, crosshair and action all sit centre-screen in GTA, so a static crop keeps them and never jitters. |
  | `motion` | Busy action | Crop follows a smoothed centroid of frame-to-frame motion. Costs a pass over the video. |
  | `face` | Face-cam / IRL only | The existing `FaceTracker`. Opt-in. |

  `center` is not a placeholder for something better — for a centre-framed
  game it is genuinely the right answer, and it's also the cheapest and the
  only one that can't drift. A misspelled strategy raises rather than falling
  back, because "centre" quietly becoming something else is how a channel's
  clips end up cropped a way nobody chose.

### Planned, not yet built

- `clip_maker.py` — trim + reframe + caption-burn, driven by `crop_strategy`.
- `publishers/instagram.py`, `publishers/facebook.py` — Graph API publishers.
  **Blocked on credentials**: a long-lived Page access token, the IG Business
  account ID, and `instagram_content_publish` + `pages_manage_posts` scopes.
  Writing these against no token means shipping untested code that posts to a
  27 K-follower account, which is the wrong trade.
- `publishers/x.py` — thin wrapper over the existing `tweepy` path in
  `social_promoter.py`, once a tier with usable write limits is in place.

Reddit and Facebook Groups get **no publisher at all** — they are approval-only
by design, so the queue parks them and a human posts.

## Config additions

```jsonc
"posting": {
  "enabled": false,                    // master switch, OFF until publishers exist
  "kill_switch_file": "./STOP_POSTING",
  "state_path": "./posting_state.json",
  "platforms": {
    "instagram": { "enabled": false, "daily_cap": 5, "min_minutes_between": 45 },
    "facebook":  { "enabled": false, "daily_cap": 5, "min_minutes_between": 45 },
    "x":         { "enabled": false, "daily_cap": 3, "min_minutes_between": 90 },
    "reddit":    { "enabled": false, "daily_cap": 1, "min_minutes_between": 720,
                   "manual_approval_only": true },   // config choice, can be turned off
    "facebook_group": { "enabled": false, "manual_approval_only": true }
                                                     // ignored - blocked in code
  },
  "circuit_breaker": { "consecutive_failures": 3 }
},
"clips": {
  "crop_strategy": "center",     // center | motion | face | auto
  "content_kind": "gameplay"
},
"features": {
  "social_promoter": { "reddit_account": "2", ... }  // -> REDDIT_*_2 in .env
}
```

Every platform ships `enabled: false`. Nothing can post until it is deliberately
turned on, one platform at a time.

The caps are well under the platform ceilings (5/day against Instagram's 25)
because the binding constraint is not the API limit — it is how an account looks
to spam classifiers. Room to raise them exists; starting there does not.

## Testing

Stop conditions are the part that must not regress, so they are tested first and
hardest — `tests/test_publish_guard.py` and `tests/test_job_queue.py` cover the
kill switch (both forms), rolling-window caps, spacing, the circuit breaker,
manual-approval enforcement (both kinds), atomic writes, lease recovery, attempt
ceilings, and the fact that enabling Reddit does not disable its limits.
`tests/test_publishers.py` covers credential gating per named Reddit account and
the centre-crop default.

## Order of work

1. ✅ Safety layer — guard + queue + tests. No network, no credentials, testable.
2. Clip maker, wiring the existing highlight/reframe code. Local-only.
3. One publisher (Instagram), behind the guard, `enabled: false`, dry-run first.
4. Facebook, then X — same pattern, one at a time.
5. Reddit: only once a separate, healthy account is configured and
   `manual_approval_only` is deliberately turned off. Groups: never automated.
