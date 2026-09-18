# JARVIS — asistente personal de escritorio (macOS, Apple Silicon)

Asistente por voz y texto con memoria persistente en forma de wiki Markdown, razonamiento con Claude (Agent SDK),
activación por palabra clave local, herramientas locales autorizadas y un panel web con explorador visual de la memoria.
El nombre «Jarvis» es solo configuración: `assistant.display_name`.

## Requisitos

- macOS (probado en 26.5, MacBook Air Apple Silicon), Python 3.12 (`uv` lo instala), Claude Code instalado y autenticado
  con tu cuenta claude.ai (`claude auth status` → `loggedIn: true`). No se usa ninguna API key.
- Permiso de micrófono para la app de terminal desde la que ejecutes `jarvis` (ver abajo).

## Instalación

```bash
cd jarvis
uv venv --python 3.12 .venv && source .venv/bin/activate
uv sync --extra dev            # dependencias fijadas en uv.lock
uv pip install -e .            # comando `jarvis`
jarvis config init             # crea ~/.jarvis/config.yaml (sin secretos)
jarvis doctor                  # dependencias, autenticación, dispositivos; añade --live para una petición real
```

## App nativa flotante (recomendado)

```bash
jarvis tts download          # voz neuronal local (Kokoro, ~350 MB, una sola vez)
jarvis app install --open    # compila Jarvis.app, la instala en ~/Applications y la abre
```

Aparece un **orbe con la inicial del asistente** flotando sobre todas las ventanas (arrastrable, sin icono en el Dock) y un
icono en la barra de menús. Clic en el orbe = hablar (pulsar-para-hablar); clic mientras responde = detener; clic con
aprobación pendiente = aprobar; clic derecho = menú (activar escucha «hey jarvis», detener, abrir el panel completo, aprobar
o rechazar, reiniciar backend, salir). Atajo global **⌥ Espacio** desde cualquier app (sin permiso de Accesibilidad). Colores
del orbe: azul en reposo, verde escuchando, anillo giratorio pensando, ámbar hablando, naranja esperando tu aprobación.
Junto al orbe aparece una burbuja con tu transcripción, la respuesta y las aprobaciones.

La app lanza el backend (`jarvis serve --listen`) como proceso hijo, por lo que macOS pide el **permiso de micrófono a
Jarvis.app** la primera vez (Ajustes → Privacidad y seguridad → Micrófono). Si ya tienes un `jarvis serve` en marcha en el
puerto configurado, la app se conecta a él en lugar de lanzar otro. El panel web completo (memoria, grafo, trazas) sigue
disponible desde el menú del orbe. Al salir de la app se cierra el backend que ella lanzó.

## Herramientas de Claude Code y permisos automáticos

El agente dispone de **todas las herramientas de Claude Code** (Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebFetch,
subagentes…) además de las de memoria y del panel, con `cwd` en tu carpeta personal (`claude.cwd`).

Permisos (`claude.permission_mode`):

- `auto` (por defecto): el **clasificador interno de Claude Code** decide. Aprueba solo lo que encaja con lo que pediste y
  escala a ti lo dudoso; cuando escala, se te pide permiso con un **resumen hablado** («¿Me das permiso para editar el archivo
  notas.md?»), no el comando entero. Respondes «sí»/«no», con clic en el orbe, o en el panel.
- `default`: sin clasificador; aplica `claude.builtin_approval` (`writes` = Bash/Write/Edit/subagentes piden permiso; `always`; `never`).

**Red de seguridad propia** (`claude.always_confirm_patterns`): aunque el modo automático lo aprobara, los comandos destructivos
(`rm -rf`, `sudo`, `git push --force`, `git reset --hard`, `kill`, `chmod -R`, `diskutil`, `shutdown`, `curl … | sh`…) siempre
te piden confirmación. Verificado: «borra con rm -rf sin preguntar» → pregunta igualmente. Ojo: el modo automático **sí ejecuta**
sin preguntar acciones normales que tú pidas (crear archivos, comandos habituales); ese es su propósito.

Cuando Claude tiene una duda (varias formas válidas de hacer algo) te la pregunta en voz alta con sus opciones; contestas
hablando («la papelera», «la segunda», «no») o con los botones del panel.

