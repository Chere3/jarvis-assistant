"""El capataz y sus herramientas: sesiones nombrables, puerta de contaminación, runs, specs, proyectos y API."""
import asyncio
import json
import os
import socket
import tempfile
import threading

from pathlib import Path

import httpx
import pytest

from jarvis.agent.foreman_tools import register_foreman_tools
from jarvis.agent.tools import TurnContext
from jarvis.app import App
from jarvis.foreman import Foreman
from jarvis.runs import builds, specs
from jarvis.sessions import watch
from jarvis.sessions.watch import build_snapshot
from jarvis.ui.server import create_web_app
from tests.test_orchestration import make
from tests.test_runs import FAKE_CLAUDE
from tests.test_sessions_watch import live, roster, transcript


@pytest.fixture
def foreman(tmp_path, monkeypatch):
    live(monkeypatch)
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    cfg.runs.projects_root = tmp_path / "proyectos"
    cfg.runs.projects_root.mkdir()
    (cfg.runs.projects_root / "pingou").mkdir()
    (cfg.runs.projects_root / "sonda").mkdir()
    cfg.sessions.notify_fallback = False
    root = tmp_path / "claude"
    sock = Path(tempfile.mkdtemp(prefix="jv", dir="/tmp")) / "pingou.sock"  # AF_UNIX exige rutas cortas
    roster(root, 1001, "s-pingou", str(cfg.runs.projects_root / "pingou"), status="idle", sock=str(sock))
    transcript(root, str(cfg.runs.projects_root / "pingou"), "s-pingou", title="Comandos de moderación",
               last_text="¿Uso Postgres o SQLite?", tools=("Read",))
    roster(root, 1002, "s-sonda", str(cfg.runs.projects_root / "sonda"), status="busy")
    transcript(root, str(cfg.runs.projects_root / "sonda"), "s-sonda", title="Thompson sampling")
    fm = Foreman(cfg, orch.bus, orch, provider=prov)
    fm.watcher.roots = [root]
    fm.watcher.poll_once()
    fm.executor.claude_path = str(tmp_path / "fake-claude")
    (tmp_path / "fake-claude").write_text(FAKE_CLAUDE)
    os.chmod(tmp_path / "fake-claude", 0o755)
    fm.executor.poll_sec = 0.05
    register_foreman_tools(reg, fm)
    orch.context_provider = fm.context_block
    return cfg, reg, approvals, orch, fm, events, sock


async def test_list_and_detail_taint_the_turn(foreman):
    cfg, reg, approvals, orch, fm, events, sock = foreman
    t = TurnContext("t1", "s")
    res = await reg.execute("list_sessions", {}, t)
    assert res.ok and "2 conversaciones (1 te esperan)" in res.text and "pingou: esperándote" in res.text
    assert t.untrusted_source == "sesiones"
    res = await reg.execute("session_detail", {"session": "sonda"}, t)
    assert res.ok and "Thompson sampling" in res.text and fm.last_mentioned == "s-sonda"
    res = await reg.execute("steer_session", {"session": "sonda", "message": "usa Postgres"}, t)
    assert not res.ok and "BLOQUEADO" in res.text and "no confiable" in res.text
    res = await reg.execute("memory_save_note", {"title": "x", "content": "y"}, t)
    assert not res.ok and "BLOQUEADO" in res.text
    assert "<contexto_operativo>" in fm.context_block() and "te esperan: 1" in fm.context_block()


async def test_steer_and_ambiguity(foreman):
    cfg, reg, approvals, orch, fm, events, sock = foreman
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock))
    srv.listen(1)
    got = []

    def serve():
        conn, _ = srv.accept()
        got.append(conn.recv(65536))
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    fm.watcher.poll_once()  # ahora el socket existe: la sesión es dirigible
    t = TurnContext("t2", "s")
    res = await reg.execute("steer_session", {"session": "pingou", "message": "Usa Postgres."}, t)
    assert res.ok and "entregado" in res.text
    await asyncio.sleep(0.1)
    assert json.loads(got[0].decode().splitlines()[-1])["message"]["content"] == "Usa Postgres."
    assert fm.store.list_steers()[0]["outcome"] == "sent"
    res = await reg.execute("steer_session", {"session": "inexistente", "message": "x"}, TurnContext("t3", "s"))
    assert not res.ok and "No encuentro" in res.text
    res = await reg.execute("steer_session", {"session": "sonda", "message": "x"}, TurnContext("t4", "s"))
    assert not res.ok and "no tiene canal" in res.text
    srv.close()


