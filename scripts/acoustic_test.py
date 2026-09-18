"""Prueba acústica real: reproduce por los ALTAVOCES del Mac la palabra de activación (wake_word.keyword_label) y una petición en español,
mientras el bucle de voz escucha por el MICRÓFONO real. Usa Claude real salvo --demo.
Requiere que el micrófono capte los altavoces (sin auriculares en el micrófono)."""
from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time

from jarvis.app import build_app
from jarvis.audio.tts import make_tts
from jarvis.audio.voice_loop import VoiceLoop


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--device", default="MacBook Air Speakers")
    ap.add_argument("--request", default="¿Qué preferencias mías conoces?")
    ap.add_argument("--voice", default="Paulina", help="voz de `say` para la palabra de activación (p. ej. Samantha para inglés)")
    args = ap.parse_args()
    app = build_app(demo=args.demo, speaker=make_tts(load := __import__("jarvis.config", fromlist=["load_config"]).load_config()), speak_responses=True)
    if not app.demo:
        await app.provider.start()
    vl = VoiceLoop(app, wake_enabled=True)
    await vl.start()
    print("estado audio:", {k: v for k, v in vl.status().items() if k in ("mic_active", "mic_error", "wake_error")}, flush=True)
    events: list[dict] = []
    done = asyncio.Event()

    def on_event(ev):
        events.append(ev)
        k = ev["kind"]
        if k in ("state", "voice.activation", "voice.transcript", "voice.discarded", "chat.message"):
            print(f"  evento {k}: { {kk: vv for kk, vv in ev.items() if kk in ('state', 'how', 'score', 'text', 'cited', 'reason')} }", flush=True)
        if k == "chat.message":
            done.set()

    app.bus.subscribe(on_event)
    task = asyncio.create_task(vl.run())
    await asyncio.sleep(1.5)
    t0 = time.time()
    keyword = app.cfg.wake_word.keyword_label
    print(f"reproduciendo «{keyword}» por", args.device, flush=True)
    subprocess.run(["say", "-a", args.device, "-v", args.voice, keyword], check=False)
    for _ in range(40):
        if any(e["kind"] == "voice.activation" for e in events):
            break
        await asyncio.sleep(0.1)
    activated = any(e["kind"] == "voice.activation" for e in events)
    print("activación detectada:", activated, f"({time.time() - t0:.1f}s)", flush=True)
    if activated:
        await asyncio.sleep(0.6)  # deja pasar el beep
        subprocess.run(["say", "-a", args.device, "-v", "Paulina", args.request], check=False)
        try:
            await asyncio.wait_for(done.wait(), timeout=150)
        except asyncio.TimeoutError:
            print("sin respuesta en el tiempo límite", flush=True)
    await asyncio.sleep(0.5)
    await vl.stop()
    task.cancel()
    if not app.demo:
        await app.provider.stop()
    kinds = [e["kind"] for e in events]
    print("RESUMEN:", {"activacion": activated, "transcripcion": [e["text"] for e in events if e["kind"] == "voice.transcript"],
                       "respuesta": [e["text"][:160] for e in events if e["kind"] == "chat.message"],
                       "estados": [e["state"] for e in events if e["kind"] == "state"]}, flush=True)
    return 0 if activated and "chat.message" in kinds else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
