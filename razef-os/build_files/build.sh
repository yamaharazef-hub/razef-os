#!/bin/bash
# ===========================================================================
#  Razef OS - image build script
#  Runs inside the container as root, with the repo mounted at /ctx
# ===========================================================================
set -euxo pipefail

echo "::group:: Razef OS build"
cat /usr/lib/os-release | head -3
echo "::endgroup::"

WITH_DEV_TOOLS="${WITH_DEV_TOOLS:-0}"
OLLAMA_VERSION="${OLLAMA_VERSION:-latest}"

# ---------------------------------------------------------------------------
# 1. Static system files (paths mirror the final filesystem)
# ---------------------------------------------------------------------------
echo "::group:: Copying system_files"
cp -avf /ctx/system_files/overrides/. /
echo "::endgroup::"

# ---------------------------------------------------------------------------
# 2. Packages
#    Everything heavy is baked in at build time so that the post-install
#    wizard only has to flip switches (flatpaks, services, settings) instead
#    of layering RPMs onto an immutable root.
# ---------------------------------------------------------------------------
echo "::group:: Installing packages"
dnf5 install -y --setopt=install_weak_deps=False \
    python3-gobject \
    gobject-introspection \
    webkitgtk6.0 \
    libadwaita \
    libgtop2 \
    xdg-utils

if [[ "${WITH_DEV_TOOLS}" == "1" ]]; then
    dnf5 install -y \
        git neovim tmux ripgrep fd-find fzf bat eza btop zsh \
        podman-compose python3-pip nodejs npm
fi
echo "::endgroup::"

# ---------------------------------------------------------------------------
# 3. Ollama - the local AI engine behind razef-assist
#    Installed from the official static tarball (no installer script), and
#    told to keep models in /var so they survive image updates.
# ---------------------------------------------------------------------------
echo "::group:: Installing Ollama ${OLLAMA_VERSION}"
if [[ "${WITH_OLLAMA:-1}" == "1" ]]; then
    case "$(uname -m)" in
        x86_64)  ollama_arch=amd64 ;;
        aarch64) ollama_arch=arm64 ;;
        *)       echo "unsupported arch $(uname -m)"; exit 1 ;;
    esac

    if [[ "${OLLAMA_VERSION}" == "latest" ]]; then
        ollama_url="https://ollama.com/download/ollama-linux-${ollama_arch}.tgz"
    else
        ollama_url="https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-linux-${ollama_arch}.tgz"
    fi

    curl --retry 5 --retry-delay 3 -fL -o /tmp/ollama.tgz "${ollama_url}"
    mkdir -p /usr/lib/ollama
    tar -xzf /tmp/ollama.tgz -C /usr/lib/ollama --strip-components=0
    rm -f /tmp/ollama.tgz

    # The tarball ships bin/ and lib/ at its root
    install -Dm755 /usr/lib/ollama/bin/ollama /usr/bin/ollama
    if [[ -d /usr/lib/ollama/lib/ollama ]]; then
        ln -sfn /usr/lib/ollama/lib/ollama /usr/lib/ollama-libs
    fi

    # system user + state dirs that live on the writable /var
    if ! getent group ollama >/dev/null; then
        groupadd -r ollama
    fi
    if ! getent passwd ollama >/dev/null; then
        useradd -r -g ollama -d /var/lib/ollama -s /sbin/nologin -c "Ollama" ollama
    fi
    install -d -o ollama -g ollama -m 0755 /var/lib/ollama /var/lib/ollama/models

    systemctl enable ollama.service
else
    echo "Ollama install skipped (WITH_OLLAMA=0)"
fi
echo "::endgroup::"

# ---------------------------------------------------------------------------
# 4. Branding assets
# ---------------------------------------------------------------------------
echo "::group:: Installing Razef branding"
ASSETS=/ctx/branding
PLY=/usr/share/plymouth/themes/razef
install -d "${PLY}"

