# Razef Assistant — how it works

A local, offline AI assistant that can inspect the machine it runs on.

```
 ┌──────────────────────────┐        HTTP (127.0.0.1:8765)      ┌──────────────────────┐
 │  GTK4 window (WebKit)    │ ───────────────────────────────▶  │  razef-assist server │
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
| Launcher (GTK4+WebKit 6 → GTK3+WebKit2 → browser) | `/usr/bin/razef-assist` |
| Backend (stdlib only) | `/usr/lib/razef/assist/server.py` |
| UI (one self-contained HTML file) | `/usr/lib/razef/assist/ui/index.html` |
| Engine service | `/etc/systemd/system/ollama.service` |
| User service for the backend | `/usr/lib/systemd/user/razef-assist.service` |

The backend binds to `127.0.0.1` only. There is no telemetry, no account, and no
outbound request except the model download you trigger yourself.

---

## The tool loop

A chat model alone cannot know why *your* controller is not detected. So the
assistant is instructed: **if you need live data, ask for a tool, don't guess.**

```
user ──▶ model ──▶ ```razef-tool {"tool":"gpu_driver"} ``` ──▶ server runs it
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
| `os_info` | Razef version, image, rpm-ostree deployment |
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

## Models

The wizard detects RAM/VRAM and picks one. Manual control:

```bash
ujust razef-ai-models                       # interactive picker, sets the default
ollama pull qwen3:8b                        # or directly
echo "qwen3:8b" > ~/.config/razef/default-model
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
* **NVIDIA** — needs the NVIDIA build of Razef (`razef-os-nvidia`); the driver is
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
`RAZEF_OLLAMA_URL` in `/usr/lib/systemd/user/razef-assist.service`:

```ini
Environment="RAZEF_OLLAMA_URL=http://127.0.0.1:4000"
```

…and run a shim on that port. **Note this breaks the offline guarantee** — the
prompt (including tool output such as your journal) then leaves the machine. That
is why Razef ships local-only by default.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| “engine offline” | `sudo systemctl enable --now ollama` |
| “backend not reachable” | `systemctl --user restart razef-assist` |
| “no model installed” | `ujust razef-ai-models` |
| Model downloaded but not listed | `ollama list`; the UI refreshes every 20 s |
| Very slow answers | You are on CPU, or the model is too big — drop to `qwen3:1.7b` |
| Window does not open | `razef-assist` from a terminal to see the error; it falls back to your browser |
| “refusing to inspect that unit” | The model asked for an invalid unit name — ask again, naming the service |

Logs:

```bash
journalctl -u ollama -b -f
systemctl --user status razef-assist
RAZEF_ASSIST_DEBUG=1 razef-assist --serve
```

### Nothing is ever written

The assistant cannot modify your system — not the model, not the UI. Anything
that changes state (installing, enabling services) is *suggested* as a command
for you to run. That is a deliberate safety boundary.
