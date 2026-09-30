# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-09-30

### Added

- `record_study` extraction tool with a strict-mode JSON Schema: required, optional and nullable
  fields, a `study_type` enum with an `other` + detail pattern, field-level confidence, and
  verbatim evidence quotes.
- Three-layer validation (JSON Schema, typed record, grounding against the source) with issues
  classified as `format`, `ungrounded`, or `missing_required`.
- Validation-retry loop that sends the document, the failed extraction and the exact errors back,
  and never retries information that is absent from the source.
- Five few-shot examples covering inline citations, numbered bibliographies, narrative prose,
  structured tables, URL DOIs, and an `other` study design.
- Message Batches runner keyed by `custom_id`: resubmits expired and errored requests, chunks
  documents whose output is truncated, and falls back to the synchronous API when the next batch
  would not fit in the SLA.
- Confidence-based human-review routing with a stricter bar for critical fields.
- Evaluation by document type and field, null-handling metrics, calibration, and retry statistics.
- Deterministic 100-document synthetic corpus with exact ground truth.
- `extractor` CLI: `corpus`, `extract`, `run`, `ab-test`, `schema`, `validate`.
- Grounding rule: a publication year must appear as a date in its evidence quote, not inside a DOI
  or URL (found with Claude Haiku 4.5, which read years out of DOIs).
- `incomplete` issue kind: a `cited_works` list under 95% of the document's numbered references
  resubmits the document as chunks (found with Haiku, which stops listing early without a
  truncation signal).
- Optional effort (`EXTRACTOR_EFFORT=none`) and model-gated refusal fallbacks, so the pipeline runs
  on Claude Haiku 4.5.
- Published run reports in `docs/results/`.
