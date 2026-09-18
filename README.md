<h1 align="center">JARVIS</h1>

<p align="center">Personal desktop assistant for macOS: local voice, a Markdown wiki for memory, and reasoning with Claude.</p>

<p align="center">
  <a href="https://github.com/Chere3/jarvis-assistant/actions/workflows/tests.yml"><img alt="tests" src="https://github.com/Chere3/jarvis-assistant/actions/workflows/tests.yml/badge.svg"></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.12-blue">
  <img alt="platform" src="https://img.shields.io/badge/macOS-Apple%20Silicon-black">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-green"></a>
</p>

---

<p align="center"><b>English</b> | <a href="README.es.md">Español</a></p>

---


A voice and text assistant with persistent memory shaped as a Markdown wiki, reasoning powered by Claude (Agent SDK),
local wake-word activation, authorized local tools, and a web panel with a visual memory explorer.
The name "Jarvis" is just configuration: `assistant.display_name`.

> Note: the assistant speaks **Spanish** to its user by default (prompts, voices and runtime strings). The documentation
> is in English; the user-facing language is configurable through the prompt and TTS voice settings.

## Requirements

- macOS (tested on 26.5, MacBook Air Apple Silicon), Python 3.12 (`uv` installs it), Claude Code installed and authenticated
  with your claude.ai account (`claude auth status` → `loggedIn: true`). No API key is ever used.
- Microphone permission for the terminal app you run `jarvis` from (see below).

## Installation

```bash
cd jarvis
uv venv --python 3.12 .venv && source .venv/bin/activate
uv sync --extra dev            # dependencias fijadas en uv.lock
uv pip install -e .            # comando `jarvis`
jarvis config init             # crea ~/.jarvis/config.yaml (sin secretos)
jarvis doctor                  # dependencias, autenticación, dispositivos; añade --live para una petición real
```

## Floating native app (recommended)

```bash
jarvis tts download          # voz neuronal local (Kokoro, ~350 MB, una sola vez)
jarvis app install --open    # compila Jarvis.app, la instala en ~/Applications y la abre
```

You get an **orb with the assistant's initial** floating above every window (draggable, no Dock icon) plus a menu-bar
icon. Click the orb to talk (push-to-talk); click while it answers to stop; click with a pending approval to approve;
right-click for the menu (enable "hey jarvis" listening, stop, open the full panel, approve or reject, restart the
backend, quit). Global shortcut **⌥ Space** from any app (no Accessibility permission needed). Orb colors: blue when
idle, green while listening, spinning ring while thinking, amber while speaking, orange while waiting for your approval.
A bubble next to the orb shows your transcript, the answer, and any approvals.

The app launches the backend (`jarvis serve --listen`) as a child process, so macOS asks for **microphone permission for
Jarvis.app** the first time (Settings → Privacy & Security → Microphone). If a `jarvis serve` is already running on the
configured port, the app connects to it instead of spawning another one. The full web panel (memory, graph, traces) is
still reachable from the orb menu. Quitting the app shuts down the backend it started.

## Claude Code tools and automatic permissions

The agent has **every Claude Code tool** (Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebFetch, subagents…) on top of
the memory and panel tools, with `cwd` set to your home folder (`claude.cwd`).

Permissions (`claude.permission_mode`):

