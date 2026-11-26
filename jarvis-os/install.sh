#!/usr/bin/env bash
# ===========================================================================
#  JARVIS OS - install the JARVIS desktop, assistant and voice control
#  directly on an existing Bazzite / Fedora Atomic system.
#
#  No custom OS image is needed. Everything that must survive updates lives in
#  /var and /usr/local; only a systemd unit and a dconf default are added under
#  /etc. Run with sudo (the script will ask for it if you forget).
#
#  Usage:
#     ./install.sh                  # full install
#     ./install.sh --no-models       # skip the ~100 MB speech/AI model download
#     ./install.sh --no-branding     # keep your current wallpaper/hotkeys
#     ./install.sh --uninstall       # remove the JARVIS layer
# ===========================================================================
set -euo pipefail

APP="JARVIS"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PAYLOAD="/var/lib/jarvis/system"
VOICE_DATA="/var/lib/jarvis/voice"
VOICE_LIB="$PAYLOAD/usr/lib/jarvis/voice"
BIN_DIR="/usr/local/bin"
OLLAMA_DIR="$PAYLOAD/ollama"

DO_MODELS=1
DO_BRANDING=1
UNINSTALL=0

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-models)   DO_MODELS=0; shift ;;
    --no-branding) DO_BRANDING=0; shift ;;
    --uninstall)   UNINSTALL=1; shift ;;
    -h|--help)     sed -n '2,18p' "$0"; exit 0 ;;
    *)             die "unknown option: $1" ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  exec sudo -E "$0" "$@"
fi

TARGET_USER="${SUDO_USER:-${USER:-}}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
  TARGET_USER="$(logname 2>/dev/null || id -un)"
fi
[[ "$TARGET_USER" == "root" ]] && die "run this as a normal user through sudo, not as root"
TARGET_UID="$(id -u "$TARGET_USER")"
TARGET_GROUP="$(id -gn "$TARGET_USER")"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
[[ -d "$TARGET_HOME" ]] || die "cannot find home directory for $TARGET_USER"

USER_ENV=(
  "XDG_RUNTIME_DIR=/run/user/${TARGET_UID}"
  "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/${TARGET_UID}/bus"
)
run_as_user() { runuser -u "$TARGET_USER" -- env "${USER_ENV[@]}" "$@"; }
user_dconf() { run_as_user dconf "$@"; }

# ---------------------------------------------------------------------------
uninstall() {
  say "Stopping JARVIS services"
  run_as_user systemctl --user disable --now jarvis-assist.service 2>/dev/null || true

  say "Removing JARVIS files"
  rm -f "$BIN_DIR/jarvis-assist" "$BIN_DIR/jarvis-voice" "$BIN_DIR/jarvis-firstboot"
  rm -f /usr/local/share/applications/org.jarvis.Assistant.desktop \
        /usr/local/share/applications/org.jarvis.Setup.desktop
  rm -f /usr/local/share/icons/hicolor/256x256/apps/jarvis-assist.png \
        /usr/local/share/icons/hicolor/256x256/apps/jarvis-os.png \
        /usr/local/share/icons/hicolor/scalable/apps/jarvis-os.png
  rm -rf "$TARGET_HOME/.config/systemd/user/jarvis-assist.service" \
         "$TARGET_HOME/.config/ublue-os/just/60-jarvis.just" \
         /usr/local/share/ublue-os/just/60-jarvis.just
  rm -rf "$PAYLOAD" "$VOICE_DATA" /var/lib/jarvis/branding
  command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database /usr/local/share/applications 2>/dev/null || true

  say "Stopping the Ollama service (files are left in /var/lib/ollama)"
  systemctl disable --now ollama.service 2>/dev/null || true
  say "JARVIS removed. Your Ollama models and AI settings were kept."
  exit 0
}

# ---------------------------------------------------------------------------
install_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    say "Ollama is already installed: $(command -v ollama)"
    return 0
  fi
  say "Installing Ollama into $OLLAMA_DIR"
  if ! command -v zstd >/dev/null 2>&1 && ! command -v unzstd >/dev/null 2>&1; then
    die "zstd is required to unpack Ollama. Install it first: rpm-ostree install zstd && reboot"
  fi
  case "$(uname -m)" in
    x86_64)  arch=amd64 ;;
    aarch64) arch=arm64 ;;
    *)       die "unsupported architecture $(uname -m)" ;;
  esac
  local tmp url
  tmp="$(mktemp -d)"
  url="https://github.com/ollama/ollama/releases/latest/download/ollama-linux-${arch}.tar.zst"
  curl -fL --retry 3 -o "${tmp}/ollama.tar.zst" "$url" || die "could not download Ollama"
  mkdir -p "$OLLAMA_DIR"
  zstd -dc "${tmp}/ollama.tar.zst" 2>/dev/null | tar -xf - -C "$OLLAMA_DIR" \
    --exclude='lib/ollama/cuda_v12/*' --exclude='lib/ollama/cuda_v13/*' \
    || unzstd -c "${tmp}/ollama.tar.zst" | tar -xf - -C "$OLLAMA_DIR"
  rm -rf "$tmp"
  ln -sfn "$OLLAMA_DIR/bin/ollama" "$BIN_DIR/ollama"
  "$BIN_DIR/ollama" --version || die "the installed Ollama binary does not run"
}

