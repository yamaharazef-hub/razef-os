#!/usr/bin/env python3
"""
jarvis-assist - backend server
=============================

A dependency-free local web service that sits between the JARVIS Assistant UI
(/usr/lib/jarvis/assist/ui/index.html) and a local Ollama instance.

Why stdlib only?  JARVIS is an immutable image: fewer moving parts means fewer
things that can break after an update. This file uses nothing outside the
Python standard library, so it cannot be broken by a bad dependency bump.

Endpoints
---------
  GET  /                 the UI
  GET  /api/status       ollama reachable? which models? what hardware?
  POST /api/chat         {model, messages, tools} -> NDJSON stream
  POST /api/pull         {model}                   -> NDJSON progress stream
  GET  /api/health       liveness probe
  GET  /api/actions      computer-control actions and their risk levels
  POST /api/actions/confirm   resolve a pending confirmation
  GET  /api/voice/status      voice engine, models and listener state
  GET  /api/voice/events      NDJSON event stream for the UI
  POST /api/voice/start|stop|toggle|speak|config|reload
  POST /api/voice/event       inject a recognised utterance (local workers)

Security model
--------------
  * binds to 127.0.0.1 only
  * diagnostics are read-only and come from a fixed allowlist
  * computer-control actions use fixed argv lists; risky ones need confirmation
  * the only shell path is the explicitly gated 'command' action, which requires
    the phrase "confirm command" before anything executes
  * speech models run in a private virtualenv as a child process

Run manually:  jarvis-assist --serve --port 8765
"""

from __future__ import annotations

import json
import os
import platform
import queue
import re
import shlex
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__version__ = "1.1.0"

HERE = Path(__file__).resolve().parent
UI_FILE = HERE / "ui" / "index.html"
VOICE_DIR = HERE.parent / "voice"
OLLAMA = os.environ.get("JARVIS_OLLAMA_URL", "http://127.0.0.1:11434")
STATE_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "jarvis"


def _default_model() -> str:
    """Env var > ~/.config/jarvis/default-model > built-in default."""
    env = os.environ.get("JARVIS_DEFAULT_MODEL")
    if env:
        return env
    try:
        saved = (STATE_DIR / "default-model").read_text().strip()
        if saved:
            return saved
    except OSError:
        pass
    return "qwen3:4b"


DEFAULT_MODEL = _default_model()
HOST = "127.0.0.1"

OLLAMA_TIMEOUT = 5
CHAT_TIMEOUT = 600
MAX_SPEECH_CHARS = 1200


# ---------------------------------------------------------------------------
# UI event bus + offline voice control
# ---------------------------------------------------------------------------
class EventBus:
    """Fan-out of small JSON events to every connected UI window."""

    def __init__(self):
        self.subscribers: set[queue.Queue] = set()
        self.lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=256)
        with self.lock:
            self.subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self.lock:
            self.subscribers.discard(sub)

    def publish(self, event: dict) -> None:
        with self.lock:
            subscribers = list(self.subscribers)
        for sub in subscribers:
            try:
                sub.put_nowait(event)
            except queue.Full:
                pass


EVENT_BUS = EventBus()

VOICE_IMPORT_ERROR: str | None = None
ACTION_MANAGER = None
try:
    if str(VOICE_DIR) not in sys.path:
        sys.path.insert(0, str(VOICE_DIR))
    from actions import ActionManager as _ActionManager  # type: ignore
    from commands import parse as parse_voice_command  # type: ignore
    import config as voice_config  # type: ignore
    import tts as voice_tts  # type: ignore

    ACTION_MANAGER = _ActionManager()
    VOICE_AVAILABLE = True
except Exception as exc:  # pragma: no cover - only when files are missing
    VOICE_AVAILABLE = False
    VOICE_IMPORT_ERROR = str(exc)

_worker_lock = threading.Lock()
_worker_proc: subprocess.Popen | None = None
voice_state: dict = {
    "listening": False,
    "mode": "wake",
    "speaking": False,
    "last_transcript": "",
    "worker_error": None,
}


def publish(event: dict) -> None:
    EVENT_BUS.publish(event)