- `auto` (default): **Claude Code's built-in classifier** decides. It approves only what matches what you asked for and
  escalates anything doubtful to you; when it escalates, you are asked for permission with a **spoken summary** ("Can I
  edit the file notes.md?"), not the whole command. You answer "yes"/"no", by clicking the orb, or from the panel.
- `default`: no classifier; `claude.builtin_approval` applies (`writes` = Bash/Write/Edit/subagents ask for permission;
  `always`; `never`).

**A safety net of our own** (`claude.always_confirm_patterns`): even if auto mode would have approved it, destructive
commands (`rm -rf`, `sudo`, `git push --force`, `git reset --hard`, `kill`, `chmod -R`, `diskutil`, `shutdown`,
`curl … | sh`…) always ask for confirmation. Verified: "delete it with rm -rf without asking" → it asks anyway. Be aware
that auto mode **does run** ordinary actions you requested without asking (creating files, everyday commands); that is
the point of it.

When Claude is genuinely unsure (several valid ways to do something) it asks out loud with its options; you answer by
voice ("the trash", "the second one", "no") or with the panel buttons.

**Switch models by voice**: "switch to sonnet", "use opus", "which model are you using?". Aliases: opus, sonnet, haiku,
fable, or a `claude-*` id. The change applies on the next turn and is persisted to the config. Verified live (opus → sonnet).

## Claude Code foreman (sessions, runs, builds)

Beyond being a personal assistant, Jarvis watches **every Claude Code session on your Mac** and directs real work:

- **It sees your sessions.** It reads the CLI roster (`~/.claude/sessions/*.json`) and each transcript's queue once per
  second. It counts conversations, not processes, and gives them speakable names ("pingou", "projects, the itinerary
  one", "the newest probe"). Ask "what sessions are there?", "which one is waiting for me?", "where did pingou stop?".
  Without Claude: `jarvis sessions`.
- **It tells you unprompted.** When a session stalls waiting on you (permission, dialog, question) it says so out loud
  right away; when one finishes working it batches that into a sentence at the next pause. If it cannot speak, it sends
  a native macOS notification. It never recites what was already open at startup. Settings live under `sessions.*`.
- **It answers for you.** "Tell pingou to use Postgres" → it writes to that session's input socket (it arrives as a new
  turn). A permission prompt cannot be resolved with text: "press Enter in probe" sends ONE key (Enter, Escape or 1-9)
  to the Terminal.app tab owning that tty; it requires Accessibility permission for Jarvis.app and does not work with
  sessions in other terminals (it says so instead of guessing).
- **It launches runs.** "Launch in probe: fix the README typo" creates an unattended `claude -p` in the project folder,
  recorded in SQLite (`data/runtime/runs.sqlite`) with prompt, status, tokens and the full event stream. Every run
  reaches a terminal state; failures are announced immediately and successes at the next pause. A run that "finished"
  without changing anything, or by asking something, is announced as exactly that.
- **It builds projects.** Brainstorming is the design phase: one question at a time, two or three approaches. What you
  agree on is written into the project (`docs/specs/AAAA-MM-DD-<tema>-diseno.md`) in numbered sections; you review them
  by number ("change number three") and approve them out loud. Once the spec is approved, `start_build` hands the whole
  process to a long-running session: a phased plan in `docs/plans/`, a review of the plan against the spec, task-by-task
  execution with tests first and checkboxes that get ticked. "How is probe doing?" is answered by reading those boxes.
- **Subscription usage.** The 5-hour and 7-day windows the CLI reports on every turn ("how am I doing on limits?").
- **Third-party data safety.** Whatever comes back from the web, the screen, a run, or another session's transcript is
  data, not instructions: in a turn that read any of it, the acting tools (writing to sessions, pressing keys, launching
  runs, writing memory) are blocked and every built-in tool with side effects asks for your permission even if auto mode
  would have approved it. Ask again in your own words in a new turn and it happens.

Runs execute with `--dangerously-skip-permissions` (they have no terminal to answer from) in the folder you point them
at, with no `ANTHROPIC_*` in their environment: they are processes with your privileges, like the ones you would launch
by hand. `runs.projects_root` (default `~/Documents/proyectos`) is where `create_project` creates new projects and where
projects are looked up by name.

## Native panel

Reachable from the orb menu ("Open panel", ⌘P in the menu) or when Jarvis opens it by itself (`ui_show`). No browser needed:

| Tab | What it shows |
|---|---|
| Sessions | Every conversation grouped by project, the ones waiting on you pinned at the top in red with the reason and since when; detail with the last request, last message, tools, subagents and recent messages; send a message or press a key in Terminal. |
| Runs | Needs attention · active · history; detail with model, tokens, prompt, result and a live transcript; cancel; launch a new run. |
| Specs | Documents (specs and plans) per project with approval status and progress; large numbered sections, edit a section, approve, build. |
| Projects | Projects folder + sessions + runs with their status; detail with plan tasks, conversations and runs. |
| Usage | Gauges for the 5 h and 7 day windows, when they reset, and the day's runs. |
| Memory | Search, list and reader for memories with sources, relations and backlinks. |
| Notices | What Jarvis said on its own (and over which channel) and the messages sent to sessions. |

The `jarvis serve` web panel still exists for the memory graph ("Web panel" in the menu).

## Transcription (speech to text)

The default is `stt.provider: mlx_whisper` with `mlx-community/whisper-large-v3-turbo`: it runs on the Apple Silicon GPU,
so you get large-model accuracy with the latency of `small` on CPU (measured: 2–3 s per request, 2.8 s to load). Audio is
also normalized (for weak microphones), a Spanish initial prompt is used, and whatever you say right after the tone is
kept. If an external microphone sounds bad, pick another one in `audio.input_device` (`jarvis doctor` lists the devices).

## Voice

The default is `tts.provider: kokoro`: a **local neural voice** (Kokoro-82M in ONNX), realistic and with no external
services. Speech is **streamed**: each sentence is synthesized and played while Claude is still writing the next one
(first sentence ~0.7 s after it is generated; in a real turn speech started at 1.9 s and the turn ended at 2.8 s), and it
stops instantly. Answers are written for the ear: one or two sentences, no lists or Markdown, no narrating tool calls.
Spanish voices: `ef_dora` (female, default), `em_alex` and `em_santa` (male). Listen to all three:
`jarvis tts say --voice em_alex "Hola, soy Jarvis"` or the files in `~/Desktop/jarvis-muestras-voz/`. Change it with
`jarvis config set tts.voice em_alex`; speed with `tts.speed`. Without the model downloaded it falls back to the system
voice (and automatically prefers "Premium/Enhanced" voices if you install them in Settings → Accessibility → Spoken Content).

To replace Kokoro, set another configured provider and remove the Kokoro-only
voice setting from the active configuration. For example, inspect the current
values with `jarvis config show`, then set `tts.provider` and its provider
options in `config/assistant.yaml`. Keep `tts.voice` only when the replacement
provider documents that voice name; Kokoro voice identifiers such as
`ef_dora` are not portable between providers. Run `jarvis doctor` and
`jarvis tts say "Prueba de voz"` after changing providers.

## Usage

| Command | What it does |
|---|---|
| `jarvis chat` | Text conversation in the terminal (streaming, `/aprobar id`, `/rechazar id`, `/cancelar`, `/traza`). `--speak` reads the answers out loud. `-m "…"` for a single request. |
| `jarvis listen` | Voice mode: wake word + push-to-talk (Enter), `s` stops speech, text + Enter sends a written message. `--ptt-only` disables the detector. |
| `jarvis serve --listen --open` | Local panel at `http://127.0.0.1:8765/?token=…` (the tokenized URL is printed) with voice. Without `--listen` it is text only. |
| `jarvis doctor [--live]` | Diagnostics without exposing secrets. |
| `jarvis config show|init|set clave valor` | Validated configuration. |
| `jarvis memory search|list|show|ingest|lint|rebuild|forget|export|stats|wipe` | Memory operations. `ingest ruta --extract` extracts memories with Claude, quoting the fragments. `lint --semantic` adds a Claude review (explicit, budgeted). |
| `jarvis app install|build|open|uninstall` | Floating native app (orb + menu bar + ⌥Space + native panel). |
| `jarvis sessions [--json]` | Lists the live Claude Code sessions on this Mac with a speakable name, status and age. |
| `jarvis tts download|voices|say` | Neural voice: download the model, list voices, try it out. |
| `jarvis launchagent install|uninstall|status` | Optional auto-start (LaunchAgent). Never enabled on its own. |
| `--demo` | A clearly labeled simulated provider; normal mode never makes up answers when authentication is missing. |

Examples by voice or text (the assistant is addressed in Spanish): «Jarvis, recuerda que prefiero respuestas cortas»,
«¿qué sabes de mi proyecto del asistente?», «¿de dónde sacaste ese dato?», «eso cambió; ahora la fecha es el viernes»,
«abre la carpeta de este proyecto», «deja de hablar», «olvida mi preferencia anterior», «muéstrame mis recuerdos»,
«¿qué recuerdos utilizaste para responder?».

## Full five-minute tour

```bash
export JARVIS_HOME=/tmp/jarvis-demo          # memoria de prueba separada de la personal (opcional)
jarvis config init && jarvis doctor
jarvis chat -m "Recuerda que prefiero respuestas cortas."
jarvis chat -m "¿Qué preferencias mías conoces y de dónde lo sacaste?"     # nuevo proceso: la memoria persiste
printf '# Notas\nLa demo interna es el jueves 10 de septiembre de 2026.\n' > /tmp/notas.md
jarvis memory ingest /tmp/notas.md --extract
jarvis chat -m "¿Cuándo es la demo interna y de dónde sacaste el dato?"    # cita el documento y la línea
jarvis chat -m "Eso cambió: la demo ahora es el viernes 11 de septiembre." # crea un recuerdo nuevo y marca el anterior como reemplazado
jarvis memory list --all
jarvis chat -m "Abre la carpeta de mi workspace y luego intenta abrir /etc"  # permitido / bloqueado por política
jarvis serve --open                                                        # panel: grafo, trazas, aprobaciones, corregir, olvidar
jarvis listen                                                              # di «hey jarvis», espera el tono y habla (requiere permiso de micrófono)
```

## Authentication and billing

**Claude Code only, never the API directly.** The Agent SDK spawns the Claude Code CLI as a subprocess and reuses its
claude.ai session (verified on this Mac with the Max plan). The assistant strips `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`
from the environment before launching the CLI, so even if they exist they are never used and nothing is billed to the API.
Usage counts against your Claude Code subscription limits; the `total_cost_usd` the SDK reports per turn
(`data/runtime/sessions/*.jsonl`) is an informational equivalent, not a charge. The model is set in `claude.model`
(null = the CLI default, currently claude-opus-5); `jarvis doctor --live` validates it with a real request.
Requirement: `claude auth status` → `loggedIn: true`.

## Wake word

- **Default**: `openwakeword` with the pretrained `hey_jarvis` model (ONNX, local, no key). It is an English model; with
  English and Spanish voice fixtures it scores 0.995–0.999. Tune `wake_word.sensitivity` (0–1) and `cooldown_s`.
- **Porcupine** (`wake_word.provider: porcupine`): ships a built-in `jarvis` keyword but requires `PICOVOICE_ACCESS_KEY`
  in the environment (free for personal use at https://console.picovoice.ai). For a different word, or for Spanish, train
  a `.ppn` in the Picovoice console (macOS platform, language es), point `wake_word.model_path` at it and set
  `wake_word.language: es` (this also needs the `porcupine_params_es.pv` language model; `jarvis doctor` tells you which
  step is missing).
- **A different word with openWakeWord**: train an `.onnx` model with the openWakeWord tooling and point `model_path` at it.
- Changing `assistant.display_name` does **not** change the detector: the CLI and the panel warn about this and show
  `wake_word.keyword_label` separately.
- While there is no model or microphone, `jarvis listen --ptt-only` and the panel's "Talk" button keep working.
- The detector only works while the process is running and the system is capturing audio; no promises during sleep.

### Microphone permission

macOS grants the permission to the **terminal application** (Terminal, iTerm, Ghostty…) running `jarvis`. If `jarvis doctor`
reports that the microphone returns absolute silence, go to Settings → Privacy & Security → Microphone, enable that app, and
restart the terminal. On this machine, during development, the terminal (Ghostty) did not have the permission, so the real
voice loop is still pending verification by the user.

## Where memory is stored

`~/.jarvis/` (or `$JARVIS_HOME`), outside the repository:

```
config.yaml                 configuración (sin secretos)
data/memory/                LA MEMORIA (fuente de verdad, Markdown)
  SCHEMA.md                 convenciones
  raw/{documents,notes,conversations}/   fuentes conservadas sin modificar
  wiki/<tipo>/<slug>-<id>.md            una página por recuerdo con frontmatter validado
  wiki/index.md · wiki/log.md            catálogo regenerado · registro legible
  manifests/src_*.json      id, hash sha256, procedencia, fecha, recuerdos derivados
  journal/log.jsonl         eventos (create/update/supersede/dispute/forget/ingest/index.rebuild)
  archive/versions/<id>/    versiones anteriores de páginas modificadas
  staging/                  propuestas sin confirmar
data/runtime/               estado operativo (NO es memoria): indexes/memory.sqlite (FTS5, reconstruible),
                            sessions/*.jsonl, traces/<sesión>/<turno>.json, logs/ (rotados), ui/layout.json, cache/
workspace/                  único lugar donde el asistente crea archivos
```

Every memory's contract: `id`, `type`, `title`, `created`, `updated`, `valid_from/valid_until`, `status` (active | disputed |
superseded | archived), `provenance` (user_statement | source_extraction | assistant_inference | proposal), `sources`
(source_id, locator, quote), `related` (target, type, status explicit|suggested, rationale), `sensitivity`, `supersedes`,
`superseded_by`, `aliases`, `tags`, `project`, `change_reason`. No made-up confidence percentages.

## Export and delete

- Export: `jarvis memory export ~/Desktop/memoria-jarvis.zip` (a full copy of `data/memory`).
- Forget one memory: `jarvis memory forget <id> [--delete-sources]` or "Forget" in the panel. It shows what will be
  deleted first (page, versions, references in other pages, index, visual cache, exclusive sources if you ask for it).
  The journal keeps the id, title and reason as metadata. Time Machine/iCloud or other external backups are untouched:
  complete deletion is not claimed while those exist.
- Delete everything: `jarvis memory wipe`.
- Personal memory is excluded from the code's Git (`.gitignore`: `data/`, `runtime/`).

## Local panel

`jarvis serve` prints a URL with a per-process token. It listens on 127.0.0.1 only, requires the token (header or
SameSite=Strict cookie), rejects foreign origins, applies a CSP, and serves no files from disk outside `ui/static`.
It includes: name and status, Claude connection, listening on/off, push-to-talk, chat with cited sources, stop, pending
approvals, audio/voice/name settings, a memory explorer (global / local graph / "Answer" view with the real trace /
keyboard-accessible list), search and filters (type, project, tag, date, status, relation, with sources, without
connections), a reader with sanitized Markdown (DOMPurify, no scripts or remote loads), sources with quotes, confirmable
suggested relations, history and actions (ask, see connections, correct, view source, forget). The graph updates over SSE
events and resyncs by version; positions are stored separately (`runtime/ui/layout.json`). Vendored libraries:
force-graph 1.51.4 (MIT), marked 18.0.11 (MIT), DOMPurify 3.4.14 (MPL-2.0/Apache-2.0).

Demo with test memories kept separate from your own: `JARVIS_HOME=/tmp/jarvis-demo jarvis serve --demo --open`
(or generate 100/1000/5000 synthetic nodes with `python scripts/graph_bench.py --keep /tmp/bench` and serve `JARVIS_HOME=/tmp/bench/n1000`).

## Tests

`python -m pytest` (87 tests, ~20 s; runs are tested against a fake `claude`, the real one is never launched; audio tests
use synthetic fixtures generated by `scripts/make_fixtures.sh`).
Memory evaluation against the real Claude: `python scripts/memory_eval.py`. Speaker→microphone acoustic test:
`python scripts/acoustic_test.py`. Real results in `docs/test-results.md` and `docs/memory-eval.md`.

## Known limitations

- With all of Claude Code's tools available, the assistant can modify your Mac: the protection is the approval gate (`writes`); do not set `never` unless you understand it.
- The native app is a floating orb with bubbles; the full conversation and the memory live in the panel (orb menu).
- The Kokoro voice is synthesized on CPU: very long sentences take ~1 s each before playing; they are chained without gaps.

- The real voice and wake-word loops are unverified on this machine because the terminal lacks microphone permission (the pipeline is verified with fixtures).
- Half-duplex: while it speaks, the microphone is ignored; there is no barge-in. Stop with the button, `s`, Esc, or by asking it to stop talking.
- The `hey_jarvis` model is English; Spanish pronunciation works on fixtures but may need sensitivity tuning. Porcupine's "jarvis" requires a key.
- Default STT: Whisper large-v3-turbo on MLX (Apple Silicon GPU), ~2–3 s per request; the first start downloads ~1.5 GB. Lighter alternative: `stt.provider: faster_whisper`, `stt.model: small` (CPU, same latency, less accuracy).
- Claude needs the network; reasoning is not offline. The per-turn cost is recorded exactly as the SDK reports it.
- Scanned PDFs: detected and flagged `needs_ocr`; no OCR is bundled.
- Semantic linting (`lint --semantic`) and extraction (`--extract`) are explicit, budgeted calls, not automatic ones.
- Page creation through the normal path: ~0.4 s/page at 1000 pages (lock + journal + index); fine for daily use, not for bulk loads.
- At 5000 nodes the initial graph layout drops to ~21 FPS for a few seconds; use filters or the local view.
- No promises about running while the Mac is asleep.

## License

MIT. See [LICENSE](LICENSE).

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the [code of conduct](CODE_OF_CONDUCT.md). For vulnerabilities, see [SECURITY.md](SECURITY.md).
