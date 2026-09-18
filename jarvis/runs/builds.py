"""Construcciones reales: la spec que sobrevive, el brief que dirige y el plan que informa.

1. La spec se escribe DENTRO del proyecto (`docs/specs/`) antes de lanzar nada: es lo único que sobrevive a una
   compactación o a un reinicio.
2. El brief entrega todo el proceso en un prompt: leer la spec, escribir un plan por fases, revisarlo contra la spec,
   ejecutarlo tarea a tarea con pruebas primero, marcando las casillas del plan.
3. El progreso se lee de las casillas del plan (`## Tarea N: título`, `- [ ]` / `- [x]`), no se adivina.
Sin servidor, sin asyncio, sin subprocesos.
"""
from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

SPEC_DIR = "docs/specs"
PLAN_DIR = "docs/plans"
_SLUG_MAX = 60


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower().replace("ñ", "n").replace("á", "a").replace("é", "e")
               .replace("í", "i").replace("ó", "o").replace("ú", "u")).strip("-")
    s = s[:_SLUG_MAX].rstrip("-") if len(s) > _SLUG_MAX else s
    return s or "build"


_TITLE_TAIL = re.compile(r"[\s\-—:]*(diseño|design|spec|especificaci[oó]n)\s*$", re.IGNORECASE)


def topic_of(spec: str) -> str:
    for raw in (spec or "").splitlines():
        line = raw.strip().lstrip("#").strip()
        if line:
            return _TITLE_TAIL.sub("", line).strip() or line
    return "build"


def spec_path(spec: str, today: datetime.date | None = None) -> str:
    day = (today or datetime.date.today()).isoformat()
    return f"{SPEC_DIR}/{day}-{slug(topic_of(spec))}-diseno.md"


def render_spec(spec: str, constraints: str = "", non_goals: str = "", today: datetime.date | None = None,
                assistant: str = "el asistente") -> str:
    day = (today or datetime.date.today()).isoformat()
    body_lines = (spec or "").strip().splitlines()
    if body_lines and body_lines[0].lstrip().startswith("# "):
        body_lines = body_lines[1:]  # el título ya va en la cabecera del documento
    body = "\n".join(body_lines).strip()
    has_sections = any(re.match(r"^##\s+\S", ln) for ln in body_lines)
    lines = [f"# {topic_of(spec)} — Diseño", "", f"Fecha: {day}",
             "Estado: aprobado — acordado con el usuario de viva voz antes de empezar la construcción.", "",
             f"> Escrito por {assistant} a partir de la conversación en la que se acordó. Es la fuente de verdad de la "
             "construcción: sobrevive a la sesión que lo lee, y una sesión compactada o sustituida vuelve a empezar desde aquí.",
             ""] + ([body, ""] if has_sections else ["## Lo acordado", "", body, ""])
    if (constraints or "").strip():
        lines += ["## Restricciones", "", constraints.strip(), ""]
    if (non_goals or "").strip():
        lines += ["## Fuera de alcance", "", non_goals.strip(), ""]
    return "\n".join(lines)


def write_spec(project_path: str, spec: str, constraints: str = "", non_goals: str = "",
               today: datetime.date | None = None, assistant: str = "el asistente") -> str:
    relative = spec_path(spec, today)
    root = Path(project_path)
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    (root / PLAN_DIR).mkdir(parents=True, exist_ok=True)
    target.write_text(render_spec(spec, constraints, non_goals, today, assistant), encoding="utf-8")
    return relative


