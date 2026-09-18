"""Panel local: FastAPI en loopback, token por ejecución, SSE para eventos, sin CORS abierto."""
from __future__ import annotations

import asyncio
import fcntl
import json
import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import Config, config_dump_safe, save_config
from ..memory.graph import build_graph, local_graph, trace_graph
from ..memory.lint import lint
from ..memory.schema import MEMORY_TYPES, MemoryProposal
from ..memory.store import MemoryError, NotFound

STATIC_DIR = Path(__file__).parent / "static"
EDITABLE_KEYS = {"assistant.display_name", "wake_word.sensitivity", "wake_word.keyword_label", "tts.voice", "tts.rate",
                 "tts.enabled", "audio.input_device", "audio.end_silence_ms", "audio.follow_up_window_s",
                 "memory.auto_save_preferences", "memory.save_conversation_summaries"}


class ChatIn(BaseModel):
    text: str


class ApproveIn(BaseModel):
    args_hash: str | None = None


class ForgetIn(BaseModel):
    confirm: bool = False
    delete_sources: bool = False


class CorrectIn(BaseModel):
    mode: str = "supersede"
    new_content: str | None = None
    new_title: str | None = None
    reason: str = "corrección desde el panel"


class RelationIn(BaseModel):
    source: str
    target: str
    type: str = "relacionado_con"


class ListeningIn(BaseModel):
    enabled: bool


class ConfigIn(BaseModel):
    key: str
    value: Any


