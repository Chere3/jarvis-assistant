"""Señales breves (listo / cancelado) generadas localmente y reproducidas con afplay."""
from __future__ import annotations

import asyncio
import math
import struct
import wave
from pathlib import Path


def _write_tone(path: Path, freqs: list[float], ms: int = 120, rate: int = 22050, volume: float = 0.35) -> None:
    n = int(rate * ms / 1000)
    frames = bytearray()
    per = n // len(freqs)
    for f in freqs:
        for i in range(per):
            env = min(1.0, i / (per * 0.1), (per - i) / (per * 0.2))
            frames += struct.pack("<h", int(volume * env * 32767 * math.sin(2 * math.pi * f * i / rate)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


class Beeper:
    def __init__(self, cache_dir: Path) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.ready = cache_dir / "beep_ready.wav"
        self.cancel = cache_dir / "beep_cancel.wav"
        if not self.ready.exists():
            _write_tone(self.ready, [660, 880], 160)
        if not self.cancel.exists():
            _write_tone(self.cancel, [440, 330], 160)

    async def play(self, which: str = "ready") -> None:
        path = self.ready if which == "ready" else self.cancel
        try:
            p = await asyncio.create_subprocess_exec("afplay", str(path), stdout=asyncio.subprocess.DEVNULL,
                                                     stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(p.wait(), timeout=3)
        except Exception:
            pass
