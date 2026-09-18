"""Transcripción local (faster-whisper). El audio no sale del equipo."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..config import STTConfig


@dataclass
class Transcript:
    text: str
    language: str | None
    duration_s: float
    elapsed_s: float


class STTProvider(Protocol):
    name: str

    def load(self) -> None: ...
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript: ...
    def status(self) -> dict: ...


class FasterWhisperSTT:
    name = "faster_whisper"

    def __init__(self, cfg: STTConfig) -> None:
        self.cfg = cfg
        self.model = None
        self._lock = threading.Lock()
        self.error: str | None = None
        self.load_s: float | None = None

    def load(self) -> None:
        with self._lock:
            if self.model:
                return
            from faster_whisper import WhisperModel
            t = time.monotonic()
            self.model = WhisperModel(self.cfg.model, device="cpu", compute_type=self.cfg.compute_type)
            self.load_s = time.monotonic() - t

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        self.load()
        t = time.monotonic()
        samples = normalize(audio)
        segments, info = self.model.transcribe(samples, language=self.cfg.language or None, beam_size=self.cfg.beam_size,
                                               vad_filter=True, condition_on_previous_text=False,
                                               initial_prompt=self.cfg.initial_prompt or None)
        text = " ".join(s.text.strip() for s in segments).strip()
        return Transcript(text=clean_transcript(text), language=getattr(info, "language", None),
                          duration_s=len(audio) / sample_rate, elapsed_s=time.monotonic() - t)

    def status(self) -> dict:
        return {"provider": self.name, "model": self.cfg.model, "loaded": self.model is not None, "load_s": self.load_s,
                "error": self.error}


def normalize(audio: np.ndarray) -> np.ndarray:
    """int16 → float32 con pico a -3 dB (micrófonos flojos degradan mucho a Whisper)."""
    x = audio.astype(np.float32) / 32768.0
    peak = float(np.abs(x).max()) if x.size else 0.0
    if peak > 1e-4:
        x = x * min(0.7 / peak, 20.0)
    return x


_WAKE_PREFIX = re.compile(r"^(?:\W*(?:hey|oye|ok|hola|eh)\s+)?\W*(?:jarvis|yarvis|jarbis|charvis|harvis)[\s,.:;!¡¿?-]*", re.I)


def clean_transcript(text: str) -> str:
    """Quita la palabra de activación si se coló al principio y espacios sobrantes."""
    text = _WAKE_PREFIX.sub("", text.strip(), count=1).strip()
    return text[:1].upper() + text[1:] if text else text


class MlxWhisperSTT:
    """Whisper sobre Apple MLX (GPU de Apple Silicon): permite modelos grandes con baja latencia."""

    name = "mlx_whisper"

    def __init__(self, cfg: STTConfig) -> None:
        self.cfg = cfg
        self.repo = cfg.model if "/" in cfg.model else "mlx-community/whisper-large-v3-turbo"
        self.loaded = False
        self.error: str | None = None
        self.load_s: float | None = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self.loaded:
                return
            import mlx_whisper
            t = time.monotonic()
            mlx_whisper.transcribe(np.zeros(16000, dtype=np.float32), path_or_hf_repo=self.repo, language=self.cfg.language, fp16=True)
            self.load_s = time.monotonic() - t
            self.loaded = True

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        import mlx_whisper
        self.load()
        t = time.monotonic()
        r = mlx_whisper.transcribe(normalize(audio), path_or_hf_repo=self.repo, language=self.cfg.language or None, fp16=True,
                                   initial_prompt=self.cfg.initial_prompt or None, condition_on_previous_text=False)
        return Transcript(text=clean_transcript(str(r.get("text", ""))), language=r.get("language"),
                          duration_s=len(audio) / sample_rate, elapsed_s=time.monotonic() - t)

    def status(self) -> dict:
        return {"provider": self.name, "model": self.repo, "loaded": self.loaded, "load_s": self.load_s, "error": self.error}


class NullSTT:
    name = "none"

    def load(self) -> None:
        pass

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
        return Transcript("", None, len(audio) / sample_rate, 0.0)

    def status(self) -> dict:
        return {"provider": "none"}


def make_stt(cfg: STTConfig) -> STTProvider:
    if cfg.provider == "faster_whisper":
        return FasterWhisperSTT(cfg)
    if cfg.provider == "mlx_whisper":
        return MlxWhisperSTT(cfg)
    return NullSTT()