# --- boot splash ---
install -m0644 "${ASSETS}/watermark.png"  "${PLY}/watermark.png"
install -m0644 "${ASSETS}"/throbber-*.png "${PLY}/"
# placeholder chrome so the disk-password dialog never renders a broken theme
install -m0644 "${ASSETS}/box.png"       "${PLY}/box.png"
install -m0644 "${ASSETS}/lock.png"      "${PLY}/lock.png"
install -m0644 "${ASSETS}/corner-image.png" "${PLY}/corner-image.png"
install -m0644 "${ASSETS}/header-image.png" "${PLY}/header-image.png"

# --- logos & icons ---
install -Dm0644 "${ASSETS}/logo-512.png" /usr/share/pixmaps/razef-os.png
install -Dm0644 "${ASSETS}/logo-512.png" /usr/share/pixmaps/razef-logo.png
install -Dm0644 "${ASSETS}/logo-256.png" /usr/share/icons/hicolor/256x256/apps/razef-os.png
install -Dm0644 "${ASSETS}/logo-256.png" /usr/share/icons/hicolor/256x256/apps/razef-assist.png
install -Dm0644 "${ASSETS}/logo-512.png" /usr/share/icons/hicolor/scalable/apps/razef-os.png

# --- wallpapers ---
install -d /usr/share/backgrounds/razef
install -m0644 "${ASSETS}/wallpaper.png"    /usr/share/backgrounds/razef/default.png
install -m0644 "${ASSETS}/wallpaper-4k.png" /usr/share/backgrounds/razef/default-4k.png
install -m0644 "${ASSETS}/wallpaper.png"    /usr/share/backgrounds/rz-wallpaper.png

# GNOME reads these for the "default" look
for f in default.png default-dark.png; do
    if [[ -e "/usr/share/backgrounds/${f}" && ! -L "/usr/share/backgrounds/${f}" ]]; then
        cp -f /usr/share/backgrounds/razef/default.png "/usr/share/backgrounds/${f}"
    fi
done

# --- GDM / login logo used by gnome-shell-extension-background-logo ---
install -d /usr/share/gnome-background-properties
cat >/usr/share/gnome-background-properties/razef.xml <<'EOF'
<?xml version="1.0"?>
<!DOCTYPE wallpapers SYSTEM "gnome-wp-list.dtd">
<wallpapers>
  <wallpaper deleted="false">
    <name>Razef</name>
    <filename>/usr/share/backgrounds/razef/default.png</filename>
    <options>zoom</options>
    <pcolor>#05070d</pcolor>
    <scolor>#05070d</scolor>
    <shade_type>solid</shade_type>
  </wallpaper>
  <wallpaper deleted="false">
    <name>Razef 4K</name>
    <filename>/usr/share/backgrounds/razef/default-4k.png</filename>
    <options>zoom</options>
    <pcolor>#05070d</pcolor>
    <scolor>#05070d</scolor>
    <shade_type>solid</shade_type>
  </wallpaper>
</wallpapers>
EOF
echo "::endgroup::"

# ---------------------------------------------------------------------------
# 5. os-release / identity
#    NOTE: ID stays "fedora" on purpose. Half of the Fedora toolchain keys off
#    it (dnf, rpm-ostree, flatpak filters). Only the *user visible* fields and
#    LOGO are rebranded - same approach as every other uBlue image.
# ---------------------------------------------------------------------------
echo "::group:: Writing os-release"
VERSION="${VERSION:-1.0}"
cat >/usr/lib/os-release <<EOF
NAME="Razef"
VERSION="${VERSION} (Zenith)"
ID=fedora
ID_LIKE="fedora"
VERSION_ID=44
VERSION_CODENAME=""
VARIANT="Razef GNOME"
VARIANT_ID=razef-gnome
PRETTY_NAME="Razef ${VERSION}"
ANSI_COLOR="0;36"
CPE_NAME="cpe:/o:razef:razef:${VERSION}"
DEFAULT_HOSTNAME="razef"
HOME_URL="https://razef.example.org/"
DOCUMENTATION_URL="https://razef.example.org/docs"
SUPPORT_URL="https://razef.example.org/support"
BUG_REPORT_URL="https://github.com/razef-os/razef-os/issues"
PRIVACY_POLICY_URL="https://razef.example.org/privacy"
LOGO=razef-os
IMAGE_ID=razef-os
IMAGE_VERSION=${VERSION}
RAZEF_BASE=bazzite
RAZEF_VARIANT=gnome
EOF
cp -f /usr/lib/os-release /etc/os-release 2>/dev/null || true