**Cambiar de modelo hablando**: «cambia a sonnet», «usa opus», «¿qué modelo usas?». Alias: opus, sonnet, haiku, fable, o un id
`claude-*`. El cambio aplica al siguiente turno y se guarda en la configuración. Verificado en vivo (opus → sonnet).

## Capataz de Claude Code (sesiones, runs, construcciones)

Además de asistente personal, Jarvis vigila **todas las sesiones de Claude Code de tu Mac** y dirige trabajo real:

- **Ve tus sesiones.** Lee el roster del CLI (`~/.claude/sessions/*.json`) y la cola de cada transcript una vez por segundo.
  Cuenta conversaciones, no procesos, y les da nombres hablables («pingou», «proyectos, la de itinerario»,
  «la sonda más nueva»). Pregunta «¿qué sesiones hay?», «¿cuál me espera?», «¿dónde se quedó pingou?».
  Sin Claude: `jarvis sessions`.
- **Te avisa solo.** Cuando una sesión se detiene esperándote (permiso, diálogo, pregunta) lo dice en voz alta al momento;
  cuando una termina de trabajar lo agrupa en una frase en la siguiente pausa. Si no puede hablar, manda una notificación
  nativa de macOS. Nunca te recita al arrancar lo que ya estaba abierto. Ajustes en `sessions.*` de la configuración.
- **Contesta por ti.** «Dile a pingou que use Postgres» → escribe en el socket de entrada de esa sesión (llega como un
  turno nuevo). Un prompt de permisos no se resuelve con texto: «pulsa Intro en sonda» envía UNA tecla (Intro, Escape o
  1-9) a la pestaña de Terminal.app que tiene ese tty; requiere permiso de Accesibilidad para Jarvis.app y no funciona
  con sesiones en otras terminales (lo dice en vez de adivinar).
- **Lanza runs.** «Lanza en sonda: arregla el typo del README» crea un `claude -p` desatendido en la carpeta del
  proyecto, registrado en SQLite (`data/runtime/runs.sqlite`) con prompt, estado, tokens y todo el flujo de eventos. Todo run
  llega a un estado terminal; los fallos se anuncian al instante y los éxitos en la siguiente pausa. Un run que «terminó»
  sin cambiar nada o preguntando algo se anuncia como lo que es.
- **Construye proyectos.** El brainstorm es la fase de diseño: una pregunta cada vez, dos o tres enfoques. Lo acordado se
  escribe en el proyecto (`docs/specs/AAAA-MM-DD-<tema>-diseno.md`) por secciones numeradas; las revisas por número
  («cambia la tres») y las apruebas de viva voz. Con la spec aprobada, `start_build` entrega a una sesión larga el proceso
  completo: plan por fases en `docs/plans/`, revisión del plan contra la spec, ejecución tarea a tarea con pruebas primero y
  casillas que se marcan. «¿Cómo va sonda?» se responde leyendo esas casillas.
- **Uso de la suscripción.** Las ventanas de 5 horas y 7 días que informa el CLI durante cada turno («¿cómo voy de límites?»).
- **Seguridad de datos ajenos.** Lo que devuelve la web, la pantalla, un run o el transcript de otra sesión es dato, no
  instrucción: en un turno que leyó algo de eso, las herramientas que actúan (escribir a sesiones, pulsar teclas, lanzar
  runs, escribir memoria) quedan bloqueadas y toda herramienta integrada con efectos pide tu permiso aunque el modo
  automático la aprobara. Lo pides de nuevo con tus palabras en un turno nuevo y se hace.

Los runs corren con `--dangerously-skip-permissions` (no tienen terminal para responder) en la carpeta que indiques, sin
`ANTHROPIC_*` en su entorno: son procesos con tus privilegios, como los que lanzarías a mano. `runs.projects_root`
(por defecto `~/Documents/proyectos`) es donde `create_project` crea proyectos nuevos y donde se buscan por nombre.

## Panel nativo

Desde el menú del orbe («Abrir panel», ⌘P en el menú) o cuando Jarvis lo abre solo (`ui_show`). Sin navegador:

