#!/bin/bash
set -euo pipefail

PIPER_VERSION="2023.11.14-2"
DEFAULT_VOICE="en_US-lessac-medium"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[tts-setup]${NC} $*"; }
warn() { echo -e "${YELLOW}[tts-setup]${NC} $*"; }
die()  { echo -e "${RED}[tts-setup] error:${NC} $*" >&2; exit 1; }

list_voices() {
    echo -e "${CYAN}Available voice models:${NC}"
    echo ""
    echo "  English (US):"
    echo "    en_US-lessac-low        en_US-lessac-medium     en_US-lessac-high"
    echo "    en_US-ryan-low          en_US-ryan-medium       en_US-ryan-high"
    echo "    en_US-amy-low           en_US-amy-medium"
    echo "    en_US-joe-medium        en_US-norman-medium"
    echo "    en_US-hfc_female-medium en_US-hfc_male-medium"
    echo "    en_US-libritts_r-medium en_US-kusal-medium"
    echo ""
    echo "  English (GB):"
    echo "    en_GB-alan-low          en_GB-alan-medium"
    echo "    en_GB-cori-medium       en_GB-cori-high"
    echo "    en_GB-jenny_dioco-medium"
    echo "    en_GB-northern_english_male-medium"
    echo "    en_GB-southern_english_female-low"
    echo ""
    echo "  German:"
    echo "    de_DE-thorsten-low      de_DE-thorsten-medium   de_DE-thorsten-high"
    echo "    de_DE-kerstin-low       de_DE-eva_k-x_low"
    echo ""
    echo "  French:"
    echo "    fr_FR-upmc-medium       fr_FR-mls-medium"
    echo ""
    echo "  Spanish:"
    echo "    es_ES-carlfm-low        es_ES-carlfm-medium"
    echo "    es_MX-ald-medium"
    echo ""
    echo "  More voices: https://huggingface.co/rhasspy/piper-voices"
}

FORCE_DISTRO=""
VOICE_MODEL="$DEFAULT_VOICE"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --distro)      FORCE_DISTRO="$2"; shift 2 ;;
        --voice)       VOICE_MODEL="$2"; shift 2 ;;
        --list-voices) list_voices; exit 0 ;;
        *) die "unknown argument: $1\nusage: tts-setup.sh [--distro arch|debian|fedora|gentoo] [--voice MODEL] [--list-voices]\nexample: tts-setup.sh --voice en_GB-alan-medium" ;;
    esac
done

parse_voice() {
    local model="$1"
    local lang_region="${model%%-*}"
    local rest="${model#*-}"
    local quality="${rest##*-}"
    local voice="${rest%-*}"
    local lang="${lang_region%%_*}"
    VOICE_URL_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/main/${lang}/${lang_region}/${voice}/${quality}"
}

parse_voice "$VOICE_MODEL"

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

info "distro: $DISTRO | arch: $ARCH | voice: $VOICE_MODEL"

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

echo "$HOME/.local/share/piper/${VOICE_MODEL}.onnx" > ~/.local/share/piper/active_model
SAMPLE_RATE=$(python3 -c "import json; d=json.load(open('$HOME/.local/share/piper/${VOICE_MODEL}.onnx.json')); print(d['audio']['sample_rate'])" 2>/dev/null || echo "22050")
echo "$SAMPLE_RATE" > ~/.local/share/piper/active_rate

mkdir -p ~/.local/bin

REPO_RAW="https://raw.githubusercontent.com/oddsclaude/tts-discord-linux/main"

info "installing tts-manage..."
curl -fL "${REPO_RAW}/tts-manage.sh" -o ~/.local/bin/tts-manage
chmod +x ~/.local/bin/tts-manage

info "installing tts-mic-init..."
curl -fL "${REPO_RAW}/tts-mic-init.sh" -o ~/.local/bin/tts-mic-init
chmod +x ~/.local/bin/tts-mic-init

info "installing tts-speak..."
curl -fL "${REPO_RAW}/tts-speak.sh" -o ~/.local/bin/tts-speak
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
echo "  active voice: $VOICE_MODEL"
echo ""
echo "  to switch voices: tts-manage switch MODEL"
echo "  to list installed: tts-manage list"
echo "  to uninstall:     tts-manage uninstall"
echo "  available voices: tts-setup.sh --list-voices"
echo ""
echo "  KDE keybind : System Settings -> Shortcuts -> Custom Shortcuts"
echo "                New -> Global Shortcut -> Command/URL"
echo "                action: $HOME/.local/bin/tts-speak"
echo ""
echo "  restart Discord to see the new input device"
