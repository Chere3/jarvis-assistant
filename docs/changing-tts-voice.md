# Changing the Kokoro TTS voice

Jarvis ships with a local Kokoro neural voice. Use this short guide to try another voice or switch providers.

## 1. Download the model (once)

```bash
jarvis tts download
```

This stores `kokoro-v1.0.onnx` and `voices-v1.0.bin` under `~/.jarvis/models/kokoro/`.

## 2. List and preview voices

```bash
jarvis tts voices
jarvis tts say --voice em_alex "Hola, soy Jarvis"
```

Spanish Kokoro voices currently available (see `jarvis/audio/tts_kokoro.py`):

| Voice | Description |
| --- | --- |
| `ef_dora` | Female (default) |
| `em_alex` | Male |
| `em_santa` | Male |

## 3. Persist the choice

```bash
jarvis config set tts.voice em_alex
jarvis config set tts.speed 1.0
```

Or edit `~/.jarvis/config.yaml` (template: `config/assistant.example.yaml`):

```yaml
tts:
  provider: kokoro
  voice: em_alex
  speed: 1.0
  enabled: true
```

## 4. Use the macOS system voice instead

```bash
jarvis config set tts.provider macos_say
jarvis config set tts.voice Paulina   # or Mónica, etc.
```

`tts.rate` applies to `macos_say`; `tts.speed` applies to Kokoro.

## 5. Confirm

```bash
jarvis config show
jarvis tts say "Prueba de voz"
```

Restart `jarvis listen` or the floating app after changing config so the new voice is picked up.
