"""Síntesis de voz local con `say` (macOS), cancelable. El texto se pasa como argumento, sin shell."""
from __future__ import annotations

import asyncio
import re
import subprocess
from typing import Protocol

from ..config import Config


class TTSProvider(Protocol):
    name: str

    async def speak(self, text: str) -> bool: ...
    async def stop(self) -> None: ...
    def is_speaking(self) -> bool: ...
    def status(self) -> dict: ...


def list_spanish_voices() -> list[dict]:
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return []
    voices = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s{2,}([a-z]{2}_[A-Z]{2})\s", line)
        if m and m.group(2).startswith("es_"):
            voices.append({"name": m.group(1).strip(), "locale": m.group(2)})
    return voices


def clean_for_speech(text: str) -> str:
    text = re.sub(r"\[mem:mem_[0-9]{8}_[a-f0-9]{6}\]", "", text)
    text = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"`{1,3}[^`]*`{1,3}", " ", text)
    text = re.sub(r"[*_#>]+", " ", text)
    text = re.sub(r"https?://\S+", "un enlace", text)
    return re.sub(r"\s+", " ", text).strip()


class MacSayTTS:
    name = "macos_say"

    def __init__(self, voice: str, rate: int = 175) -> None:
        self.voice = voice
        self.rate = rate
        self.proc: asyncio.subprocess.Process | None = None
        self._stopped = False

    async def speak(self, text: str) -> bool:
        text = clean_for_speech(text)
        if not text:
            return True
        await self.stop()
        self._stopped = False
        self.proc = await asyncio.create_subprocess_exec("say", "-v", self.voice, "-r", str(self.rate), text,
                                                         stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            await self.proc.wait()
        finally:
            done = not self._stopped and self.proc.returncode == 0
            self.proc = None
        return done

    async def stop(self) -> None:
        p = self.proc
        if p and p.returncode is None:
            self._stopped = True
            try:
                p.terminate()
                await asyncio.wait_for(p.wait(), timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def is_speaking(self) -> bool:
        return bool(self.proc and self.proc.returncode is None)

    def status(self) -> dict:
        return {"provider": self.name, "voice": self.voice, "rate": self.rate, "speaking": self.is_speaking()}


class NullTTS:
    name = "none"

    async def speak(self, text: str) -> bool:
        return True

    async def stop(self) -> None:
        pass

    def is_speaking(self) -> bool:
        return False

    def status(self) -> dict:
        return {"provider": "none"}


def make_tts(cfg: Config) -> TTSProvider:
    if not cfg.tts.enabled or cfg.tts.provider == "none":
        return NullTTS()
    if cfg.tts.provider == "kokoro":
        from .tts_kokoro import KokoroTTS, SPANISH_VOICES, is_installed
        if is_installed():
            voice = cfg.tts.voice if cfg.tts.voice in SPANISH_VOICES else "ef_dora"
            return KokoroTTS(voice, cfg.tts.speed, cache_dir=cfg.memory.runtime_dir / "cache" / "tts")
        # sin modelo descargado: voz del sistema como respaldo (jarvis doctor lo avisa)
    voices = list_spanish_voices()
    names = {v["name"] for v in voices}
    preferred = [v["name"] for v in voices if "Premium" in v["name"] or "Enhanced" in v["name"] or "Mejorada" in v["name"]]
    voice = cfg.tts.voice if cfg.tts.voice in names else (preferred[0] if preferred else (voices[0]["name"] if voices else "Paulina"))
    return MacSayTTS(voice, cfg.tts.rate)