async def test_answer_dialog_requires_approval_and_closed_keys(foreman):
    cfg, reg, approvals, orch, fm, events, sock = foreman
    t = TurnContext("t5", "s")
    res = await reg.execute("answer_dialog", {"session": "pingou", "key": "rm -rf"}, t)
    assert not res.ok and "Intro, Escape" in res.text
    res = await reg.execute("answer_dialog", {"session": "pingou", "key": "sí"}, t)
    assert res.approval_id and approvals.pending()[0].description.startswith("pulsar «sí»")


async def test_projects_spec_build_flow(foreman, monkeypatch):
    cfg, reg, approvals, orch, fm, events, sock = foreman
    t = TurnContext("t6", "s")
    res = await reg.execute("list_projects", {}, t)
    assert res.ok and "- pingou (1 sesión(es), 1 te esperan)" in res.text and "- sonda" in res.text
    res = await reg.execute("write_spec", {"project": "sonda", "spec": "# Recomendador v2 — Diseño\n\n## Objetivo\nMejor.\n\n## Datos\nSQLite."}, t)
    assert res.ok and "secciones" in res.text
    rel = res.data["path"]
    assert [e for e in events if e["kind"] == "ui.show"][-1]["view"] == "specs"
    res = await reg.execute("start_build", {"project": "sonda"}, t)
    assert not res.ok and "no está aprobada" in res.text
    res = await reg.execute("review_document", {"project": "sonda", "section": 1}, t)
    assert res.ok and res.text.startswith("Sección 1: Objetivo")
    res = await reg.execute("revise_section", {"project": "sonda", "section": 2, "body": "Postgres."}, t)
    assert res.ok
    res = await reg.execute("approve_document", {"project": "sonda"}, t)
    assert res.ok and specs.approval_of(str(cfg.runs.projects_root / "sonda"), rel)["state"] == "approved"
    res = await reg.execute("start_build", {"project": "sonda"}, t)
    assert res.approval_id  # lanzar la construcción pide permiso
    await approvals.approve(res.approval_id)
    run_id = approvals.all()[0].result_text
    runs = fm.store.list_runs()
    assert runs and builds.is_build_prompt(runs[0]["prompt"]) and runs[0]["kind"] == "build"
    await fm.executor.wait_for(runs[0]["id"], timeout=20)
    res = await reg.execute("build_status", {"project": "sonda"}, TurnContext("t7", "s"))
    assert res.ok and "En sonda" in res.text
    res = await reg.execute("run_status", {}, TurnContext("t8", "s"))
    assert res.ok and "sonda:" in res.text
    res = await reg.execute("create_project", {"name": "../fuera"}, t)
    assert not res.ok
    res = await reg.execute("create_project", {"name": "nuevo"}, t)
    await approvals.approve(res.approval_id)
    assert (cfg.runs.projects_root / "nuevo" / "README.md").exists()


async def test_run_failure_is_announced(foreman, monkeypatch):
    cfg, reg, approvals, orch, fm, events, sock = foreman
    said = []

    async def fake_say(text, priority="urgent", kind=""):
        said.append((text, priority))

    monkeypatch.setattr(fm.announcer, "say", fake_say)
    rid = await fm.spawn_run("FAIL", cfg.runs.projects_root / "pingou")
    await fm.executor.wait_for(rid, timeout=20)
    await asyncio.sleep(0.05)
    assert said == [("El trabajo en pingou falló.", "urgent")]
    rid = await fm.spawn_run("QUESTION", cfg.runs.projects_root / "pingou")
    await fm.executor.wait_for(rid, timeout=20)
    await asyncio.sleep(0.05)
    assert "se paró para hacer una pregunta" in said[-1][0]
    assert [e for e in events if e["kind"] == "runs.run_finished"]


async def test_session_needs_you_event_announces(foreman, monkeypatch):
    cfg, reg, approvals, orch, fm, events, sock = foreman
    said = []

    async def fake_say(text, priority="urgent", kind=""):
        said.append(text)

    monkeypatch.setattr(fm.announcer, "say", fake_say)
    fm._on_session_event({"kind": "needs_you", "session": {"voice_name": "sonda", "needs": "permission prompt", "needs_a_human_hand": True}})
    await asyncio.sleep(0.02)
    assert said == ["sonda está esperando un permiso. Esa necesita una tecla tuya; puedo pulsarla si me lo pides."]
    fm._on_session_event({"kind": "finished", "session": {"voice_name": "sonda"}})
    assert fm.announcer._finished_sessions == ["sonda"]


