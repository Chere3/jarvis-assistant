# Plan de implementación — JARVIS (asistente personal de escritorio)

Fecha: 2026-09-04. Máquina objetivo: MacBook Air Apple Silicon (arm64), macOS 26.5.1.

## Entorno detectado

| Componente | Resultado |
|---|---|
| Python | 3.12.13 (uv), también 3.13/3.14 disponibles. Se fija **3.12** por compatibilidad con ruedas de audio/ONNX. |
| Node | 26.8.1 (no se usa como backend; solo para descargar libs del panel) |
| Claude Code | 2.1.261 instalado, sesión claude.ai activa (plan Max). Sin `ANTHROPIC_API_KEY` en el entorno. |
| Agent SDK | `claude-agent-sdk` 0.2.152 (empaqueta CLI 2.1.259). **Verificado**: responde con la sesión claude.ai de este Mac y reporta coste/uso. |
| Audio | Micrófono interno (1 canal), salida altavoces/auriculares. `sounddevice` detecta dispositivos. |
| TTS local | Voces del sistema en español: Paulina (es_MX), Mónica (es_ES), etc. vía `say`. |
| STT local | `faster-whisper` (CTranslate2, CPU int8). Modelo `small` transcribe una frase en ~3 s. |
| Wake word | `openwakeword` 0.6.0 con modelo preentrenado **hey_jarvis** (ONNX, sin clave). `pvporcupine` 4.0.3 tiene keyword integrado `jarvis` pero **requiere AccessKey de Picovoice** (bloqueado hasta que el usuario la aporte). |
| VAD | `webrtcvad-wheels` funciona a 16 kHz. |
| Índice | SQLite 3.50 con FTS5 disponible. |

## Stack decidido (mínimo)

- **Un solo backend en Python 3.12** (asyncio) con `uv` para dependencias fijadas (`uv.lock`).
- **Razonamiento**: Claude Agent SDK (`ClaudeSDKClient`) con herramientas propias in-process (MCP SDK server), `tools=[]` (sin herramientas integradas de Claude Code), `setting_sources=[]` (no hereda configuración del entorno de desarrollo), `permission_mode="dontAsk"` + `can_use_tool` que solo admite el registro propio.
- **Memoria**: wiki Markdown con frontmatter YAML (fuente de verdad) + índice SQLite FTS5 reconstruible. Inspirado en LLM Wiki (Karpathy): raw/ inmutable, wiki/ mantenida por el modelo a través del servicio, SCHEMA.md con convenciones, index.md y log.
- **Voz**: `sounddevice` (captura), `webrtcvad` (fin de habla), `faster-whisper` (STT local), `say` (TTS local, cancelable), `afplay` (beep). Half-duplex explícito.
- **Activación**: `openwakeword` por defecto (modelo `hey_jarvis`); adaptador Porcupine opcional (`PICOVOICE_ACCESS_KEY`).
- **Panel**: FastAPI + uvicorn en 127.0.0.1 con token por ejecución, SSE para eventos, HTML/JS estático. Grafo con `force-graph` (Canvas, MIT), Markdown con `marked` + `DOMPurify`. Todo vendorizado, sin cargas remotas.
- **Sin** contenedores, bases vectoriales, embeddings ni servicios externos.

## Fases y verificación

1. **Fase 1** — `jarvis chat`: respuesta real de Claude, sesión persistida en `runtime/sessions`, memoria mínima (`recuerda que…`) persistente tras reinicio. Verificar: test de persistencia + chat real.
2. **Fase 2** — Wiki completa: esquema validado, ingesta txt/md/pdf con hash y manifiestos, referencias a fragmentos, correcciones con `supersedes`, olvido verificable, lint determinista, índice FTS5 reconstruible. Verificar: suite pytest de memoria.
3. **Fase 3** — Pulsar-para-hablar + STT + TTS + cancelación. Verificar: fixture sintética (`say`→wav→whisper) y prueba real con micrófono.
4. **Fase 4** — Wake word local + máquina de estados. Verificar: fixture de audio con "hey jarvis" y prueba real.
5. **Fase 5** — Registro de herramientas con política en código, aprobaciones ligadas a id+args+vencimiento. Verificar: tests de permisos.
6. **Fase 6** — Panel local (estado, chat, PTT, aprobaciones, explorador de memoria con grafo, trazas de respuesta), `jarvis doctor`, LaunchAgent opcional. Verificar: endpoints con httpx + revisión en navegador.

## Autenticación y facturación (documentado, no inventado)

- El Agent SDK lanza el CLI de Claude Code como subproceso y reutiliza su autenticación. En este Mac el CLI está autenticado con **claude.ai (plan Max)**, y la prueba real funcionó.
- El `ResultMessage` reporta `total_cost_usd` estimado y uso por modelo; se registra por turno en `runtime/sessions`.
- Decisión del usuario (2026-09-04): las llamadas van SIEMPRE por Claude Code (sesión claude.ai); la API directa no se usa y cualquier `ANTHROPIC_API_KEY` del entorno se ignora.
- Ni la memoria ni el audio salen del equipo salvo el texto que se envía a Claude (petición + contexto recuperado).