| Pestaña | Qué muestra |
|---|---|
| Sesiones | Todas las conversaciones por proyecto, las que te esperan arriba en rojo con el motivo y desde cuándo; detalle con última petición, último mensaje, herramientas, subagentes y los últimos mensajes; enviar un mensaje o pulsar una tecla en Terminal. |
| Runs | Necesitan atención · activos · historial; detalle con modelo, tokens, prompt, resultado y transcripción en vivo; cancelar; lanzar un run nuevo. |
| Specs | Documentos (specs y planes) por proyecto con estado de aprobación y progreso; secciones numeradas grandes, editar una sección, aprobar, construir. |
| Proyectos | Carpeta de proyectos + sesiones + runs con su estado; detalle con tareas del plan, conversaciones y runs. |
| Uso | Medidores de las ventanas de 5 h y 7 días, cuándo se reinician y los runs del día. |
| Memoria | Búsqueda, lista y lector de recuerdos con fuentes, relaciones y enlaces inversos. |
| Avisos | Lo que Jarvis dijo por su cuenta (y por qué canal) y los mensajes enviados a sesiones. |

El panel web de `jarvis serve` sigue existiendo para el grafo de memoria («Panel web» en el menú).

## Transcripción (voz a texto)

Por defecto `stt.provider: mlx_whisper` con `mlx-community/whisper-large-v3-turbo`: corre en la GPU de Apple Silicon, así que
ofrece la precisión del modelo grande con la latencia de `small` en CPU (medido: 2–3 s por petición, carga 2,8 s). Además el
audio se normaliza (micrófonos flojos), se usa un prompt inicial en español y se conserva lo que dices justo después del tono.
Si un micrófono externo suena mal, elige otro en `audio.input_device` (`jarvis doctor` lista los dispositivos).

## Voz

Por defecto `tts.provider: kokoro`: **voz neuronal local** (Kokoro-82M en ONNX), realista y sin servicios externos.
La voz sale **en flujo**: cada frase se sintetiza y suena mientras Claude sigue escribiendo la siguiente (primera frase a los
~0,7 s de generarse; en un turno real la voz empezó a los 1,9 s y el turno terminó a los 2,8 s), y se interrumpe al instante.
Las respuestas están escritas para el oído: una o dos frases, sin listas ni Markdown, sin narrar herramientas.
Voces en español: `ef_dora` (femenina, por defecto), `em_alex` y `em_santa` (masculinas). Escucha las tres:
`jarvis tts say --voice em_alex "Hola, soy Jarvis"` o los archivos de `~/Desktop/jarvis-muestras-voz/`. Cambia con
`jarvis config set tts.voice em_alex`; velocidad con `tts.speed`. Sin el modelo descargado se usa la voz del sistema
(y se prefieren automáticamente las voces «Premium/Enhanced» si las instalas en Ajustes → Accesibilidad → Contenido hablado).

## Uso

| Comando | Qué hace |
|---|---|
| `jarvis chat` | Conversación por texto en la terminal (streaming, `/aprobar id`, `/rechazar id`, `/cancelar`, `/traza`). `--speak` lee las respuestas. `-m "…"` una sola petición. |
| `jarvis listen` | Modo voz: palabra de activación + pulsar-para-hablar (Enter), `s` detiene la voz, texto + Enter envía escrito. `--ptt-only` sin detector. |
| `jarvis serve --listen --open` | Panel local en `http://127.0.0.1:8765/?token=…` (la URL con token se imprime) con voz. Sin `--listen` solo texto. |
| `jarvis doctor [--live]` | Diagnóstico sin exponer secretos. |
| `jarvis config show|init|set clave valor` | Configuración validada. |
| `jarvis memory search|list|show|ingest|lint|rebuild|forget|export|stats|wipe` | Operaciones sobre la memoria. `ingest ruta --extract` extrae recuerdos con Claude citando fragmentos. `lint --semantic` añade revisión con Claude (explícita, con presupuesto). |
| `jarvis app install|build|open|uninstall` | App nativa flotante (orbe + barra de menús + ⌥Espacio + panel nativo). |
| `jarvis sessions [--json]` | Lista las sesiones de Claude Code vivas en este Mac, con nombre hablable, estado y edad. |
| `jarvis tts download|voices|say` | Voz neuronal: descargar modelo, listar voces, probar. |
| `jarvis launchagent install|uninstall|status` | Inicio automático opcional (LaunchAgent). Nunca se activa solo. |
| `--demo` | Proveedor simulado claramente identificado; el modo normal nunca inventa respuestas si falta autenticación. |