def speak_async(text: str) -> None:
    if not (VOICE_AVAILABLE and voice_config.load_config().get("speak_replies", True)):
        return
    if not (text or "").strip():
        return

    def _speak() -> None:
        voice_state["speaking"] = True
        _worker_reader.last_spoke = time.time()  # type: ignore[attr-defined]
        try:
            status = voice_tts.speak(text, voice_config.load_config())
            publish({"type": "spoken", "text": text[:400], "status": status})
        except Exception as exc:  # pragma: no cover
            publish({"type": "voice_error", "message": str(exc)})
        finally:
            time.sleep(0.8)  # don't listen to our own voice
            voice_state["speaking"] = False

    threading.Thread(target=_speak, daemon=True).start()


def _collect_chat(model: str, messages: list[dict]) -> str:
    """Ask Ollama once and return the complete answer (no HTTP response involved)."""
    body = json.dumps({
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.4, "num_ctx": 8192},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    collected: list[str] = []
    with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT) as resp:
        for raw in resp:
            if not raw.strip():
                continue
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if chunk.get("error"):
                raise RuntimeError(chunk["error"])
            piece = (chunk.get("message") or {}).get("content", "")
            if piece:
                collected.append(piece)
            if chunk.get("done"):
                break
    return "".join(collected)


def ollama_reply(question: str, model: str | None = None) -> str:
    """Model answer for a voice utterance, including one diagnostic round."""
    model = model or DEFAULT_MODEL
    convo = [
        {"role": "system", "content": build_prompt()},
        {"role": "user", "content": question},
    ]
    try:
        for _ in range(2):
            text = _collect_chat(model, convo)
            match = TOOL_FENCE_RE.search(text or "")
            if not match:
                return (text or "").strip()
            try:
                call = json.loads(match.group(1))
            except json.JSONDecodeError:
                return (text or "").strip()
            name = str(call.get("tool", ""))
            if name not in TOOLS:
                break
            publish({"type": "tool", "name": name})
            output = run_tool(name, call.get("args") or {})
            convo.append({"role": "assistant", "content": text})
            convo.append({
                "role": "user",
                "content": f"Tool output for `{name}`:\n\n```\n{output}\n```\n\nAnswer now.",
            })
        return (text or "").strip()
    except Exception as exc:
        return f"Sorry, the local model is unavailable: {exc}"


_handle_voice_lock = threading.Lock()


def handle_voice_utterance(text: str) -> None:
    """Route a recognised utterance: local command first, model second."""
    if not (VOICE_AVAILABLE and ACTION_MANAGER):
        publish({"type": "voice_error", "message": VOICE_IMPORT_ERROR or "voice modules missing"})
        return

    cfg = voice_config.load_config()

    # A spoken "confirm" / "cancel" resolves the most recent prompt.
    normalized = text.strip().lower()
    if ACTION_MANAGER.pending and normalized in (
        "confirm", "confirm command", "yes", "y", "cancel", "deny", "no"
    ):
        pending_id = list(ACTION_MANAGER.pending)[-1]
        if normalized in ("cancel", "deny", "no"):
            result = ACTION_MANAGER.deny(pending_id, source="voice")
        else:
            result = ACTION_MANAGER.confirm(pending_id, normalized, source="voice")
        publish({"type": "action", "result": result})
        speak_async(result.get("message", ""))
        return

    cmd = parse_voice_command(text, str(cfg["wake_word"]))

    if cmd.spoken:
        publish({"type": "assistant_said", "text": cmd.spoken, "source": "local"})
        speak_async(cmd.spoken)
        return

    if cmd.action:
        result = ACTION_MANAGER.prepare(cmd.action, cmd.args or {}, source="voice")
        publish({"type": "action", "result": result})
        if result.get("status") == "confirmation_required":
            publish({
                "type": "confirmation_required",
                "id": result["id"],
                "action": result["action"],
                "message": result["message"],
                "challenge": result.get("challenge", "confirm"),
                "args": result.get("args", {}),
            })
            if cfg.get("voice_feedback", True):
                speak_async(result["message"])
        elif result.get("status") in ("ok", "error"):
            speak_async(result.get("message", ""))
        return

    # Not a local command: let the local model answer and read it aloud.
    publish({"type": "voice_thinking", "text": text})
    answer = ollama_reply(text)
    publish({"type": "assistant_said", "text": answer, "source": "model"})
    speak_async(answer)


