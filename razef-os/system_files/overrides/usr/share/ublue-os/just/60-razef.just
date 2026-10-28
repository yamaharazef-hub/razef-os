# Razef OS helper recipes.
#
# Files in /usr/share/ublue-os/just/ are picked up automatically by the
# ujust command that ships with Bazzite/universal-blue.

# Re-run the Razef first-run setup wizard
[group('Razef')]
razef-setup:
    #!/usr/bin/env bash
    set -euo pipefail
    /usr/bin/razef-firstboot --force

# Forget the first-run wizard so it shows up again on next login
[group('Razef')]
razef-setup-reset:
    #!/usr/bin/env bash
    set -euo pipefail
    /usr/bin/razef-firstboot --reset
    echo "Done. The wizard will run on your next login."

# Launch the Razef AI assistant
[group('Razef')]
razef-ai:
    #!/usr/bin/env bash
    set -euo pipefail
    exec /usr/bin/razef-assist

# Show the state of the local AI engine
[group('Razef')]
razef-ai-status:
    #!/usr/bin/env bash
    set -euo pipefail
    systemctl status ollama.service --no-pager --lines=0 2>/dev/null || echo "ollama.service: not running"
    echo
    echo "Installed models:"
    ollama list 2>/dev/null || echo "  (none yet - run 'ujust razef-ai-models')"
    echo
    echo "Default model: $(cat "$HOME/.config/razef/default-model" 2>/dev/null || echo 'qwen3:4b')"

# Download a local AI model and make it the default
[group('Razef')]
razef-ai-models:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Pick a model for Razef Assistant."
    echo "  1.7b / 4b  - fast, fine on almost anything"
    echo "  8b         - better reasoning, 16 GB RAM recommended"
    echo "  20b        - the best local quality, 24 GB+ VRAM/RAM"
    echo
    mapfile -t models < <(printf '%s\n' \
        "qwen3:1.7b" "qwen3:4b" "qwen3:8b" "gemma3:4b" "llama3.2:3b" "gpt-oss:20b")
    select model in "${models[@]}"; do
        if [[ -z "${model:-}" ]]; then
            echo "Not a valid choice."
            exit 1
        fi
        echo
        echo "Pulling ${model} ... this can take a few minutes."
        sudo systemctl enable --now ollama.service
        ollama pull "${model}"
        mkdir -p "$HOME/.config/razef"
        printf '%s\n' "${model}" >"$HOME/.config/razef/default-model"
        echo
        echo "${model} is installed and is now the default."
        break
    done

# Rebrand the boot menu entry (GRUB)
[group('Razef')]
razef-rebrand-bootloader:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v grub2-mkconfig >/dev/null 2>&1; then
        echo "grub2-mkconfig not found - this system does not boot with GRUB."
        exit 0
    fi
    if grep -q '^GRUB_DISTRIBUTOR=' /etc/default/grub; then
        sudo sed -i 's|^GRUB_DISTRIBUTOR=.*|GRUB_DISTRIBUTOR="Razef"|' /etc/default/grub
    else
        echo 'GRUB_DISTRIBUTOR="Razef"' | sudo tee -a /etc/default/grub >/dev/null
    fi
    sudo grub2-mkconfig -o /boot/grub2/grub.cfg || echo "grub2-mkconfig failed - boot menu left as is"
    echo "Boot menu updated."

# Show Razef version and image details
[group('Razef')]
razef-about:
    #!/usr/bin/env bash
    set -euo pipefail
    grep -E '^(NAME|VERSION|PRETTY_NAME|IMAGE_ID|RAZEF_)' /usr/lib/os-release
    echo
    rpm-ostree status --booted 2>/dev/null | head -8 || bootc status