def create_web_app(app: Any, voice: Any, token: str) -> FastAPI:
    web = FastAPI(title="Jarvis panel", docs_url=None, redoc_url=None, openapi_url=None)
    orch = app.orchestrator
    svc = app.service
    cfg: Config = app.cfg
    layout_path = cfg.memory.runtime_dir / "ui" / "layout.json"
    layout_path.parent.mkdir(parents=True, exist_ok=True)
    origin_ok = {f"http://{cfg.ui.host}:{cfg.ui.port}", f"http://localhost:{cfg.ui.port}"}

    @web.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and origin not in origin_ok:
                return JSONResponse({"error": "origen no permitido"}, status_code=403)
            supplied = (request.headers.get("x-jarvis-token") or request.query_params.get("token")
                        or request.cookies.get("jarvis_token") or "")
            if not supplied or not secrets.compare_digest(supplied, token):
                return JSONResponse({"error": "token inválido"}, status_code=401)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if path == "/" or path.startswith("/static/"):
            resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                                       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
        return resp

    @web.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        supplied = request.query_params.get("token", "") or request.cookies.get("jarvis_token", "")
        if not secrets.compare_digest(supplied, token):
            return HTMLResponse("<h2>Panel de Jarvis</h2><p>Abre la URL con token que imprime <code>jarvis serve</code>.</p>", status_code=401)
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        resp = HTMLResponse(html.replace("__TOKEN__", token).replace("__NAME__", cfg.assistant.display_name))
        # cookie de sesión del panel (SameSite=Strict: otros orígenes no pueden usarla; el token también viaja en cabecera)
        resp.set_cookie("jarvis_token", token, samesite="strict", httponly=False, path="/")
        return resp

    web.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ------------------------------------------------------------- estado y chat
    @web.get("/api/state")
    async def state():
        snap = orch.snapshot()
        snap["voice"] = voice.status() if voice else {"running": False}
        snap["memory"] = svc.stats()
        snap["tts"] = orch.speaker.status() if orch.speaker else {"provider": "none"}
        snap["demo"] = app.demo
        return snap

    @web.post("/api/chat")
    async def chat(body: ChatIn):
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "texto vacío")
        asyncio.create_task(orch.handle_text(text, source="text"))
        return {"accepted": True}

    @web.post("/api/cancel")
    async def cancel():
        return await orch.cancel("panel")

    @web.post("/api/ptt")
    async def ptt():
        if not voice or not voice.running:
            raise HTTPException(409, "el modo voz no está activo (inicia con `jarvis serve --listen`)")
        voice.request_ptt()
        return {"ok": True}

    @web.post("/api/listening")
    async def listening(body: ListeningIn):
        if not voice or not voice.running:
            raise HTTPException(409, "el modo voz no está activo")
        voice.set_listening(body.enabled)
        return {"listening": orch.listening_enabled}

    @web.get("/api/history")
    async def history():
        return {"session_id": orch.session_id, "items": orch.history[-200:]}

    # ------------------------------------------------------------- aprobaciones
    @web.get("/api/approvals")
    async def approvals():
        return {"pending": [a.to_dict() for a in app.approvals.pending()], "recent": [a.to_dict() for a in app.approvals.all(20)]}

    @web.post("/api/approvals/{approval_id}/approve")
    async def approve(approval_id: str, body: ApproveIn):
        return {"message": await orch.approve(approval_id, body.args_hash)}

    class AnswerIn(BaseModel):
        answer: str

    @web.post("/api/approvals/{approval_id}/answer")
    async def answer(approval_id: str, body: AnswerIn):
        return {"message": await orch.answer_question(approval_id, body.answer)}

    @web.post("/api/approvals/{approval_id}/reject")
    async def reject(approval_id: str):
        return {"message": orch.reject(approval_id)}

    # ------------------------------------------------------------- memoria
    @web.get("/api/memory/graph")
    async def graph(include_hidden: bool = False, include_sources: bool = True):
        return build_graph(svc, include_hidden=include_hidden, include_sources=include_sources)

    @web.get("/api/memory/graph/local/{mem_id}")
    async def graph_local(mem_id: str, depth: int = 1, max_nodes: int = 60, include_hidden: bool = False):
        if not svc.get(mem_id) and not svc.source_get(mem_id):
            raise HTTPException(404, "no existe")
        return local_graph(svc, mem_id, depth=max(1, min(depth, 3)), max_nodes=max(1, min(max_nodes, 500)), include_hidden=include_hidden)

    @web.get("/api/memory/trace/last")
    async def trace_last():
        if not orch.last_trace:
            return {"nodes": [], "edges": [], "turn_id": None}
        return trace_graph(svc, orch.last_trace)

    @web.get("/api/memory/trace/{turn_id}")
    async def trace(turn_id: str):
        t = orch.trace(turn_id)
        if not t:
            raise HTTPException(404, "sin traza")
        out = trace_graph(svc, t)
        out["trace"] = t
        return out

    @web.get("/api/memory/search")
    async def search(q: str = "", limit: int = 20, include_hidden: bool = False, types: str = ""):
        tl = [t for t in types.split(",") if t] or None
        hits = svc.search(q, limit=limit, types=tl, include_hidden=include_hidden) if q.strip() else []
        return {"hits": [h.__dict__ for h in hits]}

    @web.get("/api/memory/list")
    async def mem_list(include_hidden: bool = False, type: str = "", project: str = ""):
        items = svc.list(include_hidden=include_hidden, types=[type] if type else None, project=project or None)
        return {"items": [svc.node_summary(m) for m in items], "projects": svc.projects(), "types": list(MEMORY_TYPES)}

    @web.get("/api/memory/lint")
    async def mem_lint():
        return {"issues": [i.to_dict() for i in lint(svc)]}

    @web.get("/api/memory/journal")
    async def journal(id: str = "", limit: int = 100):
        return {"entries": svc.journal(id or None, limit=limit)}

    @web.get("/api/memory/source/{source_id}")
    async def source(source_id: str, quote: str = ""):
        src = svc.source_get(source_id)
        if not src:
            raise HTTPException(404, "fuente inexistente")
        text = svc.source_text(source_id) or ""
        excerpt, pos = text[:4000], -1
        if quote:
            pos = text.find(quote[:60])
            if pos >= 0:
                excerpt = text[max(0, pos - 600):pos + 1200]
        return {"source": src.model_dump(mode="json"), "excerpt": excerpt, "found": pos >= 0, "total_chars": len(text)}

    @web.get("/api/memory/{mem_id}")
    async def mem_get(mem_id: str):
        m = svc.get(mem_id)
        if not m:
            raise HTTPException(404, "no existe")
        related = []
        for r in m.related:
            t = svc.get(r.target)
            related.append({**r.model_dump(), "title": t.title if t else "(inexistente)", "type_of_target": t.type if t else None})
        backlinks = [{"id": o.id, "title": o.title, "type": o.type} for o in svc.list(include_hidden=True)
                     if o.id != m.id and m.id in o.all_links()]
        sources = []
        for s in m.sources:
            src = svc.source_get(s.source_id)
            sources.append({**s.model_dump(), "title": src.title if src else "(fuente ausente)", "stored_path": src.stored_path if src else None})
        return {"memory": m.model_dump(mode="json"), "body": m.body, "node": svc.node_summary(m), "related": related,
                "backlinks": backlinks, "sources": sources, "journal": svc.journal(m.id, 50),
                "versions": [str(v.name) for v in svc.versions(m.id)]}

    @web.post("/api/memory/{mem_id}/forget/plan")
    async def forget_plan(mem_id: str):
        try:
            return svc.plan_forget(mem_id).__dict__
        except NotFound:
            raise HTTPException(404, "no existe")

    @web.post("/api/memory/{mem_id}/forget")
    async def forget(mem_id: str, body: ForgetIn):
        if not body.confirm:
            raise HTTPException(400, "confirma el borrado")
        try:
            rep = svc.forget(mem_id, reason="olvidado desde el panel", delete_sources=body.delete_sources, actor="panel")
        except NotFound:
            raise HTTPException(404, "no existe")
        return rep.__dict__

    @web.post("/api/memory/{mem_id}/correct")
    async def correct(mem_id: str, body: CorrectIn):
        try:
            old = svc.require(mem_id)
            if body.mode == "edit":
                m = svc.update(mem_id, body=body.new_content, title=body.new_title, reason=body.reason, actor="panel")
            elif body.mode == "dispute":
                m = svc.dispute(mem_id, body.reason, actor="panel")
            elif body.mode == "archive":
                m = svc.update(mem_id, status="archived", reason=body.reason, actor="panel")
            else:
                if not body.new_content:
                    raise HTTPException(400, "new_content obligatorio")
                m = svc.supersede(mem_id, MemoryProposal(type=old.type, title=body.new_title or old.title, body=body.new_content,
                                                         provenance="user_statement", project=old.project, tags=old.tags),
                                  reason=body.reason, actor="panel")
        except NotFound:
            raise HTTPException(404, "no existe")
        except MemoryError as e:
            raise HTTPException(400, str(e))
        return {"node": svc.node_summary(m)}

    @web.post("/api/memory/relations/confirm")
    async def relation_confirm(body: RelationIn):
        try:
            svc.confirm_relation(body.source, body.target, body.type, actor="panel")
        except NotFound:
            raise HTTPException(404, "no existe")
        return {"ok": True}

    @web.post("/api/memory/relations/delete")
    async def relation_delete(body: RelationIn):
        try:
            svc.unlink(body.source, body.target, body.type, actor="panel")
        except NotFound:
            raise HTTPException(404, "no existe")
        return {"ok": True}

    @web.post("/api/memory/rebuild")
    async def rebuild():
        return {"pages": svc.rebuild_index()}

    @web.post("/api/memory/ask/{mem_id}")
    async def ask_about(mem_id: str):
        m = svc.get(mem_id)
        if not m:
            raise HTTPException(404, "no existe")
        asyncio.create_task(orch.handle_text(f"Cuéntame qué recuerdas sobre «{m.title}» (id {m.id}) y de dónde salió.", source="text"))
        return {"accepted": True}

    # ------------------------------------------------------------- layout, config, audio
    @web.get("/api/layout")
    async def layout_get():
        if layout_path.exists():
            return json.loads(layout_path.read_text(encoding="utf-8"))
        return {"positions": {}}

    @web.put("/api/layout")
    async def layout_put(request: Request):
        data = await request.json()
        positions = data.get("positions", {})
        if not isinstance(positions, dict) or len(positions) > 20000:
            raise HTTPException(400, "layout inválido")
        clean = {k: [float(v[0]), float(v[1])] for k, v in positions.items() if isinstance(v, (list, tuple)) and len(v) == 2}
        tmp = layout_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"positions": clean}), encoding="utf-8")
        os.replace(tmp, layout_path)
        return {"saved": len(clean)}

    @web.get("/api/config")
    async def config_get():
        d = config_dump_safe(cfg)
        d["editable"] = sorted(EDITABLE_KEYS)
        return d

    @web.post("/api/config")
    async def config_set(body: ConfigIn):
        if body.key not in EDITABLE_KEYS:
            raise HTTPException(400, "clave no editable desde el panel")
        data = cfg.model_dump(mode="json")
        node = data
        parts = body.key.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = body.value
        try:
            new = Config.model_validate(data).expand()
        except Exception as e:
            raise HTTPException(400, f"valor inválido: {e}")
        save_config(new)
        for section in ("assistant", "wake_word", "tts", "audio", "memory"):
            setattr(cfg, section, getattr(new, section))
        if body.key.startswith("wake_word.sensitivity") and voice and voice.wake_provider:
            voice.wake_provider.set_sensitivity(float(body.value))
        if body.key.startswith("tts.") and orch.speaker and hasattr(orch.speaker, "voice"):
            orch.speaker.voice = cfg.tts.voice
            orch.speaker.rate = cfg.tts.rate
        app.bus.publish("config.changed", key=body.key, value=body.value, display_name=cfg.assistant.display_name,
                        wake_word=cfg.wake_word.model_dump())
        note = None
        if body.key == "assistant.display_name":
            note = ("El nombre visible cambió. La palabra de activación acústica sigue siendo "
                    f"«{cfg.wake_word.keyword_label}» (modelo {cfg.wake_word.model_path}); requiere otro modelo para cambiar.")
        return {"ok": True, "note": note}

    @web.get("/api/audio/devices")
    async def devices():
        from ..audio.capture import MicCapture
        from ..audio.tts import list_spanish_voices
        try:
            devs = MicCapture.list_devices()
        except Exception as e:
            devs = [{"error": str(e)}]
        return {"devices": devs, "voices": list_spanish_voices()}

    # ------------------------------------------------------------- SSE
    @web.get("/api/events")
    async def events(request: Request):
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        loop = asyncio.get_running_loop()

        def listener(ev: dict) -> None:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, ev)
            except Exception:
                pass

        unsub = app.bus.subscribe(listener)

        async def gen():
            hello = {"kind": "hello", "memory_version": svc.version(), "state": orch.snapshot()}
            yield f"event: message\ndata: {json.dumps(hello, ensure_ascii=False)}\n\n"
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"event: message\ndata: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                unsub()

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return web


