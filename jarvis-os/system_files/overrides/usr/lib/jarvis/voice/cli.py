"""Command-line control for JARVIS voice features."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import commands  # noqa: E402
import tts  # noqa: E402
from config import (  # noqa: E402
    MODEL_DIR, VENV_PYTHON, load_config, save_config, stt_model_path,
    stt_ready, tts_model_path, tts_ready,
)

API = os.environ.get("JARVIS_ASSIST_URL", "http://127.0.0.1:8765")


def api(path: str, payload: dict | None = None, timeout: int = 10) -> dict:
    data = json.dumps(payload or {}).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method="POST" if data is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except (urllib.error.URLError, ValueError):
        return {"error": "assistant backend is not reachable"}


def cmd_status(argv) -> int:
    cfg = load_config()
    state = api("/api/voice/status")
    print(json.dumps({
        "config": cfg,
        "models": {
            "stt": str(stt_model_path(cfg)),
            "stt_ready": stt_ready(cfg),
            "tts": str(tts_model_path(cfg)),
            "tts_ready": tts_ready(cfg),
        },
        "worker_python": str(VENV_PYTHON),
        "backend": state,
    }, indent=2))
    return 0


def cmd_setup(argv) -> int:
    script = Path(__file__).resolve().parent / "setup_models.py"
    rc = subprocess.call([sys.executable, str(script)])
    if rc == 0:
        print("\nVoice models are installed.")
        print("Enable the listener with:  jarvis-voice start")
        print("Or start it from the JARVIS Assistant sidebar.")
    return rc


def cmd_speak(argv) -> int:
    text = " ".join(argv.text)
    print(tts.speak(text))
    return 0


def cmd_listen_once(argv) -> int:
    if not stt_ready():
        print("Speech model is not installed. Run: jarvis-voice setup", file=sys.stderr)
        return 2
    worker = Path(__file__).resolve().parent / "stt_worker.py"
    python = VENV_PYTHON if VENV_PYTHON.exists() else Path("/usr/bin/python3")
    proc = subprocess.Popen(
        [str(python), str(worker)], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True,
    )
    try:
        for line in proc.stdout or []:
            event = json.loads(line)
            if event.get("type") == "transcript":
                print(event.get("text", ""))
                return 0
            if event.get("type") == "error":
                print(event.get("message", "speech error"), file=sys.stderr)
                return 3
    finally:
        proc.terminate()
    return 1


def cmd_control(argv) -> int:
    if argv.command == "start":
        result = api("/api/voice/start", {"wake": not argv.ptt})
    elif argv.command == "stop":
        result = api("/api/voice/stop", {})
    elif argv.command == "toggle":
        result = api("/api/voice/toggle", {})
    elif argv.command == "wake-on":
        result = api("/api/voice/config", {"wake_enabled": True})
    elif argv.command == "wake-off":
        result = api("/api/voice/config", {"wake_enabled": False})
    elif argv.command == "confirm":
        result = api("/api/actions/confirm", {
            "id": argv.id, "confirm": True, "phrase": argv.phrase or "confirm",
        })
    elif argv.command == "actions":
        result = api("/api/actions")
    else:  # pragma: no cover
        return 2
    print(json.dumps(result, indent=2))
    return 1 if result.get("error") else 0


def cmd_set(argv) -> int:
    patch = {}
    if argv.speak_replies is not None:
        patch["speak_replies"] = argv.speak_replies
    if argv.wake is not None:
        patch["wake_enabled"] = argv.wake
    if argv.free_form is not None:
        patch["allow_free_form"] = argv.free_form
    if argv.sensitivity is not None:
        patch["sensitivity"] = argv.sensitivity
    print(json.dumps(save_config(patch), indent=2))
    api("/api/voice/reload", {})
    return 0


def cmd_test(argv) -> int:
    cfg = load_config()
    checks = {
        "speech model": stt_ready(cfg),
        "voice models directory": MODEL_DIR.exists(),
        "tts engine": tts_ready(cfg) or bool(tts._which("espeak-ng")),
        "backend": not api("/api/health").get("error"),
    }
    for name, ok in checks.items():
        print(f"{'OK  ' if ok else 'FAIL'} {name}")
    for sample in ("jarvis volume up", "jarvis open steam", "jarvis take a screenshot",
                   "jarvis install spotify", "jarvis reboot the computer"):
        parsed = commands.parse(sample)
        print(f"  {sample:<32} -> {parsed.action or '(chat)'}")
    return 0 if all(checks.values()) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show voice configuration and model state").set_defaults(fn=cmd_status)
    sub.add_parser("setup", help="download the offline STT/TTS models").set_defaults(fn=cmd_setup)
    speak = sub.add_parser("speak", help="speak text through the offline TTS engine")
    speak.add_argument("text", nargs="+")
    speak.set_defaults(fn=cmd_speak)
    sub.add_parser("listen-once", help="recognise one utterance and print it").set_defaults(fn=cmd_listen_once)
    for name in ("start", "stop", "toggle", "wake-on", "wake-off", "actions"):
        p = sub.add_parser(name)
        if name == "start":
            p.add_argument("--ptt", action="store_true", help="start in push-to-talk mode")
        p.set_defaults(fn=cmd_control, command=name)
    confirm = sub.add_parser("confirm", help="confirm a pending high-risk action")
    confirm.add_argument("id")
    confirm.add_argument("phrase", nargs="?")
    confirm.set_defaults(fn=cmd_control, command="confirm")
    setting = sub.add_parser("set", help="change voice settings")
    setting.add_argument("--speak-replies", dest="speak_replies", action="store_true", default=None)
    setting.add_argument("--no-speak-replies", dest="speak_replies", action="store_false")
    setting.add_argument("--wake", action="store_true", default=None)
    setting.add_argument("--no-wake", dest="wake", action="store_false")
    setting.add_argument("--free-form", dest="free_form", action="store_true", default=None)
    setting.add_argument("--no-free-form", dest="free_form", action="store_false")
    setting.add_argument("--sensitivity", type=float, default=None)
    setting.set_defaults(fn=cmd_set)
    sub.add_parser("test", help="run a local voice self-test").set_defaults(fn=cmd_test)

    args = parser.parse_args(argv)
    return args.fn(args)
