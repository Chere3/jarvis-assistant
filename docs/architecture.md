# Arquitectura de JARVIS

```
                 ┌──────────────────────────── Interfaz ─────────────────────────────┐
                 │  Terminal (jarvis chat / listen)      Panel web loopback (serve)  │
                 │  texto · PTT · /aprobar               chat · PTT · grafo · trazas │
                 └───────────────┬───────────────────────────────┬───────────────────┘
                                 │ handle_text / cancel / approve │ SSE eventos
                          ┌──────▼────────────────────────────────▼──────┐
   Audio (half-duplex)    │             Orquestador (estado)              │
 ┌──────────────────────┐ │ IDLE→LISTENING→TRANSCRIBING→THINKING→SPEAKING │
 │ mic → ring buffer    │ │ turn_id / session_id · cancelación · trazas   │
 │ openWakeWord (local) ├─►│ recuperación con presupuesto (FTS5 + enlaces) │
 │ VAD → faster-whisper │ │ aprobaciones ligadas a id+args+vencimiento     │
 │ say (TTS, cancelable)│◄┤ persistencia: runtime/sessions, runtime/traces │
 └──────────────────────┘ └──────┬───────────────────────────────┬────────┘
                                 │ AgentRequest (texto + contexto)│ herramientas MCP en proceso
                          ┌──────▼──────────┐              ┌──────▼──────────────────────┐
                          │ Adaptador Claude│              │ Registro de herramientas     │
                          │ Agent SDK       │◄────────────►│ contratos + política en código│
                          │ streaming/timeout│  tool calls │ memory_* · read_file · open_* │
                          │ cancel/reintento│              │ create_file · ui_show         │
                          └─────────────────┘              └──────┬───────────────────────┘
                                                                  │ propone / valida / persiste
                                                    ┌─────────────▼───────────────────────┐
                                                    │ Servicio de memoria (fuente de verdad)│
                                                    │ wiki/*.md + frontmatter validado       │
                                                    │ raw/ manifests/ journal/ archive/      │
                                                    │ escrituras atómicas · flock · versión  │
                                                    │ índice SQLite FTS5 (reconstruible)     │
                                                    │ lint · grafo (proyección) · eventos    │
                                                    └───────────────────────────────────────┘
```

## Componentes (paquete `jarvis/`)

| Letra | Responsabilidad | Módulo |
|---|---|---|
| A | Captura, búfer circular, VAD, reproducción | `audio/capture.py`, `audio/vad.py`, `audio/beep.py` |
| B | Wake word local (openWakeWord / Porcupine) y PTT | `audio/wakeword.py`, `audio/voice_loop.py` |
| C | Transcripción local | `audio/stt.py` (faster-whisper) |
| D | Orquestación, estados, cancelación, trazas | `orchestrator/session.py`, `orchestrator/state.py` |
| E | Agente Claude (Agent SDK, permisos cerrados) | `agent/claude_adapter.py`, `agent/prompts.py` |
| F | Servicio de memoria | `memory/store.py`, `schema.py`, `index.py`, `retrieval.py`, `ingest.py`, `lint.py`, `graph.py` |
| G | Herramientas con contrato y aprobaciones | `agent/tools.py`, `agent/approvals.py` |
| H | Síntesis de voz cancelable | `audio/tts.py` |
| I | Interfaz terminal y panel | `cli.py`, `ui/server.py`, `ui/static/` |
| J | Persistencia operativa | `runtime/` (logs rotados, doctor, LaunchAgent), `data/runtime/` |

## Flujo de datos y privacidad

- **Local**: audio del micrófono, wake word, VAD, STT, TTS, wiki, índice, trazas.
- **Sale del equipo**: solo texto (petición + páginas de memoria recuperadas + resultados de herramientas) hacia Claude a través del CLI de Claude Code/Agent SDK. "Almacenamiento local" no significa razonamiento offline.
- Mientras se espera la palabra clave no se envía ni guarda audio; el búfer circular vive en RAM.

## Seguridad del agente instalado

- `tools=[]`, `setting_sources=[]`, `permission_mode="default"` + `can_use_tool` que solo admite `mcp__jarvis__*`; herramientas integradas de Claude Code explícitamente prohibidas.
- La política vive en `ToolRegistry.execute` (validación de esquema, rutas resueltas con symlinks, aprobación por id+hash+vencimiento). Documentos, páginas y memoria son datos y no amplían permisos.
- El panel escucha en 127.0.0.1, exige token por ejecución (cabecera o cookie SameSite=Strict), rechaza orígenes ajenos, no sirve archivos arbitrarios, aplica CSP y no expone claves.
