# Tests run and real results (2026-09-04)

## Automated — `python -m pytest` → **38 passed** (17.8 s)

| File | Covers |
|---|---|
| `tests/test_memory.py` (13) | persistence across restarts, source deduplication (hash), temporal correction (supersede), references with locator and quote, index deletion and rebuild, recovery after an interrupted write (`*.tmp`), 3 concurrent writers (24 pages, version 24), optimistic conflict, secret rejected, scoped forgetting (relations, links, versions, index), lint (broken link, stale index), budgeted retrieval with a trace, graph projection and truncated local graph |
| `tests/test_permissions.py` (8) | paths outside the directory and symlink escapes, unapproved action not executed, approval invalidated by changing arguments / hash / expiry, URL policy (schemes), invalid arguments and unknown tool, a canceled turn blocks tools, a malicious document treated as data, no secrets in logs |
| `tests/test_orchestration.py` (8) | a turn persists and cites, canceling generation leaves a pending approval unexecuted (and a late "yes" does not authorize it), events from a previous turn discarded, provider timeout, network failure without duplicating side effects (no retry), the yes/no approval flow, activation with no useful speech discarded, state machine transitions |
| `tests/test_audio_fixtures.py` (5) | Spanish STT over synthetic fixtures (system voices), the "hey jarvis" wake word with English and Spanish voices + cooldown + silence without triggering, end-of-speech detector, interruptible TTS, beep generation. **These are recorded/synthetic tests, not real microphone tests.** |
| `tests/test_ui_server.py` (4) | token and origin, graph/memory/search/forget endpoints, correction, layout and configuration (non-editable keys), a real trace for one answer |

## Second iteration (Claude Code tools, native app, Kokoro voice)

- `python -m pytest` → **44 passed**. New: `tests/test_builtin_tools.py` (6): automatic reads, approving Bash mid-turn, rejection and expiry, always/never policies, canceled turn, default values.
- Live with Claude: "Use Glob to list…" → resolved without approval; "Run `sw_vers`" → AWAITING_APPROVAL mid-turn, approved → "Your Mac runs macOS 26.5.1"; "Create /tmp/jarvis_prueba.txt with Write" → rejected → file not created.
- Kokoro: 3 Spanish voices synthesized (9 s of audio in ~1.3 s with the model loaded; ~3.5 s to load); a short sentence 0.82 s; playback interrupted at 2.5 s → `completó: False`, no hung process.
- Jarvis.app: built with Swift 6.2 (CLT), installed in ~/Applications, running with 0 child processes after detecting the user's backend on port 8765 (connected mode). The screenshot with the orb and the bubble was taken before the `app.json` fix.

## Third iteration (auto mode, questions, model by voice, streamed speech)

- `python -m pytest` → **49 passed**. New `tests/test_voice_flow.py` (5): streamed speech starts before the turn ends, short and human permission summaries, a clarifying question answered by voice (label / "the third one" / "no"), the `set_model` tool with aliases and validation, sentence splitting.
- Live (claude-opus-5 → sonnet): sentences spoken at 1.9 / 2.6 / 2.8 s with the turn ending at 2.8 s; "Switch to the sonnet model" → `set_model` → "I'm using Sonnet 5" (the SDK reports claude-sonnet-5).
- Auto mode: `Read` and `echo > /tmp/...` without asking; an explicit `rm -rf` → **executed by the classifier** (it deleted `~/Desktop/jarvis-muestras-voz`; regenerated). With `always_confirm_patterns`: "Delete … with rm -rf, don't ask" → "PENDING: run in the terminal: Delete test folder" → rejected → folder untouched.
- Streamed Kokoro: 0.66 s from `say_async` to the first sound; `stop()` empties the queue and halts playback.

## Fourth iteration (transcription and repeated speech, 2026-09-05)

A real STT comparison over synthetic Spanish fixtures (including a hard sentence with names, a time and "USB-C"), MacBook Air Apple Silicon:

