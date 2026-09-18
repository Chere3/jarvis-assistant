import os
from pathlib import Path

import pytest

from jarvis.memory.store import MemoryService


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path_factory, monkeypatch):
    """Ninguna prueba debe tocar ~/.jarvis (config, memoria, app.json)."""
    home = tmp_path_factory.mktemp("jarvis-home")
    monkeypatch.setenv("JARVIS_HOME", str(home))
    monkeypatch.setenv("JARVIS_CONFIG", str(home / "config.yaml"))
    yield


@pytest.fixture
def mem_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def service(mem_dir: Path) -> MemoryService:
    return MemoryService(mem_dir / "memory", mem_dir / "runtime", timezone="America/Mexico_City")
