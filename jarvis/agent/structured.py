"""Peticiones puntuales a Claude que devuelven JSON validado (sin herramientas, con presupuesto)."""
from __future__ import annotations

import json
import re
from typing import Any

from ..config import Config


def parse_json_block(text: str) -> Any:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("la respuesta no contiene JSON")
    return json.loads(text[start:])


async def ask_json(cfg: Config, system: str, prompt: str, timeout_s: float = 120.0, effort: str | None = "low") -> tuple[Any, dict[str, Any]]:
    """Una sola petición sin herramientas. Devuelve (json, meta)."""
    import asyncio
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query
    from .auth import ensure_claude_code_auth
    ensure_claude_code_auth()

    kwargs: dict[str, Any] = dict(system_prompt=system, tools=[], allowed_tools=[], setting_sources=[],
                                  permission_mode="dontAsk", max_turns=1)
    if cfg.claude.model:
        kwargs["model"] = cfg.claude.model
    if effort:
        kwargs["effort"] = effort
    texts: list[str] = []
    meta: dict[str, Any] = {}

    async def run() -> None:
        async for msg in query(prompt=prompt, options=ClaudeAgentOptions(**kwargs)):
            if isinstance(msg, AssistantMessage):
                meta["model"] = msg.model
                texts.extend(b.text for b in msg.content if isinstance(b, TextBlock))
            elif isinstance(msg, ResultMessage):
                meta["cost_usd"] = msg.total_cost_usd
                if msg.is_error:
                    raise RuntimeError("; ".join(msg.errors or []) or msg.result or "error del proveedor")

    await asyncio.wait_for(run(), timeout=timeout_s)
    return parse_json_block("\n".join(texts)), meta
