# TTS Voice Configuration & Customization

Jarvis uses local Text-to-Speech (TTS) to read answers aloud without transmitting your audio or data to third-party cloud services.

---

## 1. Available Voices

### Kokoro Neural Voices (Local, Recommended)
When `tts.provider` is set to `kokoro` (and models have been downloaded via `jarvis tts download`), the following Spanish neural voices are available:

| Voice Identifier | Gender | Tone / Characteristics |
|---|---|---|
| `ef_dora` *(default)* | Female | Clear, natural European Spanish tone |
| `em_alex` | Male | Calm, conversational tone |
| `em_santa` | Male | Warm, deeper tone |

### macOS System Voices (`say` fallback)
If the Kokoro model has not been downloaded, or if `tts.provider` is set to `macos_say`, Jarvis uses the native macOS speech synthesizer (`say`).

Common Spanish system voices include:
- `Paulina` (es_MX)
- `Mónica` (es_ES)
- `Jorge` (es_ES)
- `Juan` (es_MX)
- Any installed "Enhanced", "Premium", or "Mejorada" voices from macOS **System Settings → Accessibility → Spoken Content → System Voice**.

---

## 2. Testing Voices from the Terminal

### List available voices
Run the following command to see which Kokoro and system voices are currently installed and active:
```bash
jarvis tts voices
```

### Preview a specific voice
You can test any voice with a custom phrase directly from the CLI without changing your configuration:

```bash
# Test Kokoro voices
jarvis tts say --voice ef_dora "Hola, soy Dora. ¿En qué te puedo ayudar hoy?"
jarvis tts say --voice em_alex "Hola, soy Alex. Todo listo para trabajar."
jarvis tts say --voice em_santa "Hola, soy Santa. Los modelos están sincronizados."

# Test macOS system voices
jarvis tts say --voice Paulina "Hola, esta es una voz del sistema."
jarvis tts say --voice Mónica "Hola, probando la voz Mónica."
```

### Save synthesized audio to a file
For Kokoro voices, you can export the synthesized audio to a `.wav` file:
```bash
jarvis tts say --voice em_alex "Generando archivo de audio de prueba." --out ~/Desktop/prueba.wav
```

---

## 3. Configuring the Active Voice

### Option A: Using the CLI
Use `jarvis config set` to update your settings in `~/.jarvis/config.yaml`:

```bash
# Switch Kokoro voice
jarvis config set tts.voice em_alex

# Adjust playback speed (Kokoro only: default 1.0)
jarvis config set tts.speed 1.1

# Switch provider to macOS system say
jarvis config set tts.provider macos_say
jarvis config set tts.voice Paulina

# Disable speech output entirely
jarvis config set tts.provider none
```

### Option B: Editing `~/.jarvis/config.yaml`
You can also edit `~/.jarvis/config.yaml` directly:

```yaml
tts:
  enabled: true
  provider: kokoro          # Options: "kokoro", "macos_say", "none"
  voice: em_alex            # Kokoro: "ef_dora", "em_alex", "em_santa"
                            # macOS say: "Paulina", "Mónica", etc.
  speed: 1.0                # Kokoro only: speech speed multiplier (1.0 = normal)
  rate: 175                 # macos_say only: words per minute
```

---

## 4. Downloading the Kokoro Model

To download the Kokoro ONNX model files (~350 MB, one-time setup):
```bash
jarvis tts download
```
The model files are saved to `~/.jarvis/models/kokoro/`:
- `kokoro-v1.0.onnx`
- `voices-v1.0.bin`

If these files are missing or incomplete, Jarvis automatically falls back to macOS `say` with system voices without crashing.
