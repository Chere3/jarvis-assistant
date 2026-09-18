"""Avisos proactivos: lo que el asistente dice sin que se lo pregunten.

Dos prioridades:
- URGENTE (una sesión te necesita, un run falló): se dice en cuanto el asistente no está ocupado; si está
  pensando o hablando, espera a que termine ese turno.
- BAJA (algo terminó): se agrupa y se dice en UNA frase en la siguiente pausa.
Si no se puede hablar (sin voz o TTS apagado), cae a una notificación nativa de macOS.
Cada aviso se registra (para el panel y para el contexto del siguiente turno) y se publica en el bus.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from .orchestrator.state import State

URGENT = "urgent"
LOW = "low"
_QUIET_STATES = (State.IDLE, State.DISABLED, State.AWAITING_APPROVAL, State.ERROR)
_BATCH_DELAY_S = 2.5


def list_join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" y {items[-1]}"


def cap_listing(items: list[str]) -> str:
    if len(items) <= 3:
        return list_join(items)
    rest = len(items) - 3
    return list_join(items[:3]) + f" y {rest} más"


class Announcer:
    def __init__(self, orch: Any, bus: Any, cfg: Any, store: Any = None, logger: Any = None,
                 notifier: Callable[..., Any] | None = None, clients: Callable[[], int] | None = None) -> None:
        self.orch = orch
        self.bus = bus
        self.cfg = cfg
        self.store = store
        self.log = logger
        self.notifier = notifier
        self.clients = clients or (lambda: 0)
        self.enabled = True
        self.recent: list[dict[str, Any]] = []
        self._urgent: list[tuple[str, str]] = []
        self._finished_sessions: list[str] = []
        self._finished_runs: list[str] = []
        self._lock = asyncio.Lock()
        self._batch_task: asyncio.Task | None = None
        self._unsub = bus.subscribe(self._on_bus)

    # ------------------------------------------------------------- entrada
    async def say(self, text: str, priority: str = URGENT, kind: str = "") -> None:
        if not self.enabled or not text.strip():
            return
        if priority == URGENT:
            if self._quiet():
                await self._deliver(text, priority, kind)
            else:
                self._urgent.append((text, kind))
        else:
            self._finished_sessions.append(text)
            self._schedule_batch()

    def session_finished(self, name: str) -> None:
        if name and name not in self._finished_sessions:
            self._finished_sessions.append(name)
        self._schedule_batch()

    def run_finished(self, project: str) -> None:
        if project and project not in self._finished_runs:
            self._finished_runs.append(project)
        self._schedule_batch()

    # ------------------------------------------------------------- mecánica
    def _quiet(self) -> bool:
        return self.orch.state in _QUIET_STATES and not (self.orch.speaker and self.orch.speaker.is_speaking())

    def _on_bus(self, ev: dict[str, Any]) -> None:
        if ev.get("kind") == "state" and ev.get("state") in ("IDLE", "AWAITING_APPROVAL"):
            if self._urgent or self._finished_sessions or self._finished_runs:
                try:
                    asyncio.get_running_loop().create_task(self._drain())
                except RuntimeError:
                    pass

    def _schedule_batch(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._batch_task and not self._batch_task.done():
            return
        self._batch_task = loop.create_task(self._batch_later())

    async def _batch_later(self) -> None:
        await asyncio.sleep(_BATCH_DELAY_S)
        await self._drain()

    async def _drain(self) -> None:
        async with self._lock:
            while self._urgent and self._quiet():
                text, kind = self._urgent.pop(0)
                await self._deliver(text, URGENT, kind)
            if not self._quiet():
                return
            names, self._finished_sessions = list(self._finished_sessions), []
            projects, self._finished_runs = list(self._finished_runs), []
            parts = []
            if names:
                parts.append(f"{names[0]} ha terminado." if len(names) == 1
                             else f"Han terminado {len(names)} conversaciones: {cap_listing(names)}.")
            if projects:
                parts.append(f"El trabajo en {projects[0]} está hecho." if len(projects) == 1
                             else f"El trabajo en {cap_listing(projects)} está hecho.")
            if parts:
                await self._deliver(" ".join(parts), LOW, "finished")

    async def _deliver(self, text: str, priority: str, kind: str) -> None:
        channel = "text"
        spoke = False
        speaker = self.orch.speaker
        if speaker and self.cfg.tts.enabled:
            prev = self.orch.state
            try:
                if prev in (State.IDLE, State.DISABLED):
                    self.orch.set_state(State.SPEAKING, "aviso")
                spoke = bool(await speaker.speak(text))
                channel = "voice"
            except Exception as e:
                if self.log:
                    self.log.warning("aviso no se pudo decir: %s", e)
            finally:
                if self.orch.state == State.SPEAKING:
                    self.orch.set_state(State.IDLE if prev in (State.IDLE, State.DISABLED) else prev, "fin del aviso")
        if not spoke and priority == URGENT and self.notifier and self.cfg.sessions.notify_fallback:
            try:
                if await self.notifier(self.cfg.assistant.display_name, text):
                    channel = "notification"
            except Exception:
                pass
        entry = {"ts": time.time(), "text": text, "priority": priority, "kind": kind, "channel": channel}
        self.recent.append(entry)
        del self.recent[:-30]
        self.orch.history.append({"role": "system", "text": text, "ts": entry["ts"], "announcement": True})
        self.bus.publish("announce", text=text, priority=priority, reason=kind, channel=channel, ts=entry["ts"])
        if self.store:
            try:
                self.store.record_announcement(priority, text, channel, kind)
            except Exception:
                pass

    def recent_lines(self, limit: int = 5, max_age_s: float = 3600) -> list[str]:
        now = time.time()
        out = [e for e in self.recent if now - e["ts"] <= max_age_s]
        return [f"- hace {int((now - e['ts']) // 60)} min: {e['text']}" for e in out[-limit:]]

    def close(self) -> None:
        self._unsub()
