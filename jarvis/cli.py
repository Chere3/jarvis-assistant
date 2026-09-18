"""Interfaz de línea de comandos: doctor, chat, listen, serve, config, memory."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import Config, config_dump_safe, config_path, load_config, save_config


def _print(*a, **k):
    print(*a, **k, flush=True)


# ----------------------------------------------------------------------------- chat
async def _chat(args: argparse.Namespace) -> int:
    from .app import build_app
    speaker = None
    if args.speak:
        from .audio.tts import make_tts
        speaker = make_tts(load_config())
    app = build_app(demo=args.demo, speaker=speaker, speak_responses=bool(args.speak), resume=args.resume)
    orch = app.orchestrator
    name = app.cfg.assistant.display_name
    if app.demo:
        _print("*** MODO DEMO: las respuestas son simuladas, no provienen de Claude ***")
    else:
        _print(f"Conectando con Claude (Agent SDK)…")
        try:
            await app.provider.start()
        except Exception as e:
            _print(f"No se pudo conectar con Claude: {e}\nEjecuta `jarvis doctor` para revisar la autenticación.")
            return 2
    _print(f"{name} listo. Sesión {orch.session_id}. Comandos: /salir /cancelar /aprobar <id> /rechazar <id> /estado /traza /memoria <id>")
    streaming_turn: dict[str, str | None] = {"id": None}

    def on_event(ev: dict) -> None:
        k = ev["kind"]
        if k == "chat.text_delta":
            if streaming_turn["id"] != ev["turn_id"]:
                streaming_turn["id"] = ev["turn_id"]
                _print(f"{name}: ", end="")
            _print(ev["text"], end="")
        elif k == "chat.tool_use":
            _print(f"\n  [herramienta: {ev['name']}]", end="")
        elif k == "approval.requested":
            a = ev["approval"]
            _print(f"\n  >> APROBACIÓN PENDIENTE {a['id']}: {a['description']}\n     escribe /aprobar {a['id']} o /rechazar {a['id']} (vence en {app.cfg.tools.approval_ttl_s}s)")
        elif k == "chat.system":
            _print(f"\n[{name}] {ev['text']}")
        elif k == "state" and args.verbose:
            _print(f"\n  (estado: {ev['state']})")

    app.bus.subscribe(on_event)
    loop = asyncio.get_running_loop()
    if args.message:
        r = await orch.handle_text(args.message)
        _finish_line(r, streaming_turn, name)
        await app.provider.stop()
        return 0
    task: asyncio.Task | None = None
    while True:
        try:
            line = await loop.run_in_executor(None, lambda: input("\ntú> " if not task or task.done() else ""))
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if task and not task.done():
            if line in ("", "/cancelar"):
                rep = await orch.cancel()
                _print(f"[cancelado] {rep}")
                continue
        if line == "/salir":
            break
        if line == "/cancelar":
            _print(json.dumps(await orch.cancel(), ensure_ascii=False))
            continue
        if line == "/estado":
            _print(json.dumps(orch.snapshot(), ensure_ascii=False, indent=1))
            continue
        if line == "/traza":
            _print(json.dumps(orch.last_trace, ensure_ascii=False, indent=1) if orch.last_trace else "sin traza")
            continue
        if line.startswith("/memoria "):
            m = app.service.get(line.split(" ", 1)[1].strip())
            _print(m.to_markdown() if m else "no existe")
            continue
        if not line:
            continue

        async def run(text: str) -> None:
            r = await orch.handle_text(text)
            _finish_line(r, streaming_turn, name)

        task = asyncio.create_task(run(line))
        await task
    if task and not task.done():
        await orch.cancel("salida")
    await app.provider.stop()
    _print("Hasta luego.")
    return 0


def _finish_line(r, streaming_turn, name):
    if r.turn_id == "cmd":
        return
    if streaming_turn["id"] != r.turn_id:
        _print(f"{name}: {r.display_text}", end="")
    elif r.display_text and r.display_text.startswith("["):
        _print(f" {r.display_text}", end="")
    streaming_turn["id"] = None
    meta = []
    if r.cited:
        meta.append("fuentes: " + ", ".join(r.cited))
    if r.cost_usd is not None:
        meta.append(f"coste ~${r.cost_usd:.4f}")
    if r.model:
        meta.append(r.model)
    if r.cancelled:
        meta.append("CANCELADO")
    _print(("\n  · " + " · ".join(meta)) if meta else "")


# ----------------------------------------------------------------------------- config
def _config(args: argparse.Namespace) -> int:
    path = config_path()
    if args.action == "init":
        if path.exists() and not args.force:
            _print(f"ya existe {path} (usa --force para sobrescribir)")
            return 1
        save_config(Config().expand(), path)
        _print(f"configuración creada en {path}")
        return 0
    cfg = load_config()
    if args.action == "show":
        _print(json.dumps(config_dump_safe(cfg), ensure_ascii=False, indent=1, default=str))
        return 0
    if args.action == "set":
        key, value = args.key, args.value
        data = cfg.model_dump(mode="json")
        node = data
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = value
        node[parts[-1]] = parsed
        new = Config.model_validate(data).expand()
        save_config(new, path)
        _print(f"{key} = {parsed!r} guardado en {path}")
        if key == "assistant.display_name":
            _print("Nota: cambiar el nombre visible NO cambia la palabra de activación acústica "
                   f"(wake_word.keyword_label = {new.wake_word.keyword_label!r}, modelo {new.wake_word.model_path!r}).")
        return 0
    return 1


# ----------------------------------------------------------------------------- memory
def _memory(args: argparse.Namespace) -> int:
    from .memory.store import MemoryService
    cfg = load_config()
    svc = MemoryService(cfg.memory.dir, cfg.memory.runtime_dir, cfg.assistant.timezone)
    if args.maction == "search":
        hits = svc.search(" ".join(args.query), limit=args.limit, include_hidden=args.all)
        for h in hits:
            _print(f"{h.id}  [{h.type}/{h.status}] {h.title}\n    {h.snippet}")
        if not hits:
            _print("sin resultados")
        return 0
    if args.maction == "show":
        m = svc.get(args.id)
        _print(m.to_markdown() if m else "no existe")
        return 0 if m else 1
    if args.maction == "list":
        for m in svc.list(include_hidden=args.all, types=[args.type] if args.type else None):
            _print(f"{m.id}  [{m.type}/{m.status}] {m.title}")
        return 0
    if args.maction == "ingest":
        from .memory.ingest import ingest_file
        res = ingest_file(svc, Path(args.path), title=args.title, max_bytes=cfg.memory.max_source_bytes)
        s = res.source
        _print(f"{'YA EXISTÍA' if res.duplicate else 'importado'}: {s.id} «{s.title}» ({s.text_chars} caracteres, sha {s.sha256[:12]})")
        if s.needs_ocr:
            _print("AVISO: el PDF parece escaneado (sin texto extraíble); requiere OCR, que no está incluido.")
        if args.extract and not s.needs_ocr:
            from .agent.extract import extract_from_source
            n = asyncio.run(extract_from_source(cfg, svc, s.id, max_items=args.max_items, demo=args.demo))
            _print(f"recuerdos creados a partir de la fuente: {n}")
        return 0
    if args.maction == "lint":
        from .memory.lint import lint
        issues = lint(svc)
        for i in issues:
            _print(f"[{i.severity}] {i.kind}: {i.message}")
        if args.fix and any(i.kind == "stale_index" for i in issues):
            _print(f"índice reconstruido: {svc.rebuild_index()} páginas")
        if args.semantic:
            from .agent.review import semantic_review
            for line in asyncio.run(semantic_review(cfg, svc, max_pages=args.max_pages)):
                _print(f"[claude] {line}")
        _print(f"{len(issues)} incidencias")
        return 0
    if args.maction == "rebuild":
        _print(f"índice reconstruido: {svc.rebuild_index()} páginas")
        return 0
    if args.maction == "forget":
        plan = svc.plan_forget(args.id)
        _print(f"Se eliminará «{plan.title}» ({plan.memory_id}):\n  página: {plan.page}\n  versiones: {len(plan.versions)}")
        for r in plan.referencing:
            _print(f"  referencia en {r['id']} «{r['title']}» ({r['how']}) → se quitará")
        for s in plan.sources:
            _print(f"  fuente exclusiva {s['id']} «{s['title']}» → {'se borrará' if args.delete_sources else 'se conserva (usa --delete-sources)'}")
        for n in plan.notes:
            _print(f"  nota: {n}")
        if not args.yes:
            if input("¿Confirmas? [s/N] ").strip().lower() not in ("s", "si", "sí"):
                _print("cancelado")
                return 1
        rep = svc.forget(args.id, delete_sources=args.delete_sources)
        _print(f"eliminado. archivos: {rep.removed_files}; páginas actualizadas: {rep.updated_pages}; fuentes borradas: {rep.removed_sources}; se conserva: {rep.kept}")
        return 0
    if args.maction == "export":
        dest = Path(args.dest).expanduser()
        shutil.make_archive(str(dest.with_suffix("")), "zip", root_dir=svc.root)
        _print(f"exportado a {dest.with_suffix('.zip')}")
        return 0
    if args.maction == "stats":
        _print(json.dumps(svc.stats(), ensure_ascii=False, indent=1))
        return 0
    if args.maction == "wipe":
        _print(f"Esto borra TODA la memoria en {svc.root} y los índices en {svc.runtime_dir}.")
        if not args.yes and input("Escribe BORRAR para confirmar: ").strip() != "BORRAR":
            _print("cancelado")
            return 1
        shutil.rmtree(svc.root, ignore_errors=True)
        shutil.rmtree(svc.runtime_dir / "indexes", ignore_errors=True)
        _print("memoria borrada (las copias de seguridad externas no se tocan).")
        return 0
    return 1


# ----------------------------------------------------------------------------- tts
def _tts(args: argparse.Namespace) -> int:
    from .audio import tts_kokoro
    from .audio.tts import list_spanish_voices, make_tts
    cfg = load_config()
    if args.taction == "download":
        tts_kokoro.download(_print)
        return 0
    if args.taction == "voices":
        _print("Kokoro (neuronal, local):", "instalado" if tts_kokoro.is_installed() else "NO descargado (jarvis tts download)")
        for k, v in tts_kokoro.SPANISH_VOICES.items():
            _print(f"  {k}  {v}")
        _print("Voces del sistema (say) en español:")
        for v in list_spanish_voices():
            _print(f"  {v['name']}  {v['locale']}")
        _print(f"Configurado: provider={cfg.tts.provider} voice={cfg.tts.voice}")
        return 0
    if args.taction == "say":
        text = " ".join(args.text)
        if args.voice:
            cfg.tts.voice = args.voice
            if args.voice in tts_kokoro.SPANISH_VOICES:
                cfg.tts.provider = "kokoro"
            else:
                cfg.tts.provider = "macos_say"
        tts = make_tts(cfg)
        if args.out:
            if not hasattr(tts, "synth_to_wav"):
                _print("--out solo con voces Kokoro")
                return 1
            tts.synth_to_wav(text, Path(args.out).expanduser())
            _print(f"guardado en {args.out} (síntesis {tts.last_synthesis_s:.2f}s)")
            return 0
        asyncio.run(tts.speak(text))
        _print(json.dumps(tts.status(), ensure_ascii=False))
        return 0
    return 1


# ----------------------------------------------------------------------------- doctor
def _doctor(args: argparse.Namespace) -> int:
    from .runtime.doctor import run_doctor
    ok = run_doctor(live=args.live)
    return 0 if ok else 1


# ----------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jarvis", description="Asistente personal de escritorio")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("chat", help="conversación por texto en la terminal")
    c.add_argument("-m", "--message", help="una sola petición y salir")
    c.add_argument("--demo", action="store_true", help="proveedor simulado (sin Claude)")
    c.add_argument("--speak", action="store_true", help="leer las respuestas en voz alta")
    c.add_argument("--resume", action="store_true", help="continuar la última sesión del SDK")
    c.add_argument("-v", "--verbose", action="store_true")

    l = sub.add_parser("listen", help="modo voz: palabra de activación y pulsar-para-hablar")
    l.add_argument("--ptt-only", action="store_true", help="sin detector de palabra clave")
    l.add_argument("--demo", action="store_true")
    l.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("serve", help="panel local en el navegador (y voz opcional)")
    s.add_argument("--listen", action="store_true", help="activar también la escucha por voz")
    s.add_argument("--demo", action="store_true")
    s.add_argument("--open", action="store_true", help="abrir el navegador")
    s.add_argument("--port", type=int)
    s.add_argument("--parent-pid", type=int, help="termina cuando ese proceso (la app) deje de existir")

    d = sub.add_parser("doctor", help="comprueba dependencias, configuración, dispositivos y conectividad")
    d.add_argument("--live", action="store_true", help="hace una petición real a Claude para validar modelo y autenticación")

    cf = sub.add_parser("config", help="ver o cambiar la configuración")
    cfs = cf.add_subparsers(dest="action", required=True)
    cfs.add_parser("show")
    ci = cfs.add_parser("init")
    ci.add_argument("--force", action="store_true")
    cs = cfs.add_parser("set")
    cs.add_argument("key")
    cs.add_argument("value")

    m = sub.add_parser("memory", help="operaciones sobre la memoria")
    ms = m.add_subparsers(dest="maction", required=True)
    q = ms.add_parser("search"); q.add_argument("query", nargs="+"); q.add_argument("--limit", type=int, default=10); q.add_argument("--all", action="store_true")
    sh = ms.add_parser("show"); sh.add_argument("id")
    li = ms.add_parser("list"); li.add_argument("--type"); li.add_argument("--all", action="store_true")
    ig = ms.add_parser("ingest"); ig.add_argument("path"); ig.add_argument("--title"); ig.add_argument("--extract", action="store_true", help="extraer recuerdos con Claude"); ig.add_argument("--max-items", type=int, default=8); ig.add_argument("--demo", action="store_true")
    ln = ms.add_parser("lint"); ln.add_argument("--fix", action="store_true"); ln.add_argument("--semantic", action="store_true", help="revisión adicional con Claude (con presupuesto)"); ln.add_argument("--max-pages", type=int, default=20)
    ms.add_parser("rebuild")
    fg = ms.add_parser("forget"); fg.add_argument("id"); fg.add_argument("--delete-sources", action="store_true"); fg.add_argument("-y", "--yes", action="store_true")
    ex = ms.add_parser("export"); ex.add_argument("dest")
    ms.add_parser("stats")
    wp = ms.add_parser("wipe"); wp.add_argument("-y", "--yes", action="store_true")

    tt = sub.add_parser("tts", help="voz: descargar el modelo neuronal, listar voces, probar")
    tts_sub = tt.add_subparsers(dest="taction", required=True)
    tts_sub.add_parser("download")
    tts_sub.add_parser("voices")
    tsay = tts_sub.add_parser("say"); tsay.add_argument("text", nargs="+"); tsay.add_argument("--voice"); tsay.add_argument("--out", help="guardar wav en vez de reproducir")

    ap_ = sub.add_parser("app", help="app nativa flotante para macOS (orbe + barra de menús + atajo ⌥Espacio)")
    ap_.add_argument("action", choices=["install", "build", "open", "uninstall"])
    ap_.add_argument("--open", action="store_true", help="abrir tras instalar")

    la = sub.add_parser("launchagent", help="inicio automático en macOS (instalar/desinstalar explícitamente)")
    la.add_argument("action", choices=["install", "uninstall", "status"])

    args = p.parse_args(argv)
    if args.cmd == "chat":
        return asyncio.run(_chat(args))
    if args.cmd == "listen":
        from .audio.voice_loop import run_listen
        return asyncio.run(run_listen(args))
    if args.cmd == "serve":
        from .ui.server import run_serve
        return asyncio.run(run_serve(args))
    if args.cmd == "doctor":
        return _doctor(args)
    if args.cmd == "config":
        return _config(args)
    if args.cmd == "memory":
        return _memory(args)
    if args.cmd == "tts":
        return _tts(args)
    if args.cmd == "app":
        from .runtime.macapp import run_app
        return run_app(args.action, open_after=args.open)
    if args.cmd == "launchagent":
        from .runtime.launchagent import run_launchagent
        return run_launchagent(args.action)
    return 1


if __name__ == "__main__":
    sys.exit(main())
