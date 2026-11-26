# JARVIS Assistant — how it works

A local, offline AI assistant that can inspect **and control** the machine it runs on.
For the speech stack see [VOICE-CONTROL.md](VOICE-CONTROL.md).

```
 ┌──────────────────────────┐        HTTP (127.0.0.1:8765)      ┌──────────────────────┐
 │  GTK4 window (WebKit)    │ ───────────────────────────────▶  │  jarvis-assist server │
 │  index.html (single file)│ ◀─────────── NDJSON stream ────── │  (python stdlib)     │
 └──────────────────────────┘                                   └──────────┬───────────┘
                                                                            │ HTTP
                                                              ┌─────────────▼───────────┐
                                                              │  Ollama 127.0.0.1:11434 │
                                                              │  models in /var/lib/... │
                                                              └─────────────────────────┘
```

| Piece | Path |
|---|---|
| Launcher (GTK4+WebKit 6 → GTK3+WebKit2 → browser) | `/usr/local/bin/jarvis-assist` → `/var/lib/jarvis/system/usr/bin/jarvis-assist` |
| Backend (stdlib only) | `/var/lib/jarvis/system/usr/lib/jarvis/assist/server.py` |
| UI (one self-contained HTML file) | `/var/lib/jarvis/system/usr/lib/jarvis/assist/ui/index.html` |
| Engine service | `/etc/systemd/system/ollama.service` |
| User service for the backend | `/usr/lib/systemd/user/jarvis-assist.service` |
| Voice modules (parser, actions, STT/TTS) | `/var/lib/jarvis/system/usr/lib/jarvis/voice/` |

The backend binds to `127.0.0.1` only. There is no telemetry, no account, and no
outbound request except the model downloads you trigger yourself.

The same HTTP port also carries an NDJSON event stream (`/api/voice/events`) used
by the UI for live transcripts, action results and confirmation cards.

---

## The tool loop

A chat model alone cannot know why *your* controller is not detected. So the
assistant is instructed: **if you need live data, ask for a tool, don't guess.**

```
user ──▶ model ──▶ ```jarvis-tool {"tool":"gpu_driver"} ``` ──▶ server runs it
                                                                     │
user ◀── final answer ◀── model ◀── tool output injected ◀───────────┘
```

One tool round per message, maximum two. If the model hallucinates a tool name,
the server refuses and the model has to answer without it.

### The complete allowlist

Every tool is a **fixed argv list** — no shell, no user- or model-supplied
arguments, read-only commands only:

| Tool | What it reports |
|---|---|
| `os_info` | JARVIS version, image, rpm-ostree deployment |
| `hardware` | CPU, GPU, memory, kernel |
| `gpu_driver` | `nvidia-smi`, loaded graphics modules, Mesa version, `glxinfo` |
| `services_failed` | failed system + user units |
| `recent_errors` | journal warnings/errors for this boot |
| `disk_usage` | `df`, deployment size |
| `flatpaks` | installed apps and runtimes |
| `audio` | PipeWire/WirePlumber state, sinks |
| `controllers` | USB input devices, `/dev/input/by-id`, `uinput` |
| `network` | interfaces, routes, DNS |
| `gaming_stack` | steam / gamescope / mangohud / gamemode / vkbasalt presence |
| `unit_status` | `systemctl status` for **one** unit (name validated against `^[A-Za-z0-9@._:-]{1,80}$`) |

Adding your own: append to the `TOOLS` dict in `server.py`. Keep it read-only.

---

## The action loop

Read-only diagnostics answer questions. To *do* something, the model can emit a
second kind of fence:

```
user ──▶ model ──▶ ```jarvis-action {"action":"open_app","args":{"app":"steam"}} ```
                                      │
                     server validates + executes  ◀── user confirms if risky
                                      │
user ◀── short answer ◀── model ◀── result event ◀──┘
```

