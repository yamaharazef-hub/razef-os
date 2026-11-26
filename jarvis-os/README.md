# JARVIS OS

A custom operating system built on **[Bazzite](https://github.com/ublue-os/bazzite)** — the
gaming-focused Fedora Atomic image — with four things layered on top:

| | What | Where it lives |
|---|---|---|
| 🎨 | **Custom boot experience** — JARVIS splash, animated spinner, branded GRUB entry, wallpapers, icons | `branding/`, `system_files/overrides/usr/share/plymouth/` |
| 🧭 | **Post-install setup wizard** — first login asks what you want, then configures the machine | `system_files/overrides/usr/bin/jarvis-firstboot` |
| 🤖 | **Built-in AI assistant** — local, offline, inspects the machine *and controls it* | `system_files/overrides/usr/bin/jarvis-assist`, `.../usr/lib/jarvis/assist/` |
| 🎙️ | **JARVIS voice control** — offline wake word, speech recognition, spoken replies and voice commands | `.../usr/lib/jarvis/voice/`, `/usr/bin/jarvis-voice` |

![JARVIS boot splash](branding/boot-preview.png)

---

## What you are actually getting

JARVIS is **not** a fork of Bazzite. Bazzite is an *atomic* (immutable) OS: the root
filesystem is a signed container image, and every custom OS built on it is a
container image that starts `FROM ghcr.io/ublue-os/bazzite-gnome:stable` and adds
its own layer. That is exactly what `Containerfile` does here.

This means:

* **Updates keep working.** When Bazzite ships a new image, JARVIS rebuilds on top
  of it automatically (the CI runs daily) and your users get normal atomic updates.
* **Nothing is patched by hand.** Every change is a file in this repo, so the
  whole OS is reproducible and reviewable.
* **Rollback is free.** Every update keeps the previous deployment on disk.

Base: `ghcr.io/ublue-os/bazzite-gnome:stable` (Fedora 44, GNOME, Wayland).
An NVIDIA variant (`jarvis-os-nvidia`) is built from
`ghcr.io/ublue-os/bazzite-gnome-nvidia-open:stable` by the same workflow.

---

## Repository layout

```
.
├── Containerfile              # the whole OS build: FROM bazzite-gnome + our layer
├── image-template.env         # image name, vendor, tags
├── Justfile                   # build / rechunk / vm / lint recipes
├── build_files/
│   └── build.sh               # the actual build steps (packages, branding, initramfs)
├── branding/
│   ├── master/                # source art: logo.png, wallpaper.png
│   ├── build-assets.py        # generates every sized/format asset from master/
│   └── assets/                # generated: logos, splash, 36 spinner frames, wallpapers
├── system_files/overrides/    # files copied verbatim into the image (paths mirror /)
│   ├── etc/                   # ollama.service, autostart, dconf defaults
│   └── usr/
│       ├── bin/jarvis-assist          # AI assistant launcher (GTK4/WebKit)
│       ├── bin/jarvis-firstboot       # setup wizard (GTK4/libadwaita)
│       ├── bin/jarvis-voice           # voice CLI (setup, status, speak, test)
│       ├── lib/jarvis/assist/         # backend server + single-file UI
│       ├── lib/jarvis/voice/          # offline STT/TTS, command parser, actions
│       ├── share/plymouth/themes/jarvis/
│       └── share/ublue-os/just/60-jarvis.just   # ujust recipes
├── disk_config/               # bootc-image-builder configs (VM disk / ISO)
├── iso/flatpaks              # apps baked into the live ISO
├── .github/workflows/
│   ├── build.yml              # lint → build → rechunk → push → sign
│   └── build-iso.yml          # live installer ISO (titanoboa)
└── docs/                      # INSTALL, CUSTOMIZING, AI-ASSISTANT, TROUBLESHOOTING
```

---

## Quick start

### 1. Put this in your own GitHub repo

```bash
git init && git add -A && git commit -m "JARVIS OS"
gh repo create jarvis-os --public --source=. --push     # or create it in the UI
```

### 2. Let Actions build it

Push to `main`. The workflow runs:

```
just check → compile python → shellcheck → podman build → rechunk → push to ghcr.io/<you>/jarvis-os
```

The first build takes ~35–60 minutes (it pulls a ~4 GB base image). It then
rebuilds daily at 10:05 UTC so upstream Bazzite fixes flow in.

Make the package public afterwards: **GitHub → your profile → Packages →
jarvis-os → Package settings → Change visibility → Public**. (Anonymous
installs need this.)

### 3. Install it

**From an existing Bazzite / Fedora Atomic machine (no reinstall, keeps files):**

```bash
sudo bootc switch ghcr.io/<your-username>/jarvis-os:stable
sudo systemctl reboot
```

<details>
<summary>Older rpm-ostree systems</summary>

```bash
sudo rpm-ostree rebase ostree-image-signed:docker://ghcr.io/<your-username>/jarvis-os:stable
sudo systemctl reboot
```
</details>

**On NVIDIA hardware:** switch to `jarvis-os-nvidia` instead. Don't mix — the
NVIDIA image carries the driver.

**Clean install on a new machine:**

1. Grab an ISO: **Actions → “Build JARVIS OS installer ISO” → Run workflow**, then
   download the artifact (or `just build-iso` locally).
2. Write it to a USB stick: `sudo dd if=jarvis-stable-live-amd64.iso of=/dev/sdX bs=4M status=progress oflag=sync`
3. Boot it, install, reboot.

Full details and rollback instructions: **[docs/INSTALL.md](docs/INSTALL.md)**

### 4. First login

The **JARVIS Setup** wizard opens automatically. It will:

* detect your RAM/GPU and recommend a local AI model
* install Flathub + the apps you tick
* enable the local AI engine and download the model
* download the offline speech models and enable hands-free voice control
* apply the JARVIS look (dark theme, wallpaper, dock)
* brand the GRUB entry

Anything you skip can be re-run later with `ujust jarvis-setup`.

---

## The AI assistant

`JARVIS Assistant` is in the app grid and in the dock. It is **fully local**:
a bundled [Ollama](https://ollama.com) instance runs on `127.0.0.1:11434` and a
stdlib-only Python service (`jarvis-assist`) serves a WebKit UI. No account, no
API key, no telemetry, works with the network cable unplugged.

The part that makes it more than a chat box: it can **read the real machine**.
When a question needs live data, the model asks for one of twelve read-only
diagnostics (GPU/driver state, failed units, journal errors, controllers, disk,
audio, Flatpaks, the gaming stack…) and the answer is written from the actual
output.

It can also **act** on the machine. Apps, volume, brightness, media, timers,
Flatpaks, services, updates, power and even arbitrary shell commands are exposed
through a fixed allowlist. Safe actions run immediately; anything that changes
software or interrupts your session asks for confirmation first, and every
attempt is written to an audit log.

### Voice control

Say **“Jarvis, open Steam”**, **“Jarvis, volume up”**, **“Jarvis, check the GPU
driver”** or **“Jarvis, install Spotify”**. The wake word, recognition and speech
all happen locally (Vosk + Piper); nothing is sent to a cloud service. Press
`Super+Alt+J` or the microphone button in the assistant to toggle listening.
Replies are spoken aloud when *Speak replies* is enabled.

```bash
ujust jarvis-ai              # open it
ujust jarvis-ai-models       # pull a different model
ujust jarvis-ai-status       # is the engine up? which models?
ujust jarvis-voice-setup     # (re)download the offline speech models
ujust jarvis-voice-status    # listener + model state
ujust jarvis-voice-test      # offline self-test
```

Full command list, risk levels and safety notes:
**[docs/VOICE-CONTROL.md](docs/VOICE-CONTROL.md)**

Model sizing (the wizard picks automatically):

| RAM / VRAM available | Model | Notes |
|---|---|---|
| < 6 GB | `qwen3:1.7b` | fast, runs on anything |
| 6–14 GB | `qwen3:4b` | **default** — good balance |
| 14 GB+ | `qwen3:8b` | noticeably better reasoning |
| 24 GB+ | `gpt-oss:20b` | best local quality, slower |

Details, the exact tool allowlist, and how to point it at a cloud API instead:
**[docs/AI-ASSISTANT.md](docs/AI-ASSISTANT.md)**

---

## Making it yours

**Change the name** (boot logo, About screen, installer, wizard, assistant):

```bash
./scripts/rename.sh "Nova OS" nova
```

**Change the artwork**: drop your own `logo.png` (bright mark on pure black) and
`wallpaper.png` into `branding/master/`, then:

```bash
just brand        # regenerates every logo size, splash, 36 spinner frames, wallpapers
git commit -am "new art"
```

**Change colours**: edit `CYAN` / `VIOLET` at the top of `branding/build-assets.py`
and the `--cyan` / `--violet` variables at the top of the assistant UI.

**Add packages, Flatpaks, settings**: **[docs/CUSTOMIZING.md](docs/CUSTOMIZING.md)**

---

## Everyday commands

```bash
ujust jarvis-ai              # open the assistant
ujust jarvis-setup           # re-run the setup wizard
ujust jarvis-ai-models       # install / switch local models
ujust jarvis-voice-setup     # set up offline voice
ujust jarvis-voice-status    # voice diagnostics
ujust jarvis-rebrand-bootloader
just build                  # build the image locally
just build-qcow2            # bootable VM image (test in QEMU)
just build-iso              # installer ISO via bootc-image-builder
just check                  # validate just files
just lint                   # shellcheck
just test                   # voice unit + smoke tests
```

---

## Honest limitations

* **This is a derived image, not a distribution.** Kernel, drivers, GNOME and
  gaming plumbing are Bazzite's work. If Bazzite breaks, JARVIS inherits it.
* **Branding reaches far, not everywhere.** `os-release`, the boot splash, login
  logo, wallpapers, app grid and installer say JARVIS. Some upstream strings
  remain: the Fedora logo in a few help pages, `fedora` as the package-manager
  ID (kept on purpose — dozens of Fedora tools key off `ID=fedora`), and the
  firmware (BIOS) logo, which no OS can change.
* **The AI assistant is a 4B model.** It is good at "explain this, check that,
  decode this log". It is not a frontier model and it will occasionally be
  wrong. It can change your system only through the documented action allowlist,
  with confirmation for anything risky.
* **Voice recognition is a small offline model.** It handles short commands well;
  long dictation or strong background noise will need the push-to-talk button.
* **The ISO workflow is the heavy one.** It needs a `workflow_dispatch` run and
  ~20 GB of runner disk. `bootc switch` is the cheap path.
* **No signing by default.** Add a `SIGNING_SECRET` (cosign key) if you want
  users to be able to enforce signatures.

---

## Credits & licence

* Built on **[Bazzite](https://github.com/ublue-os/bazzite)** and the
  **[Universal Blue](https://github.com/ublue-os)** tooling (`image-template`,
  `titanoboa`, `bootc-image-builder`). All the hard parts are theirs.
* Plymouth two-step integration follows the approach used by
  **[Aurora](https://github.com/ublue-os/aurora)** for initramfs regeneration.
* JARVIS-specific code (`Containerfile` layer, `jarvis-firstboot`, `jarvis-assist`,
  brand pipeline) is Apache-2.0 — see [LICENSE](LICENSE).
* “Bazzite” and the Bazzite logo are the property of the Universal Blue project.
  This repo replaces that branding; if you redistribute a modified image, keep
  the upstream attribution in this README.
