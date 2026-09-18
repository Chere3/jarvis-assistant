"""Adaptador tipado hacia Claude: streaming, timeouts, cancelación, reintentos limitados y consumo."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from ..config import Config
from .tools import ToolRegistry, TurnContext

OnEvent = Callable[[str, dict[str, Any]], None]


@dataclass
class AgentRequest:
    turn_id: str
    session_id: str
    prompt: str  # petición del usuario + contexto recuperado (datos)


@dataclass
class AgentResponse:
    text: str = ""
    model: str | None = None
    cost_usd: float | None = None
    usage: dict[str, Any] | None = None
    duration_ms: int = 0
    terminal_reason: str | None = None
    cancelled: bool = False
    error: str | None = None
    sdk_session_id: str | None = None
    tool_uses: list[dict[str, Any]] = field(default_factory=list)


class ClaudeProvider(Protocol):
    name: str

    async def start(self) -> None: ...
    async def run_turn(self, req: AgentRequest, turn: TurnContext, on_event: OnEvent) -> AgentResponse: ...
    async def cancel(self) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> dict[str, Any]: ...


TRANSIENT_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


class AgentSDKProvider:
    """Claude Agent SDK con herramientas propias en proceso y permisos cerrados."""

    name = "agent_sdk"

    def __init__(self, cfg: Config, registry: ToolRegistry, system_prompt: str, logger: Any = None,
                 resume_session: str | None = None) -> None:
        self.cfg = cfg
        self.registry = registry
        self.system_prompt = system_prompt
        self.log = logger
        self.resume_session = resume_session
        self.client = None
        self.connected = False
        self.last_model: str | None = None
        self.last_error: str | None = None
        self.sdk_session_id: str | None = None
        self._cancel_flag = False
        self._turn_lock = asyncio.Lock()
        self.on_rate_limit: Callable[[Any], None] | None = None  # recibe el RateLimitInfo del CLI (uso de la suscripción)

    def _options(self):
        from claude_agent_sdk import ClaudeAgentOptions
        from claude_agent_sdk.types import PermissionResultAllow, PermissionResultDeny

        allowed = self.registry.sdk_tool_names()
        c = self.cfg.claude
        registry = self.registry

        async def can_use_tool(tool_name: str, input_data: dict, context: Any):
            # Herramientas propias: la política vive dentro de cada una (ToolRegistry.execute).
            if tool_name in allowed:
                return PermissionResultAllow(updated_input=input_data)
            if tool_name == "AskUserQuestion":
                res = await registry.gate_question(input_data)
                if isinstance(res, dict):
                    return PermissionResultAllow(updated_input=res)
                return PermissionResultDeny(message=str(res))
            if not c.builtin_tools:
                return PermissionResultDeny(message=f"herramienta no autorizada para el asistente: {tool_name}")
            # Herramientas integradas de Claude Code. En modo "auto" el clasificador de Claude Code ya aprobó lo seguro:
            # todo lo que llega aquí es lo que él escaló, así que se pregunta siempre al usuario (resumen breve).
            policy = "always" if c.permission_mode == "auto" else c.builtin_approval
            if registry.turn.untrusted_source and tool_name not in registry.READ_ONLY_BUILTINS:
                policy = "always"
            decision = await registry.gate_builtin(tool_name, input_data, policy)
            if decision is True:
                return PermissionResultAllow(updated_input=input_data)
            return PermissionResultDeny(message=str(decision))

        import re
        from claude_agent_sdk import HookMatcher
        patterns = [re.compile(p, re.I) for p in c.always_confirm_patterns]

        async def guardrail(input_data: dict, tool_use_id: Any, context: Any) -> dict:
            """PreToolUse: obliga a preguntar al usuario (aunque el modo auto lo aprobara) ante comandos destructivos."""
            if input_data.get("tool_name") == "Bash":
                cmd = str(input_data.get("tool_input", {}).get("command", ""))
                if any(p.search(cmd) for p in patterns):
                    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                                   "permissionDecisionReason": "comando potencialmente destructivo: requiere confirmación del usuario"}}
            return {}

        async def taint_gate(input_data: dict, tool_use_id: Any, context: Any) -> dict:
            """PreToolUse: en un turno contaminado (leyó web/sesiones), toda herramienta con efectos pide permiso al usuario;
            las que contaminan (WebFetch, WebSearch, leer transcripts) marcan el turno aunque el modo auto las apruebe."""
            name = str(input_data.get("tool_name") or "")
            args = input_data.get("tool_input") or {}
            registry.note_builtin_taint(name, args if isinstance(args, dict) else {})
            if registry.turn.untrusted_source and name not in registry.READ_ONLY_BUILTINS and not name.startswith("mcp__"):
                return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                               "permissionDecisionReason": f"este turno leyó contenido no confiable ({registry.turn.untrusted_source}); requiere confirmación del usuario"}}
            return {}

        kwargs: dict[str, Any] = dict(
            system_prompt=self.system_prompt,
            hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[guardrail]), HookMatcher(matcher=None, hooks=[taint_gate])]},
            allowed_tools=[],  # nada preaprobado: cada llamada pasa por can_use_tool
            permission_mode=c.permission_mode if c.builtin_tools else "default",
            can_use_tool=can_use_tool,
            mcp_servers={"jarvis": self.registry.build_mcp_server()},
            strict_mcp_config=True,
            setting_sources=list(c.setting_sources),  # por defecto no hereda configuración del entorno de desarrollo
            include_partial_messages=True,
            max_turns=c.max_turns,
            cwd=str(c.cwd or self.cfg.tools.workspace_dir),
            stderr=(lambda line: self.log.debug("sdk: %s", line.rstrip()) if self.log else None),
        )
        if c.builtin_tools:
            kwargs["tools"] = {"type": "preset", "preset": "claude_code"}  # todas las herramientas de Claude Code
        else:
            kwargs["tools"] = []
            kwargs["disallowed_tools"] = ["Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep",
                                          "WebFetch", "WebSearch", "Task", "Agent", "TodoWrite", "KillShell", "BashOutput"]
        if c.model:
            kwargs["model"] = c.model
        if c.effort:
            kwargs["effort"] = c.effort
        if c.max_budget_usd_per_turn:
            kwargs["max_budget_usd"] = c.max_budget_usd_per_turn
        if self.resume_session:
            kwargs["resume"] = self.resume_session
        return ClaudeAgentOptions(**kwargs)

    async def start(self) -> None:
        from claude_agent_sdk import ClaudeSDKClient
        from .auth import ensure_claude_code_auth
        ensure_claude_code_auth(self.log)  # solo sesión de Claude Code, nunca API directa
        self.cfg.tools.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.client = ClaudeSDKClient(options=self._options())
        try:
            await asyncio.wait_for(self.client.connect(), timeout=60)
            self.connected = True
            self.last_error = None
        except Exception as e:
            self.connected = False
            self.last_error = f"{type(e).__name__}: {e}"
            raise

    async def stop(self) -> None:
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.connected = False

    async def cancel(self) -> None:
        self._cancel_flag = True
        if self.client and self.connected:
            try:
                await asyncio.wait_for(self.client.interrupt(), timeout=5)
            except Exception as e:
                if self.log:
                    self.log.warning("interrupt falló: %s", e)

    async def set_model(self, model: str) -> tuple[bool, str]:
        """Cambia el modelo en la sesión viva del SDK (aplica al siguiente turno)."""
        self.cfg.claude.model = model
        if self.client and self.connected:
            try:
                await asyncio.wait_for(self.client.set_model(model), timeout=10)
            except Exception as e:
                return False, f"{type(e).__name__}: {e}"
        self.last_model = model
        return True, "ok"

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "connected": self.connected, "model": self.last_model or self.cfg.claude.model or "(por defecto)",
                "last_error": self.last_error, "sdk_session_id": self.sdk_session_id}

    async def run_turn(self, req: AgentRequest, turn: TurnContext, on_event: OnEvent) -> AgentResponse:
        async with self._turn_lock:
            attempts = 0
            while True:
                attempts += 1
                resp = await self._run_once(req, turn, on_event)
                transient = resp.error and resp.error.startswith("transient")
                if transient and attempts <= self.cfg.claude.retries and not turn.effects_executed and not turn.cancelled:
                    on_event("status", {"text": "error transitorio, reintentando…"})
                    await asyncio.sleep(1.5 * attempts)
                    continue
                return resp

    async def _run_once(self, req: AgentRequest, turn: TurnContext, on_event: OnEvent) -> AgentResponse:
        from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock
        from claude_agent_sdk import CLIConnectionError, ProcessError, CLIJSONDecodeError
        try:
            from claude_agent_sdk import RateLimitEvent
        except ImportError:  # pragma: no cover
            RateLimitEvent = None  # type: ignore

        if not self.client or not self.connected:
            try:
                await self.start()
            except Exception as e:
                return AgentResponse(error=f"sin conexión con Claude: {e}", terminal_reason="error")
        self._cancel_flag = False
        self.registry.turn = turn
        started = time.monotonic()
        resp = AgentResponse()
        texts: list[str] = []
        streamed = ""
        try:
            await self.client.query(req.prompt)
            deadline = started + self.cfg.claude.timeout_s
            it = self.client.receive_response().__aiter__()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    await self.cancel()
                    resp.error = "timeout del proveedor"
                    resp.terminal_reason = "timeout"
                    break
                try:
                    msg = await asyncio.wait_for(it.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    await self.cancel()
                    resp.error = "timeout del proveedor"
                    resp.terminal_reason = "timeout"
                    break
                if isinstance(msg, StreamEvent):
                    ev = msg.event
                    if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                        delta = ev["delta"].get("text", "")
                        streamed += delta
                        on_event("text_delta", {"turn_id": req.turn_id, "text": delta})
                elif isinstance(msg, AssistantMessage):
                    self.last_model = msg.model or self.last_model
                    for b in msg.content:
                        if isinstance(b, TextBlock) and b.text.strip():
                            texts.append(b.text)
                        elif isinstance(b, ToolUseBlock):
                            resp.tool_uses.append({"name": b.name, "input": b.input})
                            on_event("tool_use", {"turn_id": req.turn_id, "name": b.name.replace("mcp__jarvis__", "")})
                    if msg.error:
                        resp.error = f"error del modelo: {msg.error}"
                elif RateLimitEvent is not None and isinstance(msg, RateLimitEvent):
                    if self.on_rate_limit:
                        try:
                            self.on_rate_limit(msg.rate_limit_info)
                        except Exception:
                            pass
                elif isinstance(msg, ResultMessage):
                    resp.cost_usd = msg.total_cost_usd
                    resp.usage = msg.usage
                    resp.terminal_reason = msg.terminal_reason or msg.subtype
                    resp.sdk_session_id = msg.session_id
                    self.sdk_session_id = msg.session_id
                    if msg.is_error:
                        status = msg.api_error_status
                        detail = "; ".join(msg.errors or []) or msg.result or msg.subtype
                        prefix = "transient: " if status in TRANSIENT_STATUS else ""
                        resp.error = f"{prefix}{detail} (http {status})" if status else f"{prefix}{detail}"
                    if msg.terminal_reason in ("aborted_streaming", "aborted_tools"):
                        resp.cancelled = True
                    break
        except (CLIConnectionError, ProcessError, CLIJSONDecodeError) as e:
            self.connected = False
            self.last_error = f"{type(e).__name__}: {e}"
            resp.error = f"transient: conexión con Claude perdida ({type(e).__name__})"
        except asyncio.CancelledError:
            await self.cancel()
            resp.cancelled = True
        except Exception as e:  # pragma: no cover
            self.last_error = f"{type(e).__name__}: {e}"
            resp.error = f"fallo del proveedor: {type(e).__name__}: {e}"
        if self._cancel_flag:
            resp.cancelled = True
        resp.text = "\n\n".join(texts).strip() or streamed.strip()
        resp.model = self.last_model
        resp.duration_ms = int((time.monotonic() - started) * 1000)
        return resp


class FakeProvider:
    """Solo para pruebas y `--demo`. Nunca se usa en modo normal."""

    name = "fake"

    def __init__(self, registry: ToolRegistry, script: list[Any] | None = None, delay: float = 0.0) -> None:
        self.registry = registry
        self.script = list(script or [])
        self.delay = delay
        self.connected = False
        self.cancel_flag = False
        self.calls: list[str] = []

    async def start(self) -> None:
        self.connected = True

    async def stop(self) -> None:
        self.connected = False

    async def cancel(self) -> None:
        self.cancel_flag = True

    def status(self) -> dict[str, Any]:
        return {"provider": "fake (DEMO)", "connected": self.connected, "model": "simulado", "last_error": None}

    async def run_turn(self, req: AgentRequest, turn: TurnContext, on_event: OnEvent) -> AgentResponse:
        self.cancel_flag = False
        self.registry.turn = turn
        self.calls.append(req.prompt)
        step = self.script.pop(0) if self.script else "(demo) Respuesta simulada."
        texts: list[str] = []
        steps = step if isinstance(step, list) else [step]
        for s in steps:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.cancel_flag or turn.cancelled:
                return AgentResponse(text=" ".join(texts), cancelled=True, model="simulado")
            if isinstance(s, tuple) and s[0] == "tool":
                res = await self.registry.execute(s[1], s[2], turn)
                on_event("tool_use", {"turn_id": req.turn_id, "name": s[1]})
                texts.append(res.text if len(s) < 4 else s[3](res))
            else:
                for word in str(s).split(" "):
                    on_event("text_delta", {"turn_id": req.turn_id, "text": word + " "})
                texts.append(str(s))
        return AgentResponse(text=" ".join(texts).strip(), model="simulado", cost_usd=0.0, terminal_reason="completed")
