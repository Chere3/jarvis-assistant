# Implementation plan — JARVIS (personal desktop assistant)

Date: 2026-09-04. Target machine: MacBook Air Apple Silicon (arm64), macOS 26.5.1.

## Detected environment

| Component | Result |
|---|---|
| Python | 3.12.13 (uv), 3.13/3.14 also available. **3.12** is pinned for compatibility with the audio/ONNX wheels. |
| Node | 26.8.1 (not used as a backend; only to download the panel's libraries) |
| Claude Code | 2.1.261 installed, claude.ai session active (Max plan). No `ANTHROPIC_API_KEY` in the environment. |
| Agent SDK | `claude-agent-sdk` 0.2.152 (bundles CLI 2.1.259). **Verified**: it answers using this Mac's claude.ai session and reports cost/usage. |
| Audio | Built-in microphone (1 channel), speaker/headphone output. `sounddevice` detects the devices. |
| Local TTS | System Spanish voices: Paulina (es_MX), Mónica (es_ES), etc. via `say`. |
| Local STT | `faster-whisper` (CTranslate2, CPU int8). The `small` model transcribes a sentence in ~3 s. |
| Wake word | `openwakeword` 0.6.0 with the pretrained **hey_jarvis** model (ONNX, no key). `pvporcupine` 4.0.3 has a built-in `jarvis` keyword but **requires a Picovoice AccessKey** (blocked until the user provides one). |
| VAD | `webrtcvad-wheels` works at 16 kHz. |
| Index | SQLite 3.50 with FTS5 available. |

## Chosen stack (minimal)

- **A single Python 3.12 backend** (asyncio) with `uv` for pinned dependencies (`uv.lock`).
- **Reasoning**: Claude Agent SDK (`ClaudeSDKClient`) with our own in-process tools (MCP SDK server), `tools=[]` (no Claude Code built-in tools), `setting_sources=[]` (does not inherit configuration from the development environment), `permission_mode="dontAsk"` plus a `can_use_tool` that only admits our own registry.
- **Memory**: a Markdown wiki with YAML frontmatter (source of truth) plus a rebuildable SQLite FTS5 index. Inspired by LLM Wiki (Karpathy): immutable raw/, wiki/ maintained by the model through the service, SCHEMA.md with the conventions, index.md and a log.
- **Voice**: `sounddevice` (capture), `webrtcvad` (end of speech), `faster-whisper` (local STT), `say` (local, cancelable TTS), `afplay` (beep). Explicitly half-duplex.
- **Activation**: `openwakeword` by default (the `hey_jarvis` model); optional Porcupine adapter (`PICOVOICE_ACCESS_KEY`).
- **Panel**: FastAPI + uvicorn on 127.0.0.1 with a per-run token, SSE for events, static HTML/JS. Graph with `force-graph` (Canvas, MIT), Markdown with `marked` + `DOMPurify`. All vendored, no remote loads.
- **No** containers, vector databases, embeddings or external services.

## Phases and verification

1. **Phase 1** — `jarvis chat`: a real answer from Claude, the session persisted in `runtime/sessions`, minimal memory (`recuerda que…`) surviving a restart. Verify with: a persistence test plus a real chat.
2. **Phase 2** — Full wiki: validated schema, txt/md/pdf ingestion with hashes and manifests, references to fragments, corrections through `supersedes`, verifiable forgetting, deterministic lint, rebuildable FTS5 index. Verify with: the memory pytest suite.
3. **Phase 3** — Push-to-talk + STT + TTS + cancellation. Verify with: a synthetic fixture (`say`→wav→whisper) and a real microphone test.
4. **Phase 4** — Local wake word + state machine. Verify with: an audio fixture saying "hey jarvis" and a real test.
5. **Phase 5** — Tool registry with policy in code, approvals bound to id+args+expiry. Verify with: permission tests.
6. **Phase 6** — Local panel (status, chat, PTT, approvals, memory explorer with graph, answer traces), `jarvis doctor`, optional LaunchAgent. Verify with: endpoint tests using httpx plus a browser review.

## Authentication and billing (documented, not invented)

- The Agent SDK spawns the Claude Code CLI as a subprocess and reuses its authentication. On this Mac the CLI is authenticated with **claude.ai (Max plan)**, and the real test worked.
- The `ResultMessage` reports an estimated `total_cost_usd` and per-model usage; it is logged per turn in `runtime/sessions`.
- User decision (2026-09-04): calls ALWAYS go through Claude Code (the claude.ai session); the API is never used directly and any `ANTHROPIC_API_KEY` in the environment is ignored.
- Neither memory nor audio leaves the machine, only the text sent to Claude (the request plus the retrieved context).
