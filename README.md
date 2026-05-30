# tts-discord-linux

TTS virtual microphone setup for Discord on Linux. Uses [Piper](https://github.com/rhasspy/piper) (neural TTS) and PipeWire/PulseAudio to fake a microphone that speaks typed text.

## Install

```bash
bash <(curl -fL https://raw.githubusercontent.com/oddsclaude/tts-discord-linux/main/tts-setup.sh)
```

Force a specific distro family:

```bash
bash <(curl -fL https://raw.githubusercontent.com/oddsclaude/tts-discord-linux/main/tts-setup.sh) --distro arch
```

Supported: `arch`, `debian`, `fedora`, `gentoo`

## What it does

- Installs `piper-tts` (via AUR on Arch, binary install elsewhere)
- Downloads the `en_US-lessac-medium` voice model (~61MB)
- Creates `~/.local/bin/tts-mic-init` - sets up virtual sink/source via pactl
- Creates `~/.local/bin/tts-speak` - dialog prompt -> piper -> virtual mic + speakers
- Registers startup: systemd user service (systemd) or XDG autostart (OpenRC/other)

## Usage

```bash
tts-speak                  # opens dialog (kdialog/zenity/rofi)
tts-speak hello world      # speaks directly, no dialog
```

After install, set **TTS_Virtual_Mic** as your input device in Discord voice settings. Restart Discord first if it was already open.

## KDE keybind

System Settings - Shortcuts - Custom Shortcuts - New - Global Shortcut - Command/URL

Set action to: `~/.local/bin/tts-speak`
