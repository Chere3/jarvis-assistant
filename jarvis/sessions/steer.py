"""Envía un mensaje a una sesión de Claude Code en marcha por su socket Unix de entrada.

Una línea JSON en el socket. Llega con autoridad de *par*: la sesión lo recibe como un turno nuevo
(o lo encola si está ocupada), pero NO puede cerrar un prompt de permisos ni un diálogo modal.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

SENT = "sent"          # los bytes salieron por el socket; no se lee confirmación de vuelta
NOT_LIVE = "not_live"
REFUSED = "refused"
FAILED = "failed"


def post_to_session(socket_path: str | None, prompt: str, timeout: float = 5.0) -> str:
    if not prompt or not prompt.strip():
        return REFUSED
    if not socket_path or not Path(socket_path).exists():
        return NOT_LIVE
    lines = []
    token = os.getenv("CLAUDE_CODE_MESSAGING_TOKEN", "")
    if token:
        lines.append(json.dumps({"type": "auth", "token": token}))
    lines.append(json.dumps({"type": "user", "message": {"role": "user", "content": prompt.strip()}}))
    payload = ("\n".join(lines) + "\n").encode()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
    except (ConnectionRefusedError, FileNotFoundError):
        return NOT_LIVE
    except OSError:
        return FAILED
    try:
        sock.sendall(payload)
    except OSError:
        return FAILED
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return SENT


OUTCOME_LABEL = {SENT: "enviado", NOT_LIVE: "la sesión ya no está viva", REFUSED: "mensaje vacío, no se envió",
                 FAILED: "no se pudo entregar"}
