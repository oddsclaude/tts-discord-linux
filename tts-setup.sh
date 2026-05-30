#!/bin/bash
set -euo pipefail

PIPER_VERSION="2023.11.14-2"
VOICE_MODEL="en_US-lessac-medium"
VOICE_URL_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info() { echo -e "${GREEN}[tts-setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[tts-setup]${NC} $*"; }
die()  { echo -e "${RED}[tts-setup] error:${NC} $*" >&2; exit 1; }

FORCE_DISTRO=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --distro) FORCE_DISTRO="$2"; shift 2 ;;
        *) die "unknown argument: $1 (usage: tts-setup.sh [--distro arch|debian|fedora|gentoo])" ;;
    esac
done

if [[ -n "$FORCE_DISTRO" ]]; then
    DISTRO="$FORCE_DISTRO"
elif [[ -f /etc/os-release ]]; then
    source /etc/os-release
    COMBINED="${ID:-} ${ID_LIKE:-}"
    if   echo "$COMBINED" | grep -qi arch;   then DISTRO="arch"
    elif echo "$COMBINED" | grep -qiE "debian|ubuntu"; then DISTRO="debian"
    elif echo "$COMBINED" | grep -qiE "fedora|rhel|centos"; then DISTRO="fedora"
    elif echo "$COMBINED" | grep -qi gentoo; then DISTRO="gentoo"
    else die "unsupported distro: ${ID:-unknown}. use --distro arch|debian|fedora|gentoo"
    fi
else
    die "no /etc/os-release found. use --distro arch|debian|fedora|gentoo"
fi

ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  PIPER_ARCH="x86_64" ;;
    aarch64) PIPER_ARCH="aarch64" ;;
    *) die "unsupported architecture: $ARCH" ;;
esac

info "distro: $DISTRO | arch: $ARCH"

install_piper_binary() {
    local url="https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_linux_${PIPER_ARCH}.tar.gz"
    info "downloading piper binary from GitHub..."
    sudo mkdir -p /opt/piper-tts
    curl -fL "$url" | sudo tar -xz -C /opt/piper-tts --strip-components=1
    sudo ln -sf /opt/piper-tts/piper /usr/local/bin/piper-tts
}

info "installing piper-tts..."
case "$DISTRO" in
    arch)
        if   command -v paru &>/dev/null; then paru -S --noconfirm piper-tts-bin
        elif command -v yay  &>/dev/null; then yay  -S --noconfirm piper-tts-bin
        else warn "no AUR helper found, falling back to binary install"; install_piper_binary
        fi
        ;;
    debian)
        command -v pactl &>/dev/null || sudo apt-get install -y libpulse0
        install_piper_binary
        ;;
    fedora)
        command -v pactl &>/dev/null || sudo dnf install -y pulseaudio-utils
        install_piper_binary
        ;;
    gentoo)
        install_piper_binary
        ;;
esac

info "downloading voice model (${VOICE_MODEL})..."
mkdir -p ~/.local/share/piper
if [[ ! -f ~/.local/share/piper/${VOICE_MODEL}.onnx ]]; then
    curl -fL "${VOICE_URL_BASE}/${VOICE_MODEL}.onnx"      -o ~/.local/share/piper/${VOICE_MODEL}.onnx
    curl -fL "${VOICE_URL_BASE}/${VOICE_MODEL}.onnx.json" -o ~/.local/share/piper/${VOICE_MODEL}.onnx.json
else
    info "voice model already present, skipping"
fi

mkdir -p ~/.local/bin

info "writing tts-mic-init..."
cat > ~/.local/bin/tts-mic-init << 'EOF'
#!/bin/bash
pactl list short modules | grep -q tts_sink || \
    pactl load-module module-null-sink sink_name=tts_sink \
        sink_properties=device.description="TTS_Virtual_Sink"

pactl list short modules | grep -q tts_mic || \
    pactl load-module module-virtual-source source_name=tts_mic \
        source_properties=device.description="TTS_Virtual_Mic" \
        master=tts_sink.monitor
EOF
chmod +x ~/.local/bin/tts-mic-init

info "writing tts-speak..."
cat > ~/.local/bin/tts-speak << 'EOF'
#!/bin/bash
MODEL="$HOME/.local/share/piper/en_US-lessac-medium.onnx"

if   command -v kdialog &>/dev/null; then TEXT=$(kdialog --title "TTS" --inputbox "Say:")
elif command -v zenity  &>/dev/null; then TEXT=$(zenity --entry --title "TTS" --text "Say:")
elif command -v rofi    &>/dev/null; then TEXT=$(echo "" | rofi -dmenu -p "Say:")
else read -rp "Say: " TEXT
fi
[[ -z "${TEXT:-}" ]] && exit 0

piper-tts --model "$MODEL" --output_raw <<< "$TEXT" \
    | tee >(pacat --device=tts_sink --volume=65536 --format=s16le --rate=22050 --channels=1) \
    | pacat --volume=65536 --format=s16le --rate=22050 --channels=1
EOF
chmod +x ~/.local/bin/tts-speak

INIT="$(basename "$(readlink /proc/1/exe)" 2>/dev/null || cat /proc/1/comm)"
if echo "$INIT" | grep -qi systemd; then
    info "writing systemd user service..."
    mkdir -p ~/.config/systemd/user
    cat > ~/.config/systemd/user/tts-mic.service << 'EOF'
[Unit]
Description=TTS Virtual Microphone
After=pipewire-pulse.service
Wants=pipewire-pulse.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=%h/.local/bin/tts-mic-init

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now tts-mic.service
else
    info "non-systemd init detected ($INIT), using XDG autostart..."
    mkdir -p ~/.config/autostart
    cat > ~/.config/autostart/tts-mic.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=TTS Virtual Microphone
Exec=/bin/bash -c "$HOME/.local/bin/tts-mic-init"
X-GNOME-Autostart-enabled=true
EOF
    ~/.local/bin/tts-mic-init
fi

info "done!"
echo ""
echo "  virtual mic : TTS_Virtual_Mic  (set as Discord input device)"
echo "  speak script: ~/.local/bin/tts-speak"
echo "  KDE keybind : System Settings -> Shortcuts -> Custom Shortcuts"
echo "                New -> Global Shortcut -> Command/URL"
echo "                action: $HOME/.local/bin/tts-speak"
echo ""
echo "  restart Discord to see the new input device"
