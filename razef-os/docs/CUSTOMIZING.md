# Customising Razef OS

Everything in this image is a file in this repo. Nothing is patched by hand, so
every change is reviewable, diffable and reversible.

---

## 1. Rename the OS

```bash
./scripts/rename.sh "Nova OS" nova
```

That rewrites `Razef` → `Nova OS` and `razef`/`razef-os` → `nova`/`nova-os` in
every text file (os-release, boot splash title, wizard, assistant, desktop
entries, CI, docs) and renames the matching directories. Review with
`git diff`, then commit.

Places where the name appears, if you prefer to do it by hand:

| File | What |
|---|---|
| `build_files/build.sh` | `/usr/lib/os-release`, `/etc/issue` |
| `branding/build-assets.py` | docstring only (art is generated) |
| `system_files/overrides/usr/share/plymouth/themes/*/` | `Title=` / `SubTitle=` |
| `system_files/overrides/usr/bin/razef-firstboot` | `BRAND`, `APP_ID` |
| `system_files/overrides/usr/lib/razef/assist/*` | UI title, system prompt |
| `system_files/overrides/usr/share/applications/*.desktop` | app names |
| `image-template.env`, `Containerfile` | image name + labels |

---

## 2. Branding / artwork

### Swap the art

```
branding/master/logo.png        bright mark on a pure black background, square
branding/master/wallpaper.png   any 16:9 image, 2560x1440 or larger
```

```bash
just brand
```

`branding/build-assets.py` then produces, in `branding/assets/`:

| Asset | Used for |
|---|---|
| `logo-1024/512/256/128.png` | app icon, About screen, GDM logo |
| `watermark.png` | the plymouth boot splash mark (with a baked-in bloom) |
| `throbber-0001..0036.png` | the boot spinner (10 dots, cyan→violet trail) |
| `box.png`, `lock.png` | disk-encryption password dialog chrome |
| `wallpaper.png`, `wallpaper-4k.png` | desktop wallpaper, graded + vignetted |

The logo pipeline knocks the black background out of `master/logo.png` using
luminance-as-alpha, keeps the outer bloom as a soft falloff, and **re-colours the
mark from a brand gradient** so it stays saturated at every size. If you want to
keep your own colours instead, delete the gradient block in `load_mark()`.

### Change the colours

* Art / boot splash: `CYAN` and `VIOLET` at the top of `branding/build-assets.py`.
* Boot splash background: `BackgroundStartColor` / `BackgroundEndColor` in
  `system_files/overrides/usr/share/plymouth/themes/razef/razef.plymouth`
  (format `0xRRGGBB`).
* Assistant UI: `--cyan`, `--violet`, `--bg` in the `<style>` block of
  `system_files/overrides/usr/lib/razef/assist/ui/index.html`.
* Desktop defaults: `etc/dconf/db/distro.d/02-razef`.

### Boot splash specifics

Razef uses plymouth's `two-step` module (the same one Fedora's spinner uses):
centred watermark, optional title, throbber underneath, progress bar on shutdown.

The theme is selected in `/etc/plymouth/plymouthd.conf` and the **initramfs is
regenerated during the build** — without that step the splash only appears once
the root filesystem is mounted, which looks like a flicker instead of a boot
experience. `build_files/build.sh` verifies the theme actually landed inside the
initramfs and fails the build if it did not:

```
OK: razef plymouth theme is inside the initramfs
```

If you change the theme after the image is built, users can re-apply it with:

```bash
sudo dracut --force --regenerate-all
```

### The GRUB menu

The *content* of the boot menu comes from BLS entries, but the distributor name
and the menu's title are configurable:

```bash
ujust razef-rebrand-bootloader     # sets GRUB_DISTRIBUTOR="Razef", re-runs grub2-mkconfig
```

The firmware (BIOS/UEFI) logo cannot be changed by any OS.

---

## 3. Adding packages

**Flatpak (preferred — no reboot, no layering):** add to the wizard task list in
`system_files/overrides/usr/bin/razef-firstboot` (class `FlatpakInstall`), or
just document them for users.

**Baked into the image:** add to the `dnf5 install` block in
`build_files/build.sh`:

```bash
dnf5 install -y --setopt=install_weak_deps=False \
    python3-gobject webkitgtk6.0 libadwaita \
    htop neovim        # <- your packages
```

Keep the list short. Every package grows the image and slows delta updates, and
removals are impossible without a new build.

**Homebrew** is already available (Bazzite ships it) for CLI tools without root.

---

## 4. Changing the setup wizard

`system_files/overrides/usr/bin/razef-firstboot` is a small task framework:

```python
class MyTask(Task):
    key = "my_task"                     # unique id, stored in the state file
    title = "Do the thing"
    subtitle = "Short explanation shown in the wizard"
    default_on = True                   # pre-ticked?
    requires_network = False            # informational

    def status(self) -> bool:           # True => already done, skip
        return Path.home().joinpath(".thing").exists()

    def apply(self, report) -> tuple[bool, str]:
        report("writing ~/.thing …")    # sent to the wizard's log pane
        Path.home().joinpath(".thing").write_text("ok")
        return True, ""                 # (ok, error output)
```

Register it in `build_task_list()`. Tasks can freely fail — the wizard logs the
error and continues.

State lives in `/var/lib/razef/firstboot.json` and
`~/.config/razef/firstboot.json`. Delete both (or `razef-firstboot --reset`) to
see the wizard again.

---

## 5. Desktop defaults

`system_files/overrides/etc/dconf/db/distro.d/02-razef` is a dconf system
database — the right place for defaults on an immutable image (it applies to new
users and is overridable, unlike a `gschema.override` lock).

Keys must be GVariant-encoded: strings in single quotes, booleans `true/false`,
arrays `['a', 'b']`.

Check the result without rebooting:

```bash
dconf read /org/gnome/desktop/background/picture-uri
```

---

## 6. CI knobs

| What | Where |
|---|---|
| Rebuild schedule | `.github/workflows/build.yml` → `cron` |
| Extra hardware targets | the `matrix.include` list (`image` + `base`) |
| Image signing | repo secret `SIGNING_SECRET` |
| ISO app list | `iso/flatpaks` |
| Publish to a registry other than ghcr.io | `IMAGE_REGISTRY` in `build.yml` |

### Building locally

```bash
just build                                   # -> localhost/razef-os:stable
just build mytest latest ghcr.io/ublue-os/bazzite-gnome:testing
podman run --rm -it localhost/razef-os:stable bootc container lint
```

You need podman ≥ 4.9 and ~20 GB free. Cross-checking the image without booting:

```bash
podman run --rm localhost/razef-os:stable cat /usr/lib/os-release
podman run --rm localhost/razef-os:stable ls /usr/share/plymouth/themes/
```
