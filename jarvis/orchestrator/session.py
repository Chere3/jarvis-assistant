"""Orquestación: estado, turnos, cancelación, recuperación de contexto, aprobaciones y persistencia operativa."""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from ..agent.approvals import ApprovalError, ApprovalManager
from ..agent.claude_adapter import AgentRequest, AgentResponse, ClaudeProvider
from ..agent.tools import ToolRegistry, TurnContext
from ..config import Config
from ..memory.events import EventBus
from ..memory.retrieval import find_citations, retrieve, strip_citations
from ..memory.store import MemoryService
from .state import State, check_transition

YES = {"si", "sí", "confirmo", "adelante", "hazlo", "ok", "dale", "aprobar", "apruebo", "aprobado", "claro", "vale", "de acuerdo", "acepto"}
NO = {"no", "cancela", "cancelar", "rechaza", "rechazar", "mejor no", "no lo hagas", "rechazado"}
STOP_PHRASES = {"deja de hablar", "detente", "para", "cállate", "callate", "silencio", "stop", "cancela eso", "basta"}


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", t).strip()


class Speaker(Protocol):
    async def speak(self, text: str) -> bool: ...
    async def stop(self) -> None: ...
    def is_speaking(self) -> bool: ...


@dataclass
class TurnResult:
    turn_id: str
    user_text: str
    assistant_text: str
    display_text: str
    cited: list[str]
    cancelled: bool
    error: str | None
    cost_usd: float | None
    model: str | None
    pending_approvals: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0


