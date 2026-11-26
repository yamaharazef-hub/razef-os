# QUICKSTART — JARVIS on an installed Bazzite system

You already have Bazzite. This repo is the JARVIS layer you run on top of it —
no custom OS image, no rebase, no ISO.

```
Bazzite (already installed)
   │
   └── jarvis-os/install.sh
          ├── assistant + voice → /var/lib/jarvis/system
          ├── commands         → /usr/local/bin
          ├── services         → /etc/systemd + ~/.config/systemd
          └── wallpaper/hotkey → your desktop
```

---

## 1. Install (5–10 minutes)

```bash
git clone <this-repo> jarvis
cd jarvis/jarvis-os
./install.sh
```

The script asks for sudo. It downloads Ollama if needed, the offline speech
models, and installs the desktop integration. If you want to skip downloads:

```bash
./install.sh --no-models
```

Afterwards, open a terminal or log out and back in so systemd user services and
keybindings are picked up.

---

## 2. First checks

```bash
ujust jarvis-ai-status        # engine + installed models
ujust jarvis-voice-status     # listener + speech models
ujust jarvis-voice-test       # self-test (no microphone needed)
jarvis-voice listen-once      # speak once, see what was heard
```

If no AI model is installed yet:

```bash
ujust jarvis-ai-models
```

---

## 3. Use it

* Open **JARVIS Assistant** from the app grid, or run `ujust jarvis-ai`.
* Press **Super+Alt+J** or click the microphone button to toggle listening.
* Say **“Jarvis, volume up”**, **“Jarvis, open Steam”**,
  **“Jarvis, check the GPU driver”**.

Risky commands show a confirmation card. Say or type **confirm** to allow them;
otherwise say **cancel**. Every action is logged to
`~/.config/jarvis/voice/audit.jsonl`.

---

## 4. Common tasks

```bash
ujust jarvis-setup            # re-run the first-run wizard
ujust jarvis-ai-models        # switch local AI model
ujust jarvis-voice-setup      # re-download speech models
ujust jarvis-voice-unlock off # forbid arbitrary shell commands
sudo ./install.sh --uninstall # remove everything
```

---

## 5. If something goes wrong

| Symptom | Fix |
|---|---|
| `jarvis-voice: command not found` | re-run `./install.sh`, or open a new shell |
| “voice backend not reachable” | `systemctl --user restart jarvis-assist` |
| “speech model missing” | `ujust jarvis-voice-setup` |
| Ollama offline | `sudo systemctl enable --now ollama` |
| No AI model | `ujust jarvis-ai-models` |
| Hotkey does nothing | log out/in, or check GNOME → Keyboard Shortcuts |
| Assistant window missing | `ujust jarvis-ai` from a terminal |

More: **[docs/INSTALL.md](docs/INSTALL.md)**,
**[docs/VOICE-CONTROL.md](docs/VOICE-CONTROL.md)**,
**[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**.
