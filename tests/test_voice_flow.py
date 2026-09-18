"""Voz en flujo, preguntas de aclaración, resúmenes de permiso y elección de modelo."""
import asyncio

import pytest

from jarvis.agent.tools import ToolRegistry
from tests.test_orchestration import make


class RecordingSpeaker:
    """Simula un TTS en flujo: registra las frases y cuándo llegaron."""
    def __init__(self):
        self.said = []
        self._idle = asyncio.Event(); self._idle.set()
        self.stopped = False
    def say_async(self, text):
        self.said.append(text)
    async def wait_idle(self):
        return not self.stopped
    async def speak(self, text):
        self.said.append(text); return True
    async def stop(self):
        self.stopped = True
    def is_speaking(self):
        return False
    def status(self):
        return {"provider": "recording"}


async def test_streaming_speech_starts_before_turn_ends(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path, script=["Primera frase lista. Segunda frase que llega después. Tercera"], delay=0.0)
    spk = RecordingSpeaker()
    orch.speaker = spk; orch.speak_responses = True; cfg.tts.enabled = True
    r = await orch.handle_text("hola")
    joined = " ".join(spk.said)
    assert "Primera frase lista." in spk.said[0] and "Tercera" in joined
    assert not r.cancelled and orch.state.value == "IDLE"


def test_describe_builtin_is_short_and_human():
    d = ToolRegistry.describe_builtin
    assert d("Bash", {"command": "rm -rf ~/Desktop/x", "description": "Borrar la carpeta x del Escritorio"}) == "ejecutar en la terminal: Borrar la carpeta x del Escritorio"
    assert "borrar o mover" in d("Bash", {"command": "rm -rf /tmp/x"})
    assert d("Write", {"file_path": "/Users/diego/Desktop/notas.md", "content": "x" * 5000}) == "crear o sobrescribir el archivo Desktop/notas.md"
    assert d("WebFetch", {"url": "https://docs.python.org/3/"}) == "leer la web docs.python.org"
    assert len(d("Bash", {"command": "x" * 500})) < 120


async def test_clarifying_question_answered_by_voice(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    reg.turn = reg.turn.__class__("t1", "s")
    q = {"questions": [{"question": "¿Cómo procedo?", "header": "Borrado", "multiSelect": False,
                        "options": [{"label": "Mandar a la Papelera", "description": ""}, {"label": "Borrado definitivo", "description": ""}, {"label": "Cancelar", "description": ""}]}]}
    task = asyncio.create_task(reg.gate_question(q))
    await asyncio.sleep(0.05)
    pend = approvals.pending()
    assert pend and pend[0].kind == "question"
    r = await orch.handle_text("a la papelera")  # el orquestador enruta la respuesta hablada a la pregunta
    assert "Respondido" in r.display_text
    res = await task
    assert res["answers"]["¿Cómo procedo?"] == "Mandar a la Papelera"
    # respuesta por número
    task = asyncio.create_task(reg.gate_question(q))
    await asyncio.sleep(0.05)
    await orch.handle_text("la tercera")
    assert (await task)["answers"]["¿Cómo procedo?"] == "Cancelar"
    # rechazo
    task = asyncio.create_task(reg.gate_question(q))
    await asyncio.sleep(0.05)
    await orch.handle_text("no")
    assert isinstance(await task, str)


async def test_set_model_tool_and_aliases(tmp_path):
    cfg, svc, reg, approvals, prov, orch, events = make(tmp_path)
    calls = []
    class P:
        async def set_model(self, m): calls.append(m); return True, "ok"
        def status(self): return {"model": calls[-1] if calls else None}
    reg.set_model_provider(P())
    cfg_path = tmp_path / "cfg.yaml"
    import os; os.environ["JARVIS_CONFIG"] = str(cfg_path)
    try:
        r = await reg.execute("set_model", {"model": "sonnet"})
        assert r.ok and calls == ["claude-sonnet-5"] and cfg.claude.model == "claude-sonnet-5"
        r = await reg.execute("set_model", {"model": "gpt-9"})
        assert not r.ok
        r = await reg.execute("get_model", {})
        assert "claude-sonnet-5" in r.text
    finally:
        os.environ.pop("JARVIS_CONFIG", None)


def test_kokoro_sentence_split():
    from jarvis.audio.tts_kokoro import split_sentences
    parts = split_sentences("Hola. Esto es una prueba más larga de verdad. ¿Vale? Sí.")
    assert parts[0].startswith("Hola.") and all(len(p) > 0 for p in parts) and parts[-1].endswith("Sí.")


def test_clean_transcript_and_normalize():
    import numpy as np
    from jarvis.audio.stt import clean_transcript, normalize
    assert clean_transcript("Jarvis, recuerda que prefiero respuestas cortas.") == "Recuerda que prefiero respuestas cortas."
    assert clean_transcript("hey jarvis abre la carpeta") == "Abre la carpeta"
    assert clean_transcript("¿Qué sabes de mi proyecto?") == "¿Qué sabes de mi proyecto?"
    quiet = (np.sin(np.linspace(0, 100, 16000)) * 3000).astype(np.int16)  # micrófono flojo (pico ~ -21 dB)
    x = normalize(quiet)
    assert 0.6 < float(np.abs(x).max()) <= 0.71


def test_kokoro_unique_temp_files(tmp_path):
    """Dos frases encoladas seguidas nunca comparten archivo temporal (causa de audio repetido)."""
    from jarvis.audio.tts_kokoro import KokoroTTS
    t = KokoroTTS(cache_dir=tmp_path)
    seen = []
    t.synth_to_wav = lambda text, path: seen.append(path) or path  # sin modelo
    import asyncio
    async def run():
        t.say_async("Primera frase completa.")
        t.say_async("Segunda frase completa.")
        await asyncio.sleep(0.2)
        await t.stop()
    asyncio.run(run())
    assert len(seen) == 2 and seen[0] != seen[1]