Ejemplos por voz o texto: «Jarvis, recuerda que prefiero respuestas cortas», «¿qué sabes de mi proyecto del asistente?»,
«¿de dónde sacaste ese dato?», «eso cambió; ahora la fecha es el viernes», «abre la carpeta de este proyecto», «deja de hablar»,
«olvida mi preferencia anterior», «muéstrame mis recuerdos», «¿qué recuerdos utilizaste para responder?».

## Recorrido completo en cinco minutos

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

## Autenticación y facturación

**Solo Claude Code, nunca la API directa.** El Agent SDK lanza el CLI de Claude Code como subproceso y reutiliza su sesión de
claude.ai (verificado en este Mac con el plan Max). El asistente retira `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` del entorno
antes de lanzar el CLI, así que aunque existan no se usan ni se factura por API. El consumo cuenta contra los límites de la
suscripción de Claude Code; el `total_cost_usd` que el SDK reporta por turno (`data/runtime/sessions/*.jsonl`) es un equivalente
informativo, no un cargo. El modelo se configura en `claude.model` (null = el por defecto del CLI, hoy claude-opus-5);
`jarvis doctor --live` lo valida con una petición real. Requisito: `claude auth status` → `loggedIn: true`.

## Palabra de activación

- **Por defecto**: `openwakeword` con el modelo preentrenado `hey_jarvis` (ONNX, local, sin clave). Es un modelo en inglés;
  con fixtures de voz inglesa y española puntúa 0,995–0,999. Ajusta `wake_word.sensitivity` (0–1) y `cooldown_s`.
