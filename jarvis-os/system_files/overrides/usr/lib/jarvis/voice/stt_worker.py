#!/usr/bin/env python3
"""Offline speech-to-text worker (run with the private voice virtualenv).

Reads raw 16 kHz mono PCM from ``arecord`` (which talks to PipeWire through
ALSA) and writes newline-delimited JSON events to stdout. The assistant backend
owns this process and forwards the events to the UI.

Commands on stdin (one per line): PTT_ON, PTT_OFF, WAKE_ON, WAKE_OFF, STOP.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time

# The worker is executed with the private venv python, so the repo package is
# not necessarily importable. Add its own directory to sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import RECORD_BIN, load_config, stt_model_path  # noqa: E402

SAMPLE_RATE = 16000
CHUNK = 4000


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def clean(text: str, wake_word: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip().lower())
    text = re.sub(r"^(hey|hi|ok|okay)\s+", "", text)
    text = re.sub(rf"\b{re.escape(wake_word.lower())}\b", "", text)
    return re.sub(r"[^a-z0-9 _.@:+/-]", "", text).strip()


def clean_light(text: str, wake_word: str) -> str:
    """Keep shell punctuation so 'run echo hi=1' survives recognition."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = re.sub(r"(?i)^(hey|hi|ok|okay)\s+", "", text)
    return re.sub(rf"(?i)\b{re.escape(wake_word)}\b\s*", "", text).strip()


def main() -> int:
    cfg = load_config()
    wake_word = str(cfg["wake_word"])
    state = {"wake": bool(cfg["wake_enabled"]), "ptt": False, "stop": False}

    def commands() -> None:
        for line in sys.stdin:
            line = line.strip().upper()
            if line == "PTT_ON":
                state["ptt"] = True
            elif line == "PTT_OFF":
                state["ptt"] = False
            elif line == "WAKE_ON":
                state["wake"] = True
            elif line == "WAKE_OFF":
                state["wake"] = False
            elif line == "STOP":
                state["stop"] = True
                break

    threading.Thread(target=commands, daemon=True).start()

    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel
    except ImportError:
        emit({"type": "error", "message": "vosk is not installed; run 'jarvis-voice setup'"})
        return 2

    model_path = stt_model_path(cfg)
    if not model_path.is_dir():
        emit({"type": "error", "message": f"speech model missing at {model_path}; run 'jarvis-voice setup'"})
        return 3

    if not os.path.exists(RECORD_BIN):
        emit({"type": "error", "message": "arecord not found; install alsa-utils"})
        return 4

    SetLogLevel(0)
    model = Model(str(model_path))
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)
    recognizer.SetWords(True)

    emit({"type": "state", "listening": True, "mode": "wake" if state["wake"] else "continuous"})
    proc = subprocess.Popen(
        [RECORD_BIN, "-q", "-t", "raw", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=CHUNK,
    )
    last_partial = ""
    last_partial_at = 0.0
    try:
        while not state["stop"]:
            data = proc.stdout.read(CHUNK) if proc.stdout else b""
            if not data:
                break
            raw = ""
            if recognizer.AcceptWaveform(data):
                raw = clean_light(json.loads(recognizer.Result()).get("text", ""), wake_word)
                text = clean(raw, wake_word)
                if state["ptt"]:
                    state["ptt"] = False
            else:
                text = ""
            if text:
                emit({
                    "type": "transcript",
                    "text": text,
                    "raw": raw,
                    "wake": state["wake"],
                })
                last_partial = ""
            elif time.time() - last_partial_at > 0.35:
                partial = clean(json.loads(recognizer.PartialResult()).get("partial", ""), wake_word)
                if partial and partial != last_partial:
                    emit({"type": "partial", "text": partial})
                    last_partial = partial
                    last_partial_at = time.time()
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        emit({"type": "state", "listening": False})
    return 0


if __name__ == "__main__":
    sys.exit(main())
