# Contributing

Thanks for your interest. This is a personal project, but improvements are welcome.

## Before opening a PR

1. Open an issue describing the problem or the idea.
2. Set up the environment: `uv venv --python 3.12 .venv && source .venv/bin/activate && uv sync --extra dev && uv pip install -e .`
3. Run the tests: `pytest`
4. Keep changes focused: one topic per PR.

## Style

- Python 3.12, type hints where they help, no new dependencies unless they are essential.
- No keys, tokens or personal data in the repository.
- Commit messages in the imperative mood, in English or Spanish, used consistently.
