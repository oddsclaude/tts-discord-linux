#!/bin/bash
MODEL=$(cat "$HOME/.local/share/piper/active_model" 2>/dev/null || echo "$HOME/.local/share/piper/en_US-lessac-medium.onnx")
RATE=$(cat "$HOME/.local/share/piper/active_rate" 2>/dev/null || echo "22050")

speak() {
    local text="$1"
    if [[ "$MODEL" == "sam" ]]; then
        sam -stdout "$text" \
            | sox -t raw -r 22050 -b 8 -c 1 -e unsigned - -t raw -r "$RATE" -b 16 -e signed - \
            | tee >(pacat --device=tts_sink --volume=65536 --format=s16le --rate="$RATE" --channels=1) \
            | pacat --volume=65536 --format=s16le --rate="$RATE" --channels=1
    else
        piper-tts --model "$MODEL" --output_raw <<< "$text" \
            | tee >(pacat --device=tts_sink --volume=65536 --format=s16le --rate="$RATE" --channels=1) \
            | pacat --volume=65536 --format=s16le --rate="$RATE" --channels=1
    fi
}

if [[ $# -gt 0 ]]; then
    speak "$*"
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
    speak "$TEXT"
fi