class Orchestrator:
    def __init__(self, cfg: Config, service: MemoryService, provider: ClaudeProvider, registry: ToolRegistry,
                 approvals: ApprovalManager, bus: EventBus, logger: Any = None, speaker: Speaker | None = None,
                 speak_responses: bool = False, session_id: str | None = None) -> None:
        self.cfg = cfg
        self.service = service
        self.provider = provider
        self.registry = registry
        self.approvals = approvals
        self.bus = bus
        self.log = logger
        self.speaker = speaker
        self.speak_responses = speak_responses
        self.session_id = session_id or f"s{datetime.now():%Y%m%d}_{secrets.token_hex(3)}"
        self.state = State.IDLE
        self.turn_counter = 0
        self.current_turn: TurnContext | None = None
        self.active_project: str | None = None
        self.listening_enabled = False
        self.history: list[dict[str, Any]] = []
        self.last_trace: dict[str, Any] | None = None
        self._turn_lock = asyncio.Lock()
        self.sessions_dir = cfg.memory.runtime_dir / "sessions"
        self.traces_dir = cfg.memory.runtime_dir / "traces" / self.session_id
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.traces_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------- estado
    def set_state(self, new: State, reason: str = "") -> None:
        try:
            check_transition(self.state, new)
        except Exception as e:
            if self.log:
                self.log.warning("transición inválida %s (%s)", e, reason)
            if new not in (State.IDLE, State.ERROR):
                return
        if self.state != new:
            old, self.state = self.state, new
            self.bus.publish("state", state=new.value, previous=old.value, reason=reason)

    def snapshot(self) -> dict[str, Any]:
        return {"assistant": self.cfg.assistant.display_name, "state": self.state.value, "session_id": self.session_id,
                "turn": self.current_turn.turn_id if self.current_turn else None, "claude": self.provider.status(),
                "listening": self.listening_enabled, "wake_word": {"provider": self.cfg.wake_word.provider,
                                                                     "keyword": self.cfg.wake_word.keyword_label},
                "pending_approvals": [a.to_dict() for a in self.approvals.pending()],
                "speaking": bool(self.speaker and self.speaker.is_speaking()), "memory_version": self.service.version()}

    # ------------------------------------------------------------- comandos deterministas
    def _match_option(self, text: str, options: list[str]) -> str | None:
        n = _norm(text)
        if not n:
            return None
        for label in options:
            ln = _norm(label)
            if n == ln or (len(ln) > 3 and (ln in n or n in ln)):
                return label
        # respuesta por número ("la dos") o palabras clave de cada opción
        words = {"uno": 0, "primera": 0, "primero": 0, "dos": 1, "segunda": 1, "segundo": 1, "tres": 2, "tercera": 2, "tercero": 2, "cuatro": 3, "cuarta": 3}
        for w, i in words.items():
            if w in n.split() and i < len(options):
                return options[i]
        return None

    def _handle_command(self, text: str) -> str | None:
        n = _norm(text)
        pending = self.approvals.pending()
        if n in STOP_PHRASES:
            return "stop"
        questions = [a for a in pending if a.kind == "question"]
        if questions:
            q = questions[0]
            if n in NO or n in ("ninguna", "cancela la pregunta"):
                return f"rechazar:{q.id}"
            label = self._match_option(text, q.args.get("options", []))
            return f"responder:{q.id}:{label or text.strip()}"
        pending = [a for a in pending if a.kind != "question"]
        m = re.match(r"^/(aprobar|rechazar)\s+([a-f0-9]{6})$", text.strip().lower())
        if m:
            return f"{m.group(1)}:{m.group(2)}"
        if len(pending) == 1 and n in YES:
            return f"aprobar:{pending[0].id}"
        if len(pending) == 1 and n in NO:
            return f"rechazar:{pending[0].id}"
        return None

    async def approve(self, approval_id: str, expected_hash: str | None = None) -> str:
        try:
            ap = await self.approvals.approve(approval_id, expected_hash)
            msg = f"Acción aprobada y ejecutada: {ap.description}. Resultado: {ap.result_text}"
        except ApprovalError as e:
            msg = f"No se ejecutó: {e}"
        except Exception as e:
            msg = f"La acción aprobada falló: {type(e).__name__}: {e}"
        self._after_approval_change()
        self._emit_system(msg)
        return msg

    async def answer_question(self, question_id: str, answer: str) -> str:
        try:
            ap = await self.approvals.answer(question_id, answer)
            msg = f"Respondido: {ap.result_text}"
        except ApprovalError as e:
            msg = f"No se pudo responder: {e}"
        self._after_approval_change()
        self._emit_system(msg)
        return msg

    def reject(self, approval_id: str) -> str:
        try:
            ap = self.approvals.reject(approval_id)
            msg = f"Acción rechazada: {ap.description}. No se ejecutó."
        except ApprovalError as e:
            msg = f"No se pudo rechazar: {e}"
        self._after_approval_change()
        self._emit_system(msg)
        return msg

    def _after_approval_change(self) -> None:
        self.bus.publish("approvals", pending=[a.to_dict() for a in self.approvals.pending()])
        if self.state == State.AWAITING_APPROVAL and not self.approvals.pending():
            self.set_state(State.THINKING if self._turn_lock.locked() else State.IDLE, "aprobaciones resueltas")

    def _say(self, text: str) -> None:
        """Habla sin bloquear (en flujo si el proveedor lo permite)."""
        if not (self.speaker and self.cfg.tts.enabled):
            return
        if hasattr(self.speaker, "say_async"):
            self.speaker.say_async(text)
        else:
            asyncio.get_running_loop().create_task(self.speaker.speak(text))

    def _emit_system(self, text: str) -> None:
        self.history.append({"role": "system", "text": text, "ts": time.time()})
        self.bus.publish("chat.system", text=text)

    # ------------------------------------------------------------- cancelación
    async def cancel(self, reason: str = "usuario") -> dict[str, Any]:
        report: dict[str, Any] = {"cancelled_turn": None, "stopped_speech": False, "cancelled_approvals": [],
                                  "already_executed": []}
        if self.speaker and self.speaker.is_speaking():
            await self.speaker.stop()
            report["stopped_speech"] = True
        turn = self.current_turn
        if turn and not turn.cancelled and self.state in (State.THINKING, State.SPEAKING, State.AWAITING_APPROVAL):
            turn.cancelled = True
            report["cancelled_turn"] = turn.turn_id
            await self.provider.cancel()
            report["already_executed"] = [c["tool"] for c in turn.tool_calls if c["outcome"] == "ok"
                                          and self.registry.specs[c["tool"]].effects != "read"]
        if turn:
            report["cancelled_approvals"] = [a.id for a in self.approvals.cancel_turn(turn.turn_id)]
        self.bus.publish("cancelled", reason=reason, **report)
        if self.state != State.IDLE:
            self.set_state(State.IDLE, f"cancelado ({reason})")
        return report

    # ------------------------------------------------------------- turno
    async def handle_text(self, text: str, source: str = "text") -> TurnResult:
        text = text.strip()
        cmd = self._handle_command(text)
        if cmd == "stop":
            rep = await self.cancel("orden de detener")
            msg = "Detenido." + (" Ya se había ejecutado: " + ", ".join(rep["already_executed"]) + "." if rep["already_executed"] else "")
            self._emit_system(msg)
            return TurnResult("cmd", text, msg, msg, [], True, None, None, None)
        if cmd and cmd.startswith("aprobar:"):
            msg = await self.approve(cmd.split(":", 1)[1])
            return TurnResult("cmd", text, msg, msg, [], False, None, None, None)
        if cmd and cmd.startswith("rechazar:"):
            msg = self.reject(cmd.split(":", 1)[1])
            return TurnResult("cmd", text, msg, msg, [], False, None, None, None)
        if cmd and cmd.startswith("responder:"):
            _, qid, answer = cmd.split(":", 2)
            msg = await self.answer_question(qid, answer)
            return TurnResult("cmd", text, msg, msg, [], False, None, None, None)
        if self._turn_lock.locked():
            await self.cancel("nueva petición")
        async with self._turn_lock:
            return await self._run_turn(text, source)

    async def _run_turn(self, text: str, source: str) -> TurnResult:
        self.turn_counter += 1
        turn = TurnContext(turn_id=f"t{self.turn_counter}", session_id=self.session_id)
        self.current_turn = turn
        self.history.append({"role": "user", "text": text, "ts": time.time(), "turn_id": turn.turn_id, "source": source})
        self.bus.publish("chat.user", turn_id=turn.turn_id, text=text, source=source)
        self.set_state(State.THINKING, "petición recibida")
        m = self.cfg.memory
        ctx, trace = retrieve(self.service, text, turn.turn_id, budget_chars=m.context_budget_chars,
                              max_pages=m.max_pages, link_expansion=m.link_expansion, active_project=self.active_project)
        if self.log and self.cfg.logging.log_retrieval:
            self.log.info("turno %s: recuperados %s (%d chars de %d)", turn.turn_id,
                          [p.id for p in trace.retrieved], trace.total_chars, m.context_budget_chars)
        prompt = f"{ctx}\n\nPetición del usuario ({'voz' if source == 'voice' else 'texto'}): {text}" if ctx else text

        stream_voice = bool(self.speak_responses and self.speaker and self.cfg.tts.enabled and hasattr(self.speaker, "say_async"))
        spoken_upto = {"n": 0, "buf": ""}

        def flush_speech(final: bool = False) -> None:
            if not stream_voice or turn.cancelled:
                return
            buf = spoken_upto["buf"]
            parts = re.split(r"(?<=[.!?…])\s+", buf)
            ready, rest = (parts, "") if final else (parts[:-1], parts[-1])
            text = " ".join(p for p in ready if p.strip())
            if text.strip():
                if self.state == State.THINKING:
                    self.set_state(State.SPEAKING, "respuesta en voz")
                self._say(text)
            spoken_upto["buf"] = rest

        def on_event(kind: str, data: dict[str, Any]) -> None:
            if turn.cancelled or self.current_turn is not turn:
                return  # evento atrasado de un turno cancelado
            self.bus.publish(f"chat.{kind}", **data)
            if kind == "text_delta" and stream_voice:
                spoken_upto["buf"] += data.get("text", "")
                if len(spoken_upto["buf"]) > 24:
                    flush_speech()
            elif kind == "tool_use" and stream_voice:
                flush_speech(final=True)

        def on_bus(ev: dict[str, Any]) -> None:
            if ev["kind"] == "approval.requested" and self.current_turn is turn and self.state in (State.THINKING, State.SPEAKING):
                self.set_state(State.AWAITING_APPROVAL, "herramienta pendiente de aprobación")
                if self.speaker and self.cfg.tts.enabled and self.speak_responses:
                    ap = ev["approval"]
                    if ap.get("kind") == "question":
                        opts = ap.get("args", {}).get("options", [])
                        spoken = f"{ap['args'].get('question', '')} ¿{', '.join(opts[:-1])} o {opts[-1]}?" if len(opts) > 1 else ap["description"]
                    else:
                        spoken = f"¿Me das permiso para {ap['description']}?"
                    self._say(spoken[:220])

        unsub = self.bus.subscribe(on_bus)

        started = time.monotonic()
        try:
            resp: AgentResponse = await self.provider.run_turn(AgentRequest(turn.turn_id, self.session_id, prompt), turn, on_event)
        finally:
            unsub()
        if self.state == State.AWAITING_APPROVAL and not self.approvals.pending():
            self.set_state(State.THINKING, "aprobación resuelta")
        if self.current_turn is not turn:  # llegó tarde: se descarta
            return TurnResult(turn.turn_id, text, "", "", [], True, None, None, None)
        trace.consulted = list(turn.consulted)
        trace.cited = [c for c in find_citations(resp.text) if c in {p.id for p in trace.retrieved} | set(turn.consulted)]
        trace_dict = trace.to_dict()
        trace_dict["tool_calls"] = turn.tool_calls
        trace_dict["ui_events"] = turn.ui_events
        self.last_trace = trace_dict
        (self.traces_dir / f"{turn.turn_id}.json").write_text(json.dumps(trace_dict, ensure_ascii=False, indent=1), encoding="utf-8")
        display = strip_citations(resp.text)
        if resp.error and not resp.text:
            display = f"[error] {resp.error}"
        elif resp.cancelled and not resp.text:
            display = "[cancelado]"
        pending = [a.to_dict() for a in self.approvals.pending() if a.turn_id == turn.turn_id]
        result = TurnResult(turn.turn_id, text, resp.text, display, trace.cited, resp.cancelled or turn.cancelled, resp.error,
                            resp.cost_usd, resp.model, pending, int((time.monotonic() - started) * 1000))
        self.history.append({"role": "assistant", "text": display, "cited": trace.cited, "ts": time.time(),
                             "turn_id": turn.turn_id, "error": resp.error, "cancelled": result.cancelled})
        self._persist_turn(result, resp)
        self.bus.publish("chat.message", turn_id=turn.turn_id, text=display, cited=trace.cited, error=resp.error,
                         cancelled=result.cancelled, cost_usd=resp.cost_usd, model=resp.model,
                         pending_approvals=pending, trace=trace_dict)
        if resp.error and not resp.text:
            self.set_state(State.ERROR, resp.error)
            self.set_state(State.IDLE, "recuperado")
            return result
        if not turn.cancelled and display and not display.startswith("[") and self.speak_responses and self.speaker \
                and self.cfg.tts.enabled:
            if self.state != State.SPEAKING:
                self.set_state(State.SPEAKING, "reproduciendo respuesta")
            try:
                if stream_voice:
                    flush_speech(final=True)
                    await self.speaker.wait_idle()
                else:
                    await self.speaker.speak(display)
            finally:
                if self.current_turn is turn and self.state == State.SPEAKING:
                    self.set_state(State.AWAITING_APPROVAL if pending else State.IDLE, "fin de voz")
        elif self.state != State.IDLE:
            self.set_state(State.AWAITING_APPROVAL if pending else State.IDLE, "turno terminado")
        return result

    def _persist_turn(self, r: TurnResult, resp: AgentResponse) -> None:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), "turn_id": r.turn_id, "user": r.user_text,
                 "assistant": r.display_text, "cited": r.cited, "cost_usd": r.cost_usd, "model": r.model,
                 "cancelled": r.cancelled, "error": r.error, "duration_ms": r.duration_ms,
                 "sdk_session_id": resp.sdk_session_id, "usage": resp.usage}
        with open(self.sessions_dir / f"{self.session_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def trace(self, turn_id: str) -> dict[str, Any] | None:
        p = self.traces_dir / f"{turn_id}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    @staticmethod
    def last_sdk_session(sessions_dir: Path) -> str | None:
        files = sorted(sessions_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            for line in reversed(f.read_text(encoding="utf-8").splitlines()):
                try:
                    sid = json.loads(line).get("sdk_session_id")
                    if sid:
                        return sid
                except json.JSONDecodeError:
                    continue
        return None
