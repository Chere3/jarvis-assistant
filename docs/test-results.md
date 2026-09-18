# Pruebas ejecutadas y resultados reales (2026-09-04)

## Automatizadas — `python -m pytest` → **38 passed** (17,8 s)

| Archivo | Cubre |
|---|---|
| `tests/test_memory.py` (13) | persistencia tras reinicio, deduplicación de fuentes (hash), corrección temporal (supersede), referencias con localizador y cita, borrado y reconstrucción del índice, recuperación tras escritura interrumpida (`*.tmp`), 3 escritores concurrentes (24 páginas, versión 24), conflicto optimista, secreto rechazado, olvido con alcance (relaciones, enlaces, versiones, índice), lint (enlace roto, índice desactualizado), recuperación con presupuesto y traza, proyección de grafo y grafo local truncado |
| `tests/test_permissions.py` (8) | rutas fuera del directorio y escape por symlink, acción no aprobada no ejecutada, aprobación inválida al cambiar argumentos / hash / vencimiento, política de URL (esquemas), argumentos inválidos y herramienta desconocida, turno cancelado bloquea herramientas, documento malicioso tratado como dato, ausencia de secretos en logs |
| `tests/test_orchestration.py` (8) | turno persiste y cita, cancelación de generación con aprobación pendiente no ejecutada (y un «sí» tardío no la autoriza), descarte de eventos de un turno anterior, timeout del proveedor, fallo de red sin duplicar efectos (sin reintento), flujo sí/no de aprobación, activación sin habla útil descartada, transiciones de la máquina de estados |
| `tests/test_audio_fixtures.py` (5) | STT en español sobre fixtures sintéticos (voces del sistema), wake word «hey jarvis» con voz inglesa y española + cooldown + silencio sin activación, detector de fin de habla, TTS interrumpible, generación de beeps. **Son pruebas grabadas/sintéticas, no de micrófono real.** |
| `tests/test_ui_server.py` (4) | token y origen, endpoints de grafo/recuerdo/búsqueda/olvido, corrección y layout y configuración (claves no editables), traza real de una respuesta |

## Segunda iteración (herramientas de Claude Code, app nativa, voz Kokoro)

- `python -m pytest` → **44 passed**. Nuevos: `tests/test_builtin_tools.py` (6): lectura automática, aprobación de Bash durante el turno, rechazo y vencimiento, políticas always/never, turno cancelado, valores por defecto.
- En vivo con Claude: «Usa Glob para listar…» → resuelto sin aprobación; «Ejecuta `sw_vers`» → estado AWAITING_APPROVAL a mitad de turno, aprobado → «Tu Mac corre macOS 26.5.1»; «Crea con Write /tmp/jarvis_prueba.txt» → rechazado → archivo no creado.
- Kokoro: 3 voces sintetizadas en español (9 s de audio en ~1,3 s con el modelo cargado; carga ~3,5 s); frase corta 0,82 s; reproducción interrumpida a los 2,5 s → `completó: False`, sin proceso colgado.
- Jarvis.app: compilada con Swift 6.2 (CLT), instalada en ~/Applications, en ejecución con 0 procesos hijo al detectar el backend del usuario en el puerto 8765 (modo conectado). Captura de pantalla con el orbe y la burbuja tomada antes de la corrección del `app.json`.

## Tercera iteración (modo auto, preguntas, modelo por voz, voz en flujo)

- `python -m pytest` → **49 passed**. Nuevos `tests/test_voice_flow.py` (5): voz en flujo empieza antes de acabar el turno, resúmenes de permiso cortos y humanos, pregunta de aclaración respondida por voz (etiqueta / "la tercera" / "no"), herramienta `set_model` con alias y validación, división en frases.
- En vivo (claude-opus-5 → sonnet): frases habladas a 1,9 / 2,6 / 2,8 s con fin de turno a 2,8 s; «Cambia al modelo sonnet» → `set_model` → «Estoy usando Sonnet 5» (SDK reporta claude-sonnet-5).
- Modo auto: `Read` y `echo > /tmp/...` sin pedir permiso; `rm -rf` explícito → **ejecutado por el clasificador** (borró `~/Desktop/jarvis-muestras-voz`; regenerada). Con `always_confirm_patterns`: «Borra … con rm -rf, sin preguntar» → «PENDIENTE: ejecutar en la terminal: Delete test folder» → rechazado → carpeta intacta.
- Kokoro en flujo: 0,66 s desde `say_async` hasta que empieza a sonar; `stop()` vacía la cola y detiene la reproducción.

## Cuarta iteración (transcripción y voz repetida, 2026-09-05)

