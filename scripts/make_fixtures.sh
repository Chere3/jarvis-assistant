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
# palabra de activación sola (modelo propio models/jarvis.onnx) y negativos fonéticamente parecidos
gen Samantha jarvis_en        "Jarvis"
gen Paulina  jarvis_es        "Jarvis"
gen Mónica   es_negativo      "Javier dejó el jarabe en el jardín y ya vi que Travis se fue con Elvis"
gen Daniel   en_negativo      "The harvest service was nervous about Travis, Mavis and the jar of jazz"
echo "fixtures en $OUT"
