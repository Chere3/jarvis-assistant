import json

import httpx
import pytest

from jarvis.ui.server import create_web_app
from tests.test_orchestration import make
from jarvis.memory.schema import MemoryProposal, Relation
from jarvis.app import App


@pytest.fixture
def client(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    app = App(cfg, orch.bus, svc, reg, approvals, prov, orch, None, demo=True)
    web = create_web_app(app, None, "tok123")
    transport = httpx.ASGITransport(app=web)
    c = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765", headers={"X-Jarvis-Token": "tok123"})
    return c, svc, prov, orch


async def test_auth_and_origin(client):
    c, svc, prov, orch = client
    r = await c.get("/api/state", headers={"X-Jarvis-Token": "bad"})
    assert r.status_code == 401
    r = await c.get("/api/state", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = await c.get("/")
    assert r.status_code == 401
    r = await c.get("/?token=tok123")
    assert r.status_code == 200 and "__TOKEN__" not in r.text and "Content-Security-Policy" in r.headers
    r = await c.get("/api/state")
    assert r.status_code == 200 and r.json()["state"] == "IDLE"


async def test_graph_and_memory_endpoints(client):
    c, svc, prov, orch = client
    a = svc.create(MemoryProposal(title="Proyecto X", body="cuerpo <script>alert(1)</script>", type="project", project="x"))
    b = svc.create(MemoryProposal(title="Decisión Y", body=f"ver [[{a.id}]]", type="decision", related=[Relation(target=a.id, type="decidido_para")]))
    g = (await c.get("/api/memory/graph")).json()
    assert {n["id"] for n in g["nodes"]} == {a.id, b.id} and len(g["edges"]) == 2
    lg = (await c.get(f"/api/memory/graph/local/{a.id}?depth=1&max_nodes=1")).json()
    assert lg["truncated"] and len(lg["nodes"]) == 1
    m = (await c.get(f"/api/memory/{a.id}")).json()
    assert m["body"].startswith("cuerpo") and m["backlinks"][0]["id"] == b.id
    s = (await c.get("/api/memory/search?q=proyecto")).json()
    assert s["hits"][0]["id"] == a.id
    plan = (await c.post(f"/api/memory/{a.id}/forget/plan")).json()
    assert plan["referencing"][0]["id"] == b.id
    r = await c.post(f"/api/memory/{a.id}/forget", json={"confirm": False})
    assert r.status_code == 400 and svc.get(a.id)
    r = await c.post(f"/api/memory/{a.id}/forget", json={"confirm": True})
    assert r.status_code == 200 and svc.get(a.id) is None
    g2 = (await c.get("/api/memory/graph")).json()
    assert {n["id"] for n in g2["nodes"]} == {b.id} and not g2["edges"]


async def test_correct_and_layout_and_config(client):
    c, svc, prov, orch = client
    a = svc.create(MemoryProposal(title="Fecha", body="jueves", type="fact"))
    r = await c.post(f"/api/memory/{a.id}/correct", json={"mode": "supersede", "new_content": "viernes", "reason": "cambió"})
    new_id = r.json()["node"]["id"]
    assert svc.get(a.id).status == "superseded" and svc.get(new_id).supersedes == a.id
    r = await c.put("/api/layout", json={"positions": {a.id: [1.5, 2.5]}})
    assert r.json()["saved"] == 1 and (await c.get("/api/layout")).json()["positions"][a.id] == [1.5, 2.5]
    assert svc.get(new_id).body == "viernes"  # el layout no toca los recuerdos
    r = await c.post("/api/config", json={"key": "claude.model", "value": "x"})
    assert r.status_code == 400
    r = await c.post("/api/config", json={"key": "assistant.display_name", "value": "Viernes"})
    assert r.status_code == 200 and "activación" in r.json()["note"]
    assert (await c.get("/api/state")).json()["assistant"] == "Viernes"


async def test_chat_trace_and_events(client):
    c, svc, prov, orch = client
    m = svc.create(MemoryProposal(title="Prefiere respuestas cortas", body="cortas", type="preference"))
    prov.script = [f"Cortas [mem:{m.id}]"]
    await orch.handle_text("¿qué prefiero?")
    t = (await c.get("/api/memory/trace/last")).json()
    assert t["nodes"][0]["id"] == m.id and t["nodes"][0]["cited"] is True
    t2 = (await c.get("/api/memory/trace/t1")).json()
    assert t2["trace"]["cited"] == [m.id]
    h = (await c.get("/api/history")).json()
    assert h["items"][-1]["cited"] == [m.id]
