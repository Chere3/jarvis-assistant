# Build status — JARVIS

Last updated: 2026-09-04 (end of the initial session). Code in `proyectos/jarvis`, test data in a temporary `JARVIS_HOME`.

## Second iteration changes (2026-09-04, evening)
- All Claude Code tools available to the agent with approval enforced in code (`claude.builtin_tools`, `builtin_approval`), verified live: Glob/read automatic, Bash approved and executed, Write rejected and not created.
- Native `Jarvis.app` (Swift/AppKit, `macapp/`): always-visible floating orb, menu bar, ⌥Space shortcut, bubbles, click-to-approve, launches the backend as a child (microphone permission attributed to the app) or connects to an existing one. Installed in ~/Applications and verified running (connected to the user's own `jarvis serve`).
- Local neural voice Kokoro (`tts.provider: kokoro`, voices ef_dora/em_alex/em_santa), ~0.8 s per sentence; samples in ~/Desktop/jarvis-muestras-voz.
- 44 tests green.

## Third iteration (2026-09-05, early morning)
- `claude.permission_mode: auto` (Claude Code classifier) plus the `always_confirm_patterns` safety net (PreToolUse hook → "ask"). Verified: reads and `echo > file` automatic; an explicit `rm -rf` → without the net it ran (it deleted the voice samples, since regenerated); with the net → it asks for permission and, once rejected, deletes nothing.
- Clarifying questions (AskUserQuestion) routed to voice/panel/app: answer by label, by number ("the second one") or free text.
- `set_model`/`get_model`: switching models by voice ("switch to sonnet"), applied to the live session and persisted. Verified.
- Streamed speech (sentence by sentence while Claude writes): first sentence at 1.9 s in a real turn; Kokoro startup latency 0.66 s; clean interruption.
- Permission prompts summarized and speakable ("Can I run in the terminal: delete folder x?"); voice-oriented prompt (1–2 sentences, no Markdown, no narrating tools); `effort: low` by default.
- 49 tests green. Jarvis.app rebuilt (question/permission bubbles).

## Fourth iteration (2026-09-05, midday)
- Repeated speech: fixed (unique temp files per sentence in Kokoro). Transcription: volume normalization, Spanish prompt, stripping a leading "Jarvis", audio after the tone no longer discarded, 1.1 s closing silence, and mlx-whisper large-v3-turbo (GPU) as the default STT after a measured comparison. 52 tests green. App restarted with everything loaded (STT loaded in 2.8 s).

## Incident fixed (2026-09-05, morning)
- A panel test (`/api/config`) was writing to the REAL config at `~/.jarvis/config.yaml` (pytest temp paths and the name "Viernes"). Effect: the user's memory was being saved to a temp folder (it only held 2 test pages; nothing personal was lost). Fix: `tests/conftest.py` isolates `JARVIS_HOME`/`JARVIS_CONFIG` in every test (verified: the real config's hash does not change after the suite) and `jarvis config init --force` restored the configuration (Jarvis, real paths, kokoro, auto).
- The backend launched by Jarvis.app survived the app quitting: it now receives `--parent-pid`, a watchdog thread shuts it down (os._exit after a 3 s grace period, immune to broken pipes) and it also handles SIGTERM. Verified: 0 backends 7 s after quitting the app; reopening it launches a new one (or connects if one is already on the port).

## What works
- Phases 1-6 implemented: real chat with Claude (Agent SDK, claude.ai session), validated memory wiki with FTS5, txt/md/pdf ingestion with
  cited extraction, correction/dispute/forget, deterministic and semantic lint, PTT + local STT + cancelable local TTS, local wake word
  (openWakeWord `hey_jarvis`; Porcupine optional with a key), state machine, tools with policy and approvals, local panel with
  graph/list/traces/approvals/configuration, doctor, optional LaunchAgent.

## What was verified
- 38 automated tests green (`docs/test-results.md`).
- Acceptance criteria 1, 2, 3, 4, 7, 8, 9, 10 demonstrated against the real Claude (terminal and panel in Chrome).
- Memory evaluation: 6/6 on retrieval and attribution, 0 unsupported claims (`docs/memory-eval.md`).
- Graph performance measured with 100/1000/5000 synthetic nodes.

## Pending
- Criteria 5 and 6 (the real voice and wake-word loops) require granting microphone permission to the terminal app and running
  `jarvis listen` or `python scripts/acoustic_test.py`.
- Barge-in (voice interruption during TTS): not implemented (half-duplex documented).
- Minimal conversation summaries (`memory.save_conversation_summaries`): the option is exposed in the configuration, but automatic saving on session close is not implemented (the model can save notes on request).

## Blocked, and why
- Microphone: the terminal (Ghostty) returns absolute silence (no TCC permission). Minimum step: Settings → Privacy & Security → Microphone → enable the terminal app, restart it, `jarvis doctor`.
- Porcupine: `PICOVOICE_ACCESS_KEY` missing (optional; openWakeWord works without a key).

## Pending from this iteration
- Visually verify the app with the user (orb + bubbles + click-to-approve) and the microphone permission for Jarvis.app once the app launches the backend itself.
- The `jarvis serve` the user has open is running older code: restart it (or close it and use the app) to get the full tool set and the Kokoro voice.

## Next concrete action
1. Grant microphone permission and run `jarvis listen` (or `python scripts/acoustic_test.py`) to close out criteria 5 and 6.
2. Optionally, `jarvis config set wake_word.sensitivity 0.6` after testing activation with a real voice.
