"""Detección local de palabra clave. No se sube audio mientras se espera la activación."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from ..config import WakeWordConfig


@dataclass
class Detection:
    keyword: str
    score: float
    ts: float


class WakeWordProvider(Protocol):
    name: str

    def initialize(self) -> None: ...
    def process(self, frame: np.ndarray) -> Detection | None: ...
    def set_sensitivity(self, value: float) -> None: ...
    def release(self) -> None: ...
    def status(self) -> dict: ...


class OpenWakeWordProvider:
    """openWakeWord (ONNX). Ruta a un .onnx propio (por defecto models/jarvis.onnx, «Jarvis» en es/en) o nombre de un
    modelo integrado como `hey_jarvis`. Frames de 80 ms a 16 kHz."""

    name = "openwakeword"

    def __init__(self, cfg: WakeWordConfig) -> None:
        self.cfg = cfg
        self.model = None
        self.error: str | None = None
        self.threshold = self._threshold(cfg.sensitivity)
        self.keyword = cfg.keyword_label
        self._model_key: str | None = None

    @staticmethod
    def _threshold(sensitivity: float) -> float:
        return float(min(0.95, max(0.1, 1.0 - sensitivity)))

    def initialize(self) -> None:
        try:
            from openwakeword import utils
            from openwakeword.model import Model
            spec = self.cfg.model_path
            if not Path(spec).exists():
                if spec.endswith((".onnx", ".tflite")) or "/" in spec:
                    raise FileNotFoundError(f"no existe el modelo de activación {spec}; entrena uno con "
                                            "scripts/train_wakeword.py o usa el integrado hey_jarvis")
                utils.download_models(model_names=[spec])
            self.model = Model(wakeword_models=[spec], inference_framework="onnx")
            self._model_key = list(self.model.models.keys())[0]
            self.error = None
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            self.model = None
            raise

    def process(self, frame: np.ndarray) -> Detection | None:
        if not self.model:
            return None
        scores = self.model.predict(frame)
        score = float(scores.get(self._model_key, 0.0))
        if score >= self.threshold:
            return Detection(self.keyword, score, time.time())
        return None

    def set_sensitivity(self, value: float) -> None:
        self.threshold = self._threshold(value)

    def release(self) -> None:
        self.model = None

    def status(self) -> dict:
        return {"provider": self.name, "keyword": self.keyword, "model": self.cfg.model_path, "language": self.cfg.language,
                "threshold": self.threshold, "ready": self.model is not None, "error": self.error,
                "note": ("modelo propio entrenado con voces sintéticas en español e inglés (scripts/train_wakeword.py)"
                         if self.cfg.model_path.endswith(".onnx") else
                         "modelo preentrenado de openWakeWord en inglés; funciona con pronunciación aproximada en español")}


class PorcupineProvider:
    """Picovoice Porcupine. Requiere PICOVOICE_ACCESS_KEY en el entorno (nunca en la configuración)."""

    name = "porcupine"

    def __init__(self, cfg: WakeWordConfig) -> None:
        self.cfg = cfg
        self.handle = None
        self.error: str | None = None
        self.keyword = cfg.keyword_label
        self._buf = np.zeros(0, dtype=np.int16)
        self.frame_length = 512

    def initialize(self) -> None:
        key = os.environ.get("PICOVOICE_ACCESS_KEY")
        if not key:
            self.error = "falta PICOVOICE_ACCESS_KEY (obtén una clave gratuita en https://console.picovoice.ai)"
            raise RuntimeError(self.error)
        import pvporcupine
        kwargs: dict = {"access_key": key, "sensitivities": [self.cfg.sensitivity]}
        spec = self.cfg.model_path
        if Path(spec).exists():
            kwargs["keyword_paths"] = [spec]
        elif spec in pvporcupine.KEYWORDS:
            kwargs["keywords"] = [spec]
        else:
            self.error = (f"no hay modelo Porcupine para «{spec}». Integrados: {sorted(pvporcupine.KEYWORDS)}. "
                          "Para otra palabra o para español, entrena un .ppn en console.picovoice.ai (plataforma macOS, "
                          "idioma es) y pon su ruta en wake_word.model_path; con idioma es también hace falta "
                          "model_path del modelo de idioma porcupine_params_es.pv")
            raise RuntimeError(self.error)
        if self.cfg.language and self.cfg.language != "en":
            self.error = "Porcupine en español requiere el archivo porcupine_params_es.pv (descárgalo del repo de Picovoice) y un .ppn entrenado para es"
            raise RuntimeError(self.error)
        try:
            self.handle = pvporcupine.create(**kwargs)
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            raise
        self.frame_length = self.handle.frame_length
        self.error = None

    def process(self, frame: np.ndarray) -> Detection | None:
        if not self.handle:
            return None
        self._buf = np.concatenate([self._buf, frame.astype(np.int16)])
        det = None
        while len(self._buf) >= self.frame_length:
            chunk, self._buf = self._buf[:self.frame_length], self._buf[self.frame_length:]
            if self.handle.process(chunk) >= 0:
                det = Detection(self.keyword, 1.0, time.time())
        return det

    def set_sensitivity(self, value: float) -> None:
        self.cfg.sensitivity = value  # Porcupine fija la sensibilidad al crear; se aplica al reiniciar

    def release(self) -> None:
        if self.handle:
            self.handle.delete()
            self.handle = None

    def status(self) -> dict:
        return {"provider": self.name, "keyword": self.keyword, "model": self.cfg.model_path, "language": self.cfg.language,
                "sensitivity": self.cfg.sensitivity, "ready": self.handle is not None, "error": self.error,
                "access_key": "presente" if os.environ.get("PICOVOICE_ACCESS_KEY") else "ausente"}


class WakeWordDetector:
    """Envuelve un proveedor con cooldown, frames consecutivos y protección frente a repeticiones."""

    def __init__(self, provider: WakeWordProvider, cooldown_s: float = 2.0, min_consecutive: int = 1) -> None:
        self.provider = provider
        self.cooldown_s = cooldown_s
        self.min_consecutive = max(1, min_consecutive)
        self.last_ts = 0.0
        self._streak = 0
        self.detections = 0

    def process(self, frame: np.ndarray) -> Detection | None:
        det = self.provider.process(frame)
        if not det:
            self._streak = 0
            return None
        self._streak += 1
        if self._streak < self.min_consecutive:
            return None
        if det.ts - self.last_ts < self.cooldown_s:
            return None
        self.last_ts = det.ts
        self._streak = 0
        self.detections += 1
        return det


def make_wakeword(cfg: WakeWordConfig) -> WakeWordProvider | None:
    if cfg.provider == "openwakeword":
        return OpenWakeWordProvider(cfg)
    if cfg.provider == "porcupine":
        return PorcupineProvider(cfg)
    return None
