"""Allowlisted computer-control actions for JARVIS.

Design rules
------------
* Every action is a fixed argv list. Only values that are validated here are
  substituted into that list. There is exactly one exception: the explicitly
  opt-in ``command`` action, which is gated behind a typed confirmation.
* No ``shell=True``. No model-supplied text is ever passed to a shell except
  through that one gated action.
* Risky actions never run on the first request. They return a confirmation id
  which the UI (or a spoken "confirm") must resolve.
* Every attempt is written to an audit log, including denials.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from config import CONFIG_PATH, load_config

SAFE = "safe"
MODERATE = "moderate"
HIGH = "high"
_RISK_ORDER = {SAFE: 0, MODERATE: 1, HIGH: 2}


class ActionError(ValueError):
    """Raised when an action's arguments fail validation."""


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    risk: str
    build: Callable[[dict], list[str]]
    note: str = ""
    examples: tuple[str, ...] = ()

    def public(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk,
            "note": self.note,
            "examples": list(self.examples),
        }


# ---------------------------------------------------------------------------
# Validators - strict on purpose
# ---------------------------------------------------------------------------
UNIT_RE = re.compile(r"^[A-Za-z0-9@._:-]{1,80}$")
FLATPAK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,120}$")
SERVICE_ACTION_RE = re.compile(r"^(start|stop|restart|status)$")

APP_MAP = {
    "assistant": ["gtk-launch", "org.jarvis.Assistant"],
    "browser": ["gtk-launch", "org.mozilla.firefox.desktop"],
    "firefox": ["gtk-launch", "org.mozilla.firefox.desktop"],
    "files": ["nautilus", "--new-window"],
    "file manager": ["nautilus", "--new-window"],
    "steam": ["gtk-launch", "com.valvesoftware.Steam.desktop"],
    "terminal": ["kgx"],
    "settings": ["gnome-control-center"],
    "music": ["gtk-launch", "com.spotify.Client.desktop"],
}

FLATPAK_ALIASES = {
    "spotify": "com.spotify.Client",
    "discord": "com.discordapp.Discord",
    "firefox": "org.mozilla.firefox",
    "chrome": "com.google.Chrome",
    "vlc": "org.videolan.VLC",
    "steam": "com.valvesoftware.Steam",
    "obs": "com.obsproject.Studio",
    "flatseal": "com.github.tchx84.Flatseal",
}

# Read-only diagnostics the voice layer is allowed to request directly.
DIAGNOSTICS = {
    "os_info", "hardware", "gpu_driver", "services_failed", "recent_errors",
    "disk_usage", "flatpaks", "audio", "controllers", "network", "gaming_stack",
}

MEDIA_MAP = {
    "play": "play", "play_pause": "play-pause", "pause": "play-pause",
    "next": "next", "next_track": "next", "previous": "previous",
    "previous_track": "previous", "stop": "stop",
}


def _app(args: dict) -> str:
    key = str(args.get("app", "")).strip().lower()
    if key not in APP_MAP:
        raise ActionError(f"unknown app '{key}'")
    return key


def _flatpak(args: dict) -> str:
    raw = str(args.get("app", "")).strip()
    app = FLATPAK_ALIASES.get(raw.lower(), raw)
    if not FLATPAK_ID_RE.match(app):
        raise ActionError(f"invalid flatpak id '{app}'")
    return app


def _unit(args: dict) -> str:
    unit = str(args.get("unit", "")).strip()
    if not UNIT_RE.match(unit):
        raise ActionError("invalid systemd unit name")
    return unit


def _seconds(args: dict) -> int:
    try:
        value = int(args.get("seconds", 0))
    except (TypeError, ValueError):
        raise ActionError("timer needs a whole number of seconds")
    if not 1 <= value <= 86400:
        raise ActionError("timer must be between 1 second and 24 hours")
    return value


