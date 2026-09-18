"""Ensamblado de componentes (un único lugar donde se construye el sistema)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent.approvals import ApprovalManager
from .agent.claude_adapter import AgentSDKProvider, FakeProvider
from .agent.prompts import system_prompt
from .agent.tools import ToolRegistry
from .config import Config, load_config
from .memory.events import EventBus
from .memory.store import MemoryService
from .orchestrator.session import Orchestrator
from .runtime.logs import setup_logging


@dataclass
class App:
    cfg: Config
    bus: EventBus
    service: MemoryService
    registry: ToolRegistry
    approvals: ApprovalManager
    provider: Any
    orchestrator: Orchestrator
    logger: Any
    demo: bool = False


def build_app(cfg: Config | None = None, demo: bool = False, speaker: Any = None, speak_responses: bool = False,
              resume: bool = False, demo_script: list | None = None) -> App:
    cfg = cfg or load_config()
    logger = setup_logging(cfg.logging.dir, cfg.logging.level)
    bus = EventBus()
    service = MemoryService(cfg.memory.dir, cfg.memory.runtime_dir, cfg.assistant.timezone, bus, logger)
    approvals = ApprovalManager(cfg.tools.approval_ttl_s)
    registry = ToolRegistry(cfg, service, approvals, bus, logger)
    if demo or cfg.claude.provider == "fake":
        provider: Any = FakeProvider(registry, demo_script)
        demo = True
    else:
        resume_id = Orchestrator.last_sdk_session(cfg.memory.runtime_dir / "sessions") if resume else None
        provider = AgentSDKProvider(cfg, registry, system_prompt(cfg), logger, resume_session=resume_id)
    registry.set_model_provider(provider)
    orch = Orchestrator(cfg, service, provider, registry, approvals, bus, logger, speaker=speaker,
                        speak_responses=speak_responses)
    return App(cfg, bus, service, registry, approvals, provider, orch, logger, demo)
