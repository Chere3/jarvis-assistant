"""Specs escritas en el proyecto, brief de construcción, progreso por casillas y aprobación registrada."""
import datetime

from jarvis.runs import builds, specs

PLAN = """# Plan de la app

## Objetivo
hacerlo

## Tarea 1: Modelo de datos
- [x] escribir la prueba
- [x] implementar

## Task 2: API
- [x] endpoint
- [ ] pruebas

## Tarea 3: UI
- [ ] pantalla
"""


def test_parse_plan_accepts_spanish_and_english_headings():
    tasks = builds.parse_plan(PLAN)
    assert [(t.number, t.title, t.done) for t in tasks] == [(1, "Modelo de datos", True), (2, "API", False), (3, "UI", False)]
    assert builds.task_number_of("Tarea 7: algo") == 7 and builds.task_number_of("Objetivo") == 0


def test_write_spec_and_progress(tmp_path):
    day = datetime.date(2026, 9, 17)
    rel = builds.write_spec(str(tmp_path), "# Bot de tareas — Diseño\n\n## Objetivo\nUn bot.\n\n## Arquitectura\nBun.",
                            constraints="Sin API key", non_goals="Web", today=day, assistant="Viernes")
    assert rel == "docs/specs/2026-09-17-bot-de-tareas-diseno.md"
    text = (tmp_path / rel).read_text()
    assert text.startswith("# Bot de tareas — Diseño") and "## Restricciones" in text and "Viernes" in text
    assert text.count("# Bot de tareas") == 1 and "## Lo acordado" not in text
    plain = builds.write_spec(str(tmp_path), "Solo un párrafo sin secciones.", today=datetime.date(2026, 9, 18))
    assert "## Lo acordado" in (tmp_path / plain).read_text()
    assert (tmp_path / builds.PLAN_DIR).is_dir()
    brief = builds.compose_build_brief(rel)
    assert builds.is_build_prompt(brief) and rel in brief and "## Tarea N" in brief and "NADIE PUEDE RESPONDER" in brief
    assert builds.gist_of_build(brief) == "bot de tareas"
    assert builds.plan_progress(str(tmp_path)) is None  # sin plan todavía
    (tmp_path / builds.PLAN_DIR / "2026-09-17-plan.md").write_text(PLAN)
    pr = builds.plan_progress(str(tmp_path))
    assert (pr.done, pr.total, pr.current.title, pr.finished) == (1, 3, "API", False)


def test_sections_numbering_and_approval(tmp_path):
    rel = builds.write_spec(str(tmp_path), "# Tienda — Diseño\n\n## Objetivo\nVender.\n\n## Datos\nSQLite.\n\n```md\n## no cuenta\n```\n\n## Fuera\nNada.")
    doc = specs.read_document(str(tmp_path), rel)
    titles = [(s["number"], s["title"]) for s in doc["sections"]]
    assert titles == [(1, "Objetivo"), (2, "Datos"), (3, "Fuera")]  # el ## dentro del bloque de código no cuenta
    assert doc["title"] == "Tienda — Diseño" and doc["approval"]["state"] == "awaiting"
    rec = specs.record_approval(str(tmp_path), rel, by="voz")
    assert rec["sections"] == len(doc["sections"]) and specs.approval_of(str(tmp_path), rel)["state"] == "approved"
    assert specs.replace_section(str(tmp_path), rel, 2, "Postgres.")
    assert specs.approval_of(str(tmp_path), rel)["state"] == "superseded"
    doc = specs.read_document(str(tmp_path), rel)
    assert doc["sections"][1]["body"] == "Postgres." and [s["title"] for s in doc["sections"]] == ["Objetivo", "Datos", "Fuera"]
    review = specs.project_review(str(tmp_path))
    assert review["state"] == "awaiting" and review["pending"] == [rel]
    specs.record_approval(str(tmp_path), rel)
    assert specs.project_review(str(tmp_path))["state"] == "planning"
    (tmp_path / builds.PLAN_DIR / "p.md").write_text(PLAN)
    assert specs.project_review(str(tmp_path))["state"] == "building"
    (tmp_path / builds.PLAN_DIR / "p.md").write_text(PLAN.replace("[ ]", "[x]"))
    assert specs.project_review(str(tmp_path))["state"] == "review"


def test_document_containment(tmp_path):
    rel = builds.write_spec(str(tmp_path), "# A\n\n## B\nc")
    assert specs.resolve_document(str(tmp_path), rel) is not None
    (tmp_path / "secreto.md").write_text("x")
    assert specs.resolve_document(str(tmp_path), "secreto.md") is None
    assert specs.resolve_document(str(tmp_path), "../" + rel) is None
    assert specs.resolve_document(str(tmp_path), "/etc/passwd") is None
    assert specs.resolve_document(str(tmp_path), "docs/specs/../../secreto.md") is None
    assert specs.read_document(str(tmp_path), "docs/specs/no.md") is None


def test_command_problem(tmp_path):
    assert builds.command_problem("npm run dev", str(tmp_path)) is None
    assert builds.command_problem("", str(tmp_path))
    assert "tuberías" in builds.command_problem("ls | sh", str(tmp_path))
    assert "No arranco" in builds.command_problem("rm -rf x", str(tmp_path))
    (tmp_path / "run.sh").write_text("#!/bin/sh\n")
    assert builds.command_problem("./run.sh", str(tmp_path)) is None
    assert "No hay" in builds.command_problem("./nope.sh", str(tmp_path))
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "vite"}}')
    assert builds.is_documented("npm run dev", str(tmp_path)) and not builds.is_documented("npm run build", str(tmp_path))
