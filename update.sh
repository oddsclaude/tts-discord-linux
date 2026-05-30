#!/bin/bash
R="https://raw.githubusercontent.com/oddsclaude/tts-discord-linux/main"
curl -fL "$R/tts-manage.sh"   -o ~/.local/bin/tts-manage   && chmod +x ~/.local/bin/tts-manage
curl -fL "$R/tts-speak.sh"    -o ~/.local/bin/tts-speak    && chmod +x ~/.local/bin/tts-speak
curl -fL "$R/tts-mic-init.sh" -o ~/.local/bin/tts-mic-init && chmod +x ~/.local/bin/tts-mic-init
echo "updated"