cat >/etc/issue <<'EOF'

  ██████╗  █████╗ ███████╗███████╗███████╗
  ██╔══██╗██╔══██╗╚══███╔╝██╔════╝██╔════╝
  ██████╔╝███████║  ███╔╝ █████╗  █████╗
  ██╔══██╗██╔══██║ ███╔╝  ██╔══╝  ██╔══╝
  ██║  ██║██║  ██║███████╗███████╗██║
  ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚══════╝╚═╝

  Razef \v \n \l

EOF
echo "::endgroup::"

# ---------------------------------------------------------------------------
# 6. Plymouth: select the theme, then rebuild the initramfs so the splash is
#    actually there *before* the root filesystem mounts.
#    The os-release trick below keeps the initramfs reproducible (dracut
#    embeds version strings that change on every build) - same as Aurora.
# ---------------------------------------------------------------------------
echo "::group:: Plymouth theme + initramfs"
install -d /etc/plymouth
cat >/etc/plymouth/plymouthd.conf <<'EOF'
[Daemon]
Theme=razef
ShowDelay=0
DeviceTimeout=8
EOF

KERNEL_VERSION="$(rpm -q --queryformat='%{evr}.%{arch}' kernel-core | head -n1)"
INITRAMFS="/usr/lib/modules/${KERNEL_VERSION}/initramfs.img"

if [[ -f "${INITRAMFS}" ]]; then
    TMP_OS_RELEASE="$(mktemp --tmpdir 'os-release-XXXXXXXXXX')"
    cp /usr/lib/os-release "${TMP_OS_RELEASE}"
    sed -Ei -e '/^((OSTREE_)?(IMAGE_)?VERSION|PRETTY_NAME|BUILD_ID)=/d' /usr/lib/os-release

    DRACUT_NO_XATTR=1 dracut \
        --kver "${KERNEL_VERSION}" \
        --reproducible \
        --force \
        --add 'plymouth' \
        "${INITRAMFS}"

    cp -f "${TMP_OS_RELEASE}" /usr/lib/os-release
    rm -f "${TMP_OS_RELEASE}"
    chmod 0600 "${INITRAMFS}"

    # verify the splash really made it into the initramfs
    if command -v lsinitrd >/dev/null 2>&1; then
        if lsinitrd "${INITRAMFS}" | grep -q 'plymouth/themes/razef'; then
            echo "OK: razef plymouth theme is inside the initramfs"
        else
            echo "ERROR: razef plymouth theme is NOT in the initramfs."
            echo "       The system would boot with the default Fedora splash."
            echo "       Check that /etc/plymouth/plymouthd.conf sets Theme=razef"
            exit 1
        fi
    fi
else
    echo "WARNING: no initramfs at ${INITRAMFS}; skipping splash regeneration"
fi
echo "::endgroup::"

# ---------------------------------------------------------------------------
# 7. Refresh desktop / GTK / dconf caches
# ---------------------------------------------------------------------------
echo "::group:: Refreshing caches"
glib-compile-schemas /usr/share/glib-2.0/schemas || true
if command -v dconf >/dev/null 2>&1; then
    dconf update || true
fi
gtk-update-icon-cache -f -t /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database /usr/share/applications 2>/dev/null || true
echo "::endgroup::"

# ---------------------------------------------------------------------------
# 8. Services
# ---------------------------------------------------------------------------
echo "::group:: Enabling services"
# razef-firstboot is an XDG autostart entry (see system_files), not a unit,
# so it lands in the user's graphical session. Nothing to enable here except
# the bits the assistant needs.
systemctl enable ollama.service
echo "::endgroup::"

# ---------------------------------------------------------------------------
# 9. Cleanup
# ---------------------------------------------------------------------------
echo "::group:: Cleanup"
dnf5 clean all || true
rm -rf /var/cache/dnf /var/cache/libdnf5 /tmp/* || true
echo "::endgroup::"

echo "Razef OS layer applied."
