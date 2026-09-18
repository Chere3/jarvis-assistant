import asyncio
import json

import pytest

from jarvis.agent.claude_adapter import AgentResponse, AgentRequest
from jarvis.agent.approvals import ApprovalManager
from jarvis.agent.claude_adapter import FakeProvider
from jarvis.agent.tools import ToolRegistry
from jarvis.config import Config
from jarvis.memory.events import EventBus
from jarvis.memory.schema import MemoryProposal
from jarvis.memory.store import MemoryService
from jarvis.orchestrator.session import Orchestrator
from jarvis.orchestrator.state import State


def make(tmp_path, script=None, delay=0.0, provider=None):
    cfg = Config()
    cfg.memory.dir = tmp_path / "memory"
    cfg.memory.runtime_dir = tmp_path / "runtime"
    cfg.tools.workspace_dir = tmp_path / "ws"
    cfg.expand()
    bus = EventBus()
    svc = MemoryService(cfg.memory.dir, cfg.memory.runtime_dir, bus=bus)
    approvals = ApprovalManager(60)
    reg = ToolRegistry(cfg, svc, approvals, bus)
    prov = provider or FakeProvider(reg, script, delay)
    orch = Orchestrator(cfg, svc, prov, reg, approvals, bus)
    events = []
    bus.subscribe(events.append)
    return cfg, svc, reg, approvals, prov, orch, events


async def test_turn_persists_and_cites(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    m = svc.create(MemoryProposal(title="Prefiere respuestas cortas", body="cortas", type="preference"))
    prov.script = [f"Prefieres respuestas cortas [mem:{m.id}]"]
    r = await orch.handle_text("¿qué prefiero?")
    assert r.cited == [m.id] and "[mem:" not in r.display_text
    assert orch.state == State.IDLE
    lines = (cfg.memory.runtime_dir / "sessions" / f"{orch.session_id}.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["cited"] == [m.id]
    trace = orch.trace("t1")
    assert trace["retrieved"][0]["id"] == m.id and trace["cited"] == [m.id]


async def test_cancel_generation_and_pending_tools(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    m = svc.create(MemoryProposal(title="Borrable", body="x", type="note"))
    prov.script = [[("tool", "memory_forget", {"id": m.id}), "esperando aprobación", "más texto"]]
    prov.delay = 0.3
    task = asyncio.create_task(orch.handle_text("olvida borrable"))
    await asyncio.sleep(0.45)  # ya se pidió la aprobación, el modelo sigue "generando"
    assert approvals.pending()
    rep = await orch.cancel()
    r = await task
    assert r.cancelled and rep["cancelled_approvals"] and not approvals.pending()
    assert svc.get(m.id) is not None  # la acción pendiente nunca se ejecutó
    assert orch.state == State.IDLE
    await orch.handle_text("sí")  # un «sí» tardío no autoriza nada
    assert svc.get(m.id) is not None


async def test_stale_events_discarded(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path, script=["primera respuesta lenta", "segunda"], delay=0.2)
    t1 = asyncio.create_task(orch.handle_text("uno"))
    await asyncio.sleep(0.05)
    t2 = asyncio.create_task(orch.handle_text("dos"))
    r1, r2 = await asyncio.gather(t1, t2)
    assert r1.cancelled and not r2.cancelled
    deltas_t1 = [e for e in events if e["kind"] == "chat.text_delta" and e["turn_id"] == "t1"]
    assert all("lenta" not in e["text"] for e in deltas_t1)


class TimeoutProvider(FakeProvider):
    async def run_turn(self, req, turn, on_event):
        await asyncio.sleep(0.2)
        return AgentResponse(error="timeout del proveedor", terminal_reason="timeout")


async def test_provider_timeout_reported(tmp_path):
    cfg, *_ = make(tmp_path)
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path, provider=None)
    orch.provider = TimeoutProvider(reg)
    r = await orch.handle_text("hola")
    assert r.error and "timeout" in r.error and orch.state == State.IDLE
    assert any(e["kind"] == "state" and e["state"] == "ERROR" for e in events)


class FlakyProvider(FakeProvider):
    """Falla la red después de ejecutar una herramienta con efectos: no debe reintentar."""
    def __init__(self, reg):
        super().__init__(reg)
        self.attempts = 0

    async def run_turn(self, req, turn, on_event):
        self.attempts += 1
        self.registry.turn = turn
        await self.registry.execute("memory_save_note", {"title": "Nota red", "content": "x"}, turn)
        if turn.effects_executed:
            return AgentResponse(error="transient: conexión perdida", terminal_reason="error")
        return AgentResponse(text="ok")


async def test_network_failure_does_not_duplicate_effects(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    flaky = FlakyProvider(reg)
    orch.provider = flaky
    r = await orch.handle_text("guarda nota")
    assert flaky.attempts == 1 and len(svc.list()) == 1 and r.error


async def test_approval_flow_yes_no(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    m = svc.create(MemoryProposal(title="Borrable", body="x", type="note"))
    prov.script = [[("tool", "memory_forget", {"id": m.id}), "¿confirmas?"]]
    r = await orch.handle_text("olvida borrable")
    assert r.pending_approvals and orch.state == State.AWAITING_APPROVAL
    r2 = await orch.handle_text("no")
    assert "rechazada" in r2.display_text and svc.get(m.id) and orch.state == State.IDLE
    prov.script = [[("tool", "memory_forget", {"id": m.id}), "¿confirmas?"]]
    await orch.handle_text("olvida borrable")
    r3 = await orch.handle_text("sí")
    assert "ejecutada" in r3.display_text and svc.get(m.id) is None


def test_endpointer_discards_activation_without_speech():
    import numpy as np
    from jarvis.audio.vad import VoiceActivity, Endpointer
    vad = VoiceActivity(16000, 2)
    ep = Endpointer(vad, 80, 900, 300, 15)
    out = None
    for _ in range(100):
        out = ep.feed(np.zeros(1280, dtype=np.int16))
        if out:
            break
    assert out == "nospeech" and not ep.has_speech()


def test_state_machine_transitions():
    from jarvis.orchestrator.state import check_transition, InvalidTransition
    check_transition(State.IDLE, State.LISTENING)
    check_transition(State.LISTENING, State.TRANSCRIBING)
    check_transition(State.TRANSCRIBING, State.THINKING)
    check_transition(State.THINKING, State.SPEAKING)
    check_transition(State.SPEAKING, State.IDLE)
    with pytest.raises(InvalidTransition):
        check_transition(State.LISTENING, State.SPEAKING)
