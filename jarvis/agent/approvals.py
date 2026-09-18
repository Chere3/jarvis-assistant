"""Aprobaciones ligadas a id, argumentos (hash) y vencimiento. Un «sí» antiguo no autoriza otra acción."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


def args_hash(tool: str, args: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({"tool": tool, "args": args}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


@dataclass
class Approval:
    id: str
    tool: str
    args: dict[str, Any]
    args_hash: str
    description: str
    turn_id: str
    created: float
    expires: float
    status: str = "pending"  # pending | approved | rejected | expired | cancelled | executed | failed
    result_text: str | None = None
    executor: Callable[[dict[str, Any]], Awaitable[Any]] | None = field(default=None, repr=False)
    on_close: Callable[[], None] | None = field(default=None, repr=False)  # avisa a quien espera (p. ej. herramienta integrada)
    kind: str = "approval"  # approval | question

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "tool": self.tool, "args": self.args, "args_hash": self.args_hash,
                "description": self.description, "turn_id": self.turn_id, "created": self.created,
                "expires": self.expires, "status": self.status, "result_text": self.result_text,
                "expired": self.is_expired(), "kind": self.kind}

    def is_expired(self) -> bool:
        return self.status == "pending" and time.time() > self.expires


class ApprovalError(Exception):
    pass


class ApprovalManager:
    def __init__(self, ttl_s: int = 120) -> None:
        self.ttl_s = ttl_s
        self._items: dict[str, Approval] = {}

    def request(self, tool: str, args: dict[str, Any], description: str, turn_id: str,
                executor: Callable[[dict[str, Any]], Awaitable[Any]]) -> Approval:
        now = time.time()
        ap = Approval(id=secrets.token_hex(3), tool=tool, args=dict(args), args_hash=args_hash(tool, args),
                      description=description, turn_id=turn_id, created=now, expires=now + self.ttl_s, executor=executor)
        self._items[ap.id] = ap
        return ap

    def get(self, approval_id: str) -> Approval | None:
        ap = self._items.get(approval_id)
        if ap and ap.is_expired():
            ap.status = "expired"
        return ap

    def pending(self) -> list[Approval]:
        for ap in self._items.values():
            if ap.is_expired():
                ap.status = "expired"
        return [a for a in self._items.values() if a.status == "pending"]

    def all(self, limit: int = 50) -> list[Approval]:
        return sorted(self._items.values(), key=lambda a: a.created, reverse=True)[:limit]

    def cancel_turn(self, turn_id: str) -> list[Approval]:
        out = []
        for ap in self._items.values():
            if ap.turn_id == turn_id and ap.status == "pending":
                ap.status = "cancelled"
                if ap.on_close:
                    ap.on_close()
                out.append(ap)
        return out

    def reject(self, approval_id: str) -> Approval:
        ap = self.get(approval_id)
        if not ap:
            raise ApprovalError("aprobación desconocida")
        if ap.status != "pending":
            raise ApprovalError(f"la aprobación ya está en estado {ap.status}")
        ap.status = "rejected"
        if ap.on_close:
            ap.on_close()
        return ap

    async def answer(self, approval_id: str, answer: str) -> Approval:
        """Responde una pregunta de aclaración (kind=question) con la etiqueta elegida o texto libre."""
        ap = self.get(approval_id)
        if not ap or ap.kind != "question":
            raise ApprovalError("pregunta desconocida")
        if ap.status != "pending":
            raise ApprovalError(f"la pregunta ya está en estado {ap.status}")
        ap.status = "approved"
        try:
            await ap.executor({"answer": answer}) if ap.executor else None
            ap.status = "executed"
            ap.result_text = answer
        except Exception as e:
            ap.status = "failed"
            ap.result_text = str(e)
            raise
        return ap

    async def approve(self, approval_id: str, expected_hash: str | None = None) -> Approval:
        ap = self.get(approval_id)
        if not ap:
            raise ApprovalError("aprobación desconocida")
        if ap.status == "expired":
            raise ApprovalError("la aprobación venció; vuelve a pedir la acción")
        if ap.status != "pending":
            raise ApprovalError(f"la aprobación ya está en estado {ap.status}")
        if args_hash(ap.tool, ap.args) != ap.args_hash or (expected_hash and expected_hash != ap.args_hash):
            ap.status = "rejected"
            raise ApprovalError("los argumentos de la acción cambiaron; aprobación inválida")
        ap.status = "approved"
        try:
            result = await ap.executor(ap.args) if ap.executor else None
            ap.status = "executed"
            ap.result_text = getattr(result, "text", None) or (str(result) if result is not None else "hecho")
        except Exception as e:
            ap.status = "failed"
            ap.result_text = str(e)
            raise
        return ap
