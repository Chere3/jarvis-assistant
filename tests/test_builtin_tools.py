"""Herramientas integradas de Claude Code: la política de aprobación se aplica en código (gate_builtin)."""
import asyncio

import pytest

from tests.test_orchestration import make


async def test_readonly_builtins_auto_allowed(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    reg.turn = reg.turn.__class__("t1", "s")
    assert await reg.gate_builtin("Read", {"file_path": "/etc/hosts"}, "writes") is True
    assert await reg.gate_builtin("Glob", {"pattern": "*.md"}, "writes") is True
    assert not approvals.pending()


async def test_write_builtins_require_approval_and_can_be_approved(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    cfg.tools.approval_ttl_s = 5
    reg.turn = reg.turn.__class__("t2", "s")
    task = asyncio.create_task(reg.gate_builtin("Bash", {"command": "ls"}, "writes"))
    await asyncio.sleep(0.05)
    pend = approvals.pending()
    assert len(pend) == 1 and "ls" in pend[0].description and not task.done()
    await approvals.approve(pend[0].id, expected_hash=pend[0].args_hash)
    assert await task is True
    assert reg.turn.effects_executed


async def test_write_builtin_rejected_and_timeout(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    cfg.tools.approval_ttl_s = 1
    reg.turn = reg.turn.__class__("t3", "s")
    task = asyncio.create_task(reg.gate_builtin("Write", {"file_path": "/tmp/x", "content": "y"}, "writes"))
    await asyncio.sleep(0.05)
    approvals.reject(approvals.pending()[0].id)
    r = await task
    assert isinstance(r, str) and "rechaz" in r
    task = asyncio.create_task(reg.gate_builtin("Edit", {"file_path": "/tmp/x"}, "writes"))
    r = await task  # nadie responde → vence
    assert isinstance(r, str) and "tiempo" in r and not approvals.pending()


async def test_policy_always_and_never(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    reg.turn = reg.turn.__class__("t4", "s")
    assert await reg.gate_builtin("Bash", {"command": "rm -rf x"}, "never") is True  # política explícita del usuario
    cfg.tools.approval_ttl_s = 1
    r = await reg.gate_builtin("Read", {"file_path": "/etc/hosts"}, "always")
    assert isinstance(r, str)  # con "always" hasta la lectura espera aprobación (y aquí vence)


async def test_cancelled_turn_denies_builtin(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    reg.turn = reg.turn.__class__("t5", "s", cancelled=True)
    assert await reg.gate_builtin("Bash", {"command": "ls"}, "never") == "turno cancelado"


def test_config_defaults():
    from jarvis.config import Config
    c = Config().expand()
    assert c.claude.builtin_tools and c.claude.builtin_approval == "writes"
    assert c.tts.provider == "kokoro" and c.tts.voice == "ef_dora"
    assert str(c.claude.cwd).startswith("/")
