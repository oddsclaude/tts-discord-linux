#!/bin/bash
echo "tts-speak is deprecated - use tts-gui or tts-gui --speak" >&2
MODEL=$(cat "$HOME/.local/share/piper/active_model" 2>/dev/null || echo "$HOME/.local/share/piper/en_US-lessac-medium.onnx")
RATE=$(cat "$HOME/.local/share/piper/active_rate" 2>/dev/null || echo "22050")

if [[ $# -gt 0 ]]; then
    TEXT="$*"
    piper-tts --model "$MODEL" --output_raw <<< "$TEXT" \
        | tee >(pacat --device=tts_sink --volume=65536 --format=s16le --rate="$RATE" --channels=1) \
        | pacat --volume=65536 --format=s16le --rate="$RATE" --channels=1
else
    if command -v tts-gui &>/dev/null; then
        exec tts-gui --speak
    elif command -v kdialog &>/dev/null; then
        TEXT=$(kdialog --title "TTS" --inputbox "Say:") || exit 0
    elif command -v zenity  &>/dev/null; then
        TEXT=$(zenity --entry --title "TTS" --text "Say:") || exit 0
    elif command -v rofi    &>/dev/null; then
        TEXT=$(echo "" | rofi -dmenu -p "Say:") || exit 0
    else
        read -rp "Say: " TEXT
    fi
    [[ -z "${TEXT:-}" ]] && exit 0
    piper-tts --model "$MODEL" --output_raw <<< "$TEXT" \
        | tee >(pacat --device=tts_sink --volume=65536 --format=s16le --rate="$RATE" --channels=1) \
        | pacat --volume=65536 --format=s16le --rate="$RATE" --channels=1
fi
