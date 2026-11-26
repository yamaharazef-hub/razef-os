"""Shared configuration for JARVIS voice control."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

DEFAULT_CONFIG = {
    # Start the microphone listener with the assistant session.
    "enabled": False,
    # "jarvis" must be spoken before a command when wake_enabled is true.
    "wake_enabled": True,
    "wake_word": "jarvis",
    # Push-to-talk is toggled from the UI/hotkey; ignored while wake is on.
    "ptt": False,
    # Speak assistant replies and action results out loud.
    "speak_replies": True,
    # Short confirmation beeps before risky actions.
    "voice_feedback": True,
    # Arbitrary shell commands are allowed, but always require a confirmation.
    "allow_free_form": True,
    "stt_model": "vosk-model-small-en-us-0.15",
    "tts_voice": "en_US-lessac-medium",
    "sensitivity": 0.6,
}

CONFIG_PATH = Path(
    os.environ.get(
        "JARVIS_VOICE_CONFIG",
        Path.home() / ".config" / "jarvis" / "voice" / "config.json",
    )
)
MODEL_DIR = Path(
    os.environ.get(
        "JARVIS_VOICE_MODELS",
        Path.home() / ".local" / "share" / "jarvis" / "voice" / "models",
    )
)
# The image ships a read-only copy so voice works on first boot; a user-level
# download in MODEL_DIR takes precedence when present.
SYSTEM_MODEL_DIR = Path("/usr/share/jarvis/voice/models")
VENV_PYTHON = Path("/usr/lib/jarvis/voice/venv/bin/python")
PIPER_BIN = Path("/usr/lib/jarvis/voice/bin/piper")
RECORD_BIN = "/usr/bin/arecord"
PLAY_BIN = "/usr/bin/aplay"


def load_config() -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    try:
        saved = json.loads(CONFIG_PATH.read_text())
        if isinstance(saved, dict):
            for key in cfg:
                if key in saved:
                    cfg[key] = saved[key]
    except (OSError, ValueError):
        pass
    return cfg


def save_config(patch: dict) -> dict:
    cfg = load_config()
    for key, value in (patch or {}).items():
        if key in DEFAULT_CONFIG:
            cfg[key] = value
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    return cfg


def stt_model_path(cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    user_path = MODEL_DIR / str(cfg["stt_model"])
    shipped = SYSTEM_MODEL_DIR / str(cfg["stt_model"])
    return user_path if user_path.is_dir() or not shipped.is_dir() else shipped


def tts_model_path(cfg: dict | None = None) -> Path:
    cfg = cfg or load_config()
    voice = str(cfg["tts_voice"])
    user_path = MODEL_DIR / "piper" / f"{voice}.onnx"
    shipped = SYSTEM_MODEL_DIR / "piper" / f"{voice}.onnx"
    return user_path if user_path.exists() or not shipped.exists() else shipped


def stt_ready(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    venv = VENV_PYTHON if VENV_PYTHON.exists() else Path("/usr/bin/python3")
    return stt_model_path(cfg).is_dir() and venv.exists()


def tts_ready(cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    return tts_model_path(cfg).exists() and (PIPER_BIN.exists() or bool(_which("piper")))


def _which(name: str) -> str | None:
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(folder) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
