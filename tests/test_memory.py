import json
import sqlite3
import threading
from pathlib import Path

import pytest

from jarvis.memory.ingest import ingest_file, ingest_text, locate_quote
from jarvis.memory.lint import lint
from jarvis.memory.graph import build_graph, local_graph
from jarvis.memory.retrieval import retrieve, find_citations
from jarvis.memory.schema import MemoryProposal, SourceRef, Relation
from jarvis.memory.store import MemoryService, DuplicateMemory, ConflictError, NotFound


def prop(title="Prefiero respuestas cortas", body="El usuario prefiere respuestas cortas.", **kw):
    kw.setdefault("type", "preference")
    kw.setdefault("provenance", "user_statement")
    return MemoryProposal(title=title, body=body, **kw)


def test_persistence_after_restart(mem_dir):
    s1 = MemoryService(mem_dir / "memory", mem_dir / "runtime")
    m = s1.create(prop())
    assert m.path and m.path.exists()
    s2 = MemoryService(mem_dir / "memory", mem_dir / "runtime")
    got = s2.get(m.id)
    assert got and got.title == "Prefiero respuestas cortas" and got.provenance == "user_statement"
    hits = s2.search("respuestas cortas")
    assert hits and hits[0].id == m.id


def test_duplicate_detection(service):
    service.create(prop())
    with pytest.raises(DuplicateMemory):
        service.create(prop())


def test_source_dedup_and_references(service, tmp_path):
    doc = tmp_path / "notas.md"
    doc.write_text("# Proyecto asistente\n\nLa entrega es el viernes 12.\n", encoding="utf-8")
    r1 = ingest_file(service, doc)
    r2 = ingest_file(service, doc)
    assert not r1.duplicate and r2.duplicate and r1.source.id == r2.source.id
    assert len(service.sources()) == 1
    assert (service.root / r1.source.stored_path).exists()
    loc = locate_quote(r1.text, "La entrega es el viernes 12.")
    assert loc and loc.startswith("líneas")
    m = service.create(MemoryProposal(type="fact", title="Fecha de entrega", body="La entrega es el viernes 12.",
                                      provenance="source_extraction",
                                      sources=[SourceRef(source_id=r1.source.id, locator=loc, quote="La entrega es el viernes 12.")]))
    back = service.get(m.id)
    assert back.sources[0].locator == loc and back.sources[0].quote
    assert m.id in service.source_get(r1.source.id).derived_memories
    with pytest.raises(Exception):
        service.create(MemoryProposal(type="fact", title="Sin fuente", body="x", provenance="source_extraction"))


def test_temporal_correction_supersedes(service):
    old = service.create(MemoryProposal(type="fact", title="Fecha de la demo", body="La demo es el jueves."))
    new = service.supersede(old.id, MemoryProposal(type="fact", title="Fecha de la demo", body="La demo es el viernes."),
                            reason="el usuario corrigió la fecha")
    o, n = service.get(old.id), service.get(new.id)
    assert o.status == "superseded" and o.superseded_by == n.id
    assert n.supersedes == o.id and any(r.type == "reemplaza" and r.target == o.id for r in n.related)
    ids = [h.id for h in service.search("demo")]
    assert n.id in ids and o.id not in ids
    assert o.id in [h.id for h in service.search("demo", include_hidden=True)]
    assert any(e["op"] == "supersede" for e in service.journal(n.id))
    assert service.versions(old.id)


def test_index_rebuild(mem_dir):
    s = MemoryService(mem_dir / "memory", mem_dir / "runtime")
    m = s.create(prop("Mi ciudad", "Vivo en Guadalajara.", type="fact"))
    s.index.close()
    db = mem_dir / "runtime" / "indexes" / "memory.sqlite"
    db.unlink()
    s2 = MemoryService(mem_dir / "memory", mem_dir / "runtime")
    assert s2.index.count() == 1
    assert s2.search("Guadalajara")[0].id == m.id
    (mem_dir / "memory" / "wiki" / "facts").mkdir(exist_ok=True)
    assert s2.rebuild_index() == 1


