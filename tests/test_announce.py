"""Avisos proactivos: urgente cuando está libre, en cola cuando está ocupado, terminados agrupados en una frase."""
import asyncio

from jarvis import announce
from jarvis.announce import LOW, URGENT, Announcer
from jarvis.orchestrator.state import State
from tests.test_orchestration import make


class FakeSpeaker:
    def __init__(self):
        self.said = []
        self._speaking = False

    async def speak(self, text):
        self.said.append(text)
        return True

    async def stop(self):
        pass

    def is_speaking(self):
        return self._speaking


async def test_urgent_speaks_now_when_idle_and_records(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    orch.speaker = FakeSpeaker()
    an = Announcer(orch, orch.bus, cfg)
    await an.say("pingou está esperando un permiso.", URGENT, "needs_you")
    assert orch.speaker.said == ["pingou está esperando un permiso."] and orch.state == State.IDLE
    assert orch.history[-1]["announcement"] and [e for e in events if e["kind"] == "announce"][0]["channel"] == "voice"
    assert an.recent_lines()[0].endswith("pingou está esperando un permiso.")


async def test_urgent_waits_for_the_turn_to_end(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    orch.speaker = FakeSpeaker()
    an = Announcer(orch, orch.bus, cfg)
    orch.set_state(State.THINKING, "test")
    await an.say("sonda te necesita.", URGENT)
    assert orch.speaker.said == []
    orch.set_state(State.IDLE, "fin")
    await asyncio.sleep(0.05)
    assert orch.speaker.said == ["sonda te necesita."]


async def test_low_priority_is_batched_into_one_sentence(tmp_path, monkeypatch):
    monkeypatch.setattr(announce, "_BATCH_DELAY_S", 0.05)
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    orch.speaker = FakeSpeaker()
    an = Announcer(orch, orch.bus, cfg)
    an.session_finished("pingou")
    an.session_finished("sonda")
    an.run_finished("fluo")
    await asyncio.sleep(0.2)
    assert orch.speaker.said == ["Han terminado 2 conversaciones: pingou y sonda. El trabajo en fluo está hecho."]
    an.session_finished("nexo")
    await asyncio.sleep(0.2)
    assert orch.speaker.said[-1] == "nexo ha terminado."


async def test_notification_fallback_when_it_cannot_speak(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    sent = []

    async def notifier(title, text, subtitle=""):
        sent.append((title, text))
        return True

    an = Announcer(orch, orch.bus, cfg, notifier=notifier)
    await an.say("pingou te necesita.", URGENT)
    assert sent == [(cfg.assistant.display_name, "pingou te necesita.")]
    assert [e for e in events if e["kind"] == "announce"][0]["channel"] == "notification"
    an.enabled = False
    await an.say("otra", URGENT)
    assert len(sent) == 1


def test_listing_helpers():
    assert announce.list_join(["a"]) == "a" and announce.list_join(["a", "b", "c"]) == "a, b y c"
    assert announce.cap_listing(["a", "b", "c", "d", "e"]) == "a, b y c y 2 más"
