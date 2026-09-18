# JARVIS architecture

```
                 ┌───────────────────────────── Interfaces ──────────────────────────┐
                 │  Terminal (jarvis chat / listen)      Loopback web panel (serve)  │
                 │  text · PTT · /aprobar                chat · PTT · graph · traces │
                 └───────────────┬───────────────────────────────┬───────────────────┘
                                 │ handle_text / cancel / approve │ SSE events
                          ┌──────▼────────────────────────────────▼──────┐
   Audio (half-duplex)    │             Orchestrator (state)              │
 ┌──────────────────────┐ │ IDLE→LISTENING→TRANSCRIBING→THINKING→SPEAKING │
 │ mic → ring buffer    │ │ turn_id / session_id · cancellation · traces  │
 │ openWakeWord (local) ├─►│ budgeted retrieval (FTS5 + links)             │
 │ VAD → faster-whisper │ │ approvals bound to id+args+expiry              │
 │ say (TTS, cancelable)│◄┤ persistence: runtime/sessions, runtime/traces  │
 └──────────────────────┘ └──────┬───────────────────────────────┬────────┘
                                 │ AgentRequest (text + context)  │ in-process MCP tools
                          ┌──────▼──────────┐              ┌──────▼──────────────────────┐
                          │ Claude Agent SDK│              │ Tool registry                │
                          │ adapter         │◄────────────►│ contracts + policy in code   │
                          │ streaming/timeout│  tool calls │ memory_* · read_file · open_* │
                          │ cancel/retry    │              │ create_file · ui_show         │
                          └─────────────────┘              └──────┬───────────────────────┘
                                                                  │ propose / validate / persist
                                                    ┌─────────────▼───────────────────────┐
                                                    │ Memory service (source of truth)      │
                                                    │ wiki/*.md + validated frontmatter      │
                                                    │ raw/ manifests/ journal/ archive/      │
                                                    │ atomic writes · flock · versioning     │
                                                    │ SQLite FTS5 index (rebuildable)        │
                                                    │ lint · graph (projection) · events     │
                                                    └───────────────────────────────────────┘
```

## Components (`jarvis/` package)

| Letter | Responsibility | Module |
|---|---|---|
| A | Capture, ring buffer, VAD, playback | `audio/capture.py`, `audio/vad.py`, `audio/beep.py` |
| B | Local wake word (openWakeWord / Porcupine) and PTT | `audio/wakeword.py`, `audio/voice_loop.py` |
| C | Local transcription | `audio/stt.py` (faster-whisper) |
| D | Orchestration, states, cancellation, traces | `orchestrator/session.py`, `orchestrator/state.py` |
| E | Claude agent (Agent SDK, locked-down permissions) | `agent/claude_adapter.py`, `agent/prompts.py` |
| F | Memory service | `memory/store.py`, `schema.py`, `index.py`, `retrieval.py`, `ingest.py`, `lint.py`, `graph.py` |
| G | Tools with contracts and approvals | `agent/tools.py`, `agent/approvals.py` |
| H | Cancelable speech synthesis | `audio/tts.py` |
| I | Terminal interface and panel | `cli.py`, `ui/server.py`, `ui/static/` |
| J | Operational persistence | `runtime/` (rotated logs, doctor, LaunchAgent), `data/runtime/` |

## Data flow and privacy

- **Local**: microphone audio, wake word, VAD, STT, TTS, wiki, index, traces.
- **Leaves the machine**: text only (the request + retrieved memory pages + tool results) on its way to Claude through the Claude Code CLI / Agent SDK. "Local storage" does not mean offline reasoning.
- While waiting for the wake word, no audio is sent or stored; the ring buffer lives in RAM.

## Security of the installed agent

- `tools=[]`, `setting_sources=[]`, `permission_mode="default"` plus a `can_use_tool` that only admits `mcp__jarvis__*`; Claude Code's built-in tools are explicitly forbidden.
- The policy lives in `ToolRegistry.execute` (schema validation, paths resolved through symlinks, approval by id+hash+expiry). Documents, pages and memories are data and never widen permissions.
- The panel listens on 127.0.0.1, requires a per-run token (header or SameSite=Strict cookie), rejects foreign origins, serves no arbitrary files, applies a CSP and exposes no keys.