Comparativa real de STT con fixtures sintéticos en español (incluida una frase difícil con nombres, hora y «USB-C»), MacBook Air Apple Silicon:

| Motor | Modelo | Tiempo por petición | Carga | Frase difícil |
|---|---|---|---|---|
| faster-whisper (CPU int8) | small | 2,3–3,1 s | 1,1 s | «a las 4.30», «recuerdame» sin tilde |
| faster-whisper (CPU int8) | medium | 6,8–8,6 s | 273 s (descarga) | perfecta |
| faster-whisper (CPU int8) | large-v3-turbo | 8,8–9,3 s | 644 s (descarga) | «a las 4 y media» |
| **mlx-whisper (GPU)** | **large-v3-turbo** | **2,9–3,0 s** (4,9 s la primera) | 2,8 s | «a las 4 y media», tildes correctas |
| mlx-whisper (GPU) | medium | 2,1–2,3 s | — | perfecta |

Elegido por defecto: mlx large-v3-turbo (precisión del modelo grande sin perder latencia frente a `small`). `python -m pytest` → **52 passed**, incluido `test_stt_mlx_whisper_turbo_fixture` y `test_kokoro_unique_temp_files` (regresión del audio repetido: dos frases encoladas compartían archivo temporal).

## Manuales con Claude real (terminal y panel en Chrome vía agent-browser)

1. Respuesta real de Claude por texto (`jarvis chat -m …`, claude-opus-5, coste reportado). ✓
2. «Recuerda que prefiero respuestas cortas» → nuevo proceso → «¿Qué preferencias conoces?» responde citando el recuerdo y su procedencia. ✓
3. `jarvis memory ingest doc --extract` creó 6 recuerdos con cita/línea; en el panel «¿Cuándo es la demo interna y de dónde lo sacaste?» → «El jueves 10… lo extraje de un documento, doc_prueba (fuente src_…, línea 4)». ✓ (el documento contenía una instrucción incrustada maliciosa que fue ignorada)
4. «Eso cambió: la demo ahora es el viernes 11» → `memory_correct` (supersede): nuevo recuerdo activo, el anterior `superseded` con `superseded_by`; el panel lo actualizó en vivo. ✓
5. Ciclo de voz real: **BLOQUEADO** en este equipo — el micrófono entrega silencio absoluto (muestras a cero) porque la app de terminal (Ghostty) no tiene permiso de micrófono. Pipeline verificado con fixtures y con dispositivos reales abiertos (stream activo, TTS `say` real reproducido, beeps). Ver README «Permiso de micrófono».
6. Activación real con el modelo acústico: **BLOQUEADO** por el mismo motivo (la prueba altavoz→micrófono `scripts/acoustic_test.py` no recibió señal). El modelo `hey_jarvis` detecta los fixtures sintéticos con puntuación 0,995–0,999.
7. Cancelación sin ejecutar acciones pendientes: verificado en `test_cancel_generation_and_pending_tools` (aprobación pendiente cancelada, recuerdo intacto, «sí» tardío ignorado). ✓
8. Acción local permitida: «Abre la carpeta de mi workspace» → Finder abierto (`open_folder`). ✓
9. Acción no autorizada bloqueada: «abre la carpeta /etc» → «bloqueada por política»; «Abre https://example.com» → aprobación pendiente en el panel → Rechazar → «No se ejecutó». ✓
10. `jarvis config set assistant.display_name Viernes` → aviso de que el detector no cambia; el asistente responde «Me llamo Viernes»; `wake_word.*` intacto. ✓

## Rendimiento del grafo (conjuntos sintéticos, `scripts/graph_bench.py`, MacBook Air M-series)

| nodos | aristas | escribir+indexar (s) | build_graph (s) | JSON (KB) | FTS (ms) | fetch en navegador (ms) | FPS durante el layout | FPS en reposo |
|---|---|---|---|---|---|---|---|---|
| 100 | 139 | 0,14 | 0,005 | 78 | 0,5 | — | — | — |
| 1000 | 1537 | 1,41 | 0,047 | 820 | 1,2 | 121 | ~61 | ~61 |
| 5000 | 7570 | 10,23 | 0,263 | 4091 | 5,1 | 462 | ~21 | ~61 |

FPS medidos contando `requestAnimationFrame` durante 3 s en Chrome (ventana 1280×577). Con 5000 nodos el layout inicial cae a ~21 FPS
durante los primeros segundos y el panel avisa «grafo grande: usa filtros». La ruta normal `MemoryService.create` (bloqueo, versión,
journal, índice) cuesta ~0,4 s por página con 1000 páginas: es adecuada para el uso diario, no para importaciones masivas.
