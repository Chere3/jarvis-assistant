"""La superficie de revisión: lo que el asistente propone (specs) y lo que produjo (planes).

- La numeración es el mecanismo: el usuario dice «cambia la tres», «aprobado». Hay UNA función que numera (`sections_of`)
  y la usan tanto el panel como la herramienta de voz.
- La aprobación es un acto registrado: un JSON en el proyecto con el hash del texto aprobado. Si el texto cambia, el
  documento pasa a «superseded» y necesita otra mirada.
- Se leen archivos de proyectos arbitrarios: la ruta se resuelve dentro del proyecto y solo en los dos directorios de documentos.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import builds

APPROVAL_DIR = "docs/approvals"
DOCUMENT_DIRS = (builds.SPEC_DIR, builds.PLAN_DIR)
KIND_OF_DIR = {builds.SPEC_DIR: "spec", builds.PLAN_DIR: "plan"}
MAX_DOCUMENT_BYTES = 400_000
_ON_ONE_LINE = r"[^\r\n\v\f\x1c\x1d\x1e\x85  ]"
_HEADING = re.compile(rf"(#{{1,6}})[ \t]+({_ON_ONE_LINE}*?)[ \t]*#*[ \t]*")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Section:
    number: int
    title: str
    level: int
    body: str


@dataclass
class Document:
    preamble: str = ""
    sections: list[Section] = field(default_factory=list)
    title: str = ""


def _headings(text: str) -> list[tuple[int, int, str]]:
    found = []
    fence = None
    for index, line in enumerate(text.splitlines()):
        marker = _FENCE.match(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif line.strip().startswith(fence):
                fence = None
            continue
        if fence is not None:
            continue
        h = _HEADING.fullmatch(line)
        if h and h.group(2):
            found.append((index, len(h.group(1)), h.group(2)))
    return found


def _section_level(headings: list[tuple[int, int, str]]) -> int:
    levels = [lvl for _, lvl, _ in headings]
    top = min(levels)
    if len(levels) > 1 and levels[0] == top and levels.count(top) == 1:
        deeper = [lvl for lvl in levels if lvl > top]
        if deeper:
            return min(deeper)
    return top


def parse_document(text: str) -> Document:
    lines = (text or "").splitlines()
    headings = _headings(text or "")
    if not headings:
        return Document(preamble="\n".join(lines))
    level = _section_level(headings)
    starts = [h for h in headings if h[1] == level]
    title = headings[0][2] if headings[0][1] < level else ""
    first = starts[0][0] if starts else len(lines)
    preamble_lines = [ln for i, ln in enumerate(lines[:first]) if not (title and i == headings[0][0])]
    sections = []
    for number, (index, _, heading_title) in enumerate(starts, start=1):
        end = starts[number][0] if number < len(starts) else len(lines)
        sections.append(Section(number=number, title=heading_title, level=level, body="\n".join(lines[index + 1:end]).strip("\n")))
    return Document(preamble="\n".join(preamble_lines).strip("\n"), sections=sections, title=title)


def sections_of(text: str) -> list[Section]:
    return parse_document(text).sections


def section_number(text: str, number: int) -> Section | None:
    return next((s for s in sections_of(text) if s.number == number), None)


def resolve_document(project_path: str, relative: str) -> Path | None:
    """El archivo real dentro del proyecto, o None (nunca una excepción ni un motivo)."""
    if not relative or "\x00" in relative:
        return None
    root = Path(project_path)
    try:
        root_real = root.resolve()
        real = (root / relative).resolve()
        inside = real.relative_to(root_real)
    except (OSError, ValueError, RuntimeError):
        return None
    if inside.parent.as_posix() not in DOCUMENT_DIRS or real.suffix.lower() != ".md" or not real.is_file():
        return None
    return real


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def digest_of(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def approval_filename(relative: str) -> str:
    stem = Path(relative).as_posix()
    for prefix in DOCUMENT_DIRS:
        if stem.startswith(prefix + "/"):
            return f"{Path(prefix).name}__{stem[len(prefix) + 1:]}.json"
    return f"{stem.replace('/', '__')}.json"


def approval_record_path(project_path: str, relative: str) -> Path:
    return Path(project_path) / APPROVAL_DIR / approval_filename(relative)


def record_approval(project_path: str, relative: str, by: str = "voz") -> dict:
    path = resolve_document(project_path, relative)
    if path is None:
        raise ValueError("no existe ese documento")
    text = _read(path)
    if text is None:
        raise ValueError("documento ilegible")
    record = {"document": Path(relative).as_posix(), "digest": digest_of(text), "approved_at": time.time(),
              "approved_by": by, "sections": len(sections_of(text))}
    target = approval_record_path(project_path, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def _stored_approval(project_path: str, relative: str) -> dict | None:
    try:
        record = json.loads(approval_record_path(project_path, relative).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def approval_of(project_path: str, relative: str, text: str | None = None) -> dict:
    """awaiting (nadie aprobó este texto) | approved | superseded (se revisó tras aprobarlo)."""
    if text is None:
        path = resolve_document(project_path, relative)
        text = _read(path) if path is not None else None
    record = _stored_approval(project_path, relative)
    if record is None or text is None:
        return {"state": "awaiting", "approved_at": None, "approved_by": ""}
    return {"state": "approved" if record.get("digest") == digest_of(text) else "superseded",
            "approved_at": float(record.get("approved_at") or 0.0), "approved_by": str(record.get("approved_by") or "")}


def _section_of_task(text: str, task: builds.PlanTask) -> int:
    for s in sections_of(text):
        if builds.task_number_of(s.title) == task.number:
            return s.number
    return 0


def _progress_of(text: str) -> dict | None:
    tasks = builds.parse_plan(text)
    if not tasks:
        return None
    current = next((t for t in tasks if not t.done), None)
    return {"done": sum(1 for t in tasks if t.done), "total": len(tasks), "current": current.title if current else "",
            "current_section": _section_of_task(text, current) if current else 0,
            "steps_done": sum(t.steps_done for t in tasks), "steps_total": sum(t.steps_total for t in tasks)}


def _meta(project_path: str, path: Path, kind: str) -> dict | None:
    text = _read(path)
    if text is None:
        return None
    relative = f"{builds.SPEC_DIR if kind == 'spec' else builds.PLAN_DIR}/{path.name}"
    parsed = parse_document(text)
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    return {"path": relative, "kind": kind, "title": parsed.title or path.stem, "modified": modified,
            "sections": len(parsed.sections), "approval": approval_of(project_path, relative, text),
            "progress": _progress_of(text) if kind == "plan" else None}


def list_documents(project_path: str) -> list[dict]:
    out = []
    root = Path(project_path)
    for directory, kind in KIND_OF_DIR.items():
        try:
            found = sorted((root / directory).glob("*.md"))
        except OSError:
            continue
        for path in found:
            if path.is_file():
                m = _meta(project_path, path, kind)
                if m is not None:
                    out.append(m)
    out.sort(key=lambda d: d["modified"], reverse=True)
    return out


def read_document(project_path: str, relative: str) -> dict | None:
    path = resolve_document(project_path, relative)
    if path is None:
        return None
    text = _read(path)
    if text is None:
        return None
    relative = Path(relative).as_posix()
    kind = KIND_OF_DIR.get(Path(relative).parent.as_posix(), "spec")
    parsed = parse_document(text)
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0.0
    return {"path": relative, "kind": kind, "title": parsed.title or path.stem, "modified": modified,
            "preamble": parsed.preamble,
            "sections": [{"number": s.number, "title": s.title, "level": s.level, "body": s.body} for s in parsed.sections],
            "approval": approval_of(project_path, relative, text), "progress": _progress_of(text) if kind == "plan" else None}


def replace_section(project_path: str, relative: str, number: int, new_body: str, new_title: str | None = None) -> bool:
    """Reescribe el cuerpo (y opcionalmente el título) de la sección `number`. La aprobación queda superseded."""
    path = resolve_document(project_path, relative)
    if path is None:
        return False
    text = _read(path)
    if text is None:
        return False
    doc = parse_document(text)
    target = next((s for s in doc.sections if s.number == number), None)
    if target is None:
        return False
    lines = text.splitlines()
    heads = [(i, lvl, t) for i, lvl, t in _headings(text) if lvl == target.level]
    idx = [i for i, _, t in heads if t == target.title]
    if not idx:
        return False
    start = idx[0]
    nxt = [i for i, _, _ in heads if i > start]
    end = nxt[0] if nxt else len(lines)
    head_line = f"{'#' * target.level} {new_title or target.title}"
    new_lines = lines[:start] + [head_line, ""] + new_body.strip("\n").splitlines() + [""] + lines[end:]
    path.write_text("\n".join(new_lines).rstrip("\n") + "\n", encoding="utf-8")
    return True


def project_review(project_path: str) -> dict | None:
    """awaiting (algo espera tu aprobación) | planning | building | review (todo marcado, falta mirarlo)."""
    documents = list_documents(project_path)
    if not documents:
        return None
    plans = [d for d in documents if d["kind"] == "plan" and d["progress"]]
    plan = max(plans, key=lambda d: d["modified"]) if plans else None
    progress = plan["progress"] if plan else None
    pending = [d for d in documents if d["kind"] == "spec" and d["approval"]["state"] != "approved"]
    if pending:
        state = "awaiting"
    elif progress is None:
        state = "planning"
    elif progress["done"] >= progress["total"]:
        state = "review"
    else:
        state = "building"
    return {"state": state, "documents": documents, "plan": plan, "pending": [d["path"] for d in pending]}
