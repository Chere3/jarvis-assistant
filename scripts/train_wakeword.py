"""Entrena un modelo propio de openWakeWord para la palabra «Jarvis» (sola, sin «hey») con voces sintéticas
en español e inglés: voces del sistema (`say`, es_MX/es_ES/en_*) y Kokoro (ef_/em_ en español, af_/am_/bf_/bm_ en inglés).
Sin Porcupine ni claves de Picovoice.

Etapas (idempotentes; todo bajo data/wakeword/, que está en .gitignore):
  download  features de validación (10,7 h de audio real, 185 MB), ~3 GB de negativos ACAV100M ya en features
            (publicados por openWakeWord) y las respuestas al impulso (RIR) del MIT (12 MB)
  gen       clips: positivos («Jarvis»/«Yarvis»), negativos adversarios (jardín, Javier, harvest, Travis…),
            frases negativas en es/en, ruido de fondo y RIR
  features  aumentación (openwakeword.data.augment_clips) + features (.npy) con openwakeword.utils
  train     openwakeword.train.Model.auto_train → exporta models/jarvis.onnx (+ models/jarvis.json con métricas)
  eval      barre umbrales sobre positivos de prueba, negativos, fixtures y las 10,7 h reales; recomienda sensitivity

Uso:  .venv/bin/python scripts/train_wakeword.py all        (o una etapa: gen | features | train | eval)
Requiere `uv sync --extra train` (torch, audiomentations, speechbrain…) y el modelo Kokoro (`jarvis tts download`).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import subprocess
import sys
import time
import wave
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data" / "wakeword"
DL = DATA / "dl"
MODEL_DIR = REPO / "models"
MODEL_NAME = "jarvis"
SR = 16000
CLIP = 32000  # 2 s: entrada del modelo = 16 frames de 80 ms

HF = "https://huggingface.co/datasets/davidscripka/openwakeword_features/resolve/main/"
DOWNLOADS = {
    "validation_set_features.npy": (HF + "validation_set_features.npy", None),
    "acav_partial.npy": (HF + "openwakeword_features_ACAV100M_2000_hrs_16bit.npy", "0-3000000127"),  # primeros 3 GB
    "mit_rir.zip": ("https://mcdermottlab.mit.edu/Reverb/IRMAudio/Audio.zip", None),
}

# ----------------------------------------------------------------------------- textos
POS_ES = ["Jarvis", "Yarvis", "¡Jarvis!", "¿Jarvis?"]   # las voces en español leen «Jarvis» con jota; «Yarvis» cubre la /ʝ/
POS_EN = ["Jarvis", "Jarvis!", "Jarvis?"]

ADV_ES = ["jardín", "Javier", "jarra", "jarabe", "Jaime", "Jaimito", "Carlos", "Marvin", "Travis", "Elvis", "servicio",
          "nervios", "varios", "harás", "hará", "ya vi", "ya ves", "ya vas", "ahí vas", "jabalí", "charla", "chavos", "Javi",
          "ya viste", "árbitro", "Harry", "jarritos", "Jalisco", "vista", "avisa", "avísame", "Jaramillo", "jazmín", "Marcos",
          "Álvaro", "Darwin", "Marvel", "Harvard", "jarcia", "jardinero", "Yamil", "Yaris", "Clarisa", "Larissa", "París",
          "gratis", "lápiz", "Chávez", "jar", "yar", "arvis", "vis", "jarvi", "gracias", "Jaraba", "Jacobo", "Javiera", "tapiz"]
ADV_EN = ["harvest", "service", "nervous", "Travis", "Mavis", "jar this", "car keys", "jarring", "Jarred", "carves", "starves",
          "Charles", "Jervis", "Elvis", "Davis", "purpose", "Harris", "jazz", "java", "javelin", "Marvin", "Marvel", "Harvard",
          "carve it", "jar", "jarhead", "Garvey", "Darvish", "Chavez", "larvae", "arbiters", "orbits", "Alvis", "Parvis",
          "Tarvis", "Jarvie's cousin", "carpets", "gardens", "starving", "Charlotte", "Jasper", "Gervais", "harbors", "Dervish"]

SENT_ES = [
    "Recuerda que prefiero respuestas cortas.", "¿Qué sabes de mi proyecto del asistente?",
    "Abre la carpeta de este proyecto, por favor.",
    "Mañana tengo una reunión a las diez de la mañana con el equipo de diseño.",
    "Javier dejó el jarabe en el jardín y ya vi que hará frío esta noche.",
    "¿Me pasas la jarra de agua que está en la mesa de la cocina?",
    "Carlos y Marcos vieron varios videos de Travis y de Elvis en la televisión.",
    "El servicio de streaming tiene nervios de acero cuando hay muchos usuarios.",
    "Ya vi la película, no hace falta que me la cuentes otra vez.",
    "Necesito comprar un cargador nuevo en Amazon antes del viernes.",
    "La demo interna es el jueves diez de septiembre a las cuatro de la tarde.",
    "¿Cuánto falta para que termine de compilar la aplicación?",
    "Pon música tranquila mientras trabajo en el informe trimestral.",
    "Mariana me pidió que revisara el código del bot de Discord esta tarde.",
    "El jardinero de la esquina vende jarritos de barro y jabalíes de madera.",
    "Hoy hace mucho calor en Guadalajara, mejor salimos cuando baje el sol.",
    "¿Puedes resumir el correo que me mandó el profesor de matemáticas?",
    "Quiero que me avises cuando termine la descarga del modelo de voz.",
    "Los nervios antes del examen son normales, respira hondo y avanza.",
    "Álvaro y Darwin están jugando ajedrez en la sala desde hace dos horas.",
    "Traduce esta frase al inglés y guárdala en mis notas de la semana.",
    "El jazmín del patio huele muy fuerte cuando llueve por la noche.",
    "Dile a Jaime que la charla sobre inteligencia artificial empieza a las seis.",
    "¿Cuál es el estado de la sesión que dejé corriendo en el proyecto de música?",
    "Apaga las luces del estudio y baja el volumen de los altavoces.",
    "Harvard y Stanford publicaron artículos sobre modelos de lenguaje pequeños.",
    "Ya vas tarde, el camión pasa a las siete y media por la avenida principal.",
    "Guarda el archivo como borrador y recuérdame revisarlo mañana temprano.",
    "La jarcia del velero se rompió durante la tormenta del sábado pasado.",
    "Marvin es el robot deprimido de la guía del autoestopista galáctico.",
    "No entiendo por qué el servidor devuelve un error quinientos cada tanto.",
    "Compra jabón, arroz, jamón y jugo de naranja en el supermercado.",
    "Envíale un mensaje a mi mamá diciendo que llego a las nueve.",
    "Este párrafo tiene varios errores de ortografía, corrígelos por favor.",
    "El árbitro marcó penal en el último minuto y el estadio explotó.",
    "Ahí vas otra vez con la misma historia del jarabe de maple.",
    "¿Qué modelo estás usando ahora mismo para responder mis preguntas?",
    "Cambia a sonnet para las tareas sencillas y a opus para las difíciles.",
    "Ayer vimos las jirafas y los jaguares en el zoológico de Chapultepec.",
    "Avísame si alguna sesión de Claude Code necesita mi aprobación.",
]
SENT_EN = [
    "Remember that I prefer short answers.", "What do you know about my assistant project?",
    "Open the project folder and show me the latest changes.",
    "The harvest service was nervous about Travis and Elvis last night.",
    "Please pass me the jar of honey that is on the kitchen table.",
    "Charles carved a jarring statue during the service on Sunday.",
    "Marvin the paranoid android was built by the Sirius Cybernetics Corporation.",
    "Set a timer for twenty minutes and play some quiet music.",
    "Mavis and Davis are driving to Harvard for the conference tomorrow.",
    "The server returns a five hundred error every few minutes.",
    "Summarize the email from my professor and save it to my notes.",
    "I need to buy a new charger on Amazon before Friday.",
    "Java and JavaScript are not the same language, despite the name.",
    "Turn off the studio lights and lower the speaker volume.",
    "The jazz concert starts at seven, so we should leave by six.",
    "Tell me the weather in Mexico City for the rest of the week.",
    "Jarred and Harris are starving after the marathon this morning.",
    "Switch to the smaller model for simple tasks and the larger one for hard ones.",
    "Let me know when any coding session needs my approval.",
    "The purpose of this meeting is to review the quarterly report.",
    "We saw giraffes and jaguars at the zoo yesterday afternoon.",
    "Can you translate this sentence into Spanish and read it back to me?",
    "The javelin thrower from Jalisco broke the national record.",
    "Our bus leaves at half past seven from the main avenue.",
    "Draft a reply saying that I will arrive around nine tonight.",
    "The garden hose is leaking again, we should call the plumber.",
    "My car keys are in the jar next to the door, not in the drawer.",
    "This paragraph has several spelling mistakes, please fix them.",
    "The referee gave a penalty in the last minute and the stadium exploded.",
    "Chorus, verse, and bridge: that is the structure of the song.",
]

# Voces del sistema descartadas: robóticas o de efectos, no parecen habla humana.
NOVELTY = {"Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos", "Wobble", "Good News", "Jester", "Organ", "Superstar",
           "Trinoids", "Whisper", "Zarvox", "Junior", "Ralph", "Fred", "Albert", "Kathy", "Hysterical", "Pipe Organ",
           "Deranged", "Bruce", "Agnes", "Princess", "Vicki", "Victoria"}


# ----------------------------------------------------------------------------- utilidades
def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def install_shims() -> None:
    """Parches mínimos para que las utilidades de entrenamiento de openWakeWord funcionen en este entorno:
    - `acoustics` importa scipy.special.sph_harm (retirada en scipy ≥ 1.17);
    - torchaudio ≥ 2.9 exige torchcodec para cargar audio; usamos soundfile."""
    import scipy.special as sp
    if not hasattr(sp, "sph_harm"):
        sp.sph_harm = lambda m, n, theta, phi: sp.sph_harm_y(n, m, phi, theta)
    import soundfile as sf
    import torch
    import torchaudio

    class _Info:
        def __init__(self, frames: int, sr: int, ch: int) -> None:
            self.num_frames, self.sample_rate, self.num_channels = frames, sr, ch

    def _info(path, *a, **k):
        i = sf.info(str(path))
        return _Info(i.frames, i.samplerate, i.channels)

    def _load(path, frame_offset: int = 0, num_frames: int = -1, *a, **k):
        data, sr = sf.read(str(path), start=frame_offset, frames=num_frames if num_frames and num_frames > 0 else -1,
                           dtype="float32", always_2d=True)
        return torch.from_numpy(np.ascontiguousarray(data.T)), sr

    torchaudio.info = _info
    torchaudio.load = _load


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        assert w.getframerate() == SR and w.getnchannels() == 1, path
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def write_wav(path: Path, pcm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.clip(pcm, -32768, 32767).astype(np.int16).tobytes())


def trim_silence(x: np.ndarray, db: float = -38.0, margin_ms: int = 60) -> np.ndarray:
    """Recorta el silencio inicial y final (RMS por tramos de 10 ms) dejando un margen."""
    hop = SR // 100
    n = len(x) // hop
    if n == 0:
        return x
    rms = np.sqrt((x[: n * hop].astype(np.float32).reshape(n, hop) ** 2).mean(axis=1))
    thr = rms.max() * 10 ** (db / 20)
    idx = np.where(rms > thr)[0]
    if len(idx) == 0:
        return x
    m = int(SR * margin_ms / 1000)
    return x[max(0, idx[0] * hop - m): min(len(x), (idx[-1] + 1) * hop + m)]


def say_voices() -> list[tuple[str, str]]:
    import re
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=20).stdout
    voices = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s+([a-z]{2}_[A-Z]{2})\s", line)  # «Eddy (Spanish (Mexico)) es_MX    # ¡Hola!…»
        if not m:
            continue
        name, loc = m.group(1).strip(), m.group(2)
        if loc[:2] in ("es", "en") and name.split(" (")[0] not in NOVELTY:
            voices.append((name, loc[:2]))
    return sorted(set(voices))


def say_to(voice: str, text: str, rate: int, out: Path) -> Path | None:
    tmp = out.with_suffix(".raw.wav")
    r = subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(tmp), "--data-format=LEI16@16000", text],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not tmp.exists():
        return None
    x = trim_silence(read_wav(tmp))
    tmp.unlink()
    if len(x) < SR // 10:
        return None
    write_wav(out, x)
    return out


class KokoroSynth:
    EN = {p: ("en-us" if p[0] == "a" else "en-gb") for p in ("af", "am", "bf", "bm")}

    def __init__(self) -> None:
        sys.path.insert(0, str(REPO))
        from kokoro_onnx import Kokoro
        from jarvis.audio.tts_kokoro import is_installed, model_files
        if not is_installed():
            raise SystemExit("modelo Kokoro no descargado: ejecuta `jarvis tts download`")
        m, v = model_files()
        self.model = Kokoro(str(m), str(v))
        names = sorted(np.load(str(v)).files)
        self.voices = [(n, "es") for n in names if n[:2] in ("ef", "em")] + \
                      [(n, self.EN[n[:2]]) for n in names if n[:2] in self.EN]

    def to(self, voice: str, lang: str, text: str, speed: float, out: Path) -> Path | None:
        from scipy.signal import resample_poly
        s, sr = self.model.create(text, voice=voice, speed=speed, lang=lang)
        x = resample_poly(np.asarray(s, dtype=np.float32), SR, sr) if sr != SR else np.asarray(s, dtype=np.float32)
        x = trim_silence((np.clip(x, -1, 1) * 32767).astype(np.int16))
        if len(x) < SR // 10:
            return None
        write_wav(out, x)
        return out


# ----------------------------------------------------------------------------- etapa: download
def stage_download() -> None:
    DL.mkdir(parents=True, exist_ok=True)
    for name, (url, byte_range) in DOWNLOADS.items():
        dest = DL / name
        if dest.exists() and dest.stat().st_size > 1_000_000:
            continue
        log(f"descargando {name} …")
        cmd = ["curl", "-L", "--fail", "--progress-bar", "-o", str(dest), url]
        if byte_range:
            cmd[1:1] = ["-r", byte_range]
        subprocess.run(cmd, check=True)
    prepare_acav()


def prepare_acav() -> Path:
    """El archivo ACAV se descargó parcialmente (rango de bytes): reescribe la cabecera .npy con las filas completas."""
    from numpy.lib import format as fmt
    src = DL / "acav_partial.npy"
    out = DL / "acav_negatives.npy"
    if out.exists():
        return out
    with open(src, "rb") as fh:
        ver = fmt.read_magic(fh)
        shape, fortran, dtype = fmt.read_array_header_1_0(fh)
        header_len = fh.tell()
    row_bytes = int(np.prod(shape[1:])) * dtype.itemsize
    rows = (src.stat().st_size - header_len) // row_bytes
    buf = io.BytesIO()
    fmt.write_array_header_1_0(buf, {"descr": fmt.dtype_to_descr(dtype), "fortran_order": fortran, "shape": (rows,) + tuple(shape[1:])})
    header = buf.getvalue()
    assert len(header) == header_len, (len(header), header_len)
    os.rename(src, out)
    with open(out, "r+b") as fh:
        fh.seek(0)
        fh.write(header)
        fh.truncate(header_len + rows * row_bytes)
    log(f"negativos ACAV100M: {rows} ejemplos de 1,28 s ({rows * 1.28 / 3600:.0f} h) en {out.name}")
    return out


# ----------------------------------------------------------------------------- etapa: gen
def stage_gen(seed: int) -> None:
    install_shims()
    rng = random.Random(seed)
    voices = say_voices()
    log(f"voces del sistema: {len(voices)} ({sum(1 for _, l in voices if l == 'es')} es, {sum(1 for _, l in voices if l == 'en')} en)")
    kk = KokoroSynth()
    log(f"voces Kokoro: {len(kk.voices)} ({sum(1 for _, l in kk.voices if l == 'es')} es)")

    try:
        from openwakeword.data import generate_adversarial_texts
        extra = sorted({t for t in generate_adversarial_texts("jarvis", 300, include_partial_phrase=0.0) if t.lower() != "jarvis"})
        rng.shuffle(extra)
        adv_en = ADV_EN + extra[:60]
        log(f"adversarios en inglés generados por openWakeWord (CMUdict): {len(extra)} (se usan 60)")
    except Exception as e:  # sin conexión o sin diccionario: la lista manual basta
        log(f"generate_adversarial_texts no disponible ({type(e).__name__}); solo lista manual")
        adv_en = ADV_EN

    jobs: list[tuple[str, Path, str, str, str, float]] = []  # (engine, out, voice, lang, text, rate/speed)

    def add(engine, split, voice, lang, text, rate):
        d = DATA / split
        d.mkdir(parents=True, exist_ok=True)
        out = d / f"{engine}_{voice.replace(' ', '_').replace('(', '').replace(')', '')}_{len(jobs):05d}.wav"
        jobs.append((engine, out, voice, lang, text, rate))

    # positivos ---------------------------------------------------------------
    for voice, lang in voices:
        for text in (POS_ES if lang == "es" else POS_EN):
            for rate in (130, 165, 200, 240):
                add("say", "positive_train", voice, lang, text, rate)
            for rate in (150, 220):
                add("say", "positive_test", voice, lang, text, rate)
    for voice, lang in kk.voices:
        for text in (POS_ES if lang == "es" else POS_EN):
            for speed in (0.75, 0.85, 1.0, 1.15, 1.3):
                add("kokoro", "positive_train", voice, lang, text, speed)
            for speed in (0.9, 1.2):
                add("kokoro", "positive_test", voice, lang, text, speed)
    # negativos adversarios ---------------------------------------------------
    for voice, lang in voices:
        pool = ADV_ES if lang == "es" else adv_en
        for text in rng.sample(pool, 12):
            add("say", "negative_train", voice, lang, text, rng.choice((140, 175, 210, 240)))
        for text in rng.sample(pool, 4):
            add("say", "negative_test", voice, lang, text, rng.choice((155, 225)))
    for voice, lang in kk.voices:
        pool = ADV_ES if lang == "es" else adv_en
        for text in rng.sample(pool, 14 if lang == "es" else 8):
            add("kokoro", "negative_train", voice, lang, text, rng.choice((0.8, 1.0, 1.2)))
        for text in rng.sample(pool, 4):
            add("kokoro", "negative_test", voice, lang, text, rng.choice((0.9, 1.1)))
    # frases negativas (habla general) ----------------------------------------
    for voice, lang in voices:
        pool = SENT_ES if lang == "es" else SENT_EN
        for text in rng.sample(pool, 6):
            add("say", "speech_train", voice, lang, text, rng.choice((150, 175, 200)))
        add("say", "speech_test", voice, lang, rng.choice(pool), 185)
    for voice, lang in kk.voices:
        pool = SENT_ES if lang == "es" else SENT_EN
        for text in rng.sample(pool, 16 if lang == "es" else 3):
            add("kokoro", "speech_train", voice, lang, text, rng.choice((0.9, 1.0, 1.1)))
        add("kokoro", "speech_test", voice, lang, rng.choice(pool), 1.0)

    pending = [j for j in jobs if not j[1].exists()]
    log(f"clips planificados: {len(jobs)} (pendientes {len(pending)})")
    say_jobs = [j for j in pending if j[0] == "say"]
    kk_jobs = [j for j in pending if j[0] == "kokoro"]
    with ThreadPoolExecutor(max_workers=8) as ex:
        done = sum(1 for r in ex.map(lambda j: say_to(j[2], j[4], int(j[5]), j[1]), say_jobs) if r)
    log(f"say: {done}/{len(say_jobs)} clips")
    done = 0
    for i, j in enumerate(kk_jobs, 1):
        if kk.to(j[2], j[3], j[4], float(j[5]), j[1]):
            done += 1
        if i % 200 == 0:
            log(f"kokoro: {i}/{len(kk_jobs)}")
    log(f"kokoro: {done}/{len(kk_jobs)} clips")

    # frases → trozos de 2 s (negativos de habla) -------------------------------
    for split in ("speech_train", "speech_test"):
        out_dir = DATA / f"{split}_clips"
        if out_dir.exists() and any(out_dir.iterdir()):
            continue
        audio = np.concatenate([read_wav(p) for p in sorted((DATA / split).glob("*.wav"))])
        n = len(audio) // CLIP
        for i in range(n):
            write_wav(out_dir / f"{i:05d}.wav", audio[i * CLIP:(i + 1) * CLIP])
        log(f"{split}: {len(audio) / SR / 60:.1f} min de habla → {n} trozos de 2 s")

    # ruido de fondo: ruido coloreado + murmullo de varias voces ----------------
    bg = DATA / "background"
    if not bg.exists() or not any(bg.iterdir()):
        import acoustics
        sentences = sorted((DATA / "speech_train").glob("*.wav"))
        for i, color in enumerate(["white", "pink", "brown", "blue", "violet"] * 4):
            x = acoustics.generator.noise(SR * 10, color=color)
            write_wav(bg / f"noise_{color}_{i:02d}.wav", x / np.abs(x).max() * 32767 * rng.uniform(0.2, 0.8))
        for i in range(30):
            mix = np.zeros(SR * 10, dtype=np.float32)
            for p in rng.sample(sentences, 4):
                s = read_wav(p).astype(np.float32)
                s = np.tile(s, int(np.ceil(len(mix) / len(s))))[: len(mix)]
                mix += np.roll(s, rng.randrange(len(mix))) * rng.uniform(0.3, 1.0)
            write_wav(bg / f"babble_{i:02d}.wav", mix / (np.abs(mix).max() + 1) * 32767 * 0.7)
        log(f"ruido de fondo: {len(list(bg.glob('*.wav')))} archivos")

    # RIR del MIT → 16 kHz mono ----------------------------------------------------
    rir = DATA / "rir"
    if not rir.exists() or not any(rir.iterdir()):
        import soundfile as sf
        from scipy.signal import resample_poly
        rir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(DL / "mit_rir.zip") as z:
            names = [n for n in z.namelist() if n.lower().endswith(".wav") and not Path(n).name.startswith(".")]
            for n in names:
                data, sr = sf.read(io.BytesIO(z.read(n)), dtype="float32", always_2d=True)
                x = data[:, 0]
                if sr != SR:
                    x = resample_poly(x, SR, sr)
                sf.write(str(rir / Path(n).name), x / (np.abs(x).max() + 1e-6), SR)
        log(f"RIR: {len(names)} archivos")

    counts = {d.name: len(list(d.glob("*.wav"))) for d in sorted(DATA.iterdir()) if d.is_dir() and d.name != "dl"}
    log(f"clips por carpeta: {counts}")


# ----------------------------------------------------------------------------- etapa: features
def stage_features(overwrite: bool = False) -> None:
    install_shims()
    import torch
    from openwakeword.data import augment_clips
    from openwakeword.utils import compute_features_from_generator
    torch.set_num_threads(max(1, os.cpu_count() // 2))
    background = [str(p) for p in sorted((DATA / "background").glob("*.wav"))] + \
                 [str(p) for p in sorted((DATA / "speech_train").glob("*.wav"))]
    rirs = [str(p) for p in sorted((DATA / "rir").glob("*.wav"))]
    plan = {  # carpeta → (rondas de aumentación, archivo de salida)
        "positive_train": 4, "positive_test": 1, "negative_train": 2, "negative_test": 1,
        "speech_train_clips": 2, "speech_test_clips": 1,
    }
    for folder, rounds in plan.items():
        out = DATA / f"{folder}_features.npy"
        if out.exists() and not overwrite:
            log(f"{out.name} ya existe")
            continue
        clips = [str(p) for p in sorted((DATA / folder).glob("*.wav"))] * rounds
        random.Random(0).shuffle(clips)
        gen = augment_clips(clips, total_length=CLIP, batch_size=64, background_clip_paths=background, RIR_paths=rirs)
        log(f"{folder}: aumentando {len(clips)} clips y calculando features …")
        compute_features_from_generator(gen, n_total=len(clips), clip_duration=CLIP, output_file=str(out),
                                        device="cpu", ncpu=max(1, os.cpu_count() // 2))
        log(f"{out.name}: {np.load(out, mmap_mode='r').shape}")


# ----------------------------------------------------------------------------- etapa: train
def fp_val_loader(batch: int = 16384):
    """Ventanas deslizantes (16 frames) sobre las features de validación de openWakeWord (audio real, sin la palabra)."""
    import torch
    X = np.load(DL / "validation_set_features.npy")
    win = np.lib.stride_tricks.sliding_window_view(X, 16, axis=0).transpose(0, 2, 1)  # (n, 16, 96)
    hours = X.shape[0] * 0.08 / 3600
    ds = torch.utils.data.TensorDataset(torch.from_numpy(np.ascontiguousarray(win)), torch.zeros(win.shape[0], dtype=torch.float32))
    return torch.utils.data.DataLoader(ds, batch_size=batch), hours


def stage_train(steps: int, seed: int) -> None:
    install_shims()
    import torch
    from openwakeword.data import mmap_batch_generator
    from openwakeword.train import Model
    torch.manual_seed(seed)
    np.random.seed(seed)
    torch.set_num_threads(max(1, os.cpu_count() // 2))
    acav = prepare_acav()
    files = {"positive": str(DATA / "positive_train_features.npy"), "adversarial_negative": str(DATA / "negative_train_features.npy"),
             "speech_negative": str(DATA / "speech_train_clips_features.npy"), "ACAV100M_sample": str(acav)}
    n_per_class = {"positive": 50, "adversarial_negative": 50, "speech_negative": 40, "ACAV100M_sample": 1024}
    labels = {k: (lambda x: [1 for _ in x]) if k == "positive" else (lambda x: [0 for _ in x]) for k in files}
    gen = mmap_batch_generator(files, n_per_class=n_per_class, label_transform_funcs=labels)

    class IterDataset(torch.utils.data.IterableDataset):
        def __iter__(self):
            return gen

    X_train = torch.utils.data.DataLoader(IterDataset(), batch_size=None, num_workers=0)
    X_val_fp, hours = fp_val_loader()
    pos = np.load(DATA / "positive_test_features.npy")
    neg = np.vstack([np.load(DATA / "negative_test_features.npy"), np.load(DATA / "speech_test_clips_features.npy")])
    y = np.hstack([np.ones(len(pos)), np.zeros(len(neg))]).astype(np.float32)
    X_val = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(torch.from_numpy(np.vstack([pos, neg])), torch.from_numpy(y)),
                                        batch_size=len(y))
    log(f"entrenamiento: positivos {np.load(files['positive'], mmap_mode='r').shape[0]}, adversarios "
        f"{np.load(files['adversarial_negative'], mmap_mode='r').shape[0]}, habla {np.load(files['speech_negative'], mmap_mode='r').shape[0]}, "
        f"ACAV {np.load(acav, mmap_mode='r').shape[0]}; validación pos {len(pos)} / neg {len(neg)}; FP real {hours:.1f} h")

    oww = Model(n_classes=1, input_shape=(16, 96), model_type="dnn", layer_dim=32, seconds_per_example=1.28)
    t0 = time.time()
    best = oww.auto_train(X_train=X_train, X_val=X_val, false_positive_val_data=X_val_fp, steps=steps,
                          max_negative_weight=1000, target_fp_per_hour=0.2)
    log(f"auto_train terminado en {(time.time() - t0) / 60:.1f} min")

    MODEL_DIR.mkdir(exist_ok=True)
    onnx_path = MODEL_DIR / f"{MODEL_NAME}.onnx"
    export_onnx(best, onnx_path)
    hist = oww.history
    summary = {
        "model": MODEL_NAME, "date": date.today().isoformat(), "keyword": "Jarvis", "languages": ["es", "en"],
        "training": {"steps": steps, "seed": seed, "layer_dim": 32, "input_shape": [16, 96],
                     "positive_train": int(np.load(files["positive"], mmap_mode="r").shape[0]),
                     "adversarial_negative_train": int(np.load(files["adversarial_negative"], mmap_mode="r").shape[0]),
                     "speech_negative_train": int(np.load(files["speech_negative"], mmap_mode="r").shape[0]),
                     "acav100m_negative_examples": int(np.load(acav, mmap_mode="r").shape[0]),
                     "fp_validation_hours": round(hours, 2), "minutes": round((time.time() - t0) / 60, 1)},
        "last_val": {"accuracy": float(hist["val_accuracy"][-1]), "recall": float(hist["val_recall"][-1]),
                     "fp_per_hour_at_0.5": float(hist["val_fp_per_hr"][-1])},
    }
    (MODEL_DIR / f"{MODEL_NAME}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"modelo exportado: {onnx_path} ({onnx_path.stat().st_size // 1024} KB); resumen en {MODEL_NAME}.json")


def export_onnx(model, path: Path) -> None:
    import torch
    model = model.to("cpu").eval()
    dummy = torch.rand(1, 16, 96)
    try:
        torch.onnx.export(model, dummy, str(path), opset_version=13, dynamo=False, input_names=["input"], output_names=["output"],
                          dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}})
    except Exception as e:  # exportador antiguo no disponible: usa el de dynamo
        log(f"exportador TorchScript no disponible ({type(e).__name__}: {e}); usando dynamo")
        prog = torch.onnx.export(model, (dummy,), input_names=["input"], output_names=["output"],
                                 dynamic_shapes={"x": {0: torch.export.Dim("batch")}}, dynamo=True)
        prog.save(str(path))
    import onnxruntime as ort
    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    inp = sess.get_inputs()[0]
    assert inp.shape[1] == 16 and inp.shape[2] == 96, inp.shape
    ref = model(dummy).detach().numpy()
    got = sess.run(None, {inp.name: dummy.numpy()})[0]
    assert np.allclose(ref, got, atol=1e-4), (ref, got)


# ----------------------------------------------------------------------------- etapa: eval
def stream_scores(model, audio: np.ndarray) -> np.ndarray:
    """Puntuación por frame de 80 ms recorriendo el clip como lo hace el bucle de voz (1 s de silencio a cada lado)."""
    model.reset()
    audio = np.concatenate([np.zeros(SR, np.int16), audio, np.zeros(SR, np.int16)])
    out = []
    for i in range(0, len(audio) - 1280 + 1, 1280):
        out.append(float(model.predict(audio[i:i + 1280])[MODEL_NAME]))
    return np.array(out)


def events(scores: np.ndarray, thr: float, consecutive: int, cooldown_frames: int) -> int:
    """Activaciones que produciría WakeWordDetector: `consecutive` frames seguidos ≥ umbral y cooldown."""
    n, streak, last = 0, 0, -10 ** 9
    for i, s in enumerate(scores):
        streak = streak + 1 if s >= thr else 0
        if streak >= consecutive and i - last >= cooldown_frames:
            n += 1
            last = i
            streak = 0
    return n


def stage_eval(cooldown_s: float = 3.0) -> dict:
    from openwakeword.model import Model
    import onnxruntime as ort
    onnx_path = MODEL_DIR / f"{MODEL_NAME}.onnx"
    model = Model(wakeword_models=[str(onnx_path)], inference_framework="onnx")
    cd = int(round(cooldown_s / 0.08))
    thresholds = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95]

    sets = {"positive_test": sorted((DATA / "positive_test").glob("*.wav")),
            "negative_test": sorted((DATA / "negative_test").glob("*.wav")),
            "speech_test": sorted((DATA / "speech_test").glob("*.wav"))}
    scores = {k: [stream_scores(model, read_wav(p)) for p in v] for k, v in sets.items()}
    fixtures = {p.stem: stream_scores(model, read_wav(p)) for p in sorted((REPO / "tests" / "fixtures").glob("*.wav"))}

    # audio real sin la palabra (features de validación de openWakeWord): puntuación por ventana con el .onnx directo
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    X = np.load(DL / "validation_set_features.npy")
    win = np.ascontiguousarray(np.lib.stride_tricks.sliding_window_view(X, 16, axis=0).transpose(0, 2, 1))
    real = np.concatenate([sess.run(None, {"input": win[i:i + 8192]})[0].ravel() for i in range(0, len(win), 8192)])
    hours = X.shape[0] * 0.08 / 3600

    report = {"hours_real_audio": round(hours, 2), "n": {k: len(v) for k, v in sets.items()}, "rows": []}
    print(f"\numbral  frames  recall_pos  fp_adv  fp_habla  fp/h_real({hours:.1f}h)   fixtures(activaciones)")
    for consecutive in (1, 2):
        for thr in thresholds:
            rec = np.mean([events(s, thr, consecutive, cd) >= 1 for s in scores["positive_test"]])
            fp_adv = np.mean([events(s, thr, consecutive, cd) >= 1 for s in scores["negative_test"]])
            fp_sp = np.mean([events(s, thr, consecutive, cd) >= 1 for s in scores["speech_test"]])
            fp_h = events(real, thr, consecutive, cd) / hours
            fx = {k: events(s, thr, consecutive, cd) for k, s in fixtures.items()}
            row = {"threshold": thr, "consecutive": consecutive, "recall": round(float(rec), 4), "fp_adversarial": round(float(fp_adv), 4),
                   "fp_speech": round(float(fp_sp), 4), "fp_per_hour_real": round(float(fp_h), 3), "fixtures": fx}
            report["rows"].append(row)
            print(f"{thr:5.2f}   {consecutive}     {rec:8.3f}   {fp_adv:6.3f}  {fp_sp:7.3f}   {fp_h:8.2f}        {fx}")
    print("\nmáximo por fixture:", {k: round(float(s.max()), 3) for k, s in fixtures.items()})

    # recomendación: FP/h real ≤ 0,3 y ningún fixture negativo activado; máxima recall; a igualdad, umbral más bajo
    neg_fx = [k for k in fixtures if "negativo" in k or k == "es_proyecto"]
    ok = [r for r in report["rows"] if r["fp_per_hour_real"] <= 0.3 and all(r["fixtures"][k] == 0 for k in neg_fx)]
    best = max(ok, key=lambda r: (r["recall"], -r["threshold"], -r["consecutive"])) if ok else None
    if best:
        report["recommended"] = {"sensitivity": round(1 - best["threshold"], 2), "min_consecutive_frames": best["consecutive"],
                                 "cooldown_s": cooldown_s, **{k: best[k] for k in ("recall", "fp_per_hour_real")}}
        print("\nrecomendado:", report["recommended"])
    else:
        print("\nningún umbral cumple FP/h ≤ 0,3 sin activar fixtures negativos; revisa la tabla")
    summary_path = MODEL_DIR / f"{MODEL_NAME}.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    summary["evaluation"] = report
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


# ----------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["all", "download", "gen", "features", "train", "eval"])
    ap.add_argument("--steps", type=int, default=30000, help="pasos de la secuencia 1 de auto_train (las 2 y 3 usan steps/10)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--overwrite", action="store_true", help="recalcula las features aunque existan")
    args = ap.parse_args()
    stages = ["download", "gen", "features", "train", "eval"] if args.stage == "all" else [args.stage]
    for s in stages:
        log(f"=== {s} ===")
        {"download": stage_download, "gen": lambda: stage_gen(args.seed), "features": lambda: stage_features(args.overwrite),
         "train": lambda: stage_train(args.steps, args.seed), "eval": stage_eval}[s]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
