#!/bin/bash
pactl list short modules | grep -q tts_sink || \
    pactl load-module module-null-sink sink_name=tts_sink \
        sink_properties=device.description="TTS_Virtual_Sink"

pactl list short modules | grep -q tts_mic || \
    pactl load-module module-virtual-source source_name=tts_mic \
        source_properties=device.description="TTS_Virtual_Mic" \
        master=tts_sink.monitor
