#!/bin/bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[tts-manage]${NC} $*"; }
warn() { echo -e "${YELLOW}[tts-manage]${NC} $*"; }
die()  { echo -e "${RED}[tts-manage] error:${NC} $*" >&2; exit 1; }

PIPER_DIR="$HOME/.local/share/piper"
VOICE_URL_BASE_ROOT="https://huggingface.co/rhasspy/piper-voices/resolve/main"
REPO_RAW="https://raw.githubusercontent.com/oddsclaude/tts-discord-linux/main"

usage() {
    echo "usage: tts-manage <command>"
    echo ""
    echo "  status                 show active voice and installed models"
    echo "  switch MODEL           switch active voice (downloads if needed)"
    echo "  download MODEL         download a voice model without switching"
    echo "  download-all [FILTER]  download all voices (optional grep filter e.g. en_US)"
    echo "  remove MODEL           delete a downloaded voice model"
    echo "  list                   list installed voice models"
    echo "  update                 update scripts to latest from repo"
    echo "  uninstall              remove everything (scripts, service, models)"
    echo ""
    echo "examples:"
    echo "  tts-manage switch en_GB-alan-medium"
    echo "  tts-manage download-all en_US"
    echo "  tts-manage download-all          # all voices (several GB)"
}

parse_voice_url() {
    local model="$1"
    local lang_region="${model%%-*}"
    local rest="${model#*-}"
    local quality="${rest##*-}"
    local voice="${rest%-*}"
    local lang="${lang_region%%_*}"
    echo "${VOICE_URL_BASE_ROOT}/${lang}/${lang_region}/${voice}/${quality}/${model}"
}

get_sample_rate() {
    local model="$1"
    python3 -c "import json; d=json.load(open('${PIPER_DIR}/${model}.onnx.json')); print(d['audio']['sample_rate'])" 2>/dev/null || echo "22050"
}

cmd_status() {
    local active model rate
    active=$(cat "${PIPER_DIR}/active_model" 2>/dev/null || echo "none")
    model=$(basename "$active" .onnx 2>/dev/null || echo "none")
    rate=$(cat "${PIPER_DIR}/active_rate" 2>/dev/null || echo "?")
    echo -e "${CYAN}active voice:${NC} $model (${rate} Hz)"
    echo ""
    cmd_list
}

