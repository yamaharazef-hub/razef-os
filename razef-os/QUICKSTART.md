# QUICKSTART — how to use Razef OS

You have a **source repo**, not a finished installer. Razef is an atomic OS image:
GitHub builds it into a container image, and machines *pull* it. So the path is
always:

```
this repo  ──push──▶  GitHub Actions  ──push──▶  ghcr.io/<you>/razef-os:stable
                                                        │
                                          ┌─────────────┴─────────────┐
                                   bootc switch                 installer ISO
                                (existing Linux box)        (new machine / VM)
```

Pick where you are right now:

| You are… | Go to |
|---|---|
| Curious, just want to see the design | **[Path 0](#path-0--look-at-it-right-now-2-minutes)** |
| Ready to build (any OS, needs nothing but a browser) | **[Path 1](#path-1--build-it-on-github-this-is-the-required-step)** |
| Already running Bazzite / Fedora Silverblue / Aurora | **[Path 2](#path-2--install-on-a-machine-that-already-runs-an-atomic-linux)** |
| On Windows/macOS/Ubuntu wanting to *try* Razef | **[Path 3](#path-3--try-it-in-a-virtual-machine)** |
| Want a bootable USB installer | **[Path 4](#path-4--make-a-bootable-usb-installer)** |
| Already installed it | **[Path 5](#path-5--day-to-day-use)** |

---

## Path 0 — Look at it right now (2 minutes)

No building required:

```bash
cd razef-os
xdg-open system_files/overrides/usr/lib/razef/assist/ui/index.html   # the AI assistant
xdg-open branding/boot-preview.png                                   # the boot splash
xdg-open branding/assets/wallpaper.png                               # the wallpaper
xdg-open branding/asset-preview.png                                  # the logo
```

The assistant UI opens in preview mode (clearly marked). Click a quick-action
chip like **“Diagnose low FPS”** and it plays a real example exchange, including
a diagnostics tool card.

---

## Path 1 — Build it on GitHub (this is the required step)

Nothing gets installed until an image exists in a container registry, and GitHub
builds it for you for free.

**You need:** a GitHub account. That's it.

```bash
cd razef-os
./scripts/publish.sh          # creates the repo, pushes, starts the build
```

<details>
<summary>Prefer to do it by hand?</summary>

```bash
gh auth login                                   # if not already
gh repo create razef-os --public --source=. --push
gh workflow run "Build Razef OS image"
```
Make it **public**: private repos burn ~50 Actions minutes per build against a
2 000 min/month quota. Public repos are unlimited.
</details>

**Then:**

1. **Watch it.** `gh run watch` — or GitHub → Actions → *Build Razef OS image*.
   * First build ≈ **40–60 min** (pulls a ~4 GB base image, installs Ollama,
   regenerates the initramfs, rechunks layers).
   * It produces **two** images: `razef-os` (AMD/Intel) and `razef-os-nvidia`.
   * After that it rebuilds **daily at 10:05 UTC**, so Bazzite's fixes arrive
   automatically.

2. **Make the package public** — one-time, and easy to forget:
   `https://github.com/users/<you>/packages/container/razef-os/settings` →
   *Danger Zone* → *Change visibility* → **Public**.
   Without this, `bootc switch` fails with “denied”.

3. **Verify it worked:**
   ```bash
   podman pull ghcr.io/<you>/razef-os:stable && echo "image is live"
   ```

---

## Path 2 — Install on a machine that already runs an atomic Linux

Works on **Bazzite, Fedora Silverblue, Kinoite, Aurora, Bluefin** — anything
`rpm-ostree`/`bootc` based. Your files, games and Flatpaks are **kept**; only the
OS image changes.

```bash
# 1. are you on an atomic system?
rpm-ostree status | head -5

# 2. switch (NVIDIA? use razef-os-nvidia:stable)
sudo bootc switch ghcr.io/<you>/razef-os:stable

# 3. reboot
sudo systemctl reboot
```

Older system without `bootc`:

```bash
sudo rpm-ostree rebase ostree-image-signed:docker://ghcr.io/<you>/razef-os:stable
```

**After the reboot:** you should see the Razef splash instead of Bazzite's, then
the Razef Setup wizard opens on first login (see [Path 5](#path-5--day-to-day-use)).

**Something wrong? Pick “Previous deployment” in GRUB, or:**

```bash
rpm-ostree rollback && sudo systemctl reboot
# or go back to stock:
sudo bootc switch ghcr.io/ublue-os/bazzite-gnome:stable
```

> ⚠️ Never install the AMD and NVIDIA images interchangeably on the same GPU.
> If you're on NVIDIA, use `razef-os-nvidia`.

---

## Path 3 — Try it in a virtual machine

For Windows/macOS/Ubuntu users, or to test before touching real hardware.

<details>
<summary><b>Easiest: GitHub Codespaces-free route — grab the ISO instead</b></summary>

The ISO from [Path 4](#path-4--make-a-bootable-usb-installer) boots in
VirtualBox / VMware / Hyper-V the same way it boots on a USB stick. Give it
8 GB RAM and 64 GB disk.
</details>

**On a Linux host with podman:**

```bash
cd razef-os
just build-qcow2      # builds the image + a qcow2 disk (~20 GB free space needed)
just run-vm-qcow2     # boots it, opens a console at http://localhost:8006
```

Or with plain QEMU:

```bash
qemu-system-x86_64 -enable-kvm -m 8192 -smp 4 \
  -drive file=output/qcow2/disk.qcow2,if=virtio \
  -bios /usr/share/ovmf/OVMF.fd
```

A VM is fine for seeing the splash, the wizard and the assistant. It is **not**
representative for gaming performance, and the AI model will run on CPU.

---

## Path 4 — Make a bootable USB installer

For installing on a **new machine** or wiping a disk.

```bash
gh workflow run "Build Razef OS installer ISO" \
  -f image=razef-os -f tag=stable
gh run watch
gh run download --name razef-stable-iso
```

Then write it (this **erases the USB stick**):

```bash
lsblk                                        # find the stick, e.g. /dev/sdb
sudo dd if=razef-stable-live-amd64.iso of=/dev/sdb bs=4M status=progress oflag=sync
sync
```

Boot the stick → install from the desktop → reboot into Razef.

---

## Path 5 — Day-to-day use

### First login: the setup wizard

It appears ~8 seconds after the desktop loads. It will:

1. read your RAM/GPU and recommend a local AI model,
2. let you tick what to install (apps, gaming stack, dev tools),
3. enable the AI engine and download the model (**the slow part — 1–8 GB**),
4. apply the Razef look and brand the boot menu.

You can close it and finish later; nothing is mandatory.

### The assistant

Open **Razef Assistant** from the app grid or dock — or:

```bash
ujust razef-ai            # open it
ujust razef-ai-status     # is the engine up? which models?
ujust razef-ai-models     # install / switch models
```

Try these to see what it can actually do:

* “Check my GPU driver and tell me why my FPS is low”
* “My controller isn't detected in Steam”
* “Which services failed this boot?”
* “How do I install an app on this system?” *(it knows Razef is immutable)*

It runs entirely offline. Unplug the network and it still answers — but it can't
download a model, so do that first.

### Useful commands

```bash
ujust razef-setup                  # re-run the wizard
ujust razef-about                  # version + deployment info
ujust razef-rebrand-bootloader     # re-apply GRUB branding
ujust update                       # normal update flow (or: rpm-ostree upgrade)
rpm-ostree status                  # what deployment am I on?
```

### Updating

Nothing special — Razef is Bazzite underneath, so updates arrive like normal
atomic updates (and your Razef layer is rebuilt on top daily). Model files live
in `/var/lib/ollama`, so **updates never re-download your AI model**.

---

## Timing & resource cheat-sheet

| Step | Time | Needs |
|---|---|---|
| Look at the design | 2 min | anything |
| Push + first CI build | 40–60 min | GitHub account |
| Every later rebuild (daily) | ~20–40 min | nothing, automated |
| `bootc switch` + reboot | ~10 min | existing atomic Linux |
| ISO build | ~30 min | GitHub Actions (or podman locally) |
| First-login wizard | 5–20 min | internet (app + model download) |

## If something goes wrong

| Symptom | Fix |
|---|---|
| `bootc switch`: **denied / not found** | package still private → make it public (Path 1, step 2) |
| CI fails at `install packages` | a package name changed upstream — read the log, adjust `build_files/build.sh` |
| CI fails: **“razef plymouth theme is NOT in the initramfs”** | that check is deliberate: the splash wouldn't show. See `docs/TROUBLESHOOTING.md` |
| Boots, but the splash is Fedora's | you're on an old deployment — `rpm-ostree status`, then reboot into the newest |
| Wizard didn't appear | `/usr/bin/razef-firstboot --force` |
| Assistant says “engine offline” | `sudo systemctl enable --now ollama` |
| Assistant says “no model installed” | `ujust razef-ai-models` |
| NVIDIA: no driver after switching | switch to the `razef-os-nvidia` image |

More: `docs/INSTALL.md`, `docs/TROUBLESHOOTING.md`, `docs/AI-ASSISTANT.md`.