def _percent(args: dict) -> int:
    try:
        value = int(args.get("percent", 0))
    except (TypeError, ValueError):
        raise ActionError("percentage must be a whole number")
    if not 0 <= value <= 100:
        raise ActionError("percentage must be between 0 and 100")
    return value


def _direction(args: dict) -> str:
    value = str(args.get("direction", "")).strip()
    if value not in ("up", "down", "toggle"):
        raise ActionError("direction must be up, down or toggle")
    return value


# ---------------------------------------------------------------------------
# argv builders
# ---------------------------------------------------------------------------
def _argv_open_app(args):    return list(APP_MAP[_app(args)])
def _argv_close_app(args):
    name = _app(args).split()[0]
    proc = {"assistant": "jarvis-assist", "browser": "firefox", "firefox": "firefox",
            "files": "nautilus", "file manager": "nautilus", "steam": "steam",
            "terminal": "kgx", "settings": "gnome-control-center", "music": "spotify"}[name]
    return ["pkill", "-x", proc]


def _argv_volume(args):
    direction = args.get("direction")
    if direction == "mute" or direction == "toggle":
        return ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"]
    if direction in ("up", "down"):
        step = f"5%{'+' if direction == 'up' else '-'}"
        return ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", step]
    return ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{_percent(args)}%"]


def _argv_mic(args):
    return ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"]


def _argv_brightness(args):
    direction = args.get("direction")
    if direction in ("up", "down"):
        return ["brightnessctl", "set", f"5%{'+' if direction == 'up' else '-'}"]
    return ["brightnessctl", "set", f"{_percent(args)}%"]


def _argv_media(args):
    action = str(args.get("action", "")).strip()
    if action not in MEDIA_MAP:
        raise ActionError("unsupported media action")
    return ["playerctl", MEDIA_MAP[action]]


def _argv_screenshot(args):
    folder = Path(args.get("folder") or (Path.home() / "Pictures"))
    if ".." in str(folder) or not str(folder).startswith(str(Path.home())):
        raise ActionError("screenshot folder must be inside your home")
    name = f"JARVIS-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
    folder.mkdir(parents=True, exist_ok=True)
    return ["gnome-screenshot", "-f", str(folder / name)]


def _argv_timer(args):
    seconds = _seconds(args)
    return [
        "systemd-run", "--user", "--collect",
        f"--on-active={seconds}s",
        "--unit=jarvis-timer",
        "jarvis-voice", "speak", "Timer finished.",
    ]


def _argv_diagnostic(args):
    name = str(args.get("tool", ""))
    if name not in DIAGNOSTICS:
        raise ActionError("diagnostic is not on the allowlist")
    return ["jarvis-assist", "--diagnostic", name]


def _argv_user_service(args):
    action = str(args.get("action", ""))
    if not SERVICE_ACTION_RE.match(action):
        raise ActionError("invalid service action")
    return ["systemctl", "--user", action, _unit(args)]


def _argv_network(args):
    state = str(args.get("state", ""))
    if state not in ("on", "off"):
        raise ActionError("wifi state must be on or off")
    return ["nmcli", "radio", "wifi", state]


def _argv_install(args):
    return ["flatpak", "install", "-y", "--noninteractive", "flathub", _flatpak(args)]


def _argv_remove(args):
    return ["flatpak", "uninstall", "-y", _flatpak(args)]


def _argv_system_update(args):
    return ["pkexec", "rpm-ostree", "upgrade"]


def _argv_system_service(args):
    action = str(args.get("action", ""))
    if action not in ("start", "stop", "restart"):
        raise ActionError("invalid system service action")
    return ["pkexec", "systemctl", action, _unit(args)]


def _argv_power(args):
    action = str(args.get("action", ""))
    mapping = {
        "lock": ["loginctl", "lock-session"],
        "logout": ["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID", "self")],
        "reboot": ["pkexec", "systemctl", "reboot"],
        "shutdown": ["pkexec", "systemctl", "poweroff"],
    }
    if action not in mapping:
        raise ActionError("unsupported power action")
    return list(mapping[action])


