# Contributing

Thanks for taking the time to contribute. This guide covers the local setup, the checks every
change must pass, and how to propose changes.

## Local setup

Requirements: Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Jagoul/structured-data-extraction-pipeline.git
cd structured-data-extraction-pipeline
uv sync                      # creates .venv with runtime and dev dependencies
uv run pre-commit install    # optional: run the checks on every commit
```

## Checks

Every pull request runs these in CI. Run them locally first:

```bash
make check        # lint + format check + type check + tests
```

or individually:

| Check | Command |
|---|---|
| Lint | `uv run ruff check .` |
| Format | `uv run ruff format --check .` |
| Types (strict) | `uv run mypy` |
| Tests + coverage | `uv run pytest --cov` |

The default test suite is offline and needs no API key. Tests marked `live` call the Claude API
and are opt-in: `uv run pytest -m live` with `ANTHROPIC_API_KEY` set.

## Changing the schema

The schema is a published contract. When you change `src/extraction_pipeline/schema.py`:

1. Regenerate the published copy: `uv run extractor schema --out schemas/study_record.schema.json`.
2. Update the few-shot examples in `prompts.py` if they no longer validate
   (`tests/test_prompts_and_corpus.py` will tell you).
3. Note the change in `CHANGELOG.md`. Removing or renaming a field is a breaking change.

## Changing the corpus

The corpus is generated. Edit `corpus.py`, then run `uv run extractor corpus` and commit the
regenerated `data/`. `tests/test_prompts_and_corpus.py` fails if the committed data and the
generator disagree.

## Pull requests

- Keep each pull request focused on one change.
- Add or update tests for any behaviour change.
- Describe what changed and why, and paste the relevant `make check` output.
- Never commit `.env`, API keys, or files under `runs/`.