install_voice_runtime() {
  say "Preparing the offline voice runtime"
  mkdir -p "$VOICE_DATA" "$VOICE_DATA/models"
  if ! python3 -m venv "$VOICE_DATA/venv"; then
    warn "could not create the voice virtualenv; run 'jarvis-voice setup' later"
    return 0
  fi
  "$VOICE_DATA/venv/bin/pip" install --quiet --disable-pip-version-check --no-cache-dir vosk \
    || warn "Vosk install failed; run 'jarvis-voice setup' with network access"

  local piper_tmp piper_asset=""
  case "$(uname -m)" in
    x86_64)  piper_asset="piper_amd64.tar.gz" ;;
    aarch64) piper_asset="piper_arm64.tar.gz" ;;
  esac
  if [[ -n "$piper_asset" ]]; then
    mkdir -p "$VOICE_LIB/bin"
    piper_tmp="$(mktemp -d)"
    if curl -fL --retry 3 -o "${piper_tmp}/piper.tgz" \
        "https://github.com/rhasspy/piper/releases/download/v1.2.0/${piper_asset}"; then
      tar -xzf "${piper_tmp}/piper.tgz" -C "$piper_tmp"
      [[ -f "${piper_tmp}/piper/piper" ]] && \
        install -m0755 "${piper_tmp}/piper/piper" "$VOICE_LIB/bin/piper"
    else
      warn "Piper download failed; espeak-ng will be used for speech"
    fi
    rm -rf "$piper_tmp"
  fi

  if [[ $DO_MODELS -eq 1 ]]; then
    say "Downloading offline speech models (about 100 MB)"
    JARVIS_VOICE_MODELS="$VOICE_DATA/models" \
      python3 "$VOICE_LIB/setup_models.py" \
      || warn "speech model download failed; run 'jarvis-voice setup' later"
  fi
}

install_branding() {
  [[ $DO_BRANDING -eq 1 ]] || return 0
  say "Applying JARVIS wallpaper and hotkey"

  mkdir -p /var/lib/jarvis/branding
  install -m0644 "$SRC/branding/assets/wallpaper.png" /var/lib/jarvis/branding/wallpaper.png

  install -Dm0644 "$SRC/branding/assets/logo-256.png" \
    /usr/local/share/icons/hicolor/256x256/apps/jarvis-assist.png
  install -Dm0644 "$SRC/branding/assets/logo-256.png" \
    /usr/local/share/icons/hicolor/256x256/apps/jarvis-os.png
  install -Dm0644 "$SRC/branding/assets/logo-512.png" \
    /usr/local/share/icons/hicolor/scalable/apps/jarvis-os.png

  user_dconf write /org/gnome/desktop/background/picture-uri \
    "'file:///var/lib/jarvis/branding/wallpaper.png'" 2>/dev/null \
    || warn "could not set the wallpaper (no active user session?)"

  # Preserve existing custom keybindings and add Super+Alt+J.
  local current new
  current="$(user_dconf read /org/gnome/settings-daemon/plugins/media-keys/custom-keybindings 2>/dev/null || true)"
  new="$(python3 - "$current" <<'PY'
import ast, sys
raw = sys.argv[1].strip()
try:
    items = ast.literal_eval(raw) if raw and not raw.startswith('@') else []
except (ValueError, SyntaxError):
    items = []
if not isinstance(items, list):
    items = []
path = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/jarvis-voice/'
if path not in items:
    items.append(path)
print('[' + ', '.join("'%s'" % str(i) for i in items) + ']')
PY
)"
  user_dconf write /org/gnome/settings-daemon/plugins/media-keys/custom-keybindings "$new" 2>/dev/null || true
  user_dconf write /org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/jarvis-voice/name \
    "'Toggle JARVIS voice'" 2>/dev/null || true
  user_dconf write /org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/jarvis-voice/command \
    "'jarvis-voice toggle'" 2>/dev/null || true
  user_dconf write /org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/jarvis-voice/binding \
    "'<Super><Alt>j'" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
if [[ $UNINSTALL -eq 1 ]]; then
  uninstall
fi

[[ -f "$SRC/system_files/overrides/usr/lib/jarvis/assist/server.py" ]] \
  || die "source files missing: $SRC/system_files/overrides/usr/lib/jarvis"