async def test_usage_tool_and_store(foreman):
    cfg, reg, approvals, orch, fm, events, sock = foreman
    res = await reg.execute("usage_status", {}, TurnContext("t9", "s"))
    assert res.ok and "Todavía no tengo" in res.text
    fm.usage.record({"status": "allowed", "rateLimitType": "five_hour", "utilization": 0.62, "resetsAt": 1800000000,
                     "unifiedWindows": {"seven_day": {"utilization": 0.84, "resetsAt": 1800100000, "status": "allowed_warning"}}})
    snap = fm.usage.snapshot()
    by = {w["key"]: w for w in snap["windows"]}
    assert snap["measured"] and by["five_hour"]["utilization"] == 62.0 and by["seven_day"]["utilization"] == 84.0
    assert "62 por ciento" in fm.usage.spoken() and "84 por ciento" in fm.usage.spoken()
    fm.usage.record({"status": "allowed", "rateLimitType": "five_hour", "utilization": 0.01})
    assert {w["key"]: w["utilization"] for w in fm.usage.snapshot()["windows"]}["five_hour"] == 1.0


async def test_foreman_api(foreman):
    cfg, reg, approvals, orch, fm, events, sock = foreman
    app = App(cfg, orch.bus, orch.service, reg, approvals, orch.provider, orch, None, demo=True, foreman=fm)
    web = create_web_app(app, None, "tok")
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=web), base_url="http://127.0.0.1:8765", headers={"X-Jarvis-Token": "tok"})
    r = (await c.get("/api/sessions")).json()
    assert len(r["sessions"]) == 2 and r["needs_you"] == ["s-pingou"]
    r = (await c.get("/api/sessions/s-pingou")).json()
    assert r["session"]["voice_name"] == "pingou" and r["transcript"][-1]["text"].startswith("¿Uso Postgres")
    assert (await c.post("/api/sessions/nope/steer", json={"text": "x"})).status_code == 404
    r = (await c.post("/api/sessions/s-sonda/steer", json={"text": "hola"})).json()
    assert r["outcome"] == "not_live"
    r = (await c.get("/api/projects")).json()
    assert {p["name"] for p in r["projects"]} >= {"pingou", "sonda"} and r["projects"][0]["name"] == "pingou"
    r = await c.post("/api/runs", json={"prompt": "hola", "project": "sonda"})
    assert r.status_code == 200
    rid = r.json()["run_id"]
    await fm.executor.wait_for(rid, timeout=20)
    r = (await c.get("/api/runs")).json()
    assert r["runs"][0]["id"] == rid and r["runs"][0]["status"] == "succeeded" and r["runs"][0]["origin"] == "panel"
    r = (await c.get(f"/api/runs/{rid}/events")).json()
    assert [e["kind"] for e in r["events"]] == ["system", "assistant", "result"] and r["events"][1]["text"] == "voy"
    assert (await c.post(f"/api/runs/{rid}/cancel")).json()["cancelled"] is False
    r = (await c.get("/api/projects/detail", params={"path": "sonda"})).json()
    assert r["name"] == "sonda" and len(r["runs"]) == 1
    rel = builds.write_spec(str(cfg.runs.projects_root / "sonda"), "# X — Diseño\n\n## A\nb")
    r = (await c.get("/api/documents", params={"project": "sonda"})).json()
    assert r["documents"][0]["path"] == rel and r["documents"][0]["approval"]["state"] == "awaiting"
    r = (await c.get("/api/documents/read", params={"project": "sonda", "path": rel})).json()
    assert r["sections"][0]["number"] == 1
    assert (await c.post("/api/documents/build", json={"project": "sonda", "path": rel})).status_code == 409
    r = (await c.post("/api/documents/approve", json={"project": "sonda", "path": rel})).json()
    assert r["approved_by"] == "panel"
    r = (await c.get("/api/usage")).json()
    assert r["measured"] is False and "runs_today" in r
    r = (await c.get("/api/foreman")).json()
    assert r["sessions"] == 2 and r["needs_you"] == 1
    assert (await c.get("/api/announcements")).status_code == 200
