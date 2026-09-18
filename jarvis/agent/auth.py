"""Política de autenticación: el asistente usa SIEMPRE la sesión de Claude Code (claude.ai) a través del Agent SDK.
Nunca se usa la API directa: cualquier ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN del entorno se retira antes de lanzar el CLI."""
from __future__ import annotations

import os

API_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def ensure_claude_code_auth(logger=None) -> list[str]:
    removed = []
    for var in API_VARS:
        if os.environ.pop(var, None) is not None:
            removed.append(var)
    if removed and logger:
        logger.info("variables de API ignoradas (%s): el asistente usa la sesión de Claude Code", ", ".join(removed))
    return removed
