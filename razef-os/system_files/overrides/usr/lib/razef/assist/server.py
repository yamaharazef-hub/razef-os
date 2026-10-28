#!/usr/bin/env python3
"""
razef-assist - backend server
=============================

A dependency-free local web service that sits between the Razef Assistant UI
(/usr/lib/razef/assist/ui/index.html) and a local Ollama instance.

Why stdlib only?  Razef is an immutable image: fewer moving parts means fewer
things that can break after an update. This file uses nothing outside the
Python standard library, so it cannot be broken by a bad dependency bump.

Endpoints
---------
  GET  /                 the UI
  GET  /api/status       ollama reachable? which models? what hardware?
  POST /api/chat         {model, messages, tools} -> NDJSON stream
  POST /api/pull         {model}                   -> NDJSON progress stream
  GET  /api/health       liveness probe

Security model
--------------
  * binds to 127.0.0.1 only
  * the model can only ask for *read-only* diagnostics from a fixed allowlist
  * no shell=True anywhere, no arbitrary user/model supplied arguments

Run manually:  razef-assist --serve --port 8765
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

__version__ = "1.0.0"

HERE = Path(__file__).resolve().parent
UI_FILE = HERE / "ui" / "index.html"
OLLAMA = os.environ.get("RAZEF_OLLAMA_URL", "http://127.0.0.1:11434")
STATE_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "razef"


def _default_model() -> str:
    """Env var > ~/.config/razef/default-model > built-in default."""
    env = os.environ.get("RAZEF_DEFAULT_MODEL")
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
        "desc": "Razef/bootc version, image reference and update status",
        "argv": ["bash", "-lc",
                 "cat /usr/lib/os-release | grep -E '^(NAME|VERSION|PRETTY_NAME|IMAGE_ID|RAZEF)';"
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
            f"- OS: {rel.get('PRETTY_NAME', 'Razef')} (base: Bazzite / Fedora Atomic, image {rel.get('IMAGE_ID', 'razef-os')})",
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
You are Razef Assistant, the built-in AI helper of Razef OS — a gaming-focused
Linux distribution built on Bazzite (Fedora Atomic / bootc) with the GNOME desktop.

Your job: help the user with *this* machine. That means Linux, gaming (Steam,
Proton, gamescope, MangoHud, GameMode), drivers, controllers, audio, performance,
Flatpak/rpm-ostree, and general questions.

Current machine:
{facts}

House rules
-----------
1. Be concise and concrete. Prefer numbered steps and exact commands.
2. Razef is immutable. Never tell the user to edit files under /usr or to
   'dnf install' — use: flatpak install <app>, brew install <formula>,
   rpm-ostree install <pkg> (requires reboot), or ujust recipes.
3. When you need live data about this machine, do NOT guess. Emit exactly one
   fenced block and nothing else in that message:

   ```razef-tool
   {{"tool": "<name>", "args": {{}}}}
   ```

   Available tools:
{tools}

   After the tool output arrives you will be asked again — then answer normally.
4. Never invent command output. If a tool is not available, say so.
5. If the user asks for something that needs root (e.g. installing a driver),
   give the command and explain the reboot/pkexec implications.
6. Keep code blocks copy-pasteable, no placeholders like <your-gpu>.
"""


def build_prompt() -> str:
    return SYSTEM_PROMPT.format(facts=system_facts(), tools=tool_schema_text())


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


TOOL_FENCE_RE = re.compile(r"```razef-tool\s*(\{.*?\})\s*```", re.DOTALL)


class Handler(BaseHTTPRequestHandler):
    server_version = f"razef-assist/{__version__}"

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
        if os.environ.get("RAZEF_ASSIST_DEBUG"):
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

        if path == "/favicon.ico":
            icon = Path("/usr/share/icons/hicolor/256x256/apps/razef-assist.png")
            if icon.exists():
                self._send(200, icon.read_bytes(), "image/png")
            else:
                self._send(404, b"", "image/png")
            return

        self._json(404, {"error": "not found"})

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
            for round_no in range(2):  # one tool round-trip is plenty
                text = self._ollama_chat(model, convo, emit=True)

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
    sys.stderr.write(f"razef-assist {__version__} listening on http://{HOST}:{port}\n")
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
