"""Rendimiento del grafo con conjuntos sintéticos (100, 1.000, 5.000 nodos), separados de la memoria personal.
Mide: creación de páginas, build_graph, tamaño JSON, búsqueda FTS y grafo local. Uso: python scripts/graph_bench.py [--sizes 100,1000,5000]"""
from __future__ import annotations

import argparse
import json
import random
import tempfile
import time
from pathlib import Path

from jarvis.memory.graph import build_graph, local_graph
from jarvis.memory.schema import MEMORY_TYPES, MemoryProposal, Relation
from jarvis.memory.store import MemoryService

TYPES = [t for t in MEMORY_TYPES if t != "source"]


def synth(n: int, root: Path) -> tuple[MemoryService, float]:
    """Escribe las páginas directamente (como haría una importación masiva) y reconstruye el índice una vez.
    La ruta normal `MemoryService.create` (bloqueo, versión, journal, index.md) cuesta ~0,4 s/página con 1.000 páginas;
    no es la vía prevista para cargas masivas."""
    from datetime import datetime
    from jarvis.memory.schema import Memory, TYPE_DIRS, new_id, slugify
    svc = MemoryService(root / "memory", root / "runtime")
    rnd = random.Random(42)
    ids: list[str] = []
    t = time.perf_counter()
    now = datetime.now().astimezone()
    for i in range(n):
        rels = []
        if ids:
            for _ in range(min(3, rnd.randint(0, 3))):
                rels.append(Relation(target=rnd.choice(ids), type=rnd.choice(["relacionado_con", "pertenece_a", "menciona", "decidido_para"]),
                                     status=rnd.choice(["explicit", "explicit", "suggested"])))
        mid = new_id("mem", now)
        while mid in ids:
            mid = new_id("mem", now)
        m = Memory(id=mid, type=rnd.choice(TYPES), title=f"Sintético {i} {rnd.choice(['alfa', 'beta', 'gamma', 'delta'])}",
                   created=now, updated=now, provenance="user_statement",
                   body=f"Nodo sintético número {i}. Palabras: {' '.join(rnd.choice(['motor', 'audio', 'memoria', 'grafo', 'voz', 'claude']) for _ in range(6))}",
                   project=f"p{rnd.randint(1, 8)}", tags=[f"t{rnd.randint(1, 20)}"], related=rels)
        path = svc.wiki / TYPE_DIRS[m.type] / f"{slugify(m.title)}-{m.id[-6:]}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(m.to_markdown(), encoding="utf-8")
        ids.append(mid)
    svc.rebuild_index()
    return svc, time.perf_counter() - t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="100,1000,5000")
    ap.add_argument("--keep", help="directorio donde conservar el conjunto (subcarpeta por tamaño) para servirlo con JARVIS_HOME")
    args = ap.parse_args()
    rows = []
    for n in [int(x) for x in args.sizes.split(",")]:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(args.keep) / f"n{n}" / "data" if args.keep else Path(tmp)
            svc, t_create = synth(n, Path(td))
            t = time.perf_counter(); g = build_graph(svc); t_graph = time.perf_counter() - t
            size = len(json.dumps(g))
            t = time.perf_counter(); hits = svc.search("memoria grafo"); t_search = time.perf_counter() - t
            center = g["nodes"][len(g["nodes"]) // 2]["id"]
            t = time.perf_counter(); lg = local_graph(svc, center, depth=2, max_nodes=100); t_local = time.perf_counter() - t
            rows.append({"nodes": n, "edges": len(g["edges"]), "write_and_index_s": round(t_create, 2), "build_graph_s": round(t_graph, 3),
                         "json_kb": size // 1024, "fts_search_ms": round(t_search * 1000, 1), "hits": len(hits),
                         "local_graph_s": round(t_local, 3), "local_nodes": len(lg["nodes"]), "truncated": lg["truncated"]})
            print(json.dumps(rows[-1]), flush=True)
    print("\n| nodos | aristas | escribir+indexar (s) | build_graph (s) | JSON (KB) | FTS (ms) | grafo local d=2 (s) |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['nodes']} | {r['edges']} | {r['write_and_index_s']} | {r['build_graph_s']} | {r['json_kb']} | {r['fts_search_ms']} | {r['local_graph_s']} ({r['local_nodes']} nodos{', truncado' if r['truncated'] else ''}) |")


if __name__ == "__main__":
    main()
