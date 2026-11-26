# JARVIS OS helper recipes.
#
# Files in /usr/share/ublue-os/just/ are picked up automatically by the
# ujust command that ships with Bazzite/universal-blue.

# Re-run the JARVIS first-run setup wizard
[group('JARVIS')]
jarvis-setup:
    #!/usr/bin/env bash
    set -euo pipefail
    jarvis-firstboot --force

# Forget the first-run wizard so it shows up again on next login
[group('JARVIS')]
jarvis-setup-reset:
    #!/usr/bin/env bash
    set -euo pipefail
    jarvis-firstboot --reset
    echo "Done. The wizard will run on your next login."

# Launch the JARVIS AI assistant
[group('JARVIS')]
jarvis-ai:
    #!/usr/bin/env bash
    set -euo pipefail
    exec jarvis-assist

# Show the state of the local AI engine
[group('JARVIS')]
jarvis-ai-status:
    #!/usr/bin/env bash
    set -euo pipefail
    systemctl status ollama.service --no-pager --lines=0 2>/dev/null || echo "ollama.service: not running"
    echo
    echo "Installed models:"
    ollama list 2>/dev/null || echo "  (none yet - run 'ujust jarvis-ai-models')"
    echo
    echo "Default model: $(cat "$HOME/.config/jarvis/default-model" 2>/dev/null || echo 'qwen3:4b')"

# Download a local AI model and make it the default
[group('JARVIS')]
jarvis-ai-models:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Pick a model for JARVIS Assistant."
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
        mkdir -p "$HOME/.config/jarvis"
        printf '%s\n' "${model}" >"$HOME/.config/jarvis/default-model"
        echo
        echo "${model} is installed and is now the default."
        break
    done

# Rebrand the boot menu entry (GRUB)
[group('JARVIS')]
jarvis-rebrand-bootloader:
    #!/usr/bin/env bash
    set -euo pipefail
    if ! command -v grub2-mkconfig >/dev/null 2>&1; then
        echo "grub2-mkconfig not found - this system does not boot with GRUB."
        exit 0
    fi
    if grep -q '^GRUB_DISTRIBUTOR=' /etc/default/grub; then
        sudo sed -i 's|^GRUB_DISTRIBUTOR=.*|GRUB_DISTRIBUTOR="JARVIS"|' /etc/default/grub
    else
        echo 'GRUB_DISTRIBUTOR="JARVIS"' | sudo tee -a /etc/default/grub >/dev/null
    fi
    sudo grub2-mkconfig -o /boot/grub2/grub.cfg || echo "grub2-mkconfig failed - boot menu left as is"
    echo "Boot menu updated."

# Download the offline speech models and enable voice control
[group('JARVIS')]
jarvis-voice-setup:
    #!/usr/bin/env bash
    set -euo pipefail
    jarvis-voice setup
    systemctl --user enable --now jarvis-assist.service
    echo "Voice control is ready. Press Super+Alt+J or use the microphone button."

# Show voice configuration, model state and listener status
[group('JARVIS')]
jarvis-voice-status:
    #!/usr/bin/env bash
    set -euo pipefail
    jarvis-voice status

# Run the offline voice self-test
[group('JARVIS')]
jarvis-voice-test:
    #!/usr/bin/env bash
    set -euo pipefail
    jarvis-voice test

# Allow (or forbid) arbitrary voice-typed shell commands. Always confirmed first.
[group('JARVIS')]
jarvis-voice-unlock allow="on":
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ "{{ allow }}" == "on" ]]; then
        jarvis-voice set --free-form
        echo "Arbitrary commands enabled. They still require 'confirm command'."
    else
        jarvis-voice set --no-free-form
        echo "Arbitrary commands disabled."
    fi

# Show JARVIS version and image details
[group('JARVIS')]
jarvis-about:
    #!/usr/bin/env bash
    set -euo pipefail
    grep -E '^(NAME|VERSION|PRETTY_NAME|IMAGE_ID|JARVIS_)' /usr/lib/os-release
    echo
    rpm-ostree status --booted 2>/dev/null | head -8 || bootc status
