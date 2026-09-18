"""Bucle de voz: activación por palabra clave o pulsar-para-hablar, captura, STT, turno y TTS (half-duplex)."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from ..config import Config
from ..orchestrator.state import State
from .beep import Beeper
from .capture import MicCapture
from .stt import STTProvider, make_stt
from .tts import make_tts
from .vad import Endpointer, VoiceActivity
from .wakeword import WakeWordDetector, make_wakeword


class VoiceLoop:
    def __init__(self, app: Any, wake_enabled: bool = True) -> None:
        self.app = app
        self.cfg: Config = app.cfg
        self.orch = app.orchestrator
        self.bus = app.bus
        self.log = app.logger
        a = self.cfg.audio
        self.capture = MicCapture(a.sample_rate, a.frame_ms, a.input_device, a.preroll_ms)
        self.vad = VoiceActivity(a.sample_rate, a.vad_aggressiveness)
        self.stt: STTProvider = make_stt(self.cfg.stt)
        self.beeper = Beeper(self.cfg.memory.runtime_dir / "cache")
        self.wake_provider = make_wakeword(self.cfg.wake_word) if wake_enabled else None
        self.detector: WakeWordDetector | None = None
        self.wake_error: str | None = None
        self.ptt_event = asyncio.Event()
        self._stop = asyncio.Event()
        self.running = False
        self.last_activation: dict[str, Any] | None = None
        self.mic_error: str | None = None
        self.activations = 0
        self.discarded_activations = 0

    # ------------------------------------------------------------- ciclo de vida
    async def start(self) -> None:
        if self.wake_provider:
            try:
                await asyncio.to_thread(self.wake_provider.initialize)
                self.detector = WakeWordDetector(self.wake_provider, self.cfg.wake_word.cooldown_s,
                                                self.cfg.wake_word.min_consecutive_frames)
            except Exception as e:
                self.wake_error = str(e)
                self.detector = None
        try:
            await asyncio.to_thread(self.stt.load)
        except Exception as e:
            self.log.error("STT no disponible: %s", e)
        self._start_capture()
        self.running = True
        self.orch.listening_enabled = self.detector is not None
        self.bus.publish("voice.status", **self.status())

    def _start_capture(self) -> None:
        try:
            self.capture.start()
            self.mic_error = None
        except Exception as e:
            self.mic_error = f"{type(e).__name__}: {e}"
            self.log.error("micrófono no disponible: %s", self.mic_error)

    async def stop(self) -> None:
        self._stop.set()
        self.running = False
        self.capture.stop()
        if self.wake_provider:
            self.wake_provider.release()

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "mic_active": self.capture.is_active(), "mic_error": self.mic_error,
                "wake": self.wake_provider.status() if self.wake_provider else {"provider": "none"},
                "wake_error": self.wake_error, "listening_enabled": self.orch.listening_enabled,
                "stt": self.stt.status(), "activations": self.activations,
                "discarded_activations": self.discarded_activations, "last_activation": self.last_activation,
                "half_duplex": True}

    def request_ptt(self) -> None:
        self.ptt_event.set()

    def set_listening(self, enabled: bool) -> None:
        self.orch.listening_enabled = enabled and self.detector is not None
        self.bus.publish("voice.status", **self.status())

    # ------------------------------------------------------------- bucle principal
    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        last_restart = 0.0
        while not self._stop.is_set():
            if not self.capture.is_active():
                if time.monotonic() - last_restart > 5:
                    last_restart = time.monotonic()
                    self.capture.stop()
                    self._start_capture()
                    self.bus.publish("voice.status", **self.status())
                await asyncio.sleep(0.5)
                if self.ptt_event.is_set():
                    self.ptt_event.clear()
                    self.bus.publish("chat.system", text="No hay micrófono disponible; usa la entrada de texto.")
                continue
            if self.ptt_event.is_set():
                self.ptt_event.clear()
                await self._activation("ptt")
                continue
            if self.orch.state in (State.SPEAKING, State.THINKING, State.TRANSCRIBING):
                self.capture.paused = True
                await asyncio.sleep(0.05)
                continue
            if self.capture.paused:
                self.capture.paused = False
                self.capture.drain()
                if self.cfg.audio.follow_up_window_s > 0 and self.orch.state == State.IDLE and self._follow_up_pending:
                    self._follow_up_pending = False
                    await self._activation("follow_up")
                    continue
            frame = await loop.run_in_executor(None, self.capture.read, 0.3)
            if frame is None:
                continue
            if self.detector and self.orch.listening_enabled and self.orch.state == State.IDLE:
                det = self.detector.process(frame)
                if det:
                    await self._activation("wake_word", det.score)

    _follow_up_pending = False

    async def _activation(self, how: str, score: float | None = None) -> None:
        if self.orch.state not in (State.IDLE, State.AWAITING_APPROVAL):
            return
        self.activations += 1
        self.last_activation = {"how": how, "score": score, "ts": time.time()}
        self.bus.publish("voice.activation", how=how, score=score)
        self.orch.set_state(State.LISTENING, f"activación ({how})")
        pre = self.capture.preroll() if how != "wake_word" else []
        self.capture.drain()  # descarta lo anterior (incluida la palabra clave), pero NO lo que se diga durante o justo tras el tono
        if how != "follow_up":
            await self.beeper.play("ready")
        a = self.cfg.audio
        ep = Endpointer(self.vad, a.frame_ms, a.end_silence_ms, a.min_speech_ms, a.max_utterance_s)
        for f in pre:
            ep.feed(f)
        loop = asyncio.get_running_loop()
        outcome = None
        deadline = time.monotonic() + a.max_utterance_s + 3
        while outcome is None and time.monotonic() < deadline and self.orch.state == State.LISTENING:
            frame = await loop.run_in_executor(None, self.capture.read, 0.3)
            if frame is None:
                continue
            outcome = ep.feed(frame)
        if self.orch.state != State.LISTENING:  # cancelado desde fuera
            return
        if outcome in (None, "nospeech") or not ep.has_speech():
            self.discarded_activations += 1
            self.bus.publish("voice.discarded", reason="sin voz útil")
            self.orch.set_state(State.IDLE, "activación sin voz útil")
            return
        self.orch.set_state(State.TRANSCRIBING, "fin de habla detectado")
        try:
            tr = await asyncio.to_thread(self.stt.transcribe, ep.audio(), a.sample_rate)
        except Exception as e:
            self.log.error("STT falló: %s", e)
            self.orch.set_state(State.ERROR, "fallo de transcripción")
            self.orch.set_state(State.IDLE, "recuperado")
            return
        self.bus.publish("voice.transcript", text=tr.text, duration_s=round(tr.duration_s, 2), elapsed_s=round(tr.elapsed_s, 2))
        if not tr.text.strip():
            self.discarded_activations += 1
            self.orch.set_state(State.IDLE, "transcripción vacía")
            return
        await self.orch.handle_text(tr.text, source="voice")
        if self.cfg.audio.follow_up_window_s > 0:
            self._follow_up_pending = True


async def run_listen(args: Any) -> int:
    from ..app import build_app
    cfg_app = build_app(demo=args.demo, speak_responses=True)
    speaker = make_tts(cfg_app.cfg)
    app = build_app(demo=args.demo, speaker=speaker, speak_responses=True)
    orch = app.orchestrator
    name = app.cfg.assistant.display_name
    if app.demo:
        print("*** MODO DEMO: respuestas simuladas ***", flush=True)
    else:
        print("Conectando con Claude…", flush=True)
        try:
            await app.provider.start()
        except Exception as e:
            print(f"No se pudo conectar con Claude: {e}", flush=True)
            return 2
    await app.start_services()
    vl = VoiceLoop(app, wake_enabled=not args.ptt_only)
    print("Cargando audio (modelo STT, detector de palabra clave)…", flush=True)
    await vl.start()
    st = vl.status()
    if st["mic_error"]:
        print(f"Micrófono no disponible: {st['mic_error']} (revisa Ajustes > Privacidad > Micrófono)", flush=True)
    if vl.wake_error:
        print(f"Detector de palabra clave no disponible: {vl.wake_error}\n  → usa pulsar-para-hablar (Enter).", flush=True)
    elif vl.detector:
        print(f"Escuchando la palabra de activación «{app.cfg.wake_word.keyword_label}» ({app.cfg.wake_word.provider}).", flush=True)
    print(f"{name}: Enter = pulsar-para-hablar · texto + Enter = petición escrita · 's' = detener voz · 'q' = salir", flush=True)

    def on_event(ev: dict) -> None:
        k = ev["kind"]
        if k == "state":
            print(f"  [{ev['state']}] {ev.get('reason', '')}", flush=True)
        elif k == "voice.transcript":
            print(f"  tú (voz, {ev['duration_s']}s → {ev['elapsed_s']}s STT): {ev['text']}", flush=True)
        elif k == "chat.message":
            print(f"{name}: {ev['text']}" + (f"\n  · fuentes: {', '.join(ev['cited'])}" if ev.get("cited") else ""), flush=True)
        elif k == "approval.requested":
            a = ev["approval"]
            print(f"  >> APROBACIÓN {a['id']}: {a['description']} (di «sí»/«no» o escribe /aprobar {a['id']})", flush=True)
        elif k == "chat.system":
            print(f"  [{name}] {ev['text']}", flush=True)
        elif k == "voice.discarded":
            print("  (activación descartada: sin voz útil)", flush=True)
        elif k == "voice.activation" and args.verbose:
            print(f"  (activación {ev['how']} score={ev.get('score')})", flush=True)

    app.bus.subscribe(on_event)
    loop_task = asyncio.create_task(vl.run())
    loop = asyncio.get_running_loop()
    try:
        while True:
            line = await loop.run_in_executor(None, input)
            line = line.strip()
            if line == "q":
                break
            if line == "s":
                print(await orch.cancel("tecla s"), flush=True)
            elif line == "":
                vl.request_ptt()
            else:
                asyncio.create_task(orch.handle_text(line))
    except (EOFError, KeyboardInterrupt):
        pass
    await vl.stop()
    loop_task.cancel()
    await app.stop_services()
    await app.provider.stop()
    return 0
