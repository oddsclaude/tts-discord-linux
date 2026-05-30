#!/bin/bash
MODEL=$(cat "$HOME/.local/share/piper/active_model" 2>/dev/null || echo "$HOME/.local/share/piper/en_US-lessac-medium.onnx")
RATE=$(cat "$HOME/.local/share/piper/active_rate" 2>/dev/null || echo "22050")

if [[ $# -gt 0 ]]; then
    TEXT="$*"
else
    if   command -v kdialog &>/dev/null; then TEXT=$(kdialog --title "TTS" --inputbox "Say:")
    elif command -v zenity  &>/dev/null; then TEXT=$(zenity --entry --title "TTS" --text "Say:")
    elif command -v rofi    &>/dev/null; then TEXT=$(echo "" | rofi -dmenu -p "Say:")
    else read -rp "Say: " TEXT
    fi
fi
[[ -z "${TEXT:-}" ]] && exit 0

piper-tts --model "$MODEL" --output_raw <<< "$TEXT" \
    | tee >(pacat --device=tts_sink --volume=65536 --format=s16le --rate="$RATE" --channels=1) \
    | pacat --volume=65536 --format=s16le --rate="$RATE" --channels=1
