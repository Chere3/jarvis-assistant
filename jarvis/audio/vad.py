"""Detección de actividad de voz (webrtcvad) y detector de fin de habla."""
from __future__ import annotations

import numpy as np


class VoiceActivity:
    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2) -> None:
        import webrtcvad
        self.vad = webrtcvad.Vad(max(0, min(3, aggressiveness)))
        self.sample_rate = sample_rate
        self.sub = int(sample_rate * 0.02)  # 20 ms

    def is_speech(self, frame: np.ndarray) -> bool:
        data = frame.astype(np.int16).tobytes()
        n = len(data) // (self.sub * 2)
        if n == 0:
            return False
        votes = 0
        for i in range(n):
            chunk = data[i * self.sub * 2:(i + 1) * self.sub * 2]
            if self.vad.is_speech(chunk, self.sample_rate):
                votes += 1
        return votes * 2 >= n


class Endpointer:
    """Acumula frames y decide cuándo terminó la petición."""

    def __init__(self, vad: VoiceActivity, frame_ms: int, end_silence_ms: int, min_speech_ms: int, max_utterance_s: float) -> None:
        self.vad = vad
        self.frame_ms = frame_ms
        self.end_silence_frames = max(1, end_silence_ms // frame_ms)
        self.min_speech_frames = max(1, min_speech_ms // frame_ms)
        self.max_frames = int(max_utterance_s * 1000 / frame_ms)
        self.frames: list[np.ndarray] = []
        self.speech_frames = 0
        self.silence_run = 0
        self.started = False

    def feed(self, frame: np.ndarray) -> str | None:
        """Devuelve None (seguir), 'end' (fin por silencio), 'timeout' (duración máxima) o 'nospeech'."""
        self.frames.append(frame)
        if self.vad.is_speech(frame):
            self.speech_frames += 1
            self.silence_run = 0
            if self.speech_frames >= 2:
                self.started = True
        else:
            self.silence_run += 1
        if self.started and self.silence_run >= self.end_silence_frames:
            return "end" if self.speech_frames >= self.min_speech_frames else "nospeech"
        if len(self.frames) >= self.max_frames:
            return "timeout" if self.speech_frames >= self.min_speech_frames else "nospeech"
        if not self.started and len(self.frames) >= self.end_silence_frames * 4:
            return "nospeech"
        return None

    def audio(self) -> np.ndarray:
        return np.concatenate(self.frames) if self.frames else np.zeros(0, dtype=np.int16)

    def has_speech(self) -> bool:
        return self.speech_frames >= self.min_speech_frames