say "Installing the JARVIS payload into $PAYLOAD"
mkdir -p "$PAYLOAD/usr/bin" "$PAYLOAD/usr/lib" "$PAYLOAD/usr/share"
printf '1.1-installer\n' > /var/lib/jarvis/VERSION
rm -rf "$PAYLOAD/usr/lib/jarvis"
cp -a "$SRC/system_files/overrides/usr/lib/jarvis" "$PAYLOAD/usr/lib/"
cp -a "$SRC/system_files/overrides/usr/bin/jarvis-assist" \
      "$SRC/system_files/overrides/usr/bin/jarvis-voice" \
      "$SRC/system_files/overrides/usr/bin/jarvis-firstboot" "$PAYLOAD/usr/bin/"
chmod 0755 "$PAYLOAD/usr/bin/"* "$VOICE_LIB/stt_worker.py" "$VOICE_LIB/setup_models.py"

for app in org.jarvis.Assistant.desktop org.jarvis.Setup.desktop; do
  install -Dm0644 "$SRC/system_files/overrides/usr/share/applications/$app" \
    "/usr/local/share/applications/$app"
done
command -v update-desktop-database >/dev/null 2>&1 && \
  update-desktop-database /usr/local/share/applications 2>/dev/null || true

say "Creating commands in $BIN_DIR"
for cmd in jarvis-assist jarvis-voice jarvis-firstboot; do
  ln -sfn "$PAYLOAD/usr/bin/$cmd" "$BIN_DIR/$cmd"
done

install_ollama

say "Installing the Ollama system service"
install -m0644 "$SRC/system_files/overrides/etc/systemd/system/ollama.service" \
  /etc/systemd/system/ollama.service
sed -i 's|^ExecStart=/usr/bin/ollama |ExecStart=/usr/local/bin/ollama |' \
  /etc/systemd/system/ollama.service
if ! getent group ollama >/dev/null; then groupadd -r ollama; fi
if ! getent passwd ollama >/dev/null; then
  useradd -r -g ollama -d /var/lib/ollama -s /sbin/nologin -c "Ollama" ollama
fi
install -d -o ollama -g ollama -m 0755 /var/lib/ollama /var/lib/ollama/models
systemctl daemon-reload
systemctl enable --now ollama.service 2>/dev/null || warn "could not start ollama.service yet"

install_voice_runtime

say "Installing the user service for $TARGET_USER"
SERVICE_FILE="$TARGET_HOME/.config/systemd/user/jarvis-assist.service"
mkdir -p "$TARGET_HOME/.config/systemd/user"
cat >"$SERVICE_FILE" <<EOF
[Unit]
Description=JARVIS Assistant backend
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=$BIN_DIR/jarvis-assist --serve
Restart=on-failure
RestartSec=2
Environment=JARVIS_LOCAL=1
Environment=JARVIS_VOICE_AUTOSTART=1
Environment=JARVIS_OLLAMA_URL=http://127.0.0.1:11434

[Install]
WantedBy=default.target
EOF
chown -R "$TARGET_USER:$TARGET_GROUP" "$TARGET_HOME/.config/systemd/user"
run_as_user systemctl --user daemon-reload 2>/dev/null || true
run_as_user systemctl --user enable --now jarvis-assist.service 2>/dev/null \
  || warn "could not start the user service yet - log out and back in, or run: systemctl --user enable --now jarvis-assist"

# Enable voice in the target user's config, leaving their other choices intact.
run_as_user env JARVIS_VOICE_CONFIG="$TARGET_HOME/.config/jarvis/voice/config.json" \
  python3 -c "import sys; sys.path.insert(0, '$VOICE_LIB'); import config; config.save_config({'enabled': True})" \
  || warn "could not write the voice config for $TARGET_USER"

say "Installing ujust recipes"
install -d -m0755 /usr/local/share/ublue-os/just "$TARGET_HOME/.config/ublue-os/just"
install -m0644 "$SRC/system_files/overrides/usr/share/ublue-os/just/60-jarvis.just" \
  /usr/local/share/ublue-os/just/60-jarvis.just
install -m0644 "$SRC/system_files/overrides/usr/share/ublue-os/just/60-jarvis.just" \
  "$TARGET_HOME/.config/ublue-os/just/60-jarvis.just"
chown -R "$TARGET_USER:$TARGET_GROUP" "$TARGET_HOME/.config/ublue-os"

install_branding

printf '\n\033[1;32mJARVIS is installed.\033[0m\n'
printf '  Open:       JARVIS Assistant in the app grid, or: jarvis-ai via ujust\n'
printf '  Voice:      say "Jarvis, volume up" or press Super+Alt+J\n'
printf '  Check:      jarvis-voice status\n'
printf '  Add model:  ujust jarvis-ai-models\n'
printf '  Uninstall:  sudo %s --uninstall\n' "$0"
