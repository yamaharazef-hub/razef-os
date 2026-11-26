# JARVIS voice control

Hands-free control of the machine, entirely offline. No cloud speech API, no
account, no audio leaving the computer.

```
 microphone (PipeWire/ALSA)
        │  arecord, 16 kHz mono PCM
        ▼
 Vosk speech worker  ──JSON lines──▶  jarvis-assist backend
 (private venv)                      │
                                     ├─▶ deterministic command parser
                                     ├─▶ allowlisted computer-control actions
                                     ├─▶ Ollama for anything else
                                     └─▶ NDJSON events ─▶ assistant UI
                                                              │
 spoken reply ◀── Piper (or espeak-ng fallback) ◀──────────────┘
```

## First-time setup

`./install.sh` enables hands-free voice control by default. If you skipped the
model download, finish it from a terminal:

```bash
ujust jarvis-voice-setup      # downloads ~40 MB STT + ~60 MB TTS models
jarvis-voice test             # checks models, engine and parser
jarvis-voice listen-once      # say something; prints what was heard
```

The installer downloads the small models into
`/var/lib/jarvis/voice/models`, so voice works right after installation. A
user-level download in `~/.local/share/jarvis/voice/models` takes precedence.

## Using it

* Say **“Jarvis”** followed by a command. The wake word can be turned off in the
  assistant sidebar for continuous listening.
* Press **Super+Alt+J** or click the microphone button to toggle the listener.
  Toggling on starts *push-to-talk* for one utterance when wake word is off.
* Replies are spoken when **Speak replies** is on.

Examples:

| Say | What happens |
|---|---|
| “Jarvis, open Steam” | launches Steam |
| “Jarvis, volume up” / “set volume to 40” | changes speaker volume |
| “Jarvis, mute my microphone” | toggles the mic |
| “Jarvis, brightness down” | dims the screen |
| “Jarvis, play music” / “next track” | controls the active player |
| “Jarvis, take a screenshot” | saves in `~/Pictures` |
| “Jarvis, set a timer for 5 minutes” | systemd user timer + spoken alert |
| “Jarvis, check the GPU driver” | runs a read-only diagnostic |
| “Jarvis, install Spotify” | **confirmation** → Flatpak install |
| “Jarvis, restart Bluetooth” | **confirmation** → system service |
| “Jarvis, lock the computer” | **confirmation** → locks the session |
| “Jarvis, run `uptime`” | **confirmation with the phrase “confirm command”** |

Anything not recognised locally is passed to the local model as a normal question;
the answer is displayed and read aloud.

## Safety model

Three risk levels are assigned in `voice/actions.py`:

| Risk | Behaviour | Examples |
|---|---|---|
| `safe` | runs immediately | open app, volume, brightness, media, screenshot, timer, diagnostics |
| `moderate` | UI or spoken confirmation required | close app, user service, Wi-Fi toggle |
| `high` | confirmation with an explicit phrase required | install/remove apps, system update, system service, power, shell command |

* Every action is a **fixed argv list**. Only values validated in the parser are
  substituted — app IDs, systemd unit names, percentages, durations.
* There is no `shell=True`. The one exception is the `command` action, which is
  opt-in, always requires the spoken/typed phrase **“confirm command”**, and is
  recorded in the audit log.
* The model can request an action via a ` ```jarvis-action ` fence. The server
  validates it exactly like a voice command; the model never gets to claim it
  succeeded before the server reports the result.
* Every attempt — including denials — is appended to
  `~/.config/jarvis/voice/audit.jsonl`.

Turn arbitrary commands off with `ujust jarvis-voice-unlock off`, or set
`allow_free_form` to `false` in the config file.

## Configuration

`~/.config/jarvis/voice/config.json`:

```json
{
  "enabled": true,
  "wake_enabled": true,
  "wake_word": "jarvis",
  "ptt": false,
  "speak_replies": true,
  "voice_feedback": true,
  "allow_free_form": true,
  "stt_model": "vosk-model-small-en-us-0.15",
  "tts_voice": "en_US-lessac-medium",
  "sensitivity": 0.6
}
```

The UI writes this file through `/api/voice/config`; you can also use
`jarvis-voice set --no-wake`, `set --no-speak-replies`,
`set --no-free-form`, etc.

## Components

| Path | Purpose |
|---|---|
| `/usr/local/bin/jarvis-voice` → `/var/lib/jarvis/system/usr/bin/jarvis-voice` | CLI: `setup`, `status`, `speak`, `listen-once`, `start`, `stop`, `toggle`, `test`, `confirm`, `actions` |
| `/var/lib/jarvis/system/usr/lib/jarvis/voice/stt_worker.py` | Vosk + `arecord` worker, emits JSON events |
| `/var/lib/jarvis/system/usr/lib/jarvis/voice/commands.py` | deterministic phrase parser |
| `/var/lib/jarvis/system/usr/lib/jarvis/voice/actions.py` | allowlist, validation, confirmations, audit log |
| `/var/lib/jarvis/system/usr/lib/jarvis/voice/tts.py` | Piper TTS with espeak-ng fallback |
| `/var/lib/jarvis/system/usr/lib/jarvis/assist/server.py` | event bus, listener lifecycle, action execution |
| `~/.config/systemd/user/jarvis-assist.service` | starts backend and, if enabled, the listener |

Backend endpoints used by the UI and CLI:

```
GET  /api/voice/status      GET  /api/voice/events (NDJSON)
POST /api/voice/start       POST /api/voice/stop       POST /api/voice/toggle
POST /api/voice/speak       POST /api/voice/config     POST /api/voice/reload
GET  /api/actions           POST /api/actions/confirm
```

## Privacy

* Speech recognition and synthesis run on the machine. The backend binds only
  to `127.0.0.1`.
* The only network activity triggered by setup is the one-time model download.
  After that, unplug the cable and everything still works.
* Pointing `JARVIS_OLLAMA_URL` at a cloud-compatible gateway sends the prompt
  (and diagnostic output) off-machine, which breaks this guarantee.

## Troubleshooting

| Symptom | Fix |
|---|---|
| “Run `jarvis-voice setup` first” | models or private venv missing; run `ujust jarvis-voice-setup` |
| No microphone detected | `wpctl status`, check the input in GNOME Settings → Sound |
| “Command not installed: arecord” | `rpm-ostree install alsa-utils` + reboot, then re-run `./install.sh` |
| Wake word never fires | check the sidebar switch; `jarvis-voice set --wake`; try push-to-talk |
| It hears its own replies | enable the wake word or lower the speaker volume |
| Robotic voice | Piper model missing; `jarvis-voice setup`, or `espeak-ng` fallback is in use |
| A confirmation card disappeared | run `jarvis-voice status`; pending prompts die with the backend |
| Arbitrary commands refused | `ujust jarvis-voice-unlock on` |

Logs:

```bash
journalctl --user -u jarvis-assist -f
tail -f ~/.config/jarvis/voice/audit.jsonl
JARVIS_ASSIST_DEBUG=1 jarvis-assist --serve
```
