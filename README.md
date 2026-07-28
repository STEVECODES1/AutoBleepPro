# 🔇 AutoBleep Pro v2.0

> **AI-powered automatic profanity bleeper** — detect, review, and export clean videos in minutes.  
> 100% local. No uploads. No subscriptions. Free forever.

---

## ✨ What's New in v2.0

| Feature | v1 | v2 |
|---|---|---|
| Word-by-word review (uncheck words to keep) | ❌ | ✅ |
| Custom output folder | ❌ | ✅ |
| Batch folder processing | ❌ | ✅ |
| Beep sound presets (4 options) | ❌ | ✅ |
| Medium Whisper model (best accuracy) | ❌ | ✅ |
| Pinned dependency versions | ❌ | ✅ |
| MIT License | ❌ | ✅ |
| Memory-safe temp file cleanup | ❌ | ✅ |

---

## 🚀 Quick Start

### 1. Install
```bash
pip install -r requirements.txt
```
Or double-click **`INSTALL.bat`** on Windows.

> **First run** downloads the Whisper AI model (~74 MB for base). One-time only.

### 2. Launch
```bash
python autobleep_pro.py
```
Or double-click **`START_AUTOBLEEP.bat`**.

---

## 🎬 How to Use

### Single Video Mode
1. **Select Video** — click Browse Video, pick any MP4/MOV/AVI/MKV file
2. **Configure** — choose beep sound, AI model, custom words, output folder
3. **Analyze** — click the green button; AI transcribes and detects profanity
4. **Review** — a checklist of every detected word appears; **uncheck any word you want to KEEP**
5. **Export** — click Confirm & Export; your clean video is saved

### Batch Folder Mode (New!)
1. Switch to the **Batch Folder** tab
2. Select an input folder (all videos inside will be processed)
3. Optionally select a different output folder
4. Click **Process All Videos** — each file is processed automatically

---

## ⚙️ Settings Guide

| Setting | Options | Recommendation |
|---|---|---|
| **Censoring Method** | Beep Sound / Silence | Beep for YouTube/TikTok |
| **Beep Preset** | Classic TV, High Pitch, Low Buzz, Air Horn | Classic TV |
| **AI Model** | tiny / base / small / medium | base (speed) or small (accuracy) |
| **Custom Words** | Comma-separated list | Competitor names, brand terms |
| **Output Folder** | Any folder on your PC | Separate `output/` folder |

### Model Speed vs Accuracy

| Model | Speed | Accuracy | Best For |
|---|---|---|---|
| tiny | ⚡⚡⚡⚡ | ★★☆☆ | Quick previews |
| base | ⚡⚡⚡ | ★★★☆ | Daily use |
| small | ⚡⚡ | ★★★★ | Clear speech |
| medium | ⚡ | ★★★★★ | Accents / noisy audio |

---

## 🔧 How It Works

```
[Video File]
    │
    ▼
[Whisper AI] ──── word-level timestamps ────►  [Profanity Detector]
                                                        │
                                               [Word Review UI] ← user unchecks
                                                        │
                                               [Pydub: replace with beep/silence]
                                                        │
                                               [MoviePy: rebuild video]
                                                        │
                                                [Clean Video MP4] ✅
```

---

## 🐛 Troubleshooting

**`ModuleNotFoundError`**  
→ Run `pip install -r requirements.txt` again

**`ffmpeg not found`**  
→ Install ffmpeg and add to PATH:  
  Windows: `winget install ffmpeg`  
  Mac: `brew install ffmpeg`

**Processing is slow**  
→ Switch to `tiny` model, or check if your GPU is being used (CUDA)

**Word not being detected**  
→ Add it to the Custom Words field

**Video quality decreased**  
→ This is a re-encode; use `preset='slow'` in the code for better quality

---

## 📁 Project Structure

```
AutoBleepPro/
├── autobleep_pro.py       ← Main app (v2.0 — 400+ lines)
├── autoreel_gui.py        ← AutoReel (clip generator)
├── autoreel/              ← AutoReel modules
├── requirements.txt       ← Pinned dependencies
├── INSTALL.bat            ← Windows installer
├── START_AUTOBLEEP.bat    ← Launch AutoBleep Pro
├── START_AUTOREEL.bat     ← Launch AutoReel
├── LICENSE                ← MIT License
└── README.md              ← This file
```

---

## 📄 License

MIT — free to use, modify, and distribute. See [LICENSE](LICENSE).

---

**Built by [STEVECODES1](https://github.com/STEVECODES1) • Contributions welcome — open an Issue or PR!**
