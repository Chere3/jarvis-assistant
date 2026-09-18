#!/bin/zsh
# Genera fixtures de audio sintéticos (voces del sistema) para las pruebas grabadas.
# No sustituyen una prueba real de micrófono.
set -e
OUT="$(dirname "$0")/../tests/fixtures"
mkdir -p "$OUT"
gen() { say -v "$1" -o "$OUT/$2.aiff" "$3"; ffmpeg -y -loglevel error -i "$OUT/$2.aiff" -ar 16000 -ac 1 -sample_fmt s16 "$OUT/$2.wav"; rm "$OUT/$2.aiff"; }
gen Paulina  es_recuerda      "Jarvis, recuerda que prefiero respuestas cortas"
gen Mónica   es_proyecto      "¿Qué sabes de mi proyecto del asistente?"
gen Samantha hey_jarvis_en    "hey jarvis"
gen Paulina  hey_jarvis_es    "hey jarvis"
echo "fixtures en $OUT"
