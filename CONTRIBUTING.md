# Contribuir

Gracias por el interés. Este es un proyecto personal, pero las mejoras son bienvenidas.

## Antes de abrir un PR

1. Abre un issue describiendo el problema o la idea.
2. Instala el entorno: `uv venv --python 3.12 .venv && source .venv/bin/activate && uv sync --extra dev && uv pip install -e .`
3. Ejecuta las pruebas: `pytest`
4. Mantén los cambios acotados: un tema por PR.

## Estilo

- Python 3.12, tipado donde aporte, sin dependencias nuevas salvo que sean imprescindibles.
- Nada de claves, tokens ni datos personales en el repositorio.
- Mensajes de commit en imperativo y en español o inglés, de forma consistente.
