"""Configuración centralizada y validada.

El archivo vive fuera del repositorio (por defecto ~/.jarvis/config.yaml) y nunca
contiene secretos: las claves se leen de variables de entorno.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, field_validator


def jarvis_home() -> Path:
    return Path(os.environ.get("JARVIS_HOME", "~/.jarvis")).expanduser()


def _system_timezone() -> str:
    try:
        target = os.readlink("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


class AssistantConfig(BaseModel):
    display_name: str = "Jarvis"
    language: str = "es"
    timezone: str = Field(default_factory=_system_timezone)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"zona horaria desconocida: {v}") from e
        return v

    @field_validator("display_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 40:
            raise ValueError("display_name debe tener entre 1 y 40 caracteres")
        return v


class ClaudeConfig(BaseModel):
    provider: Literal["agent_sdk", "fake"] = "agent_sdk"  # agent_sdk = Claude Code (sesión claude.ai); nunca API directa
    model: str | None = None  # None = modelo por defecto del CLI; se valida con `jarvis doctor --live`
    builtin_tools: bool = True  # todas las herramientas de Claude Code (Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebFetch, Task…)
    permission_mode: Literal["auto", "default"] = "auto"  # auto = el clasificador interno de Claude Code decide; solo escala lo dudoso al usuario
    builtin_approval: Literal["writes", "always", "never"] = "writes"  # con permission_mode=default: qué herramientas piden aprobación
    cwd: Path | None = None  # directorio de trabajo del agente (None = carpeta personal)
    # Red de seguridad propia: aunque el clasificador automático lo apruebe, estos comandos SIEMPRE te piden permiso.
    always_confirm_patterns: list[str] = Field(default_factory=lambda: [
        r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*|--recursive)", r"\brm\s+-[a-zA-Z]*f", r"\bsudo\b", r"\bmkfs\b", r"\bdd\s+if=",
        r"\bgit\s+push\b.*(-f|--force)", r"\bgit\s+reset\s+--hard", r"\bkill(all)?\b", r"\bchmod\s+-R", r"\bchown\b",
        r">\s*/(etc|usr|System|Library)", r"\bdiskutil\b", r"\blaunchctl\s+(unload|bootout)", r"\bdefaults\s+write\b",
        r"\bosascript\b.*(delete|erase|empty)", r"\bshutdown\b", r"\breboot\b", r"\bcurl\b.*\|\s*(ba)?sh"])
    setting_sources: list[Literal["user", "project", "local"]] = Field(default_factory=list)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = "low"  # asistente de voz: rapidez; sube a medium/high si lo necesitas
    max_turns: int = 8  # límite de turnos de herramientas por petición
    timeout_s: float = 180.0
    max_budget_usd_per_turn: float | None = None
    retries: int = 1  # reintentos solo para errores transitorios sin efectos externos


class MemoryConfig(BaseModel):
    dir: Path = Field(default_factory=lambda: jarvis_home() / "data" / "memory")
    runtime_dir: Path = Field(default_factory=lambda: jarvis_home() / "data" / "runtime")
    auto_save_preferences: bool = True
    save_conversation_summaries: bool = False
    context_budget_chars: int = 12000
    max_pages: int = 8
    link_expansion: int = 4
    max_source_bytes: int = 5_000_000


class WakeWordConfig(BaseModel):
    provider: Literal["openwakeword", "porcupine", "none"] = "openwakeword"
    keyword_label: str = "Hey Jarvis"
    model_path: str = "hey_jarvis"  # nombre integrado o ruta a .onnx/.ppn
    language: str = "en"
    sensitivity: float = 0.5
    cooldown_s: float = 2.0
    min_consecutive_frames: int = 1

    @field_validator("sensitivity")
    @classmethod
    def _sens(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("sensitivity debe estar entre 0 y 1")
        return v


class AudioConfig(BaseModel):
    input_device: str | int | None = None
    output_device: str | int | None = None
    sample_rate: int = 16000
    frame_ms: int = 80  # 1280 muestras a 16 kHz (compatible con openwakeword)
    preroll_ms: int = 600  # búfer circular en RAM, se descarta sin guardar
    max_utterance_s: float = 15.0
    end_silence_ms: int = 1100
    min_speech_ms: int = 300
    vad_aggressiveness: int = 2
    follow_up_window_s: float = 0.0


class STTConfig(BaseModel):
    provider: Literal["faster_whisper", "mlx_whisper", "none"] = "mlx_whisper"  # mlx = GPU de Apple Silicon (más precisión, misma latencia)
    model: str = "mlx-community/whisper-large-v3-turbo"  # mlx_whisper: repo de HF · faster_whisper: tiny|base|small|medium|large-v3-turbo
    compute_type: str = "int8"
    language: str = "es"
    beam_size: int = 3
    initial_prompt: str = "Petición en español a un asistente llamado Jarvis."  # sesga el vocabulario (nombres, tildes)


class TTSConfig(BaseModel):
    provider: Literal["kokoro", "macos_say", "none"] = "kokoro"  # kokoro = voz neuronal local realista
    voice: str = "ef_dora"  # kokoro: ef_dora | em_alex | em_santa · macos_say: Paulina | Mónica (o Premium instaladas)
    rate: int = 175  # solo macos_say (palabras por minuto)
    speed: float = 1.0  # solo kokoro (1.0 = normal)
    enabled: bool = True


class ToolsConfig(BaseModel):
    workspace_dir: Path = Field(default_factory=lambda: jarvis_home() / "workspace")
    allowed_read_dirs: list[Path] = Field(default_factory=list)
    allowed_open_dirs: list[Path] = Field(default_factory=list)
    allowed_url_schemes: list[str] = Field(default_factory=lambda: ["https"])
    url_requires_approval: bool = True
    approval_ttl_s: int = 120


class UIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765

    @field_validator("host")
    @classmethod
    def _loopback(cls, v: str) -> str:
        if v not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("el panel solo escucha en loopback")
        return v


class LoggingConfig(BaseModel):
    level: str = "INFO"
    dir: Path = Field(default_factory=lambda: jarvis_home() / "data" / "runtime" / "logs")
    log_retrieval: bool = True  # registra ids recuperados y tamaño de contexto (no el contenido)


class Config(BaseModel):
    assistant: AssistantConfig = Field(default_factory=AssistantConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    wake_word: WakeWordConfig = Field(default_factory=WakeWordConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def expand(self) -> "Config":
        """Expande ~ en rutas y rellena listas de directorios permitidos."""
        m = self.memory
        m.dir = m.dir.expanduser()
        m.runtime_dir = m.runtime_dir.expanduser()
        t = self.tools
        t.workspace_dir = t.workspace_dir.expanduser()
        t.allowed_read_dirs = [p.expanduser() for p in t.allowed_read_dirs] or [t.workspace_dir]
        t.allowed_open_dirs = [p.expanduser() for p in t.allowed_open_dirs] or [t.workspace_dir]
        self.logging.dir = self.logging.dir.expanduser()
        self.claude.cwd = (self.claude.cwd or Path("~")).expanduser()
        return self


def config_path() -> Path:
    return Path(os.environ.get("JARVIS_CONFIG", jarvis_home() / "config.yaml")).expanduser()


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    data: dict = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    return Config.model_validate(data).expand()


def save_config(cfg: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.model_dump(mode="json")
    tmp = path.with_suffix(".yaml.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)
    return path


def config_dump_safe(cfg: Config) -> dict:
    """Volcado para mostrar: no hay secretos en la configuración, pero se
    informa solo la presencia de variables de entorno sensibles."""
    d = cfg.model_dump(mode="json")
    d["env"] = {
        "ANTHROPIC_API_KEY": ("presente (IGNORADA: solo se usa la sesión de Claude Code)" if os.environ.get("ANTHROPIC_API_KEY") else "ausente (no se usa)"),
        "PICOVOICE_ACCESS_KEY": "presente" if os.environ.get("PICOVOICE_ACCESS_KEY") else "ausente",
    }
    return d
