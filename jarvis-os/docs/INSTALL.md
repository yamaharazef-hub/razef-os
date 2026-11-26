# Installing JARVIS on Bazzite

This is a **layer on top of an existing Bazzite / Fedora Atomic installation**.
It does not replace your OS, rebase an image or touch your files.

## Install

```bash
git clone <this-repo> jarvis
cd jarvis/jarvis-os
./install.sh
```

The script re-execs itself with `sudo`. It is idempotent — running it again
repairs or refreshes the installation.

### What it does

| Step | Detail |
|---|---|
| Payload | copies `system_files/overrides/usr/lib/jarvis` to `/var/lib/jarvis/system` |
| Commands | symlinks `jarvis-assist`, `jarvis-voice`, `jarvis-firstboot` into `/usr/local/bin` |
| Desktop | installs `.desktop` files and icons under `/usr/local/share` |
| Ollama | downloads the static tarball into `/var/lib/jarvis/system/ollama` if `ollama` is not on `PATH`; enables `/etc/systemd/system/ollama.service` |
| Voice | creates `/var/lib/jarvis/voice/venv`, installs Vosk, downloads Piper and the offline models |
| User service | writes `~/.config/systemd/user/jarvis-assist.service` for the invoking user and starts it |
| Branding | sets the wallpaper and `Super+Alt+J` for the invoking user |

Only `/etc/systemd/system/ollama.service` and a few dconf keys are written
outside `/var` and `/usr/local`.

### Options

```bash
./install.sh --no-models       # skip speech + model downloads
./install.sh --no-branding     # keep wallpaper and shortcuts as they are
./install.sh --uninstall       # remove the JARVIS layer
```

Uninstall leaves `/var/lib/ollama/models` and your AI model choice alone.

## After installing

```bash
ujust jarvis-ai                # open the assistant
ujust jarvis-ai-models         # download a local model (1–8 GB)
ujust jarvis-voice-status      # voice engine state
ujust jarvis-voice-test        # offline self-test
```

Say **“Jarvis, volume up”** or press **Super+Alt+J** to start voice control.
Log out and back in once if the hotkey or user service is not active yet.

## Requirements

* Bazzite / Fedora Atomic with GNOME.
* `sudo`, `curl`, `python3`. `zstd` is needed only when Ollama is not already installed.
* Network access for the one-time downloads; everything runs offline afterwards.
* 8 GB RAM minimum, 16 GB recommended, plus disk for the AI model you pick.

## Updating JARVIS

```bash
cd jarvis/jarvis-os
git pull
./install.sh                   # re-copies payload and refreshes services
```

Bazzite system updates do not remove JARVIS because the payload lives in `/var`.

## Troubleshooting

```bash
journalctl --user -u jarvis-assist -f
systemctl status ollama --no-pager
jarvis-voice status
```

See **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** for a longer list.