| Engine | Model | Time per request | Load | Hard sentence |
|---|---|---|---|---|
| faster-whisper (CPU int8) | small | 2.3–3.1 s | 1.1 s | "a las 4.30", "recuerdame" without the accent |
| faster-whisper (CPU int8) | medium | 6.8–8.6 s | 273 s (download) | perfect |
| faster-whisper (CPU int8) | large-v3-turbo | 8.8–9.3 s | 644 s (download) | "a las 4 y media" |
| **mlx-whisper (GPU)** | **large-v3-turbo** | **2.9–3.0 s** (4.9 s the first time) | 2.8 s | "a las 4 y media", accents correct |
| mlx-whisper (GPU) | medium | 2.1–2.3 s | — | perfect |

Chosen as the default: mlx large-v3-turbo (large-model accuracy without losing latency against `small`). `python -m pytest` → **52 passed**, including `test_stt_mlx_whisper_turbo_fixture` and `test_kokoro_unique_temp_files` (a regression for the repeated audio: two queued sentences shared a temp file).

## Manual, against the real Claude (terminal and panel in Chrome via agent-browser)

1. A real text answer from Claude (`jarvis chat -m …`, claude-opus-5, cost reported). ✓
2. "Remember that I prefer short answers" → new process → "What preferences do you know about?" answers citing the memory and its provenance. ✓
3. `jarvis memory ingest doc --extract` created 6 memories with quote/line; in the panel, "When is the internal demo and where did you get that from?" → "Thursday the 10th… I extracted it from a document, doc_prueba (source src_…, line 4)". ✓ (the document contained an embedded malicious instruction, which was ignored)
4. "That changed: the demo is now Friday the 11th" → `memory_correct` (supersede): a new active memory, the previous one `superseded` with `superseded_by`; the panel updated live. ✓
5. Real voice loop: **BLOCKED** on this machine — the microphone delivers absolute silence (all-zero samples) because the terminal app (Ghostty) has no microphone permission. The pipeline is verified with fixtures and with real devices opened (active stream, real `say` TTS played back, beeps). See the README's "Microphone permission" section.
6. Real activation with the acoustic model: **BLOCKED** for the same reason (the speaker→microphone test `scripts/acoustic_test.py` received no signal). The `hey_jarvis` model detects the synthetic fixtures with scores of 0.995–0.999.
7. Cancellation without executing pending actions: verified in `test_cancel_generation_and_pending_tools` (pending approval canceled, memory untouched, a late "yes" ignored). ✓
8. Allowed local action: "Open my workspace folder" → Finder opened (`open_folder`). ✓
9. Unauthorized action blocked: "open the /etc folder" → "blocked by policy"; "Open https://example.com" → approval pending in the panel → Reject → "It was not executed". ✓
10. `jarvis config set assistant.display_name Viernes` → a warning that the detector does not change; the assistant answers "My name is Viernes"; `wake_word.*` untouched. ✓

## Graph performance (synthetic sets, `scripts/graph_bench.py`, MacBook Air M-series)

| nodes | edges | write+index (s) | build_graph (s) | JSON (KB) | FTS (ms) | browser fetch (ms) | FPS during layout | FPS at rest |
|---|---|---|---|---|---|---|---|---|
| 100 | 139 | 0.14 | 0.005 | 78 | 0.5 | — | — | — |
| 1000 | 1537 | 1.41 | 0.047 | 820 | 1.2 | 121 | ~61 | ~61 |
| 5000 | 7570 | 10.23 | 0.263 | 4091 | 5.1 | 462 | ~21 | ~61 |

FPS measured by counting `requestAnimationFrame` over 3 s in Chrome (1280×577 window). At 5000 nodes the initial layout drops to ~21 FPS
for the first few seconds and the panel warns "large graph: use filters". The normal `MemoryService.create` path (lock, version,
journal, index) costs ~0.4 s per page at 1000 pages: fine for daily use, not for bulk imports.