def _worker_reader(proc: subprocess.Popen) -> None:
    try:
        for line in proc.stdout or []:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = event.get("type")
            if kind == "transcript":
                text = str(event.get("text", "")).strip()
                if not text:
                    continue
                if voice_state["speaking"] and time.time() - _worker_reader.last_spoke < 2:
                    continue
                raw_text = str(event.get("raw") or text).strip()
                voice_state["last_transcript"] = text
                publish({"type": "transcript", "text": text})
                threading.Thread(
                    target=handle_voice_utterance, args=(raw_text,), daemon=True
                ).start()
            elif kind == "state":
                voice_state["listening"] = bool(event.get("listening"))
                voice_state["mode"] = event.get("mode", voice_state["mode"])
                publish({"type": "voice_state", **voice_state})
            elif kind in ("error", "voice_error"):
                voice_state["worker_error"] = event.get("message")
                publish({"type": "voice_error", "message": event.get("message", "speech error")})
            else:
                publish(event)
    except (ValueError, OSError):
        pass
    finally:
        voice_state["listening"] = False
        publish({"type": "voice_state", **voice_state})


_worker_reader.last_spoke = 0.0  # type: ignore[attr-defined]


def _worker_command(command: str) -> None:
    with _worker_lock:
        proc = _worker_proc
    if proc and proc.stdin and proc.poll() is None:
        try:
            proc.stdin.write(command + "\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            pass


def start_voice_listener(wake: bool | None = None) -> dict:
    global _worker_proc
    if not VOICE_AVAILABLE:
        return {"error": VOICE_IMPORT_ERROR or "voice modules are not installed"}
    cfg = voice_config.load_config()
    if wake is not None:
        cfg = voice_config.save_config({"wake_enabled": bool(wake)})
    if not voice_config.stt_ready(cfg):
        return {"error": "offline speech model is missing", "hint": "jarvis-voice setup"}

    with _worker_lock:
        if _worker_proc and _worker_proc.poll() is None:
            _worker_command("WAKE_ON" if cfg["wake_enabled"] else "WAKE_OFF")
            return {"status": "already running", "listening": True, "mode": voice_state["mode"]}

        worker = voice_config.VENV_PYTHON
        if not worker.exists():
            worker = Path("/usr/bin/python3")
        script = VOICE_DIR / "stt_worker.py"
        if not worker.exists() or not script.exists():
            return {"error": "voice worker is not installed", "hint": "jarvis-voice setup"}
        env = {**os.environ, "JARVIS_VOICE_CONFIG": str(voice_config.CONFIG_PATH)}
        try:
            _worker_proc = subprocess.Popen(
                [str(worker), str(script)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
            )
        except OSError as exc:
            return {"error": f"could not start speech worker: {exc}"}

    voice_state["listening"] = True
    voice_state["worker_error"] = None
    voice_state["mode"] = "wake" if cfg["wake_enabled"] else "continuous"
    threading.Thread(target=_worker_reader, args=(_worker_proc,), daemon=True).start()
    publish({"type": "voice_state", **voice_state})
    return {"status": "started", "listening": True, "mode": voice_state["mode"]}


def stop_voice_listener() -> dict:
    global _worker_proc
    with _worker_lock:
        proc = _worker_proc
        _worker_proc = None
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
    voice_state["listening"] = False
    publish({"type": "voice_state", **voice_state})
    return {"status": "stopped", "listening": False}


def voice_status_payload() -> dict:
    cfg = voice_config.load_config() if VOICE_AVAILABLE else {}
    return {
        "available": VOICE_AVAILABLE,
        "import_error": VOICE_IMPORT_ERROR,
        "listening": voice_state["listening"],
        "mode": voice_state["mode"],
        "speaking": voice_state["speaking"],
        "last_transcript": voice_state["last_transcript"],
        "worker_error": voice_state["worker_error"],
        "config": cfg,
        "models": {
            "stt_ready": voice_config.stt_ready(cfg) if cfg else False,
            "tts_ready": voice_config.tts_ready(cfg) if cfg else False,
            "stt_path": str(voice_config.stt_model_path(cfg)) if cfg else None,
            "tts_path": str(voice_config.tts_model_path(cfg)) if cfg else None,
        },
    }


# ---------------------------------------------------------------------------
# Read-only diagnostics the assistant is allowed to run
# ---------------------------------------------------------------------------
# Each entry is a fixed argv list. `unit` is the only substitutable value and
# it is validated against a strict pattern before use.
SYSTEMD_UNIT_RE = re.compile(r"^[A-Za-z0-9@._:-]{1,80}$")


def _run(argv: list[str], timeout: int = 12) -> str:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C.UTF-8", "NO_COLOR": "1"},
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return out.strip()[:4000] or "(no output)"
    except FileNotFoundError:
        return f"(command not found: {argv[0]})"
    except subprocess.TimeoutExpired:
        return "(command timed out)"
    except Exception as exc:  # pragma: no cover - defensive
        return f"(error: {exc})"


TOOLS: dict[str, dict] = {
    "os_info": {
        "desc": "JARVIS/bootc version, image reference and update status",
        "argv": ["bash", "-lc",
                 "cat /usr/lib/os-release | grep -E '^(NAME|VERSION|PRETTY_NAME|IMAGE_ID|JARVIS)';"
                 " echo '--- rpm-ostree ---'; rpm-ostree status --booted 2>/dev/null | head -30"],
    },
    "hardware": {
        "desc": "CPU, GPU, memory and kernel",
        "argv": ["bash", "-lc",
                 "echo '--- cpu ---'; lscpu | grep -E 'Model name|^CPU\\(s\\):|Architecture';"
                 " echo '--- gpu ---'; lspci -nnk | grep -A3 -iE 'vga|3d|display';"
                 " echo '--- mem ---'; free -h;"
                 " echo '--- kernel ---'; uname -a"],
    },
    "gpu_driver": {
        "desc": "NVIDIA / Mesa driver state and loaded graphics modules",
        "argv": ["bash", "-lc",
                 "nvidia-smi 2>/dev/null || echo 'nvidia-smi not present (AMD/Intel or driver missing)';"
                 " echo '--- modules ---'; lsmod | grep -E '^(nvidia|amdgpu|i915|xe|radeon)' ;"
                 " echo '--- mesa ---'; rpm -q mesa-dri-drivers 2>/dev/null;"
                 " glxinfo -B 2>/dev/null | head -20 || echo 'glxinfo not installed'"],
    },
    "services_failed": {
        "desc": "Systemd units that failed to start this boot",
        "argv": ["bash", "-lc",
                 "systemctl --failed --no-pager --plain | head -30;"
                 " echo '--- user ---'; systemctl --user --failed --no-pager --plain 2>/dev/null | head -20"],
    },
    "recent_errors": {
        "desc": "Errors and warnings from the journal for this boot",
        "argv": ["bash", "-lc",
                 "journalctl -b -p warning --no-pager -o short 2>/dev/null | tail -60"],
    },
    "disk_usage": {
        "desc": "Filesystem usage plus ostree deployment size",
        "argv": ["bash", "-lc",
                 "df -h / /var /boot 2>/dev/null; echo '--- deployments ---';"
                 " rpm-ostree status --booted 2>/dev/null | grep -iE 'deployment|size' | head"],
    },
    "flatpaks": {
        "desc": "Installed Flatpak applications and runtimes",
        "argv": ["flatpak", "list", "--app", "--columns=application,version,origin"],
    },
    "audio": {
        "desc": "PipeWire / WirePlumber state and active sinks",
        "argv": ["bash", "-lc",
                 "systemctl --user is-active pipewire pipewire-pulse wireplumber 2>/dev/null;"
                 " wpctl status 2>/dev/null | head -40 || echo 'wpctl not available'"],
    },
    "controllers": {
        "desc": "Game controllers and input devices seen by the kernel",
        "argv": ["bash", "-lc",
                 "lsusb | grep -iE 'sony|xbox|nintendo|8bitdo|valve|logitech|razor|steam';"
                 " echo '--- hid ---'; ls /dev/input/by-id 2>/dev/null | head -20;"
                 " echo '--- steam input ---'; ls /dev/uinput /dev/uhid 2>/dev/null"],
    },
    "network": {
        "desc": "Network interfaces, addresses and default route",
        "argv": ["bash", "-lc",
                 "ip -br addr; echo '--- route ---'; ip route | head -10;"
                 " echo '--- dns ---'; resolvectl status 2>/dev/null | head -20"],
    },
    "gaming_stack": {
        "desc": "Steam, gamescope, mangohud, gamemode availability",
        "argv": ["bash", "-lc",
                 "for p in steam gamescope mangohud gamemoderun vkbasalt; do"
                 " printf '%-14s' \"$p\"; command -v $p || echo 'not found'; done;"
                 " echo '--- flatpak ---'; flatpak list --app --columns=application 2>/dev/null | grep -iE 'steam|heroic|lutris|bottles'"],
    },
    "unit_status": {
        "desc": "Status of one systemd unit (arg: unit)",
        "argv": [],  # built at call time
        "args": ["unit"],
    },
}


def tool_schema_text() -> str:
    lines = []
    for name, spec in TOOLS.items():
        args = spec.get("args") or []
        suffix = f" args={args}" if args else ""
        lines.append(f"  - {name}: {spec['desc']}{suffix}")
    return "\n".join(lines)


def run_tool(name: str, args: dict | None = None) -> str:
    spec = TOOLS.get(name)
    if not spec:
        return f"(unknown tool '{name}')"

    args = args or {}
    if spec["argv"]:
        return _run(list(spec["argv"]))

    if name == "unit_status":
        unit = str(args.get("unit", ""))
        if not SYSTEMD_UNIT_RE.match(unit):
            return "(refusing to inspect that unit: invalid name)"
        return _run(["systemctl", "status", unit, "--no-pager", "-l"])

    return "(tool not implemented)"


# ---------------------------------------------------------------------------
# System facts injected into every conversation
# ---------------------------------------------------------------------------
_facts_cache: tuple[float, str] | None = None


def os_release() -> dict:
    data = {}
    try:
        for line in Path("/usr/lib/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k] = v.strip().strip('"')
    except OSError:
        pass
    return data


def system_facts() -> str:
    global _facts_cache
    now = time.time()
    if _facts_cache and now - _facts_cache[0] < 90:
        return _facts_cache[1]

    rel = os_release()
    mem = "unknown"
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                mem = f"{int(line.split()[1]) / 1024 / 1024:.1f} GiB"
                break
    except OSError:
        pass

    gpu = "unknown"
    try:
        out = _run(["bash", "-lc", "lspci -nn | grep -iE 'vga|3d|display' | sed 's/.*: //'"], timeout=6)
        lines = [
            ln.strip()
            for ln in (out or "").splitlines()
            if ln.strip() and "not found" not in ln.lower() and not ln.startswith("(")
        ]
        if lines:
            gpu = "; ".join(lines[:2])[:120]
    except Exception:
        pass

    facts = "\n".join(
        [
            f"- OS: {rel.get('PRETTY_NAME', 'JARVIS')} (base: Bazzite / Fedora Atomic, image {rel.get('IMAGE_ID', 'jarvis-os')})",
            f"- Desktop: GNOME on {os.environ.get('XDG_SESSION_TYPE', 'wayland')}",
            f"- Kernel: {platform.release()}",
            f"- CPU: {platform.processor() or platform.machine()} ({os.cpu_count()} threads)",
            f"- Memory: {mem}",
            f"- GPU: {gpu}",
            "- Package model: immutable root (rpm-ostree + Flatpak + Homebrew). "
            "Apps are installed with 'flatpak install', system layering uses "
            "'rpm-ostree install' and needs a reboot.",
        ]
    )
    _facts_cache = (now, facts)
    return facts


SYSTEM_PROMPT = """\
You are JARVIS Assistant, the built-in AI helper of JARVIS OS — a gaming-focused
Linux distribution built on Bazzite (Fedora Atomic / bootc) with the GNOME desktop.

Your job: help the user with *this* machine. That means Linux, gaming (Steam,
Proton, gamescope, MangoHud, GameMode), drivers, controllers, audio, performance,
Flatpak/rpm-ostree, and general questions.

Current machine:
{facts}

House rules
-----------
1. Be concise and concrete. Prefer numbered steps and exact commands.
2. JARVIS is immutable. Never tell the user to edit files under /usr or to
   'dnf install' — use: flatpak install <app>, brew install <formula>,
   rpm-ostree install <pkg> (requires reboot), or ujust recipes.
3. When you need live data about this machine, do NOT guess. Emit exactly one
   fenced block and nothing else in that message:

   ```jarvis-tool
   {{"tool": "<name>", "args": {{}}}}
   ```

   Available tools:
{tools}

   After the tool output arrives you will be asked again — then answer normally.
4. Never invent command output. If a tool is not available, say so.
5. If the user asks for something that needs root (e.g. installing a driver),
   give the command and explain the reboot/pkexec implications.
6. Keep code blocks copy-pasteable, no placeholders like <your-gpu>.
7. You can *do* things on this machine, not just explain them. When the user asks
   you to open an app, change volume/brightness, control media, install software,
   manage services, set a timer or run a command, emit exactly one fenced block
   and nothing else in that message:

   ```jarvis-action
   {{"action": "<name>", "args": {{}}}}
   ```

   Available actions (safe runs immediately; moderate/high need user confirmation):
{actions}

   The server, not you, decides whether it ran. Never claim an action succeeded
   until the server sends its result. For 'command', the user must type or say
   'confirm command'.
8. Voice users speak short commands. Answer in one or two sentences: your reply
   is read aloud. Never read tool fences or JSON to the user.
"""


def action_schema_text() -> str:
    if not ACTION_MANAGER:
        return "  (computer control unavailable)"
    return "\n".join(
        f"  - {a['name']} [{a['risk']}]: {a['description']}"
        for a in ACTION_MANAGER.list_actions()
    )


def build_prompt() -> str:
    return SYSTEM_PROMPT.format(
        facts=system_facts(), tools=tool_schema_text(), actions=action_schema_text()
    )


# ---------------------------------------------------------------------------
# Ollama plumbing
# ---------------------------------------------------------------------------
def ollama_get(path: str, timeout: int = OLLAMA_TIMEOUT):
    req = urllib.request.Request(f"{OLLAMA}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def ollama_up() -> bool:
    try:
        ollama_get("/api/version", timeout=2)
        return True
    except Exception:
        return False


def installed_models() -> list[dict]:
    try:
        data = ollama_get("/api/tags")
        return [
            {
                "name": m.get("name"),
                "size": m.get("size", 0),
                "family": (m.get("details") or {}).get("family", ""),
                "params": (m.get("details") or {}).get("parameter_size", ""),
            }
            for m in data.get("models", [])
        ]
    except Exception:
        return []


TOOL_FENCE_RE = re.compile(r"```jarvis-tool\s*(\{.*?\})\s*```", re.DOTALL)
ACTION_FENCE_RE = re.compile(r"```jarvis-action\s*(\{.*?\})\s*```", re.DOTALL)


class Handler(BaseHTTPRequestHandler):
    server_version = f"jarvis-assist/{__version__}"

    # -- helpers ----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode())

    def _stream_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

    def _stream(self, obj):
        try:
            self.wfile.write((json.dumps(obj) + "\n").encode())
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            raise SystemExit

    def log_message(self, fmt, *args):  # quieter logs
        if os.environ.get("JARVIS_ASSIST_DEBUG"):
            sys.stderr.write("assist: " + fmt % args + "\n")

    # -- routes -----------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/index.html"):
            try:
                self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(500, b"UI missing", "text/plain")
            return

        if path == "/api/health":
            self._json(200, {"ok": True, "version": __version__})
            return

        if path == "/api/status":
            up = ollama_up()
            models = installed_models() if up else []
            self._json(
                200,
                {
                    "ollama": up,
                    "ollama_url": OLLAMA,
                    "default_model": DEFAULT_MODEL,
                    "models": models,
                    "facts": system_facts(),
                    "version": __version__,
                },
            )
            return

        if path == "/api/actions":
            self._json(200, {
                "actions": ACTION_MANAGER.list_actions() if ACTION_MANAGER else [],
                "available": bool(ACTION_MANAGER),
            })
            return

        if path == "/api/voice/status":
            self._json(200, voice_status_payload())
            return

        if path == "/api/voice/events":
            self._event_stream()
            return

        if path == "/favicon.ico":
            icon = Path("/usr/share/icons/hicolor/256x256/apps/jarvis-assist.png")
            if icon.exists():
                self._send(200, icon.read_bytes(), "image/png")
            else:
                self._send(404, b"", "image/png")
            return

        self._json(404, {"error": "not found"})

    def _event_stream(self):
        """Long-lived NDJSON stream of voice/action events for the UI."""
        sub = EVENT_BUS.subscribe()
        self._stream_start()
        self._stream({"type": "voice_state", **voice_state})
        try:
            while True:
                try:
                    event = sub.get(timeout=15)
                except queue.Empty:
                    event = {"type": "ping"}
                self._stream(event)
        except (SystemExit, BrokenPipeError, ConnectionResetError):
            pass
        finally:
            EVENT_BUS.unsubscribe(sub)

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json(400, {"error": "bad json"})
            return

        if path == "/api/chat":
            self.handle_chat(payload)
        elif path == "/api/pull":
            self.handle_pull(payload)
        elif path == "/api/actions/confirm":
            if not ACTION_MANAGER:
                self._json(503, {"error": "computer control unavailable"})
            else:
                confirmed = bool(payload.get("confirm", False))
                result = (
                    ACTION_MANAGER.confirm(
                        str(payload.get("id", "")), str(payload.get("phrase", "")), source="ui"
                    ) if confirmed else
                    ACTION_MANAGER.deny(str(payload.get("id", "")), source="ui")
                )
                publish({"type": "action", "result": result})
                if result.get("status") == "ok":
                    speak_async(result.get("message", ""))
                self._json(200, result)
        elif path == "/api/voice/start":
            self._json(200, start_voice_listener(
                payload.get("wake") if "wake" in payload else None
            ))
        elif path == "/api/voice/stop":
            self._json(200, stop_voice_listener())
        elif path == "/api/voice/toggle":
            result = (
                stop_voice_listener() if voice_state["listening"]
                else start_voice_listener()
            )
            self._json(200, result)
        elif path == "/api/voice/speak":
            text = str(payload.get("text", ""))[:MAX_SPEECH_CHARS]
            speak_async(text)
            self._json(200, {"status": "queued", "text": text})
        elif path == "/api/voice/config":
            if not VOICE_AVAILABLE:
                self._json(503, {"error": VOICE_IMPORT_ERROR or "voice modules missing"})
                return
            patch = {k: v for k, v in payload.items() if k in voice_config.DEFAULT_CONFIG}
            cfg = voice_config.save_config(patch)
            if "wake_enabled" in patch:
                _worker_command("WAKE_ON" if cfg["wake_enabled"] else "WAKE_OFF")
            publish({"type": "voice_config", "config": cfg})
            self._json(200, cfg)
        elif path == "/api/voice/reload":
            if voice_config.load_config().get("enabled", False):
                self._json(200, start_voice_listener())
            else:
                self._json(200, stop_voice_listener())
        elif path == "/api/voice/event":
            # Used by local helper processes. Only the loopback interface is bound.
            text = str(payload.get("text", "")).strip()
            if not text:
                self._json(400, {"error": "text required"})
                return
            publish({"type": "transcript", "text": text})
            threading.Thread(
                target=handle_voice_utterance, args=(text,), daemon=True
            ).start()
            self._json(200, {"status": "accepted"})
        else:
            self._json(404, {"error": "not found"})

    # -- chat -------------------------------------------------------------
    def handle_chat(self, payload: dict):
        model = payload.get("model") or DEFAULT_MODEL
        messages = payload.get("messages") or []
        allow_tools = bool(payload.get("tools", True))

        if not ollama_up():
            self._json(503, {"error": "ollama is not running", "hint": "systemctl status ollama"})
            return

        convo = [{"role": "system", "content": build_prompt()}] + messages
        self._stream_start()

        try:
            for round_no in range(3):  # tool round + action round + answer
                text = self._ollama_chat(model, convo, emit=True)

                # The model asked JARVIS to actually change something.
                action_match = ACTION_FENCE_RE.search(text or "")
                if action_match:
                    try:
                        call = json.loads(action_match.group(1))
                    except json.JSONDecodeError:
                        call = {}
                    action_name = str(call.get("action", ""))
                    if ACTION_MANAGER:
                        result = ACTION_MANAGER.prepare(
                            action_name, call.get("args") or {}, source="chat"
                        )
                    else:
                        result = {"status": "error", "message": "computer control unavailable"}
                    self._stream({"type": "action", "result": result})
                    publish({"type": "action", "result": result})
                    if result.get("status") == "confirmation_required":
                        confirmation = {
                            "type": "confirmation_required",
                            "id": result["id"],
                            "action": result["action"],
                            "message": result["message"],
                            "challenge": result.get("challenge", "confirm"),
                            "args": result.get("args", {}),
                        }
                        self._stream(confirmation)
                        publish(confirmation)
                    self._stream({"type": "reset_text"})
                    convo.append({"role": "assistant", "content": text})
                    convo.append({
                        "role": "user",
                        "content": (
                            "Computer-control result from the server: "
                            + json.dumps(result)
                            + ". Do not run another action; answer briefly."
                        ),
                    })
                    if result.get("status") == "confirmation_required":
                        break
                    continue

                tool_match = TOOL_FENCE_RE.search(text or "") if allow_tools else None
                if not tool_match:
                    break

                try:
                    call = json.loads(tool_match.group(1))
                except json.JSONDecodeError:
                    break

                name = str(call.get("tool", ""))
                if name not in TOOLS:
                    break

                self._stream({"type": "tool", "name": name, "desc": TOOLS[name]["desc"]})
                output = run_tool(name, call.get("args") or {})
                self._stream({"type": "tool_result", "name": name, "output": output})

                convo.append({"role": "assistant", "content": text})
                convo.append(
                    {
                        "role": "user",
                        "content": (
                            f"Tool output for `{name}`:\n\n```\n{output}\n```\n\n"
                            "Answer the user's question now using this data."
                        ),
                    }
                )
                self._stream({"type": "reset_text"})
            self._stream({"type": "done"})
        except BrokenPipeError:
            return
        except Exception as exc:
            try:
                self._stream({"type": "error", "error": str(exc)})
            except Exception:
                pass

    def _ollama_chat(self, model: str, messages: list[dict], emit: bool) -> str:
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": 0.4, "num_ctx": 8192},
            }
        ).encode()

        req = urllib.request.Request(
            f"{OLLAMA}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        collected: list[str] = []
        with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT) as resp:
            for raw in resp:
                if not raw.strip():
                    continue
                try:
                    chunk = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])
                piece = (chunk.get("message") or {}).get("content", "")
                if piece:
                    collected.append(piece)
                    # hold back text while it might still become a tool fence
                    if emit:
                        self._stream({"type": "token", "text": piece})
                if chunk.get("done"):
                    break
        return "".join(collected)

    # -- model pulls -------------------------------------------------------
    def handle_pull(self, payload: dict):
        model = payload.get("model")
        if not model:
            self._json(400, {"error": "model required"})
            return
        if not ollama_up():
            self._json(503, {"error": "ollama is not running"})
            return

        self._stream_start()
        body = json.dumps({"model": model, "stream": True}).encode()
        req = urllib.request.Request(
            f"{OLLAMA}/api/pull",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3600) as resp:
                for raw in resp:
                    if not raw.strip():
                        continue
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    self._stream(
                        {
                            "type": "progress",
                            "status": chunk.get("status", ""),
                            "completed": chunk.get("completed", 0),
                            "total": chunk.get("total", 0),
                        }
                    )
            self._stream({"type": "done"})
        except Exception as exc:
            self._stream({"type": "error", "error": str(exc)})


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((HOST, port))
            return True
        except OSError:
            return False


def serve(port: int = 8765) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, port), Handler)
    httpd.daemon_threads = True
    if (
        VOICE_AVAILABLE
        and os.environ.get("JARVIS_VOICE_AUTOSTART", "1") == "1"
        and voice_config.load_config().get("enabled", False)
    ):
        started = start_voice_listener()
        sys.stderr.write(f"jarvis-assist: voice listener {started.get('status', started)}\n")
    sys.stderr.write(f"jarvis-assist {__version__} listening on http://{HOST}:{port}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv: list[str]) -> int:
    port = 8765
    for i, arg in enumerate(argv):
        if arg in ("--port", "-p") and i + 1 < len(argv):
            port = int(argv[i + 1])
    if not UI_FILE.exists():
        sys.stderr.write(f"UI not found at {UI_FILE}\n")
        return 1
    return serve(port)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
