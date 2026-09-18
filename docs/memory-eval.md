# Evaluación de memoria (scripts/memory_eval.py)

Conjunto sintético en un directorio temporal (no toca la memoria personal): una preferencia dicha por el usuario, un documento
importado con tres hechos extraídos (con cita y localizador) y una pregunta sin respuesta en memoria.

Resultado real (2026-09-04, claude-opus-5, coste total ~$0.30):

| Pregunta | Recuperación | Atribución (citado) | Contenido | Afirmación sin respaldo |
|---|---|---|---|---|
| ¿Qué preferencias mías conoces? | 1.0 | 1.0 | ✓ | no |
| ¿Cuándo es la reunión de arranque de Faro y de dónde lo sacaste? | 1.0 | 1.0 | ✓ (cita documento y línea) | no |
| ¿Cuál es el presupuesto de Faro? | 1.0 | 1.0 | ✓ | no |
| ¿Quién es el contacto del proveedor de sensores? | 1.0 | 1.0 | ✓ | no |
| ¿Cuál es mi color favorito? (sin datos) | — | — | responde «No lo sé, no tengo nada guardado» | no |
| ¿Qué sabes del proyecto Faro? | 1.0 | 1.0 | ✗ (solo por la comprobación literal de la palabra «Faro») | no |

Resumen: recuperación media 1.0, atribución media 1.0, 0 afirmaciones sin respaldo en 6 casos.
Ejecutar de nuevo: `python scripts/memory_eval.py` (usa Claude real) o `--demo`.
