"""TTS neuronal local (Kokoro-82M vía ONNX). Voz realista en español sin servicios externos.
Modelo: ~/.jarvis/models/kokoro/{kokoro-v1.0.onnx, voices-v1.0.bin} (descarga: `jarvis tts download`)."""
from __future__ import annotations

import asyncio
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

from ..config import jarvis_home

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
SPANISH_VOICES = {"ef_dora": "femenina (Dora)", "em_alex": "masculina (Alex)", "em_santa": "masculina (Santa)"}


def model_dir() -> Path:
    return jarvis_home() / "models" / "kokoro"


def model_files() -> tuple[Path, Path]:
    d = model_dir()
    return d / "kokoro-v1.0.onnx", d / "voices-v1.0.bin"


def is_installed() -> bool:
    m, v = model_files()
    return m.exists() and m.stat().st_size > 100_000_000 and v.exists()


def download(progress=print) -> None:
    import urllib.request
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    for url, dest in ((MODEL_URL, model_files()[0]), (VOICES_URL, model_files()[1])):
        if dest.exists() and dest.stat().st_size > 1_000_000:
            continue
        progress(f"descargando {dest.name}…")
        tmp = dest.with_suffix(".part")
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(dest)
    progress("modelo Kokoro listo")


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…:;])\s+", text.strip())
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if out and len(out[-1]) < 40:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return out


class KokoroTTS:
    """Sintetiza frase a frase (baja latencia) y reproduce con afplay; cancelable en cualquier momento."""

    name = "kokoro"

    def __init__(self, voice: str = "ef_dora", speed: float = 1.0, lang: str = "es", cache_dir: Path | None = None) -> None:
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self.cache = cache_dir or (jarvis_home() / "data" / "runtime" / "cache" / "tts")
        self.cache.mkdir(parents=True, exist_ok=True)
        self._model = None
        self._lock = threading.Lock()
        self._proc: asyncio.subprocess.Process | None = None
        self._speaking = False
        self._stopped = False
        self.error: str | None = None
        self.last_synthesis_s: float | None = None
        self._queue: list[asyncio.Future] = []  # frases pendientes (futuros con la ruta del wav)
        self._worker: asyncio.Task | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self._gen = 0
        self._seq = 0  # contador monótono para los wav temporales

    def load(self) -> None:
        with self._lock:
            if self._model:
                return
            from kokoro_onnx import Kokoro
            m, v = model_files()
            if not is_installed():
                raise FileNotFoundError("modelo Kokoro no descargado: ejecuta `jarvis tts download`")
            self._model = Kokoro(str(m), str(v))

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        self.load()
        t = time.monotonic()
        samples, rate = self._model.create(text, voice=self.voice, speed=self.speed, lang=self.lang)
        self.last_synthesis_s = time.monotonic() - t
        return samples, rate

    def synth_to_wav(self, text: str, path: Path) -> Path:
        samples, rate = self.synth(text)
        pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        return path

    # ------------------------------------------------------------- reproducción en flujo
    def say_async(self, text: str) -> None:
        """Encola texto (una o varias frases) y empieza a sonar en cuanto la primera frase esté sintetizada.
        La síntesis de las siguientes frases se solapa con la reproducción."""
        from .tts import clean_for_speech
        text = clean_for_speech(text)
        if not text:
            return
        loop = asyncio.get_running_loop()
        self._stopped = False
        self._idle.clear()
        gen = self._gen
        for sentence in split_sentences(text):
            self._seq += 1
            fut = loop.run_in_executor(None, self._synth_file, sentence, self._seq)
            fut.gen = gen  # type: ignore[attr-defined]
            self._queue.append(fut)
        if not self._worker or self._worker.done():
            self._worker = loop.create_task(self._drain())

    async def _drain(self) -> None:
        self._speaking = True
        try:
            while self._queue and not self._stopped:
                fut = self._queue.pop(0)
                try:
                    path = await fut
                except Exception as e:
                    self.error = f"{type(e).__name__}: {e}"
                    continue
                if self._stopped or getattr(fut, "gen", self._gen) != self._gen:
                    continue
                self._proc = await asyncio.create_subprocess_exec("afplay", str(path), stdout=asyncio.subprocess.DEVNULL,
                                                                  stderr=asyncio.subprocess.DEVNULL)
                await self._proc.wait()
                self._proc = None
        finally:
            self._speaking = False
            self._idle.set()

    async def wait_idle(self) -> bool:
        await self._idle.wait()
        return not self._stopped

    async def speak(self, text: str) -> bool:
        """Compatibilidad: encola y espera a que termine. Devuelve False si se interrumpió."""
        self.say_async(text)
        return await self.wait_idle()

    def _synth_file(self, sentence: str, idx: int) -> Path:
        return self.synth_to_wav(sentence, self.cache / f"utt_{idx % 256}.wav")

    async def stop(self) -> None:
        self._stopped = True
        self._gen += 1
        self._queue.clear()
        p = self._proc
        if p and p.returncode is None:
            try:
                p.terminate()
                await asyncio.wait_for(p.wait(), timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        self._idle.set()

    def is_speaking(self) -> bool:
        return self._speaking

    def status(self) -> dict:
        return {"provider": self.name, "voice": self.voice, "speed": self.speed, "lang": self.lang, "installed": is_installed(),
                "loaded": self._model is not None, "last_synthesis_s": self.last_synthesis_s, "error": self.error,
                "speaking": self._speaking}