BUILD_BRIEF_HEAD = "[Construcción larga — sin humano presente]"
BUILD_BRIEF_TEMPLATE = """\
{head} Vas a construir un proyecto real a partir de un diseño ya cerrado, de principio a fin, en una sola sesión larga. \
Nadie está mirando y nadie puede contestar preguntas: ninguna respuesta te llegará, así que terminar el turno para \
preguntar algo significa que el trabajo simplemente no se hace. No hay límite de tiempo: tómate lo que el trabajo necesite.

EL DISEÑO ESTÁ CERRADO Y APROBADO. El usuario lo acordó de viva voz y está escrito en este proyecto en:
    {spec_path}
Lee ese archivo antes que nada. NO hagas brainstorming, NO reabras el diseño, NO presentes un plan ni opciones para \
aprobar y NO invoques ninguna skill que exija aprobación humana antes de implementar. Trata la spec como el requisito \
que es: construye lo que dice y, donde calle, aplica el paso 6.

Sigue todo el proceso tú mismo, en este orden:

1. PLAN. Escribe un plan de implementación por fases en {plan_dir}/ antes de tocar código. Cada tarea lleva un \
encabezado `## Tarea N: <título>` y se descompone en pasos con casilla `- [ ]`, cada uno lo bastante pequeño para \
terminarlo y verificarlo por separado.
2. REVISA Y CORRIGE TU PROPIO PLAN antes de ejecutar una sola línea. Léelo contra la spec y busca lo que está mal: \
requisitos sin tarea, tareas que se contradicen entre sí o con la spec, pasos vagos o con marcadores, un orden que te haría \
construir sobre algo que aún no existe, y cualquier cosa que un lector no podría ejecutar sin preguntar. Arréglalo todo en \
el archivo del plan. Hazlo al menos una vez y de nuevo cada vez que el plan deje de corresponder a la realidad.
3. EJECUTA una tarea cada vez. Si hay subagentes disponibles, usa uno nuevo por tarea y otro que revise su trabajo antes \
de seguir; si no, hazlo tú, tarea a tarea, sin mezclar dos.
4. PRUEBAS PRIMERO. En cada tarea, escribe primero la prueba que falla y luego el código que la hace pasar. Antes de dar \
por terminada una tarea o la construcción, ejecuta las pruebas y lee la salida real; nunca informes de trabajo terminado \
por haberlo escrito. Si algo se rompe, depura de forma sistemática en vez de adivinar.
5. MARCA LAS CASILLAS SEGÚN AVANZAS. En cuanto un paso esté hecho y verificado, edita el plan y cambia su `- [ ]` por \
`- [x]`. Ese archivo es la única forma que tiene el usuario de ver hasta dónde has llegado: mantenlo al día y honesto.
6. NADIE PUEDE RESPONDER PREGUNTAS. Donde la spec deje una elección abierta, decídela tú por sus méritos, escribe la \
decisión y su motivo en el plan bajo la tarea correspondiente, y continúa.
7. EL LISTÓN. Debe funcionar en el primer arranque sin configuración: lo que necesites saber de la máquina, averígualo; \
nunca abras con un formulario ni un diálogo que el usuario no pueda cerrar. El camino feliz del README es un solo comando. \
La interfaz forma parte de lo que construyes y se exige igual que la lógica. Antes de informar de que terminaste, ejecuta \
lo que construiste tal como lo haría un usuario nuevo y confirma que todo eso se cumple.

Haz commit según avanzas, un commit por tarea terminada, y nunca hagas push. Cuando la última casilla esté marcada y las \
pruebas pasen, para y di qué construiste.\
"""


def compose_build_brief(spec_relative_path: str) -> str:
    return BUILD_BRIEF_TEMPLATE.format(head=BUILD_BRIEF_HEAD, spec_path=spec_relative_path, plan_dir=PLAN_DIR)


def is_build_prompt(stored_prompt: str) -> bool:
    return (stored_prompt or "").startswith(BUILD_BRIEF_HEAD)


_ON_ONE_LINE = r"[^\r\n\v\f\x1c\x1d\x1e\x85  ]"
_TASK_HEADING = re.compile(rf"#{{2,4}}[ \t]+(?:Tarea|Task)[ \t]+(\d+)[ \t]*[:.\-—][ \t]*({_ON_ONE_LINE}+?)[ \t]*", re.IGNORECASE)
_CHECKBOX = re.compile(rf"[ \t]*[-*][ \t]+\[([ xX])\][ \t]*({_ON_ONE_LINE}*?)[ \t]*")
_EMPHASIS = re.compile(r"[*_`]+")


class PlanTask:
    __slots__ = ("number", "title", "steps_done", "steps_total")

    def __init__(self, number: int, title: str) -> None:
        self.number = number
        self.title = title
        self.steps_done = 0
        self.steps_total = 0

    @property
    def done(self) -> bool:
        return self.steps_total > 0 and self.steps_done == self.steps_total

    def to_dict(self) -> dict:
        return {"number": self.number, "title": self.title, "steps_done": self.steps_done,
                "steps_total": self.steps_total, "done": self.done}


def task_number_of(heading: str) -> int:
    m = _TASK_HEADING.fullmatch(f"## {(heading or '').strip()}")
    return int(m.group(1)) if m else 0


def parse_plan(text: str) -> list[PlanTask]:
    tasks: list[PlanTask] = []
    current: PlanTask | None = None
    for line in (text or "").splitlines():
        h = _TASK_HEADING.fullmatch(line)
        if h:
            current = PlanTask(int(h.group(1)), _EMPHASIS.sub("", h.group(2)).strip())
            tasks.append(current)
            continue
        box = _CHECKBOX.fullmatch(line)
        if box and current is not None:
            current.steps_total += 1
            if box.group(1) in ("x", "X"):
                current.steps_done += 1
    return tasks


