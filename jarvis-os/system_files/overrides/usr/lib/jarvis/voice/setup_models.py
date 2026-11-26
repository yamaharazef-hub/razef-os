#!/usr/bin/env python3
"""Download the offline speech models used by JARVIS voice control.

Everything is cached under ~/.local/share/jarvis/voice/models (or
$JARVIS_VOICE_MODELS), so an image update never throws the models away.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import MODEL_DIR, load_config, save_config  # noqa: E402

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
PIPER_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "en/en_US/lessac/medium/en_US-lessac-medium.onnx"
)


def fetch(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size > 0:
        print(f"  already present: {target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp, tempfile.NamedTemporaryFile(
        dir=target.parent, delete=False
    ) as tmp:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        while chunk := resp.read(1 << 20):
            tmp.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total}%", end="", flush=True)
        tmp_path = tmp.name
    print()
    Path(tmp_path).replace(target)


def install_stt(force: bool = False) -> int:
    cfg = load_config()
    target = MODEL_DIR / str(cfg["stt_model"])
    if target.is_dir() and not force:
        print(f"  STT model already installed: {target}")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "vosk.zip"
        fetch(VOSK_URL, archive)
        print("  extracting…")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(Path(tmp) / "vosk")
        extracted = Path(tmp) / "vosk" / str(cfg["stt_model"])
        if not extracted.is_dir():
            print("  ERROR: unexpected archive layout", file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            import shutil
            shutil.rmtree(target)
        extracted.rename(target)
    print(f"  STT model ready: {target}")
    return 0


def install_tts(force: bool = False) -> int:
    cfg = load_config()
    voice = str(cfg["tts_voice"])
    folder = MODEL_DIR / "piper"
    model = folder / f"{voice}.onnx"
    meta = folder / f"{voice}.onnx.json"
    if model.exists() and meta.exists() and not force:
        print(f"  TTS voice already installed: {model}")
        return 0
    fetch(PIPER_BASE, model)
    fetch(PIPER_BASE + ".json", meta)
    print(f"  TTS voice ready: {model}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stt-only", action="store_true")
    parser.add_argument("--tts-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    rc = 0
    if not args.tts_only:
        rc |= install_stt(args.force)
    if not args.stt_only:
        rc |= install_tts(args.force)
    if rc == 0:
        save_config({"enabled": True})
    return rc


if __name__ == "__main__":
    sys.exit(main())