cmd_list() {
    echo -e "${CYAN}installed models:${NC}"
    local found=0
    for f in "${PIPER_DIR}"/*.onnx; do
        [[ -f "$f" ]] || continue
        local name
        name=$(basename "$f" .onnx)
        local active
        active=$(cat "${PIPER_DIR}/active_model" 2>/dev/null || echo "")
        if [[ "$active" == *"$name"* ]]; then
            echo -e "  ${GREEN}* $name${NC} (active)"
        else
            echo "    $name"
        fi
        found=1
    done
    [[ $found -eq 0 ]] && echo "  (none)"
}

cmd_download() {
    local model="$1"
    if [[ -f "${PIPER_DIR}/${model}.onnx" ]]; then
        info "$model already downloaded"
        return
    fi
    local base_url
    base_url=$(parse_voice_url "$model")
    info "downloading ${model}..."
    mkdir -p "$PIPER_DIR"
    curl -fL "${base_url}.onnx"      -o "${PIPER_DIR}/${model}.onnx"
    curl -fL "${base_url}.onnx.json" -o "${PIPER_DIR}/${model}.onnx.json"
    info "downloaded $model"
}

cmd_switch() {
    local model="$1"
    cmd_download "$model"
    echo "${PIPER_DIR}/${model}.onnx" > "${PIPER_DIR}/active_model"
    get_sample_rate "$model" > "${PIPER_DIR}/active_rate"
    info "switched to $model"
}

cmd_remove() {
    local model="$1"
    local active
    active=$(cat "${PIPER_DIR}/active_model" 2>/dev/null || echo "")
    [[ "$active" == *"$model"* ]] && die "can't remove active model - switch to another first"
    rm -f "${PIPER_DIR}/${model}.onnx" "${PIPER_DIR}/${model}.onnx.json"
    info "removed $model"
}

cmd_download_all() {
    local filter="${1:-}"
    info "fetching voice index..."
    local voices_json
    voices_json=$(curl -fsSL "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json")
    local models
    models=$(echo "$voices_json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for key in sorted(data.keys()):
    print(key)
")
    local total downloaded=0
    total=$(echo "$models" | wc -l)

    if [[ -n "$filter" ]]; then
        models=$(echo "$models" | grep "$filter" || true)
        local matched
        matched=$(echo "$models" | grep -c . || true)
        info "filter '$filter' matched $matched of $total models"
    else
        warn "downloading ALL $total voice models - this will use several GB of disk space"
        read -rp "Continue? [y/N] " confirm
        [[ "$confirm" =~ ^[Yy]$ ]] || { echo "cancelled"; exit 0; }
    fi

    mkdir -p "$PIPER_DIR"
    while IFS= read -r model; do
        [[ -z "$model" ]] && continue
        if [[ -f "${PIPER_DIR}/${model}.onnx" ]]; then
            echo "  skip $model (exists)"
        else
            echo -n "  $model... "
            local base_url
            base_url=$(parse_voice_url "$model")
            if curl -fsSL "${base_url}.onnx"      -o "${PIPER_DIR}/${model}.onnx" \
            && curl -fsSL "${base_url}.onnx.json" -o "${PIPER_DIR}/${model}.onnx.json"; then
                echo "done"
                ((downloaded++)) || true
            else
                echo "FAILED"
                rm -f "${PIPER_DIR}/${model}.onnx" "${PIPER_DIR}/${model}.onnx.json"
            fi
        fi
    done <<< "$models"

    info "done - $downloaded new models downloaded"
}

cmd_update() {
    info "updating scripts from repo..."
    curl -fL "${REPO_RAW}/tts-manage.sh"   -o ~/.local/bin/tts-manage   && chmod +x ~/.local/bin/tts-manage
    curl -fL "${REPO_RAW}/tts-speak.sh"    -o ~/.local/bin/tts-speak    && chmod +x ~/.local/bin/tts-speak
    curl -fL "${REPO_RAW}/tts-mic-init.sh" -o ~/.local/bin/tts-mic-init && chmod +x ~/.local/bin/tts-mic-init
    info "update complete - voice models and settings unchanged"
}

cmd_uninstall() {
    echo -e "${RED}This will remove:${NC}"
    echo "  ~/.local/bin/tts-speak"
    echo "  ~/.local/bin/tts-mic-init"
    echo "  ~/.local/bin/tts-manage"
    echo "  ~/.local/share/piper/ (all voice models)"
    echo "  ~/.config/systemd/user/tts-mic.service"
    echo "  ~/.config/autostart/tts-mic.desktop"
    echo ""
    read -rp "Are you sure? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || { echo "cancelled"; exit 0; }

    if systemctl --user is-enabled tts-mic.service &>/dev/null; then
        systemctl --user disable --now tts-mic.service
    fi

    pactl unload-module "$(pactl list short modules | awk '/tts_mic/{print $1}')" 2>/dev/null || true
    pactl unload-module "$(pactl list short modules | awk '/tts_sink/{print $1}')" 2>/dev/null || true

    rm -f ~/.local/bin/tts-speak
    rm -f ~/.local/bin/tts-mic-init
    rm -f ~/.local/bin/tts-manage
    rm -f ~/.config/systemd/user/tts-mic.service
    rm -f ~/.config/autostart/tts-mic.desktop
    rm -rf "${PIPER_DIR}"

    systemctl --user daemon-reload 2>/dev/null || true
    info "uninstalled"
}

[[ $# -eq 0 ]] && { usage; exit 0; }

case "$1" in
    status)       cmd_status ;;
    list)         cmd_list ;;
    switch)       [[ $# -lt 2 ]] && die "usage: tts-manage switch MODEL"; cmd_switch "$2" ;;
    download)     [[ $# -lt 2 ]] && die "usage: tts-manage download MODEL"; cmd_download "$2" ;;
    download-all) cmd_download_all "${2:-}" ;;
    remove)       [[ $# -lt 2 ]] && die "usage: tts-manage remove MODEL"; cmd_remove "$2" ;;
    update)       cmd_update ;;
    uninstall)    cmd_uninstall ;;
    *) usage; die "unknown command: $1" ;;
esac
