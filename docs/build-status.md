# Estado de construcción — JARVIS

Última actualización: 2026-09-04 (fin de la sesión inicial). Código en `proyectos/jarvis`, datos de prueba en un `JARVIS_HOME` temporal.

## Cambios de la segunda iteración (2026-09-04, noche)
- Todas las herramientas de Claude Code para el agente con aprobación en código (`claude.builtin_tools`, `builtin_approval`), verificado en vivo: Glob/lectura automáticas, Bash aprobado y ejecutado, Write rechazado y no creado.
- App nativa `Jarvis.app` (Swift/AppKit, `macapp/`): orbe flotante siempre visible, barra de menús, atajo ⌥Espacio, burbujas, aprobación con clic, lanza el backend como hijo (permiso de micrófono atribuido a la app) o se conecta a uno existente. Instalada en ~/Applications y verificada en ejecución (modo conectado al `jarvis serve` del usuario).
- Voz neuronal local Kokoro (`tts.provider: kokoro`, voces ef_dora/em_alex/em_santa), ~0,8 s por frase; muestras en ~/Desktop/jarvis-muestras-voz.
- 44 tests en verde.

## Tercera iteración (2026-09-05, madrugada)
- `claude.permission_mode: auto` (clasificador de Claude Code) + red de seguridad `always_confirm_patterns` (hook PreToolUse → "ask"). Verificado: lectura y `echo > archivo` automáticos; `rm -rf` explícito → sin la red se ejecutó (borró las muestras de voz, regeneradas); con la red → pide permiso y, rechazado, no borra.
- Preguntas de aclaración (AskUserQuestion) enrutadas a voz/panel/app: respuesta por etiqueta, número ("la segunda") o texto libre.
- `set_model`/`get_model`: cambio de modelo hablando ("cambia a sonnet"), aplicado en la sesión viva y guardado. Verificado.
- Voz en flujo (frase a frase mientras Claude escribe): primera frase a 1,9 s en un turno real; latencia de arranque de Kokoro 0,66 s; interrupción limpia.
- Avisos de permiso resumidos y hablables ("¿Me das permiso para ejecutar en la terminal: borrar la carpeta x?"); prompt orientado a voz (1–2 frases, sin Markdown, sin narrar herramientas); `effort: low` por defecto.
- 49 tests en verde. Jarvis.app recompilada (burbujas de pregunta/permiso).

## Cuarta iteración (2026-09-05, mediodía)
- Voz repetida: corregido (archivos temporales únicos por frase en Kokoro). Transcripción: normalización de volumen, prompt en español, limpieza de «Jarvis» inicial, no se descarta audio tras el tono, silencio de cierre 1,1 s, y STT por defecto mlx-whisper large-v3-turbo (GPU) tras comparativa medida. 52 tests en verde. App reiniciada con todo cargado (STT cargado en 2,8 s).

## Incidente corregido (2026-09-05, mañana)
- Una prueba del panel (`/api/config`) escribía en la configuración REAL `~/.jarvis/config.yaml` (rutas temporales de pytest y nombre "Viernes"). Efecto: la memoria del usuario se guardaba en una carpeta temporal (solo contenía 2 páginas de prueba; nada personal se perdió). Arreglo: `tests/conftest.py` aísla `JARVIS_HOME`/`JARVIS_CONFIG` en todas las pruebas (verificado: el hash del config real no cambia tras la suite) y `jarvis config init --force` restauró la configuración (Jarvis, rutas reales, kokoro, auto).
- El backend lanzado por Jarvis.app sobrevivía si la app se cerraba: ahora recibe `--parent-pid`, un hilo vigilante lo cierra (os._exit tras 3 s de gracia, inmune a tuberías rotas) y también atiende SIGTERM. Verificado: 0 backends 7 s después de cerrar la app; al reabrirla lanza uno nuevo (o se conecta si ya hay uno en el puerto).

## Qué funciona
- Fases 1-6 implementadas: chat real con Claude (Agent SDK, sesión claude.ai), wiki de memoria validada con FTS5, ingesta txt/md/pdf con
  extracción citada, corrección/disputa/olvido, lint determinista y semántico, PTT + STT local + TTS local cancelable, wake word local
  (openWakeWord `hey_jarvis`; Porcupine opcional con clave), máquina de estados, herramientas con política y aprobaciones, panel local con
  grafo/lista/trazas/aprobaciones/configuración, doctor, LaunchAgent opcional.

## Qué se verificó
- 38 tests automatizados en verde (`docs/test-results.md`).
- Criterios de aceptación 1, 2, 3, 4, 7, 8, 9, 10 demostrados con Claude real (terminal y panel en Chrome).
- Evaluación de memoria: 6/6 recuperación y atribución, 0 afirmaciones sin respaldo (`docs/memory-eval.md`).
- Rendimiento del grafo medido con 100/1000/5000 nodos sintéticos.

## Pendiente
- Criterios 5 y 6 (ciclo de voz y activación reales) requieren conceder permiso de micrófono a la app de terminal y ejecutar
  `jarvis listen` o `python scripts/acoustic_test.py`.
- Barge-in (interrupción por voz durante TTS): no implementado (half-duplex documentado).
- Resúmenes mínimos de conversación (`memory.save_conversation_summaries`): opción expuesta en configuración, el guardado automático al cerrar sesión no está implementado (el modelo puede guardar notas a petición).

## Bloqueado y por qué
- Micrófono: la terminal (Ghostty) devuelve silencio absoluto (sin permiso TCC). Paso mínimo: Ajustes → Privacidad y seguridad → Micrófono → activar la app de terminal, reiniciarla, `jarvis doctor`.
- Porcupine: falta `PICOVOICE_ACCESS_KEY` (opcional; openWakeWord funciona sin clave).

## Pendiente de esta iteración
- Verificar visualmente la app con el usuario (orbe + burbujas + aprobación con clic) y el permiso de micrófono para Jarvis.app cuando la app lance el backend por sí misma.
- El `jarvis serve` que el usuario tiene abierto carga el código anterior: reiniciarlo (o cerrar y usar la app) para tener herramientas completas y voz Kokoro.

## Próxima acción concreta
1. Conceder el permiso de micrófono y ejecutar `jarvis listen` (o `python scripts/acoustic_test.py`) para cerrar los criterios 5 y 6.
2. Si se desea, `jarvis config set wake_word.sensitivity 0.6` tras probar la activación con voz real.
