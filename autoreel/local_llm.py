"""
A model on this PC's own graphics card, through Ollama.

WHY
---
Every other provider here is somebody else's computer: a free tier that
runs out by mid-afternoon (Gemini's strong model gives twenty requests a
day), a shared pool that answers half the time (NVIDIA), or an account
that bills. The RTX in this machine sits idle between Whisper passes.
Qwen3-VL 4B on it reads the frames and the words, answers in seconds,
and has no quota and no bill.

HOW
---
Ollama's own API on localhost (http://127.0.0.1:11434): /api/chat with
the frames as base64 `images`, `format: json` so the reply parses, and
`think: false` so a thinking model answers instead of deliberating. If
Ollama is installed but not running, it is started once.

Settings in .env, all optional: OLLAMA_MODEL (default qwen3-vl:4b),
OLLAMA_HOST, OLLAMA_DISABLED=1 to leave the GPU out of it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import List, Optional, Tuple

DEFAULT_MODEL = "qwen3-vl:4b"
# Room for 16 frames and a long candidate list. More costs VRAM the card
# shares with Whisper.
CONTEXT = 16384
# How long the model stays loaded after a call. Short, so the card's
# memory goes back to Whisper and the music guard between clip passes.
KEEP_ALIVE = "5m"
_TIMEOUT = 300
_READY_TTL = 60.0
_STATE: dict = {"checked": 0.0, "ready": False, "started": False}


def host() -> str:
    return (os.environ.get("OLLAMA_HOST", "").strip()
            or "http://127.0.0.1:11434").rstrip("/")


def model_name(configured: str = "") -> str:
    return (configured or os.environ.get("OLLAMA_MODEL", "").strip()
            or DEFAULT_MODEL)


def _get(path: str, timeout: float = 2.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(host() + path, timeout=timeout) as reply:
            return json.loads(reply.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def _start_server() -> None:
    """Start Ollama once if it is installed and not running."""
    if _STATE["started"]:
        return
    _STATE["started"] = True
    exe = shutil.which("ollama") or os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe")
    if not exe or not os.path.isfile(exe):
        return
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags)
    except OSError:
        return
    for _ in range(20):
        time.sleep(0.5)
        if _get("/api/version") is not None:
            return


def ready(model: str = "") -> bool:
    """Ollama is up here and has the model. Cached for a minute, so the
    provider list can ask on every pass without a request each time."""
    if os.environ.get("OLLAMA_DISABLED", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return False
    now = time.time()
    if now - _STATE["checked"] < _READY_TTL:
        return _STATE["ready"]
    tags = _get("/api/tags")
    if tags is None:
        _start_server()
        tags = _get("/api/tags")
    wanted = model_name(model)
    names = {str(m.get("name", "")) for m in (tags or {}).get("models") or []}
    have = wanted in names or (":" not in wanted
                               and f"{wanted}:latest" in names)
    _STATE.update(checked=now, ready=bool(have))
    return _STATE["ready"]


def chat(system: str, prompt: str, model: str = "",
         images: Optional[List[str]] = None, json_reply: bool = True,
         timeout: int = _TIMEOUT) -> Tuple[str, str]:
    """(reply text, why not). images: base64 JPEG/PNG strings."""
    message = {"role": "user", "content": prompt}
    if images:
        message["images"] = list(images)
    payload = {
        "model": model_name(model),
        "messages": [{"role": "system", "content": system}, message],
        "stream": False,
        "think": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0.4, "num_ctx": CONTEXT},
    }
    if json_reply:
        payload["format"] = "json"
    request = urllib.request.Request(
        host() + "/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            data = json.loads(reply.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        if exc.code == 400 and "think" in detail.lower():
            # An older Ollama, or a model that refuses the switch: ask
            # again without it rather than lose the answer.
            payload.pop("think", None)
            return _retry(payload, timeout)
        return "", f"HTTP {exc.code}: {detail}"
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        _STATE["checked"] = 0.0           # look again next time
        return "", f"could not reach Ollama ({exc})"
    text = _reply_text(data)
    return (text, "") if text.strip() else ("", f"empty reply: {str(data)[:200]}")


def _reply_text(data) -> str:
    """The answer. qwen3-vl's default tag is the thinking build: with
    think off and a JSON format it puts the whole answer in `thinking`
    and leaves `content` empty (measured, Ollama 0.32) - so an empty
    content with a thinking field IS the reply."""
    message = (data or {}).get("message") or {}
    return (message.get("content") or "").strip() or \
        (message.get("thinking") or "").strip()


def _retry(payload: dict, timeout: int) -> Tuple[str, str]:
    request = urllib.request.Request(
        host() + "/api/chat", data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            data = json.loads(reply.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return "", f"Ollama: {exc}"
    text = _reply_text(data)
    return (text, "") if text.strip() else ("", "empty reply")


def parts_to_chat(parts: list) -> Tuple[str, List[str]]:
    """Gemini-shaped parts (text and inline_data) -> (text, images).

    Ollama takes a message's images as one list, not interleaved with the
    text, so each frame's place in the text is marked "[image N]" - the
    model can still tell which candidate a frame belongs to."""
    texts, images = [], []
    for part in parts or []:
        if "text" in part:
            texts.append(str(part["text"]))
        elif "inline_data" in part:
            data = (part.get("inline_data") or {}).get("data")
            if data:
                images.append(data)
                texts.append(f"[image {len(images)}]")
    return "\n".join(texts), images
