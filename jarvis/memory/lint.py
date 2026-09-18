"""Mantenimiento determinista de la wiki (sin modelo)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .schema import normalize_title
from .store import MemoryService


@dataclass
class LintIssue:
    kind: str
    severity: str  # error | warning | info
    id: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def lint(service: MemoryService) -> list[LintIssue]:
    issues: list[LintIssue] = []
    mems = service._scan(force=True)
    current = {i: m for i, m in mems.items() if m.is_current()}
    manifests = {s.id: s for s in service.sources()}

    # enlaces rotos
    for m in mems.values():
        for lid in m.all_links():
            if lid not in mems:
                issues.append(LintIssue("broken_link", "error", m.id, f"«{m.title}» enlaza a {lid}, que no existe"))
        if m.superseded_by and m.superseded_by not in mems:
            issues.append(LintIssue("broken_link", "error", m.id, f"superseded_by apunta a {m.superseded_by}, inexistente"))
    # fuentes inexistentes
    for m in mems.values():
        for s in m.sources:
            src = manifests.get(s.source_id)
            if not src:
                issues.append(LintIssue("missing_source", "error", m.id, f"«{m.title}» cita la fuente {s.source_id} sin manifiesto"))
            elif not (service.root / src.stored_path).exists():
                issues.append(LintIssue("missing_source", "error", m.id, f"el archivo de la fuente {s.source_id} no está en raw/"))
    # duplicados
    by_title: dict[tuple[str, str], list[str]] = {}
    for m in current.values():
        by_title.setdefault((m.type, normalize_title(m.title)), []).append(m.id)
    for (t, key), ids in by_title.items():
        if len(ids) > 1:
            issues.append(LintIssue("duplicate", "warning", ids[0], f"páginas duplicadas de tipo {t}: {', '.join(ids)}"))
    # reemplazados usados como actuales
    for m in mems.values():
        if m.superseded_by and m.status != "superseded":
            issues.append(LintIssue("superseded_as_current", "error", m.id, f"«{m.title}» tiene superseded_by pero estado {m.status}"))
        if m.status == "superseded" and m.superseded_by and mems.get(m.superseded_by) and not mems[m.superseded_by].is_current():
            issues.append(LintIssue("superseded_chain", "warning", m.id, f"«{m.title}» fue reemplazado por una página que ya no está vigente"))
    for m in current.values():
        for r in m.related:
            tgt = mems.get(r.target)
            if tgt and tgt.status == "superseded" and r.type != "reemplaza":
                issues.append(LintIssue("superseded_as_current", "warning", m.id,
                                        f"«{m.title}» se apoya en «{tgt.title}», que fue reemplazado por {tgt.superseded_by}"))
    # contradicciones sin resolver
    for m in current.values():
        if m.status == "disputed":
            issues.append(LintIssue("unresolved_contradiction", "warning", m.id, f"«{m.title}» está en disputa: {m.change_reason or ''}"))
        for r in m.related:
            if r.type == "contradice" and r.target in current:
                issues.append(LintIssue("unresolved_contradiction", "warning", m.id,
                                        f"«{m.title}» contradice a «{current[r.target].title}» y ambas siguen vigentes"))
    # páginas sin referencias cuando deberían tenerlas
    for m in current.values():
        if m.type in ("fact", "source") and not m.sources and m.provenance != "user_statement":
            issues.append(LintIssue("missing_references", "warning", m.id,
                                    f"«{m.title}» ({m.type}, {m.provenance}) no cita ninguna fuente"))
        if m.provenance == "assistant_inference" and m.type in ("fact", "decision"):
            issues.append(LintIssue("inference_as_fact", "info", m.id, f"«{m.title}» es una inferencia del asistente, no un hecho confirmado"))
    # huérfanas
    linked: set[str] = set()
    for m in current.values():
        linked.update(m.all_links())
    for m in current.values():
        if m.type not in ("preference",) and not m.all_links() and m.id not in linked and not m.sources:
            issues.append(LintIssue("orphan", "info", m.id, f"«{m.title}» no tiene conexiones ni fuentes"))
    # índice desactualizado
    stale = service.index.stale_entries(list(mems.values()))
    if stale:
        issues.append(LintIssue("stale_index", "warning", None, f"índice desactualizado en {len(stale)} páginas (ejecuta memory lint --fix o rebuild)"))
    return issues
