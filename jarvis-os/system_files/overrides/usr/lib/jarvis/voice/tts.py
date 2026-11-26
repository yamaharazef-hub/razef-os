"""Offline text-to-speech: Piper first, espeak-ng as a tiny fallback."""

from __future__ import annotations

import math
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from config import PIPER_BIN, PLAY_BIN, _which, load_config, tts_model_path

MAX_CHARS = 1200


def _piper() -> str | None:
    return str(PIPER_BIN) if PIPER_BIN.exists() else _which("piper")


def _player() -> str | None:
    return PLAY_BIN if Path(PLAY_BIN).exists() else _which("aplay")


def available() -> bool:
    cfg = load_config()
    return tts_model_path(cfg).exists() and _piper() is not None


def speak(text: str, cfg: dict | None = None) -> str:
    """Speak text and return a short status string."""
    cfg = cfg or load_config()
    text = (text or "").strip()[:MAX_CHARS]
    if not text:
        return "nothing to say"

    player = _player()
    if not player:
        return "no audio player found (install alsa-utils)"

    piper = _piper()
    model = tts_model_path(cfg)
    if piper and model.exists():
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                wav_path = tmp.name
            try:
                proc = subprocess.run(
                    [piper, "--model", str(model), "--output_file", wav_path],
                    input=text, text=True, capture_output=True, timeout=90,
                )
                if proc.returncode == 0 and Path(wav_path).stat().st_size > 44:
                    subprocess.run([player, "-q", wav_path], timeout=60,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return "spoken (piper)"
            finally:
                Path(wav_path).unlink(missing_ok=True)
        except (OSError, subprocess.TimeoutExpired):
            pass

    espeak = _which("espeak-ng") or _which("espeak")
    if espeak:
        try:
            subprocess.run([espeak, "-s", "175", text], timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return "spoken (espeak fallback)"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "no speech engine available - run 'jarvis-voice setup'"


def beep(kind: str = "ok") -> None:
    """Small offline confirmation tone."""
    player = _player()
    if not player:
        return
    freq = 880 if kind == "ok" else 440
    duration = 0.09
    rate = 22050
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = tmp.name
    try:
        with wave.open(path, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            frames = bytearray()
            for i in range(int(rate * duration)):
                value = int(9000 * math.sin(2 * math.pi * freq * i / rate))
                frames.extend(struct.pack("<h", value))
            out.writeframes(bytes(frames))
        subprocess.run([player, "-q", path], timeout=5, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired, wave.Error):
        pass
    finally:
        Path(path).unlink(missing_ok=True)
