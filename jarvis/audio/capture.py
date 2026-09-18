"""Captura de micrófono con búfer circular en RAM (se descarta, no se guarda)."""
from __future__ import annotations

import collections
import queue
import threading
from typing import Any

import numpy as np


class MicCapture:
    def __init__(self, sample_rate: int = 16000, frame_ms: int = 80, device: Any = None, preroll_ms: int = 600) -> None:
        self.sample_rate = sample_rate
        self.frame_samples = int(sample_rate * frame_ms / 1000)
        self.device = device
        self.queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=200)
        self.ring: collections.deque[np.ndarray] = collections.deque(maxlen=max(1, preroll_ms // frame_ms))
        self.stream = None
        self.error: str | None = None
        self.paused = False  # half-duplex: mientras el asistente habla se descartan los frames
        self._lock = threading.Lock()
        self.frames_total = 0

    def _callback(self, indata, frames, time_info, status) -> None:  # hilo de PortAudio
        if status and status.input_overflow is False and str(status):
            self.error = str(status) if "error" in str(status).lower() else self.error
        frame = np.frombuffer(bytes(indata), dtype=np.int16).copy()
        self.frames_total += 1
        if self.paused:
            return
        with self._lock:
            self.ring.append(frame)
        try:
            self.queue.put_nowait(frame)
        except queue.Full:
            pass

    def start(self) -> None:
        import sounddevice as sd
        self.error = None
        self.stream = sd.RawInputStream(samplerate=self.sample_rate, channels=1, dtype="int16",
                                        blocksize=self.frame_samples, device=self.device, callback=self._callback)
        self.stream.start()

    def stop(self) -> None:
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def is_active(self) -> bool:
        return bool(self.stream and self.stream.active and not self.error)

    def read(self, timeout: float = 0.5) -> np.ndarray | None:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> None:
        while True:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self.ring.clear()

    def preroll(self) -> list[np.ndarray]:
        with self._lock:
            return list(self.ring)

    @staticmethod
    def list_devices() -> list[dict[str, Any]]:
        import sounddevice as sd
        out = []
        for i, d in enumerate(sd.query_devices()):
            out.append({"index": i, "name": d["name"], "inputs": d["max_input_channels"], "outputs": d["max_output_channels"],
                        "default_samplerate": d["default_samplerate"]})
        return out
