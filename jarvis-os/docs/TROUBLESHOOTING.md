# Troubleshooting

## Installer

**`ujust jarvis-ai`: “justfile does not contain recipe”**

The installed `ujust` wrapper composes the system justfile with JARVIS recipes.
Re-run `./install.sh`, then open a new shell (`hash -r`). Check with:

```bash
ujust --list | grep jarvis
cat ~/.config/jarvis/justfile
```

**`jarvis-voice: command not found`**

The symlinks live in `/usr/local/bin`. Open a new shell, or re-run:

```bash
cd jarvis/jarvis-os && sudo ./install.sh
```

**`zstd is required to unpack Ollama`**

Ollama's current tarball is zstd-compressed. Install the tool and re-run:

```bash
rpm-ostree install zstd
sudo reboot
cd jarvis/jarvis-os && ./install.sh
```

**`could not start ollama.service`**

Check it manually:

```bash
systemctl status ollama --no-pager
journalctl -u ollama -b -f
```

**User service did not start**

User services need an active graphical session. Log out and back in, then:

```bash
systemctl --user enable --now jarvis-assist.service
systemctl --user status jarvis-assist
```

**Wallpaper or hotkey did not change**

Log out and back in. The installer writes per-user dconf keys for the user who
ran `sudo`; if you installed as a different user, run `./install.sh` as them.

---

## Assistant

```bash
ujust jarvis-ai-status
curl -s 127.0.0.1:8765/api/status | jq .
journalctl --user -u jarvis-assist -f
```

| Symptom | Fix |
|---|---|
| “engine offline” | `sudo systemctl enable --now ollama` |
| “backend not reachable” | `systemctl --user restart jarvis-assist` |
| “no model installed” | `ujust jarvis-ai-models` |
| Model downloaded but not listed | `ollama list`; the UI refreshes every 20 s |
| Very slow answers | use a smaller model, e.g. `qwen3:1.7b` |
| Window does not open | `jarvis-assist` from a terminal to see the error |

---

## Voice control

```bash
jarvis-voice status          # config, models, backend state
jarvis-voice test            # offline self-test
jarvis-voice listen-once     # say one command and see what was heard
tail -f ~/.config/jarvis/voice/audit.jsonl
```

| Symptom | Fix |
|---|---|
| “Run `jarvis-voice setup` first” | `ujust jarvis-voice-setup`; check `/var/lib/jarvis/voice/venv` exists |
| `arecord` not found | install `alsa-utils`: `rpm-ostree install alsa-utils && reboot` |
| Robotic voice | Piper model missing; `jarvis-voice setup`; espeak-ng is the fallback |
| Wake word never triggers | `jarvis-voice set --wake`; try push-to-talk (`Super+Alt+J`) |
| It reacts to its own replies | enable the wake word or use headphones |
| “confirmation expired” | the backend restarted; say the command again |
| Arbitrary command refused | `ujust jarvis-voice-unlock on` (still needs “confirm command”) |

The listener runs inside the assistant backend, so restarting
`jarvis-assist.service` also restarts speech recognition.

---

## Removing JARVIS

```bash
cd jarvis/jarvis-os
sudo ./install.sh --uninstall
```

This removes the payload, commands, desktop entries, user service and voice
data. Ollama and its models are left in place.
