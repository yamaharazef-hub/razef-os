# JARVIS — assistant, voice control and desktop for Bazzite

This repo turns an **already installed Bazzite / Fedora Atomic** system into a
JARVIS system. It is **not** a custom OS image any more: there is no bootc
rebase, no GHCR image to pull and no installer ISO to build.

Run one script on your machine and you get:

| | What | Where it lives |
|---|---|---|
| 🤖 | **JARVIS Assistant** — local, offline AI that inspects and controls the machine | `system_files/overrides/usr/lib/jarvis/assist/` |
| 🎙️ | **Voice control** — wake word, speech recognition, spoken replies and commands | `system_files/overrides/usr/lib/jarvis/voice/` |
| 🖥️ | **Desktop integration** — app entries, icons, wallpaper and `Super+Alt+J` | `branding/`, `system_files/overrides/usr/share/` |
| ⚙️ | **Services** — Ollama engine + user backend, both managed by systemd | installed into `/etc/systemd` and `~/.config/systemd` |

Everything stays local. No account, no telemetry, no cloud speech API.

---

## Install

On the machine that already runs Bazzite:

```bash
git clone <this-repo> jarvis
cd jarvis/jarvis-os
./install.sh
```

The script asks for sudo itself. It installs:

* the JARVIS payload in `/var/lib/jarvis/system` (survives atomic updates),
* commands in `/usr/local/bin`: `jarvis-assist`, `jarvis-voice`, `jarvis-firstboot`,
* desktop entries and icons in `/usr/local/share`,
* the Ollama service in `/etc/systemd/system` (downloads Ollama if missing),
* a user service for the assistant backend,
* offline speech models in `/var/lib/jarvis/voice/models`,
* the JARVIS wallpaper and the `Super+Alt+J` voice hotkey.

Options:

```bash
./install.sh --no-models       # skip ~100 MB of speech model downloads
./install.sh --no-branding     # keep your wallpaper and keybindings
./install.sh --uninstall       # remove the JARVIS layer
```

Then:

```bash
ujust jarvis-ai                # open the assistant
ujust jarvis-voice-status      # is the listener ready?
ujust jarvis-ai-models         # pull a local AI model
```

Full details: **[docs/INSTALL.md](docs/INSTALL.md)** and
**[QUICKSTART.md](QUICKSTART.md)**.

---

## Using it

Say **“Jarvis, open Steam”**, **“Jarvis, volume up”** or **“Jarvis, check the GPU
driver”**. Press **Super+Alt+J** or the microphone button to toggle listening.

The assistant can also act: apps, volume, brightness, media, screenshots, timers,
Flatpaks, services, updates, power and shell commands. Safe actions run
immediately; anything risky shows a confirmation card and must be confirmed by
voice or by typing **confirm**. Every attempt is audited.

```bash
jarvis-voice status            # config, models, backend state
jarvis-voice test              # offline self-test
jarvis-voice listen-once       # try speech recognition
jarvis-voice set --no-wake     # disable wake word
ujust jarvis-voice-unlock off  # forbid arbitrary shell commands
```

More: **[docs/VOICE-CONTROL.md](docs/VOICE-CONTROL.md)** and
**[docs/AI-ASSISTANT.md](docs/AI-ASSISTANT.md)**.

---

## Repository layout

```
jarvis-os/
├── install.sh                 # the installer for an existing Bazzite system
├── branding/
│   ├── master/                # source art: logo.png, wallpaper.png
│   ├── assets/                # generated logos, splash, spinner, wallpapers
│   └── build-assets.py        # regenerates assets from master/
├── system_files/overrides/    # payload copied to /var/lib/jarvis/system
│   ├── etc/systemd/system/ollama.service
│   └── usr/
│       ├── bin/               # jarvis-assist, jarvis-voice, jarvis-firstboot
│       ├── lib/jarvis/assist/ # stdlib backend + single-file WebKit UI
│       ├── lib/jarvis/voice/  # parser, actions, Vosk worker, Piper TTS
│       └── share/             # desktop entries, ujust recipes, dconf defaults
├── docs/                      # INSTALL, QUICKSTART, AI-ASSISTANT, VOICE-CONTROL, …
└── tests/test_voice.py        # unit + smoke tests
```

Why `/var/lib/jarvis/system`? Bazzite's `/usr` is immutable and resets on
update. `/var` and `/usr/local` are writable and persistent, so the install
survives updates without layering RPMs or rebooting into a new deployment.

---

## Requirements

* Bazzite / Fedora Atomic with GNOME (Wayland or X11).
* Network access once, to download Ollama and the speech models. After that it
  works with the cable unplugged.
* `sudo`, `curl`, `python3`, and `zstd` if Ollama is not already installed.
* 8 GB RAM works, 16 GB is comfortable. Plan ~100 MB for speech models plus
  1–8 GB for the AI model you choose.

---

## Development

Run the tests without installing anything:

```bash
cd jarvis-os
python3 -m unittest discover -s tests -v
```

Regenerate artwork after changing `branding/master/`:

```bash
python3 -m pip install pillow numpy
python3 branding/build-assets.py
```

Customising actions and voice commands:
**[docs/CUSTOMIZING.md](docs/CUSTOMIZING.md)**.

---

## Credits

Built on [Bazzite](https://github.com/ublue-os/bazzite) and the
[Universal Blue](https://github.com/ublue-os) tooling. JARVIS-specific code is
Apache-2.0 — see [LICENSE](LICENSE). “Bazzite” remains a trademark of the
Universal Blue project.
