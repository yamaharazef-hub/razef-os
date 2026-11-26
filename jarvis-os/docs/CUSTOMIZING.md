# Customising JARVIS

JARVIS is a plain directory of files installed on top of Bazzite. Nothing is
patched by hand: edit a file here, re-run `./install.sh`, and the change is live.

---

## 1. Names and appearance

| What | File |
|---|---|
| Assistant name / system prompt | `system_files/overrides/usr/lib/jarvis/assist/server.py` |
| UI title, colours, quick actions | `system_files/overrides/usr/lib/jarvis/assist/ui/index.html` |
| App names and comments | `system_files/overrides/usr/share/applications/*.desktop` |
| Wallpaper + hotkey defaults | `install.sh` (function `install_branding`) |
| Logo, splash, wallpaper source art | `branding/master/` |

To change the whole name, the repo still contains `scripts/rename.sh`, which
rewrites text files in this checkout:

```bash
./scripts/rename.sh "Friday" friday
./install.sh --uninstall && ./install.sh
```

Uninstall first so old command names are removed from `/usr/local/bin`.

---

## 2. Branding artwork

```
branding/master/logo.png        bright mark on a pure black background, square
branding/master/wallpaper.png   any 16:9 image, 2560x1440 or larger
```

```bash
python3 -m pip install pillow numpy
python3 branding/build-assets.py
```

This produces the logo sizes, boot splash, 36-frame spinner and wallpapers in
`branding/assets/`. Re-run `./install.sh` to reinstall the wallpaper and icons.

Change the palette with `CYAN` / `VIOLET` at the top of
`branding/build-assets.py`, and `--cyan` / `--violet` in the assistant UI CSS.

> The Plymouth boot splash belongs to the OS image and is **not** changed by the
> installer. Changing it on an installed Atomic system requires
> `rpm-ostree usroverlay` and an initramfs rebuild, which does not survive the
> next update. JARVIS therefore focuses on the logged-in desktop.

---

## 3. Adding packages

JARVIS does not layer RPMs for you. Install extras yourself with the normal
Atomic workflow:

```bash
flatpak install flathub com.example.App     # preferred, no reboot
rpm-ostree install htop                     # needs a reboot
ujust update                                # or: rpm-ostree upgrade
```

The voice installer needs `alsa-utils` for capture and `espeak-ng` as a TTS
fallback. If they are missing, install them before running `./install.sh`.

---

## 4. Adding voice commands and actions

Add a phrase mapping to `system_files/overrides/usr/lib/jarvis/voice/commands.py`
(`parse()`), and a matching `ActionSpec` in `voice/actions.py`. Keep the action
read-only unless it really needs to change state, and always build a **fixed
argv list** from validated arguments:

```python
ActionSpec(
    "my_action", "What it does", SAFE,
    lambda args: ["fixed-command", "--flag", validate(args["value"])],
    examples=("jarvis do the thing",),
)
```

Add a test in `tests/test_voice.py` and run:

```bash
cd jarvis-os
python3 -m unittest discover -s tests -v
./install.sh
```

The UI discovers actions automatically via `GET /api/actions`.

---

## 5. Desktop defaults

The installer writes per-user dconf keys for the invoking user (background and
`Super+Alt+J`). To ship different defaults, edit `install_branding()` in
`install.sh`. GVariant values need quoting, for example:

```bash
dconf write /org/gnome/desktop/background/picture-uri \
  "'file:///var/lib/jarvis/branding/wallpaper.png'"
```

---

## 6. Where things are installed

| Source | Installed location |
|---|---|
| `system_files/overrides/usr/lib/jarvis/` | `/var/lib/jarvis/system/usr/lib/jarvis/` |
| `system_files/overrides/usr/bin/` | `/var/lib/jarvis/system/usr/bin/` + symlinks in `/usr/local/bin` |
| `system_files/overrides/usr/share/applications/` | `/usr/local/share/applications/` |
| `system_files/overrides/usr/share/ublue-os/just/` | `/usr/local/share/ublue-os/just/` and `~/.config/ublue-os/just/` |
| `system_files/overrides/etc/systemd/system/ollama.service` | `/etc/systemd/system/ollama.service` |
| `branding/assets/wallpaper.png` | `/var/lib/jarvis/branding/wallpaper.png` |
| voice models | `/var/lib/jarvis/voice/models/` |
| user service | `~/.config/systemd/user/jarvis-assist.service` |

Because the payload is under `/var`, a Bazzite update never overwrites it.
Re-running `./install.sh` after `git pull` is the update path for JARVIS.
