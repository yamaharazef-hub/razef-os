# Troubleshooting

## Build

**`ERROR: jarvis plymouth theme is NOT in the initramfs`**

The build deliberately fails here: without the theme in the initramfs the system
boots with Fedora's default splash, which is the one thing that must not silently
happen. Causes:

* `/etc/plymouth/plymouthd.conf` was not written (check for an existing file being
  replaced by the base image — it should not be),
* dracut's plymouth module did not pick up the theme directory.

Debug locally:

```bash
podman run --rm -it localhost/jarvis-os:stable \
  bash -c 'cat /etc/plymouth/plymouthd.conf; ls /usr/share/plymouth/themes/jarvis; lsinitrd /usr/lib/modules/*/initramfs.img | grep plymouth'
```

**`dnf5: package X not found`**

The base image's repos are Bazzite's. Some Fedora packages are excluded or
replaced (Mesa comes from `terra-mesa`, for example). Check with:

```bash
podman run --rm -it ghcr.io/ublue-os/bazzite-gnome:stable dnf5 search <pkg>
```

**Build works but the image is huge / updates take forever**

Remove packages from `build_files/build.sh` rather than adding. Each `RUN` layer
counts; the CI rechunks afterwards to shrink deltas.

---

## Boot

**Blank screen with a text cursor, then the desktop**

The splash is drawn by plymouth in the initramfs. If `rhgb`/`quiet` was removed
from the kernel command line, or you booted an old deployment from GRUB, you get
the text boot instead. Verify:

```bash
cat /proc/cmdline        # should contain "quiet rhgb"
```

**Splash appears late or flickers**

Usually a very slow GPU firmware load. Bazzite ships `bgrt`-style behaviour on
some hardware; JARVIS forces its own theme so the flicker is the hand-off from
firmware to plymouth. Not fixable from the image.

**Wrong resolution / stretched logo**

`watermark.png` is scaled by plymouth at native resolution. If your panel is
ultra-wide the mark stays centred and proportional; if it looks stretched, the
GPU driver handed over a non-native mode — check `video=` in
`/proc/cmdline`.

**Boot menu still says Fedora / Bazzite**

```bash
ujust jarvis-rebrand-bootloader
```

The BLS entry titles come from `PRETTY_NAME`, which is JARVIS after the image
update lands. If you rebased manually, make sure you're actually booting the
JARVIS deployment: `rpm-ostree status`.

---

## Setup wizard

**Never appeared**

```bash
/usr/bin/jarvis-firstboot --force        # run it now
cat /var/lib/jarvis/firstboot.json       # state (delete to reset)
cat /etc/xdg/autostart/jarvis-firstboot.desktop
```

It only autostarts inside GNOME (`OnlyShowIn=GNOME;`) and after the session has
been up for ~8 s.

**A task failed**

The wizard keeps going and logs the error. Most failures are network related
(Flathub/ollama download) or a Flatpak that is already installed at a system
level. Re-run the individual thing:

```bash
flatpak install -y flathub com.valvesoftware.Steam
sudo systemctl enable --now ollama && ollama pull qwen3:4b
```

**Asked for a password it shouldn't need**

Only these steps escalate with `pkexec`: enabling `ollama.service` and rewriting
`/etc/default/grub`. Everything else is user-scope.

---

## Assistant

See [AI-ASSISTANT.md → Troubleshooting](AI-ASSISTANT.md#troubleshooting).

Quick triage:

```bash
systemctl status ollama --no-pager          # is the engine up?
systemctl --user status jarvis-assist        # is the backend up?
curl -s 127.0.0.1:8765/api/status | jq .    # what does it think?
curl -s 127.0.0.1:11434/api/tags | jq .     # which models does ollama see?
```

---

## Voice control

```bash
jarvis-voice status          # config, models, backend state
jarvis-voice test            # offline self-test
jarvis-voice listen-once     # say one command and see what was heard
journalctl --user -u jarvis-assist -f
tail -f ~/.config/jarvis/voice/audit.jsonl
```

| Symptom | Fix |
|---|---|
| UI says “Run `jarvis-voice setup` first” | `ujust jarvis-voice-setup`; check `/usr/lib/jarvis/voice/venv` exists |
| No speech model after setup | check disk space; `jarvis-voice setup` again with network up |
| `arecord` not found | `sudo rpm-ostree install alsa-utils` and reboot, or rebuild the image |
| Piper missing / robotic voice | `jarvis-voice setup` downloads the ONNX voice; `espeak-ng` is the fallback |
| Wake word never triggers | `jarvis-voice set --wake`; try push-to-talk (`Super+Alt+J`) |
| It reacts to its own replies | enable the wake word, or use headphones |
| “confirmation expired” | the backend restarted; say the command again |
| Arbitrary command refused | `ujust jarvis-voice-unlock on` (still needs “confirm command”) |

The listener is a child of the assistant backend, so restarting
`jarvis-assist.service` also restarts speech recognition.

---

## Installing / switching images

**`bootc switch` fails with “denied” or “not found”**

The GHCR package is private by default. Make it public
(Packages → jarvis-os → Package settings → Change visibility), or
`podman login ghcr.io` first when pulling manually.

**Signature enforcement fails**

You switched with `--enforce-container-sigpolicy` but the image was not signed
(no `SIGNING_SECRET` in the repo). Either add the secret or drop the flag.

**NVIDIA drivers gone after switching**

You switched to the AMD/Intel image. Switch back:

```bash
sudo bootc switch ghcr.io/<you>/jarvis-os-nvidia:stable
```

**Stuck at boot after switching**

In GRUB, choose the *previous* deployment. Then:

```bash
rpm-ostree rollback
journalctl -b -1 -p err        # what failed on the bad boot
```

---

## Getting help

Collect this and open an issue:

```bash
ujust jarvis-about
rpm-ostree status --booted
journalctl -b -p warning --no-pager | tail -50
systemctl --failed --no-pager
```