def _argv_command(args):
    text = str(args.get("command", "")).strip()
    if not text:
        raise ActionError("no command given")
    if not load_config().get("allow_free_form", True):
        raise ActionError("arbitrary commands are disabled in voice settings")
    return ["bash", "-lc", text]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
ACTIONS: dict[str, ActionSpec] = {spec.name: spec for spec in [
    ActionSpec("open_app", "Open an installed application", SAFE, _argv_open_app,
               examples=("open steam", "open settings")),
    ActionSpec("close_app", "Close an application", MODERATE, _argv_close_app,
               "Unsaved work may be lost.", ("close steam",)),
    ActionSpec("volume", "Change speaker volume", SAFE, _argv_volume,
               examples=("volume up", "set volume to 40")),
    ActionSpec("microphone", "Mute or unmute the microphone", SAFE, _argv_mic,
               examples=("mute my microphone",)),
    ActionSpec("brightness", "Change screen brightness", SAFE, _argv_brightness,
               examples=("brightness down", "set brightness to 60")),
    ActionSpec("media", "Control the active media player", SAFE, _argv_media,
               examples=("play music", "next track")),
    ActionSpec("screenshot", "Take a screenshot", SAFE, _argv_screenshot,
               examples=("take a screenshot",)),
    ActionSpec("timer", "Set a timer", SAFE, _argv_timer,
               examples=("set a timer for 5 minutes",)),
    ActionSpec("diagnostic", "Run a read-only system check", SAFE, _argv_diagnostic,
               examples=("check the gpu driver",)),
    ActionSpec("user_service", "Start, stop or restart a user service", MODERATE,
               _argv_user_service, "The app using it may close.",
               ("restart the pipewire service",)),
    ActionSpec("network", "Turn Wi-Fi on or off", MODERATE, _argv_network,
               "You may lose your connection.", ("turn wifi off",)),
    ActionSpec("install_app", "Install a Flatpak application", HIGH, _argv_install,
               "Downloads and installs software.", ("install spotify",)),
    ActionSpec("remove_app", "Remove a Flatpak application", HIGH, _argv_remove,
               "Deletes an installed application.", ("remove discord",)),
    ActionSpec("system_update", "Update the operating system", HIGH, _argv_system_update,
               "Needs a reboot to take effect.", ("update the system",)),
    ActionSpec("system_service", "Control a system service", HIGH,
               _argv_system_service, "Needs administrator approval.",
               ("restart bluetooth",)),
    ActionSpec("power", "Lock, log out, reboot or shut down", HIGH, _argv_power,
               "Interrupts whatever you are doing.", ("reboot the computer",)),
    ActionSpec("command", "Run an arbitrary shell command", HIGH, _argv_command,
               "DANGEROUS: exactly what you say is executed. Requires the phrase "
               "'confirm command'.", ("run ls -la",)),
]}


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
@dataclass
class Pending:
    id: str
    spec: ActionSpec
    args: dict
    source: str
    created: float = field(default_factory=time.time)


