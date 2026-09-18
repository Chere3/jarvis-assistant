"""Pruebas con audio sintético (voces del sistema). Distintas de una prueba real de micrófono."""
import asyncio
import wave
from pathlib import Path

import numpy as np
import pytest

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> np.ndarray:
    with wave.open(str(FIX / f"{name}.wav")) as w:
        assert w.getframerate() == 16000
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


@pytest.mark.skipif(not (FIX / "es_recuerda.wav").exists(), reason="ejecuta scripts/make_fixtures.sh")
def test_stt_spanish_fixture():
    from jarvis.audio.stt import FasterWhisperSTT
    from jarvis.config import STTConfig
    stt = FasterWhisperSTT(STTConfig(provider="faster_whisper", model="small"))
    tr = stt.transcribe(load("es_recuerda"), 16000)
    assert "recuerda" in tr.text.lower() and "cortas" in tr.text.lower()
    tr2 = stt.transcribe(load("es_proyecto"), 16000)
    assert "proyecto" in tr2.text.lower() and "asistente" in tr2.text.lower()


@pytest.mark.skipif(not (FIX / "jarvis_es.wav").exists(), reason="ejecuta scripts/make_fixtures.sh")
def test_wakeword_fixtures_and_cooldown():
    """Modelo propio models/jarvis.onnx («Jarvis» sola, es/en) con la configuración por defecto."""
    from jarvis.audio.wakeword import OpenWakeWordProvider, WakeWordDetector
    from jarvis.config import WakeWordConfig
    cfg = WakeWordConfig()
    assert cfg.keyword_label == "Jarvis" and cfg.model_path.endswith("jarvis.onnx")
    prov = OpenWakeWordProvider(cfg)
    prov.initialize()
    det = WakeWordDetector(prov, cooldown_s=cfg.cooldown_s, min_consecutive=cfg.min_consecutive_frames)

    def hits(name: str) -> int:
        audio = np.concatenate([np.zeros(16000, np.int16), load(name), np.zeros(16000, np.int16)])
        prov.model.reset()
        det.last_ts = 0.0
        return sum(1 for i in range(0, len(audio) - 1280, 1280) if det.process(audio[i:i + 1280]))

    # «Jarvis» sola en inglés y español, «hey jarvis» (contiene la palabra) y una petición que empieza por «Jarvis»
    positives = {n: hits(n) for n in ("jarvis_en", "jarvis_es", "hey_jarvis_en", "hey_jarvis_es", "es_recuerda", "es_dificil")}
    assert all(v == 1 for v in positives.values()), positives  # una sola activación por clip pese a varios frames sobre el umbral (cooldown)
    # frases sin la palabra, con sonidos parecidos (jarabe, jardín, Javier, Travis, harvest, service…): ninguna activación
    negatives = {n: hits(n) for n in ("es_proyecto", "es_negativo", "en_negativo")}
    assert all(v == 0 for v in negatives.values()), negatives
    # silencio: sin activaciones
    prov.model.reset()
    det.last_ts = 0.0
    assert all(det.process(np.zeros(1280, np.int16)) is None for _ in range(50))


@pytest.mark.skipif(not (FIX / "es_recuerda.wav").exists(), reason="fixtures")
def test_endpointer_on_fixture():
    from jarvis.audio.vad import VoiceActivity, Endpointer
    audio = np.concatenate([load("es_recuerda"), np.zeros(16000 * 2, np.int16)])
    ep = Endpointer(VoiceActivity(16000, 2), 80, 900, 300, 15)
    out = None
    for i in range(0, len(audio) - 1280, 1280):
        out = ep.feed(audio[i:i + 1280])
        if out:
            break
    assert out == "end" and ep.has_speech()


async def test_tts_speak_and_stop():
    from jarvis.audio.tts import MacSayTTS, clean_for_speech
    assert clean_for_speech("Hola [mem:mem_20260904_abcdef] **fuerte** [[mem_20260904_abcdef|enlace]]") == "Hola fuerte enlace"
    tts = MacSayTTS("Paulina", 200)
    task = asyncio.create_task(tts.speak("Esta es una frase larga que será interrumpida antes de terminar de leerse por completo."))
    for _ in range(30):  # `say` tarda en arrancar cuando el sistema está cargado
        await asyncio.sleep(0.1)
        if tts.is_speaking():
            break
    assert tts.is_speaking()
    await tts.stop()
    completed = await task
    assert completed is False and not tts.is_speaking()
    assert await tts.speak("Listo.") is True


def test_beeper_generates_files(tmp_path):
    from jarvis.audio.beep import Beeper
    b = Beeper(tmp_path)
    assert b.ready.exists() and b.cancel.exists() and b.ready.stat().st_size > 1000


@pytest.mark.skipif(not (FIX / "es_dificil.wav").exists(), reason="fixtures")
def test_stt_mlx_whisper_turbo_fixture():
    pytest.importorskip("mlx_whisper")
    from jarvis.audio.stt import MlxWhisperSTT
    from jarvis.config import STTConfig
    stt = MlxWhisperSTT(STTConfig(provider="mlx_whisper", model="mlx-community/whisper-large-v3-turbo"))
    tr = stt.transcribe(load("es_dificil"), 16000)
    low = tr.text.lower()
    assert "mariana" in low and "cargador" in low and "amazon" in low
    assert not low.startswith("jarvis")  # la palabra de activación se elimina del principio
    assert tr.elapsed_s < 15
