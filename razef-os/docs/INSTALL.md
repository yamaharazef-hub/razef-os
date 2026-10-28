# Installing Razef OS

Three routes, easiest first.

---

## A. Rebase an existing Bazzite / Fedora Atomic install

No reinstall. Your files, Flatpaks and home directory stay exactly where they are.
Only the OS image is replaced.

```bash
# 1. Check you are actually on an atomic system
rpm-ostree status

# 2. Switch to Razef
sudo bootc switch ghcr.io/<your-username>/razef-os:stable

# 3. Reboot
sudo systemctl reboot
```

If your machine is older and `bootc` is not present:

```bash
sudo rpm-ostree rebase ostree-image-signed:docker://ghcr.io/<your-username>/razef-os:stable
```

**NVIDIA?** Use `razef-os-nvidia:stable` — it is built on
`bazzite-gnome-nvidia-open` and carries the driver. Switching between the AMD and
NVIDIA images on the same machine works, but you must switch to the matching
variant for your GPU.

### Rolling back

Every deployment is kept. At boot, old deployments are in the GRUB menu under
“Previous”. From a running system:

```bash
rpm-ostree rollback
sudo systemctl reboot
```

Or go back to stock Bazzite:

```bash
sudo bootc switch ghcr.io/ublue-os/bazzite-gnome:stable
```

### Signed images (optional but recommended)

If you set the `SIGNING_SECRET` repository secret, CI signs every image with
cosign. Then you can enforce it:

```bash
sudo bootc switch --enforce-container-sigpolicy ghcr.io/<your-username>/razef-os:stable
```

---

## B. Live installer ISO

```bash
# in your fork
gh workflow run "Build Razef OS installer ISO" \
  -f image=razef-os -f tag=stable
gh run watch
gh run download --name razef-stable-iso
```

Write it to a USB stick (this **erases the stick**):

```bash
lsblk                                   # find your stick, e.g. /dev/sdb
sudo dd if=razef-stable-live-amd64.iso of=/dev/sdb bs=4M status=progress oflag=sync
```

Boot from it. The live environment carries the Razef splash before the desktop
appears; run the installer from the desktop. The installed system is the same
container image, so updates work identically afterwards.

Local alternative (needs podman, ~15 GB free):

```bash
just build-iso        # output/bootiso/install.iso
```

---

## C. Try it in a VM first (recommended)

```bash
just build-qcow2      # output/qcow2/disk.qcow2
just run-vm-qcow2     # opens http://localhost:8006 (web console)
```

The VM gets 4 cores / 8 GB RAM / 64 GB disk by default — enough for the desktop
and a small AI model, not enough for gaming or the 8B model.

With plain QEMU instead:

```bash
qemu-system-x86_64 -enable-kvm -m 8192 -smp 4 \
  -drive file=output/qcow2/disk.qcow2,if=virtio \
  -bios /usr/share/ovmf/OVMF.fd
```

> Testing the boot splash in a VM: shutdown and restart within the VM shows the
> Plymouth splash. The very first boot shows the firmware logo longer than bare
> metal, which is normal.

---

## After installing

1. **First login** → the Razef Setup wizard opens (~8 s delay). It needs network
   for the Flatpak and model downloads; everything else works offline.
2. If you dismissed it: `ujust razef-setup`.
3. Model download is the slow part (1.7–8 GB depending on the model). It runs in
   the background of the wizard with a progress log; you can close the wizard and
   `ujust razef-ai-models` later.

### Verifying the install

```bash
cat /usr/lib/os-release | head -3      # NAME="Razef"
ujust razef-about                      # version + deployment
systemctl status ollama                # AI engine
razef-assist                           # open the assistant
```

---

## Hardware notes

| | |
|---|---|
| **GPU** | AMD/Intel recommended (stock image). NVIDIA → use the `-nvidia` image. |
| **Secure Boot** | Works with Universal Blue's enrolled key. If you switched from stock Fedora with your own keys, you may need to re-enrol. |
| **Disk** | 25 GB minimum for the OS + Flatpaks; add the model size (1–13 GB) for the assistant. |
| **RAM** | 8 GB works, 16 GB comfortable. The assistant loads the model into RAM/VRAM. |
| **Dual boot** | Bootc images install their own ESP entries; Windows dual boot is fine but back up first. |
