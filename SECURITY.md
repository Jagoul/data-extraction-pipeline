# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/Jagoul/structured-data-extraction-pipeline/security/advisories/new)
instead. You can expect an acknowledgement within a few days.

## Handling credentials

- The pipeline reads `ANTHROPIC_API_KEY` from the environment or from a local `.env` file.
  `.env` is git-ignored; only `.env.example`, which holds no values, is committed.
- CI runs the offline test suite only and needs no secrets.
- If you ever commit a key by mistake, revoke it in the Anthropic Console immediately. Rewriting
  git history does not un-leak a key that has been pushed.

## Data handling

Documents are sent to the Claude API for extraction. Do not process documents you are not
permitted to share with a third-party API, and review your organisation's data-retention
requirements before running the pipeline on real data. The bundled corpus is synthetic.
