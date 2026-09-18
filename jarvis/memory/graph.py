"""Proyección de la memoria como grafo. No es una segunda fuente de verdad."""
from __future__ import annotations

from collections import deque
from typing import Any

from .store import MemoryService


def build_graph(service: MemoryService, include_hidden: bool = False, include_sources: bool = True) -> dict[str, Any]:
    mems = {m.id: m for m in service.list(include_hidden=True)}
    visible = {i for i, m in mems.items() if include_hidden or m.is_current()}
    nodes: dict[str, dict[str, Any]] = {i: service.node_summary(mems[i]) for i in visible}
    edges: dict[str, dict[str, Any]] = {}

    def add_edge(src: str, dst: str, typ: str, status: str, provenance: str | None, kind: str, rationale: str | None = None) -> None:
        if src not in nodes or dst not in nodes or src == dst:
            return
        eid = f"{src}->{dst}:{typ}"
        if eid in edges:
            return
        edges[eid] = {"id": eid, "source": src, "target": dst, "type": typ, "status": status,
                      "provenance": provenance, "kind": kind, "rationale": rationale}

    for i in visible:
        m = mems[i]
        for r in m.related:
            add_edge(i, r.target, r.type, r.status, r.provenance, "semantic", r.rationale)
        for lid in m.links_in_body():
            add_edge(i, lid, "menciona", "explicit", "enlace en el texto", "semantic")
        if m.supersedes:
            add_edge(i, m.supersedes, "reemplaza", "explicit", "servicio de memoria", "semantic")
    if include_sources:
        for i in visible:
            for s in mems[i].sources:
                sid = s.source_id
                if sid not in nodes:
                    src = service.source_get(sid)
                    if not src:
                        continue
                    nodes[sid] = {"id": sid, "title": src.title, "type": "source_doc", "status": "active",
                                  "summary": f"{src.kind}: {src.original_name} ({src.text_chars} caracteres)",
                                  "created": src.imported_at.isoformat(), "updated": src.imported_at.isoformat(),
                                  "path": src.stored_path, "provenance": "source", "project": None, "tags": [],
                                  "has_sources": False, "sensitivity": "normal", "degree": 0}
                add_edge(i, sid, "fundamentado_en", "explicit", s.locator or "documento", "source")
    for e in edges.values():
        nodes[e["source"]]["degree"] += 1
        nodes[e["target"]]["degree"] += 1
    return {"version": service.version(), "nodes": list(nodes.values()), "edges": list(edges.values())}


def local_graph(service: MemoryService, center: str, depth: int = 1, max_nodes: int = 60,
                include_hidden: bool = False) -> dict[str, Any]:
    full = build_graph(service, include_hidden=include_hidden)
    adj: dict[str, set[str]] = {}
    for e in full["edges"]:
        adj.setdefault(e["source"], set()).add(e["target"])
        adj.setdefault(e["target"], set()).add(e["source"])
    keep = {center}
    frontier = deque([(center, 0)])
    truncated = False
    while frontier:
        nid, d = frontier.popleft()
        if d >= depth:
            continue
        for nb in sorted(adj.get(nid, ())):
            if nb in keep:
                continue
            if len(keep) >= max_nodes:
                truncated = True
                break
            keep.add(nb)
            frontier.append((nb, d + 1))
    nodes = [n for n in full["nodes"] if n["id"] in keep]
    edges = [e for e in full["edges"] if e["source"] in keep and e["target"] in keep]
    return {"version": full["version"], "center": center, "depth": depth, "truncated": truncated,
            "nodes": nodes, "edges": edges}


def trace_graph(service: MemoryService, trace: dict[str, Any]) -> dict[str, Any]:
    ids = [p["id"] for p in trace.get("retrieved", [])] + list(trace.get("consulted", []))
    ids = list(dict.fromkeys(ids))
    full = build_graph(service, include_hidden=True)
    cited = set(trace.get("cited", []))
    nodes = []
    for n in full["nodes"]:
        if n["id"] in ids:
            n = dict(n)
            n["cited"] = n["id"] in cited
            n["retrieval_reason"] = next((p["reason"] for p in trace.get("retrieved", []) if p["id"] == n["id"]), "consulted")
            nodes.append(n)
    keep = {n["id"] for n in nodes}
    edges = [e for e in full["edges"] if e["source"] in keep and e["target"] in keep]
    return {"version": full["version"], "turn_id": trace.get("turn_id"), "nodes": nodes, "edges": edges,
            "query": trace.get("query")}