The action registry lives in `voice/actions.py` in this repo (installed under `/var/lib/jarvis/system/usr/lib/jarvis/voice/`). It is a small
allowlist with fixed argv lists and strict validators; nothing model-supplied
is passed to a shell. Actions are graded:

* **safe** — runs immediately (open apps, volume, brightness, media, screenshot,
  timer, diagnostics);
* **moderate** — one confirmation (close app, user service, Wi-Fi);
* **high** — confirmation with the phrase `confirm` / `confirm command`
  (install or remove apps, system update, system services, power, shell commands).

The confirmation is shown in the assistant UI and can also be spoken. Every
attempt is written to `~/.config/jarvis/voice/audit.jsonl`.

`GET /api/actions` lists the registry with risk levels. `POST
/api/actions/confirm` resolves a pending prompt. See
[VOICE-CONTROL.md](VOICE-CONTROL.md#safety-model) for the full safety notes.

---

## Models

The wizard detects RAM/VRAM and picks one. Manual control:

```bash
ujust jarvis-ai-models                       # interactive picker, sets the default
ollama pull qwen3:8b                        # or directly
echo "qwen3:8b" > ~/.config/jarvis/default-model
ollama list
ollama rm qwen3:1.7b
```

Anything in [Ollama's library](https://ollama.com/library) works; the UI lists
whatever is installed.

### GPU acceleration

Ollama auto-detects at start:

```bash
journalctl -u ollama -b | grep -iE 'gpu|cuda|rocm|vulkan'
```

* **AMD** — ROCm libraries are bundled with the Ollama tarball. On RDNA cards you
  should see `library=rocm`.
* **NVIDIA** — needs the NVIDIA build of JARVIS (`jarvis-os-nvidia`); the driver is
  in the image, Ollama picks up `libcuda`.
* **Fallback** — CPU inference always works, just slower (~5–15 tok/s for a 4B
  model on a modern laptop chip).

### Memory

Models are held in RAM/VRAM for 24 h (`OLLAMA_KEEP_ALIVE=24h`) so the second
question is instant. If gaming performance matters more, lower it:

```bash
sudo systemctl edit ollama
# [Service]
# Environment="OLLAMA_KEEP_ALIVE=5m"
```

---

## Using a cloud model instead (optional)

The backend talks the Ollama HTTP API. Anything that speaks the same API works —
including a gateway in front of OpenAI/Anthropic/Gemini. To switch, edit
`JARVIS_OLLAMA_URL` in `/usr/lib/systemd/user/jarvis-assist.service`:

```ini
Environment="JARVIS_OLLAMA_URL=http://127.0.0.1:4000"
```

…and run a shim on that port. **Note this breaks the offline guarantee** — the
prompt (including tool output such as your journal) then leaves the machine. That
is why JARVIS ships local-only by default.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| “engine offline” | `sudo systemctl enable --now ollama` |
| “backend not reachable” | `systemctl --user restart jarvis-assist` |
| “no model installed” | `ujust jarvis-ai-models` |
| Model downloaded but not listed | `ollama list`; the UI refreshes every 20 s |
| Very slow answers | You are on CPU, or the model is too big — drop to `qwen3:1.7b` |
| Window does not open | `jarvis-assist` from a terminal to see the error; it falls back to your browser |
| “refusing to inspect that unit” | The model asked for an invalid unit name — ask again, naming the service |

Logs:

```bash
journalctl -u ollama -b -f
systemctl --user status jarvis-assist
JARVIS_ASSIST_DEBUG=1 jarvis-assist --serve
```

### What can be changed

The assistant can no longer be described as read-only: it has a documented
action allowlist. What it cannot do is make arbitrary unvalidated changes:
actions use fixed argv lists, risky ones require confirmation, and everything is
audited. Diagnostics remain strictly read-only. If you prefer the old behaviour,
turn voice control off and avoid asking the model to act — the model cannot
invoke actions without the server accepting them first.