def test_interrupted_write_recovery(mem_dir):
    s = MemoryService(mem_dir / "memory", mem_dir / "runtime")
    m = s.create(prop())
    original = m.path.read_text()
    tmp = m.path.with_name(m.path.name + ".tmp")
    tmp.write_text("---\nbasura parcial")
    s2 = MemoryService(mem_dir / "memory", mem_dir / "runtime")
    assert not tmp.exists()
    assert s2.get(m.id).path.read_text() == original


def test_concurrent_writers(mem_dir):
    s = MemoryService(mem_dir / "memory", mem_dir / "runtime")
    errors = []

    def worker(n):
        svc = MemoryService(mem_dir / "memory", mem_dir / "runtime")
        for i in range(8):
            try:
                svc.create(prop(f"Nota {n}-{i}", f"cuerpo {n} {i}", type="note"))
            except Exception as e:
                errors.append(e)

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errors
    assert len(s.list()) == 24 and s.version() == 24


def test_optimistic_conflict(service):
    m = service.create(prop())
    service.update(m.id, body="nuevo", reason="cambio 1")
    with pytest.raises(ConflictError):
        service.update(m.id, body="otro", reason="cambio 2", expected_updated=m.updated)


def test_secret_rejected(service):
    with pytest.raises(Exception):
        service.create(prop("Clave", "api_key: sk-ant-abcdefghijklmnop", type="note"))


def test_forget_scope(service):
    a = service.create(prop("Recuerdo A", "contenido A", type="note"))
    b = service.create(MemoryProposal(type="note", title="Recuerdo B", body=f"B enlaza a [[{a.id}]]",
                                      related=[Relation(target=a.id, type="relacionado_con")]))
    service.update(a.id, body="contenido A v2", reason="prueba")
    plan = service.plan_forget(a.id)
    assert plan.referencing[0]["id"] == b.id and plan.versions
    report = service.forget(a.id)
    assert service.get(a.id) is None
    assert not service.search("contenido")
    assert not (service.archive / a.id).exists()
    b2 = service.get(b.id)
    assert not b2.related and a.id not in b2.body and "Recuerdo A" in b2.body
    assert b.id in report.updated_pages
    with pytest.raises(NotFound):
        service.plan_forget(a.id)


def test_lint_detects_broken_link_and_stale_index(service):
    a = service.create(prop("Página", "cuerpo", type="note"))
    text = a.path.read_text().replace("cuerpo", "cuerpo con [[mem_20200101_abcdef]]")
    a.path.write_text(text)
    kinds = {i.kind for i in lint(service)}
    assert "broken_link" in kinds and "stale_index" in kinds


def test_retrieval_budget_and_trace(service):
    p = service.create(prop())
    f = service.create(MemoryProposal(type="project", title="Asistente Jarvis", body="Proyecto del asistente de voz.", project="jarvis"))
    ctx, trace = retrieve(service, "qué sabes del asistente", "turn1", budget_chars=3000)
    ids = {r.id for r in trace.retrieved}
    assert p.id in ids and f.id in ids
    assert trace.total_chars <= 3000 and "DATOS" in ctx
    assert find_citations(f"Según [mem:{f.id}] y [[{p.id}]]") == [f.id, p.id]


def test_graph_projection(service):
    a = service.create(prop("A", "a", type="concept"))
    b = service.create(MemoryProposal(type="decision", title="B", body=f"ver [[{a.id}]]",
                                      related=[Relation(target=a.id, type="decidido_para")]))
    g = build_graph(service)
    assert {n["id"] for n in g["nodes"]} == {a.id, b.id}
    types = {e["type"] for e in g["edges"]}
    assert types == {"decidido_para", "menciona"}
    lg = local_graph(service, a.id, depth=1, max_nodes=1)
    assert lg["truncated"] and len(lg["nodes"]) == 1