class ActionManager:
    def __init__(self, audit_file: Path | None = None):
        self.pending: dict[str, Pending] = {}
        self.audit_file = audit_file or (CONFIG_PATH.parent / "voice" / "audit.jsonl")

    def list_actions(self) -> list[dict]:
        return [spec.public() for spec in ACTIONS.values()]

    def _audit(self, action: str, args: dict, source: str, outcome: str) -> None:
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "args": {k: v for k, v in args.items() if k != "command"} if action != "command"
                    else {"command": "[redacted in audit]"},
            "source": source,
            "outcome": outcome,
        }
        try:
            self.audit_file.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_file.open("a") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            pass

    def prepare(self, name: str, args: dict | None = None, source: str = "voice") -> dict:
        args = dict(args or {})
        spec = ACTIONS.get(str(name))
        if not spec:
            return {"status": "error", "message": f"unknown action '{name}'"}

        try:
            spec.build(args)  # validate now, before asking for confirmation
        except ActionError as exc:
            self._audit(spec.name, args, source, f"invalid: {exc}")
            return {"status": "error", "message": str(exc)}

        self._audit(spec.name, args, source, "prepared")
        result = {
            "status": "ok",
            "action": spec.name,
            "risk": spec.risk,
            "args": args,
            "description": spec.description,
        }
        if spec.risk == SAFE:
            return self._execute(spec, args, source)

        pending = Pending(id=uuid.uuid4().hex[:12], spec=spec, args=args, source=source)
        self.pending[pending.id] = pending
        result.update({
            "status": "confirmation_required",
            "id": pending.id,
            "message": self.prompt(pending),
            "challenge": "confirm command" if spec.name == "command" else "confirm",
        })
        return result

    def prompt(self, pending: Pending) -> str:
        args = pending.args
        detail = ""
        if pending.spec.name in ("install_app", "remove_app"):
            detail = f" {_flatpak(args)}"
        elif pending.spec.name in ("user_service", "system_service"):
            detail = f" {args.get('action', '')} {args.get('unit', '')}"
        elif pending.spec.name == "network":
            detail = f" Wi-Fi {args.get('state', '')}"
        elif pending.spec.name == "power":
            detail = f" ({args.get('action', '')})"
        elif pending.spec.name == "command":
            detail = f": {str(args.get('command', ''))[:160]}"
        return f"{pending.spec.description}{detail}. Say or type 'confirm' to continue."

    def confirm(self, confirmation_id: str, phrase: str = "", source: str = "ui") -> dict:
        pending = self.pending.pop(str(confirmation_id), None)
        if not pending:
            return {"status": "error", "message": "confirmation expired or unknown"}
        wanted = "confirm command" if pending.spec.name == "command" else "confirm"
        if phrase.strip().lower() not in (wanted, "yes", "y"):
            self._audit(pending.spec.name, pending.args, source, "denied")
            return {"status": "denied", "message": "Cancelled."}
        return self._execute(pending.spec, pending.args, source)

    def deny(self, confirmation_id: str, source: str = "ui") -> dict:
        pending = self.pending.pop(str(confirmation_id), None)
        if not pending:
            return {"status": "error", "message": "confirmation expired or unknown"}
        self._audit(pending.spec.name, pending.args, source, "denied")
        return {"status": "denied", "message": "Cancelled."}

    def _execute(self, spec: ActionSpec, args: dict, source: str) -> dict:
        try:
            argv = spec.build(args)
        except ActionError as exc:
            self._audit(spec.name, args, source, f"invalid: {exc}")
            return {"status": "error", "message": str(exc)}

        preview = shlex.join(argv)
        if spec.name == "command":
            preview = str(args.get("command", ""))[:200]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "LC_ALL": "C.UTF-8", "NO_COLOR": "1"},
            )
            out = ((proc.stdout or "") + (proc.stderr or "")).strip()
            ok = proc.returncode == 0
            message = out[-800:] if out else ("Done." if ok else f"Command failed ({proc.returncode}).")
            self._audit(spec.name, args, source, "ok" if ok else f"failed: {message[:120]}")
            return {
                "status": "ok" if ok else "error",
                "action": spec.name,
                "command": preview,
                "message": message,
            }
        except FileNotFoundError:
            msg = f"Command not installed: {argv[0]}"
        except subprocess.TimeoutExpired:
            msg = "The command timed out."
        except Exception as exc:  # pragma: no cover - defensive
            msg = f"Could not run the action: {exc}"
        self._audit(spec.name, args, source, f"failed: {msg}")
        return {"status": "error", "action": spec.name, "command": preview, "message": msg}