def latest_plan(project_path: str) -> Path | None:
    directory = Path(project_path) / PLAN_DIR
    try:
        plans = [p for p in directory.glob("*.md") if p.is_file()]
    except OSError:
        return None
    return max(plans, key=lambda p: p.stat().st_mtime) if plans else None


class PlanProgress:
    __slots__ = ("path", "tasks")

    def __init__(self, path: Path, tasks: list[PlanTask]) -> None:
        self.path = path
        self.tasks = tasks

    @property
    def total(self) -> int:
        return len(self.tasks)

    @property
    def done(self) -> int:
        return sum(1 for t in self.tasks if t.done)

    @property
    def current(self) -> PlanTask | None:
        return next((t for t in self.tasks if not t.done), None)

    @property
    def finished(self) -> bool:
        return self.total > 0 and self.done == self.total

    def to_dict(self) -> dict:
        return {"path": str(self.path), "total": self.total, "done": self.done, "finished": self.finished,
                "current": self.current.to_dict() if self.current else None, "tasks": [t.to_dict() for t in self.tasks]}


def plan_progress(project_path: str) -> PlanProgress | None:
    path = latest_plan(project_path)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    tasks = parse_plan(text)
    return PlanProgress(path, tasks) if tasks else None


COMMAND_MAX_CHARS = 160
_COMMAND_ALLOWED = re.compile(r"[A-Za-z0-9 _\-./:=,+@]+")
_LAUNCHERS = frozenset({"npm", "pnpm", "yarn", "bun", "npx", "node", "deno", "python", "python3", "uv", "uvicorn", "gunicorn",
                        "flask", "streamlit", "make", "cargo", "go", "ruby", "rails", "bundle", "php", "dotnet", "hugo",
                        "jekyll", "vite", "next", "serve", "http-server", "swift", "xcodebuild", "pytest", "open"})


def command_problem(command: str, project_path: str) -> str | None:
    """None si se puede ejecutar; si no, la frase que hay que decir en su lugar."""
    text = (command or "").strip()
    if not text:
        return "No había nada que ejecutar."
    if len(text) > COMMAND_MAX_CHARS:
        return "Ese comando es demasiado largo; dame el corto con el que arranca el proyecto."
    if not _COMMAND_ALLOWED.fullmatch(text):
        return "Solo ejecuto un comando simple: sin tuberías, sin punto y coma, sin encadenar."
    token = text.split(" ", 1)[0]
    if token in _LAUNCHERS:
        return None
    if "/" in token:
        root = Path(project_path).resolve()
        try:
            candidate = (root / token).resolve()
            if candidate.is_file() and candidate.is_relative_to(root):
                return None
        except OSError:
            pass
        return f"No hay ningún {token} en ese proyecto, así que no ejecuté nada."
    return f"No arranco cosas con {token}; ejecuto el comando que documenta el proyecto y nada más."


def _package_scripts(project_path: str) -> set[str]:
    try:
        scripts = json.loads((Path(project_path) / "package.json").read_text(encoding="utf-8", errors="replace")).get("scripts")
    except (OSError, ValueError, AttributeError):
        return set()
    return set(scripts) if isinstance(scripts, dict) else set()


def is_documented(command: str, project_path: str) -> bool:
    text = " ".join((command or "").split())
    if not text:
        return False
    root = Path(project_path)
    for name in ("README.md", "README", "readme.md", "Readme.md", "README.txt"):
        try:
            body = (root / name).read_text(encoding="utf-8", errors="replace")[:60_000]
        except OSError:
            continue
        if text.lower() in " ".join(body.split()).lower():
            return True
    parts = text.split()
    scripts = _package_scripts(project_path)
    if scripts and len(parts) >= 2 and parts[0] in ("npm", "pnpm", "bun", "yarn"):
        if parts[1] == "run" and len(parts) >= 3 and parts[2] in scripts:
            return True
        if parts[1] in scripts:
            return True
    if len(parts) >= 2 and parts[0] == "make":
        for name in ("Makefile", "makefile", "GNUmakefile"):
            try:
                body = (root / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if re.search(rf"^{re.escape(parts[1])}\s*:", body, re.MULTILINE):
                return True
    return False


_SPEC_LINE = re.compile(rf"^\s*({re.escape(SPEC_DIR)}/\S+\.md)\s*$", re.MULTILINE)


def gist_of_build(stored_prompt: str) -> str:
    m = _SPEC_LINE.search(stored_prompt or "")
    if not m:
        return ""
    stem = Path(m.group(1)).stem
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
    stem = re.sub(r"-diseno$", "", stem)
    return stem.replace("-", " ").strip()
