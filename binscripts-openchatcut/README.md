# BinScripts — OpenChatCut integration (AutoBleepPro companion)

Automated editing pipeline for the **BinScripts** channel (YouTube `@BinScript`, Rumble `BinScripts`),
styled after `@StackswopoGaming`. Main content: **Monkey app**; secondary: **GTA 5**.

## The stack

| Tool | Role |
|---|---|
| **OpenChatCut** (`0xsline/OpenChatCut`) | Creative editor — transcript cuts, word-pop captions, zooms, reactions, subject/face-aware reframe, per-platform export |
| **AutoBleepPro** (this repo) | Audio cleanup — faster-whisper word-level profanity detection → beep/silence → clean MP4 + `--srt`/`--txt` transcripts |
| **fal.ai** | Generated assets — thumbnails, SFX, reaction images |
| **Cerebras** (LLM) | Keeps OpenChatCut's agent alive via an OpenAI-compatible endpoint |

## What's in this branch

These files belong in a checkout of `0xsline/OpenChatCut`, under its `src/` tree. They are **not**
standalone — they import OpenChatCut's editor/reframe/captions modules.

```
binscripts-openchatcut/
├── apply.patch                      ← full git diff; apply to an OpenChatCut checkout
├── src/
│   ├── reframe/
│   │   ├── safe-focus.ts            ← safe-area-aware subject/face focal reframe (fixes vertical crop)
│   │   ├── safe-focus.verify.ts
│   │   └── geometry-focus.ts        ← focus modes + framing clamp + tracked focal path
│   └── workflows/binscripts/
│       ├── presets.ts               ← youtube-long / youtube-shorts / rumble-regular / rumble-shorts
│       ├── compiler.ts              ← edit-plan → editor reducer actions (cuts, bleeps, reactions, captions, zooms, SFX)
│       ├── resolvers.ts             ← AutoBleepPro + fal.ai resolver boundaries
│       ├── autobleep-cli.ts         ← concrete `python cli.py` subprocess runner
│       ├── fal-client.ts            ← concrete fal.run HTTP client
│       └── …verify.ts               ← tests (5 suites)
```

## Apply to OpenChatCut

```bash
git clone https://github.com/0xsline/OpenChatCut.git
cd OpenChatCut
git apply /path/to/binscripts-openchatcut/apply.patch
# or copy the src/ files over the matching paths manually
```

## Run the tests

```bash
# in the OpenChatCut checkout (needs `npm i` first)
npx tsx src/reframe/safe-focus.verify.ts
npx tsx src/reframe/geometry-focus.verify.ts
npx tsx src/workflows/binscripts/binscripts.verify.ts
npx tsx src/workflows/binscripts/compiler.verify.ts
npx tsx src/workflows/binscripts/adapters.verify.ts
```

## Configuration (server-side `.env`, gitignored — never commit keys)

```bash
# fal.ai generated assets
FAL_KEY=***

# LLM agent via Cerebras (OpenAI-compatible) — avoids the OpenAI billing error
LLM_PROVIDER=openai
LLM_OPENAI_BASE_URL=https://api.cerebras.ai/v1
LLM_OPENAI_API_KEY=***
LLM_OPENAI_MODEL=qwen-3.8-27b
LLM_OPENAI_API_MODE=chat
```

## End-to-end flow

1. `python cli.py raw.mp4 -o clean/ --srt --txt` → censored video + transcript
2. Import clean video + SRT into OpenChatCut
3. OpenChatCut does the Stackswopo-style edit + per-platform reframe
4. fal.ai for thumbnails / SFX / reactions

## Status

- ✅ 5 verify suites passing
- ✅ AutoBleepPro CLI adapter + fal HTTP client wired and unit-tested
- ⚠️ fal.ai account needs a top-up (`User is locked. Reason: TOP_UP`)
- ⏳ End-to-end run on real footage pending (needs footage + `npm i` + Python/ffmpeg)
