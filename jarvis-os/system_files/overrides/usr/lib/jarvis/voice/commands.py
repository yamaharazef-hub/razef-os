"""Deterministic offline command parser.

Anything recognised here runs without waiting for the LLM. Everything else is
handed to the local model as a normal chat message. Keeping the two paths apart
means "volume up" works even while Ollama is still loading a model.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from actions import APP_MAP, DIAGNOSTICS, FLATPAK_ALIASES

SERVICE_ALIASES = {
    "pipewire": "pipewire.service",
    "wireplumber": "wireplumber.service",
    "bluetooth": "bluetooth.service",
    "ollama": "ollama.service",
    "jarvis assistant": "jarvis-assist.service",
    "network manager": "NetworkManager.service",
}

DIAGNOSTIC_ALIASES = {
    "gpu": "gpu_driver", "gpu driver": "gpu_driver", "graphics": "gpu_driver",
    "disk": "disk_usage", "disk space": "disk_usage", "storage": "disk_usage",
    "services": "services_failed", "failed services": "services_failed",
    "errors": "recent_errors", "logs": "recent_errors",
    "network": "network", "audio": "audio", "sound": "audio",
    "hardware": "hardware", "controllers": "controllers",
    "flatpaks": "flatpaks", "apps": "flatpaks", "system": "os_info",
}


@dataclass
class Command:
    action: str | None = None
    args: dict | None = None
    spoken: str | None = None
    text: str = ""


def strip_wake(text: str, wake_word: str = "jarvis") -> str:
    """Remove a leading (or trailing) wake word and tidy punctuation."""
    text = re.sub(r"\s+", " ", (text or "").strip().lower())
    text = re.sub(r"^(hey|hi|ok|okay)\s+", "", text)
    if text.startswith(wake_word.lower()):
        text = text[len(wake_word):].lstrip(" ,.-:")
    text = re.sub(rf"\b{wake_word.lower()}\b", "", text)
    return re.sub(r"[^a-z0-9 _.@:+/-]", "", text).strip()


def strip_wake_light(text: str, wake_word: str = "jarvis") -> str:
    """Like strip_wake, but preserves shell punctuation for 'run <command>'."""
    text = (text or "").strip()
    text = re.sub(r"(?i)^(hey|hi|ok|okay)\s+", "", text)
    text = re.sub(rf"(?i)^\s*{re.escape(wake_word)}\b\s*[,:;-]?\s*", "", text)
    return re.sub(rf"(?i)\b{re.escape(wake_word)}\b\s*", "", text).strip()


def _unit(value: str) -> str | None:
    value = value.strip().lower()
    if value in SERVICE_ALIASES:
        return SERVICE_ALIASES[value]
    if re.match(r"^[a-z0-9@._:-]{1,80}$", value):
        return value if value.endswith((".service", ".socket", ".timer")) else value + ".service"
    return None


def _app(value: str) -> str | None:
    value = value.strip().lower().rstrip(".")
    for prefix in ("the ", "my "):
        if value.startswith(prefix):
            value = value[len(prefix):]
    if value in APP_MAP:
        return value
    if value.replace(" ", "") in ("filemanager",):
        return "files"
    return None


def _duration(text: str) -> int | None:
    match = re.search(r"(\d+)\s*(seconds?|minutes?|hours?|mins?|hrs?)", text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith(("h", "hr")):
        return amount * 3600
    if unit.startswith(("m", "min")):
        return amount * 60
    return amount


def parse(raw: str, wake_word: str = "jarvis") -> Command:
    text = strip_wake(raw, wake_word)
    raw_text = strip_wake_light(raw, wake_word)
    cmd = Command(text=text)
    if not text:
        return cmd

    # Informational answers need no model and no action.
    if re.search(r"\b(time|what time is it)\b", text) and not re.search(r"timer|take", text):
        cmd.spoken = "It is " + dt.datetime.now().strftime("%I:%M %p").lstrip("0")
        return cmd
    if ("what day is it" in text or "what date is it" in text
            or "whats the date" in text):
        cmd.spoken = "Today is " + dt.datetime.now().strftime("%A, %B %d")
        return cmd
    if text in ("hello", "hi", "hey") or text.startswith("good morning"):
        cmd.spoken = "JARVIS is listening."
        return cmd

    # Applications
    match = re.match(r"^(?:please\s+)?(?:open|launch|start)\s+(.+)$", text)
    if match and (app := _app(match.group(1))):
        return Command("open_app", {"app": app}, text=text)

    match = re.match(r"^(?:please\s+)?(?:close|quit|exit)\s+(.+)$", text)
    if match and (app := _app(match.group(1))):
        return Command("close_app", {"app": app}, text=text)

    # Volume / microphone
    if re.search(r"\bvolume\b", text) and re.search(r"\bset\b|\bto\b|\d", text):
        match = re.search(r"(\d{1,3})\s*(?:percent|%)?", text)
        if match:
            return Command("volume", {"percent": int(match.group(1)), "direction": "set"}, text=text)
    if re.search(r"\bvolume\b", text) and re.search(r"\bup\b|\blouder\b|\bincrease\b", text):
        return Command("volume", {"direction": "up"}, text=text)
    if re.search(r"\bvolume\b", text) and re.search(r"\bdown\b|\bquieter\b|\bdecrease\b", text):
        return Command("volume", {"direction": "down"}, text=text)
    if re.fullmatch(r"(?:please\s+)?(?:mute|unmute)(?:\s+(?:the\s+)?(?:speakers?|sound))?", text):
        return Command("volume", {"direction": "toggle"}, text=text)
    if re.search(r"\bmute\b|\bunmute\b", text) and re.search(r"\bmic(?:rophone)?\b", text):
        return Command("microphone", {}, text=text)

    # Brightness
    if re.search(r"\bbrightness\b|\bscreen\s+light\b", text):
        match = re.search(r"(\d{1,3})\s*(?:percent|%)?", text)
        if match and re.search(r"\bset\b|\bto\b", text):
            return Command("brightness", {"percent": int(match.group(1)), "direction": "set"}, text=text)
        if re.search(r"\bdown\b|\blower\b|\bdim\b", text):
            return Command("brightness", {"direction": "down"}, text=text)
        if re.search(r"\bup\b|\bhigher\b|\bbrighter\b", text):
            return Command("brightness", {"direction": "up"}, text=text)

    # Media
    if re.search(r"\bnext\s+(track|song)\b|\bskip\b", text):
        return Command("media", {"action": "next"}, text=text)
    if re.search(r"\bprevious\s+(track|song)\b|\bback\s+a\s+track\b", text):
        return Command("media", {"action": "previous"}, text=text)
    if re.search(r"\bplay\b|\bpause\b", text) and re.search(
        r"\bmusic\b|\btrack\b|\bsong\b|\bvideo\b|\bplayer\b|play\s+now|pause\s+now", text
    ):
        return Command("media", {"action": "play_pause"}, text=text)
    if text in ("play", "pause", "play pause"):
        return Command("media", {"action": "play_pause"}, text=text)

    # Screenshot / timers
    if re.search(r"screenshot|take\s+a\s+picture\s+of\s+the\s+screen", text):
        return Command("screenshot", {}, text=text)
    if re.search(r"\btimer\b|\bremind\s+me\b", text):
        seconds = _duration(text)
        if seconds:
            return Command("timer", {"seconds": seconds}, text=text)

    # Software
    match = re.match(r"^(?:please\s+)?(?:install|add)\s+(.+)$", text)
    if match:
        app = match.group(1).strip()
        return Command("install_app", {"app": FLATPAK_ALIASES.get(app, app)}, text=text)
    match = re.match(r"^(?:please\s+)?(?:remove|uninstall|delete)\s+(.+)$", text)
    if match and not re.search(r"file|folder|directory", text):
        app = match.group(1).strip()
        return Command("remove_app", {"app": FLATPAK_ALIASES.get(app, app)}, text=text)
    if re.search(r"\bupdate\b|\bupgrade\b", text) and re.search(r"\bsystem\b|\bos\b|\bcomputer\b", text):
        return Command("system_update", {}, text=text)

    # Services / network
    match = re.match(r"^(?:please\s+)?(start|stop|restart)\s+(?:the\s+)?(.+?)(?:\s+service)?$", text)
    if match:
        action, name = match.group(1), match.group(2)
        if unit := _unit(name):
            scope = "user_service" if unit == "jarvis-assist.service" else "system_service"
            return Command(scope, {"action": action, "unit": unit}, text=text)
    if re.search(r"\bwifi\b|\bwi-fi\b", text) and re.search(r"\bon\b|\boff\b|\bturn\b|\benable\b|\bdisable\b", text):
        state = "off" if re.search(r"\boff\b|\bdisable\b", text) else "on"
        return Command("network", {"state": state}, text=text)

    # Power
    if re.search(r"\block\b|\block\s+the\s+screen\b", text) and not re.search(r"account", text):
        return Command("power", {"action": "lock"}, text=text)
    if re.search(r"\blog\s*out\b|\blogout\b", text):
        return Command("power", {"action": "logout"}, text=text)
    if re.search(r"\breboot\b|\brestart\s+(?:the\s+)?(?:computer|machine|system)\b", text):
        return Command("power", {"action": "reboot"}, text=text)
    if re.search(r"\bshut\s*down\b|\bpower\s*off\b", text):
        return Command("power", {"action": "shutdown"}, text=text)

    # Free-form command (always confirmed by the UI).
    match = re.match(r"(?i)^(?:please\s+)?(?:run|execute|type)\s+(.+)$", raw_text)
    if match:
        return Command("command", {"command": match.group(1).strip()}, text=raw_text)

    # Diagnostics
    if re.search(r"\bcheck\b|\bdiagnose\b|\bwhat.s\b|\bhow\s+much\b|\bis\s+my\b", text):
        for phrase, tool in DIAGNOSTIC_ALIASES.items():
            if phrase in text and tool in DIAGNOSTICS:
                return Command("diagnostic", {"tool": tool}, text=text)

    return cmd