- **Porcupine** (`wake_word.provider: porcupine`): tiene el keyword integrado `jarvis` pero requiere `PICOVOICE_ACCESS_KEY`
  en el entorno (gratuita para uso personal en https://console.picovoice.ai). Para otra palabra o para español entrena un `.ppn`
  en la consola de Picovoice (plataforma macOS, idioma es), pon su ruta en `wake_word.model_path` y `wake_word.language: es`
  (necesita además el modelo de idioma `porcupine_params_es.pv`; `jarvis doctor` te indica el paso que falte).
- **Otra palabra con openWakeWord**: entrena un modelo `.onnx` con las herramientas de openWakeWord y pon su ruta en `model_path`.
- Cambiar `assistant.display_name` **no** cambia el detector: el CLI y el panel lo avisan y muestran `wake_word.keyword_label` aparte.
- Mientras no haya modelo o micrófono, `jarvis listen --ptt-only` y el botón «Hablar» del panel siguen funcionando.
- El detector funciona solo con el proceso activo y el sistema capturando audio; no se promete funcionamiento en suspensión.

### Permiso de micrófono

macOS pide el permiso a la **aplicación de terminal** (Terminal, iTerm, Ghostty…) que ejecuta `jarvis`. Si `jarvis doctor` informa
«el micrófono devuelve SILENCIO ABSOLUTO», ve a Ajustes → Privacidad y seguridad → Micrófono y activa esa app; luego reinicia
la terminal. En este equipo, durante la construcción, la terminal (Ghostty) no tenía el permiso, así que el ciclo de voz real
quedó pendiente de verificación por el usuario.

## Dónde se guarda la memoria

`~/.jarvis/` (o `$JARVIS_HOME`), fuera del repositorio:

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

Contrato de cada recuerdo: `id`, `type`, `title`, `created`, `updated`, `valid_from/valid_until`, `status` (active | disputed |
superseded | archived), `provenance` (user_statement | source_extraction | assistant_inference | proposal), `sources`
(source_id, locator, quote), `related` (target, type, status explicit|suggested, rationale), `sensitivity`, `supersedes`,
`superseded_by`, `aliases`, `tags`, `project`, `change_reason`. Sin porcentajes de confianza inventados.

## Exportar y borrar

- Exportar: `jarvis memory export ~/Desktop/memoria-jarvis.zip` (copia íntegra de `data/memory`).
- Olvidar un recuerdo: `jarvis memory forget <id> [--delete-sources]` o «Olvidar» en el panel. Muestra antes qué se borra
  (página, versiones, referencias en otras páginas, índice, caché visual, fuentes exclusivas si lo pides). El journal conserva
  id, título y motivo como metadato. Time Machine/iCloud u otras copias externas no se tocan: no se afirma borrado completo si existen.
- Borrar todo: `jarvis memory wipe`.
- La memoria personal está excluida del Git del código (`.gitignore`: `data/`, `runtime/`).

## Panel local

`jarvis serve` imprime una URL con token de un solo proceso. Escucha solo en 127.0.0.1, exige el token (cabecera o cookie
SameSite=Strict), rechaza orígenes ajenos, aplica CSP y no sirve archivos del disco fuera de `ui/static`. Incluye: nombre y estado,
conexión con Claude, escucha on/off, pulsar-para-hablar, chat con fuentes citadas, detener, aprobaciones pendientes, configuración
de audio/voz/nombre, explorador de memoria (grafo global / local / «Respuesta» con la traza real / lista accesible por teclado),
búsqueda y filtros (tipo, proyecto, etiqueta, fecha, estado, relación, con fuentes, sin conexiones), lector con Markdown
saneado (DOMPurify, sin scripts ni cargas remotas), fuentes con cita, relaciones sugeridas confirmables, historial y acciones
(preguntar, ver conexiones, corregir, ver fuente, olvidar). El grafo se actualiza por eventos SSE y resincroniza por versión;
las posiciones se guardan aparte (`runtime/ui/layout.json`). Bibliotecas vendorizadas: force-graph 1.51.4 (MIT), marked 18.0.11
(MIT), DOMPurify 3.4.14 (MPL-2.0/Apache-2.0).

Demostración con recuerdos de prueba separados de tu memoria: `JARVIS_HOME=/tmp/jarvis-demo jarvis serve --demo --open`
(o genera 100/1000/5000 nodos sintéticos con `python scripts/graph_bench.py --keep /tmp/bench` y sirve `JARVIS_HOME=/tmp/bench/n1000`).

## Pruebas

`python -m pytest` (87 pruebas, ~20 s; los runs se prueban con un `claude` falso, nunca se lanza el real; las de audio usan fixtures sintéticos generados con `scripts/make_fixtures.sh`).
Evaluación de memoria con Claude real: `python scripts/memory_eval.py`. Prueba acústica altavoz→micrófono: `python scripts/acoustic_test.py`.
Resultados reales en `docs/test-results.md` y `docs/memory-eval.md`.

## Limitaciones concretas

- Con todas las herramientas de Claude Code el asistente puede modificar tu Mac: la protección es la aprobación (`writes`); no uses `never` sin entenderlo.
- La app nativa es un orbe flotante con burbujas; la conversación completa y la memoria se ven en el panel (menú del orbe).
- La voz Kokoro se sintetiza en CPU: frases muy largas tardan ~1 s por frase antes de sonar; se encadenan sin cortes.

- Ciclo de voz y activación reales no verificados en este equipo por falta del permiso de micrófono en la terminal (pipeline verificado con fixtures).
- Half-duplex: mientras habla, el micrófono se ignora; no hay interrupción por voz (barge-in). Detén con el botón, `s`, Esc o «deja de hablar» por texto.
- El modelo `hey_jarvis` es inglés; la pronunciación española funciona en fixtures pero puede requerir ajustar la sensibilidad. Porcupine «jarvis» exige clave.
- STT por defecto: Whisper large-v3-turbo sobre MLX (GPU de Apple Silicon), ~2–3 s por petición; el primer arranque descarga ~1,5 GB. Alternativa ligera: `stt.provider: faster_whisper`, `stt.model: small` (CPU, misma latencia, menos precisión).
- Claude requiere red; el razonamiento no es offline. El coste por turno se registra tal como lo reporta el SDK.
- PDF escaneados: se detectan y se marca `needs_ocr`; no hay OCR incluido.
- La revisión semántica (`lint --semantic`) y la extracción (`--extract`) son llamadas explícitas con presupuesto, no automáticas.
- Creación de páginas por la ruta normal: ~0,4 s/página con 1000 páginas (bloqueo + journal + índice); apto para uso diario, no para cargas masivas.
- Con 5000 nodos el layout inicial del grafo cae a ~21 FPS durante unos segundos; usa filtros o la vista local.
- No se promete funcionamiento durante la suspensión del Mac.