async def run_serve(args: Any) -> int:
    import uvicorn
    from ..app import build_app
    from ..audio.tts import make_tts
    from ..config import load_config

    cfg = load_config()
    if args.port:
        cfg.ui.port = args.port
    lock_file = cfg.memory.runtime_dir / "serve.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("ya hay una instancia de `jarvis serve` en ejecución (bloqueo en runtime/serve.lock)", flush=True)
        return 1
    speaker = make_tts(cfg)
    app = build_app(cfg, demo=args.demo, speaker=speaker, speak_responses=True)
    if app.demo:
        print("*** MODO DEMO: respuestas simuladas ***", flush=True)
    else:
        print("Conectando con Claude…", flush=True)
        try:
            await app.provider.start()
        except Exception as e:
            print(f"Claude no disponible: {e}. El panel arranca igualmente; revisa `jarvis doctor`.", flush=True)
    voice = None
    if args.listen:
        from ..audio.voice_loop import VoiceLoop
        voice = VoiceLoop(app, wake_enabled=cfg.wake_word.provider != "none")
        print("Iniciando audio…", flush=True)
        await voice.start()
        st = voice.status()
        if st["mic_error"]:
            print(f"Micrófono no disponible: {st['mic_error']}", flush=True)
        if voice.wake_error:
            print(f"Detector no disponible: {voice.wake_error} → pulsar-para-hablar disponible", flush=True)
    token = secrets.token_urlsafe(24)
    token_file = cfg.memory.runtime_dir / "ui_token"
    token_file.write_text(token)
    os.chmod(token_file, 0o600)
    web = create_web_app(app, voice, token)
    url = f"http://{cfg.ui.host}:{cfg.ui.port}/?token={token}"
    print(f"Panel: {url}", flush=True)
    if args.open:
        subprocess.run(["open", url], check=False)
    config = uvicorn.Config(web, host=cfg.ui.host, port=cfg.ui.port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    tasks = [asyncio.create_task(server.serve())]
    if voice:
        tasks.append(asyncio.create_task(voice.run()))
    parent = getattr(args, "parent_pid", None)

    def _forced_exit(reason: str, grace: float = 3.0) -> None:
        """Hilo independiente del bucle asyncio: aunque el apagado se cuelgue, el proceso termina."""
        import threading
        import time as _t

        def go() -> None:
            try:
                app.logger.info("%s; cerrando backend", reason)
                print(f"{reason}; cerrando backend", flush=True)
            except Exception:
                pass  # la tubería hacia la app puede estar rota: no importa
            try:
                server.should_exit = True
                _t.sleep(grace)
            finally:
                os._exit(0)
        threading.Thread(target=go, daemon=True).start()

    if parent:
        def watch_parent() -> None:
            import time as _t
            while True:
                _t.sleep(2)
                try:
                    os.kill(parent, 0)
                except OSError:
                    _forced_exit("la app que me lanzó ya no existe")
                    return
        import threading
        threading.Thread(target=watch_parent, daemon=True).start()
    import signal
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: _forced_exit(f"señal {s.name}"))
        except (NotImplementedError, RuntimeError):
            pass
    try:
        await tasks[0]
    finally:
        if voice:
            await voice.stop()
        for t in tasks[1:]:
            t.cancel()
        await app.provider.stop()
        os.close(fd)
    return 0
