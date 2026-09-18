"""Evaluación de memoria con preguntas conocidas: mide recuperación, atribución y ausencia de afirmaciones sin respaldo.
Usa un directorio de datos TEMPORAL (no toca la memoria personal) y Claude real (coste pequeño). Uso:
  JARVIS_EVAL=1 python scripts/memory_eval.py [--demo]"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import tempfile
from pathlib import Path

from jarvis.app import build_app
from jarvis.config import Config
from jarvis.memory.ingest import ingest_file
from jarvis.memory.schema import MemoryProposal, SourceRef

DOC = """# Notas de evaluación (documento sintético)

La reunión de arranque del proyecto Faro será el martes 15 de septiembre de 2026 a las 10:00.
El presupuesto aprobado para Faro es de 12.000 euros.
Contacto del proveedor de sensores: Lucía Ortega.
"""

CASES = [
    # (pregunta, ids esperados citados (clave), palabras que deben aparecer, palabras que NO deben aparecer)
    {"q": "¿Qué preferencias mías conoces?", "expect": ["pref"], "must": ["corta"], "must_not": []},
    {"q": "¿Cuándo es la reunión de arranque de Faro y de dónde lo sacaste?", "expect": ["reunion"], "must": ["15", "documento"], "must_not": []},
    {"q": "¿Cuál es el presupuesto de Faro?", "expect": ["presupuesto"], "must": ["12"], "must_not": []},
    {"q": "¿Quién es el contacto del proveedor de sensores?", "expect": ["contacto"], "must": ["Lucía"], "must_not": []},
    {"q": "¿Cuál es mi color favorito?", "expect": [], "must": [], "must_not": ["azul", "rojo", "verde", "amarillo", "negro", "blanco"],
     "unsupported_ok": True},
    {"q": "¿Qué sabes del proyecto Faro?", "expect": ["reunion", "presupuesto"], "must": ["Faro"], "must_not": []},
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as td:
        cfg = Config()
        cfg.memory.dir = Path(td) / "memory"
        cfg.memory.runtime_dir = Path(td) / "runtime"
        cfg.tools.workspace_dir = Path(td) / "ws"
        cfg.logging.dir = Path(td) / "logs"
        cfg.expand()
        app = build_app(cfg, demo=args.demo)
        svc = app.service
        ids = {}
        ids["pref"] = svc.create(MemoryProposal(type="preference", title="Prefiere respuestas cortas", body="El usuario prefiere respuestas cortas.")).id
        doc = Path(td) / "eval_doc.md"
        doc.write_text(DOC, encoding="utf-8")
        src = ingest_file(svc, doc).source
        ids["reunion"] = svc.create(MemoryProposal(type="fact", title="Reunión de arranque de Faro", body="La reunión de arranque del proyecto Faro será el martes 15 de septiembre de 2026 a las 10:00.",
                                                   provenance="source_extraction", project="faro",
                                                   sources=[SourceRef(source_id=src.id, locator="líneas 3-3", quote="La reunión de arranque del proyecto Faro será el martes 15 de septiembre de 2026 a las 10:00.")])).id
        ids["presupuesto"] = svc.create(MemoryProposal(type="fact", title="Presupuesto de Faro", body="El presupuesto aprobado para Faro es de 12.000 euros.", provenance="source_extraction", project="faro",
                                                       sources=[SourceRef(source_id=src.id, locator="líneas 4-4", quote="El presupuesto aprobado para Faro es de 12.000 euros.")])).id
        ids["contacto"] = svc.create(MemoryProposal(type="person", title="Lucía Ortega", body="Contacto del proveedor de sensores del proyecto Faro.", provenance="source_extraction", project="faro",
                                                    sources=[SourceRef(source_id=src.id, locator="líneas 5-5", quote="Contacto del proveedor de sensores: Lucía Ortega.")])).id
        if not app.demo:
            await app.provider.start()
        results = []
        for case in CASES:
            r = await app.orchestrator.handle_text(case["q"])
            text = r.assistant_text
            expected = {ids[k] for k in case["expect"]}
            retrieved = {p["id"] for p in app.orchestrator.last_trace["retrieved"]} | set(app.orchestrator.last_trace["consulted"])
            cited = set(r.cited)
            recall = len(expected & retrieved) / len(expected) if expected else 1.0
            attribution = len(expected & cited) / len(expected) if expected else 1.0
            must_ok = all(w.lower() in text.lower() for w in case["must"])
            unsupported = any(w in text.lower() for w in case["must_not"])
            honest = (not unsupported) and (("no" in text.lower()) if case.get("unsupported_ok") else True)
            results.append({"q": case["q"], "recall": recall, "attribution": attribution, "must_ok": must_ok,
                            "unsupported_claim": unsupported, "honest_when_unknown": honest, "cost": r.cost_usd,
                            "answer": text[:200]})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        if not app.demo:
            await app.provider.stop()
        n = len(results)
        summary = {"cases": n, "recall_avg": round(sum(x["recall"] for x in results) / n, 2),
                   "attribution_avg": round(sum(x["attribution"] for x in results) / n, 2),
                   "content_ok": sum(x["must_ok"] for x in results), "unsupported_claims": sum(x["unsupported_claim"] for x in results),
                   "total_cost_usd": round(sum(x["cost"] or 0 for x in results), 4)}
        print("RESUMEN", json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
