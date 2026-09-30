# Hands-on Guide

A walkthrough of every capability, with the command to run, what to look for, and real output
from runs on `claude-opus-5-5`. For the design behind it, see the [README](../README.md).

---

## Contents

- [Setup](#setup)
- [1. One document, end to end](#1-one-document-end-to-end)
- [2. Null, not fabricated](#2-null-not-fabricated)
- [3. The validation-retry loop](#3-the-validation-retry-loop)
- [4. Information that isn't there](#4-information-that-isnt-there)
- [5. Oversized documents and chunking](#5-oversized-documents-and-chunking)
- [6. A 100-document batch](#6-a-100-document-batch)
- [7. Few-shot vs zero-shot](#7-few-shot-vs-zero-shot)
- [8. Reading the report](#8-reading-the-report)
- [9. Experiments to try](#9-experiments-to-try)
- [Troubleshooting](#troubleshooting)

---

## Setup

```bash
uv sync
cp .env.example .env        # set ANTHROPIC_API_KEY
make check                  # offline: lint, types, 130 tests
```

Every command below that calls Claude costs real money. Approximate costs are given with each one.

---

## 1. One document, end to end

**What it shows:** the full path for a clean document: request, strict tool call, three
validation layers, and routing.

```bash
uv run extractor extract data/documents/doc-005.md
```

The document is a conference poster whose DOI is written as a URL:

```text
POSTER P-213
TELEPHONE BEFRIENDING FOR DEPRESSION IN OLDER ADULTS: A CROSS-SECTIONAL SURVEY IN PRIMARY CARE
Authors: Marta Castellanos
Presented at the European Congress of Clinical Research 2013
Design: Cross-sectional
N = 118
...
Abstract: https://doi.org/10.5555/njph.2013.1565
Key refs: Chowdhury 2000; Silva 2002; Kimura 1999; Novak 2007
```

Output (abridged):

```text
round 1: 1 request(s) via sync (synchronous mode requested)
  · a1: tool_call
  · accepted: valid extraction
{
  "doi": "10.5555/njph.2013.1565",          ← URL stripped to a bare DOI
  "study_type": "cross_sectional_study",
  "sample_size": 118,                        ← "N = 118" as an integer
  "cited_works": [{"first_author": "Chowdhury", "year": 2000}, ...],
  "evidence": {
    "doi": "Abstract: https://doi.org/10.5555/njph.2013.1565",
    "sample_size": "N = 118", ...
  }
}
accepted → auto_accept
```

**What to look for:** every non-null grounded field has a quote that appears verbatim in the
document. That's what the grounding layer checked before accepting. Cost: about $0.03.

---

## 2. Null, not fabricated

**What it shows:** fields the document doesn't state come back `null`, with `null` evidence.

```bash
uv run pytest -m live -k absent_fields
```

The test picks a narrative abstract with no DOI and no funding statement and asserts `doi`,
`funding_source`, and `evidence.doi` are all `null`. Across the full corpus, the
[null-handling table](../README.md#2-the-extraction-contract) counts every case where a value was
returned for a field the document doesn't contain.

---

## 3. The validation-retry loop

**What it shows:** a format error sent back with the document, the failed extraction, and the
exact error, then fixed on the next attempt.

You can see the exact correction the model would receive, without an API call:

```bash
cat > /tmp/bad.json <<'EOF'
{ "title": "Community pharmacist follow-up after hospital discharge", "doi": "https://doi.org/10.5555/x", ... }
EOF
uv run extractor validate /tmp/bad.json path/to/document.md
```

```text
doi [format, retryable] 'https://doi.org/10.5555/x' does not match '^10\.\d{4,9}/\S+$'.
    Expected: Bare DOI starting with '10.' (strip any https://doi.org/ prefix). null if the
    document has no DOI.
```

In a run, each attempt is recorded in `results.jsonl` with its issues, so you can follow a
document from `extract` to `correct` to `accepted`:

```json
"attempts": [
  {"number": 1, "request": "extract", "issues": [{"field": "sample_size", "kind": "ungrounded", ...}]},
  {"number": 2, "request": "correct", "issues": []}
]
```

The report's **Validation issues and retries** table aggregates this across the run: how many
issues of each kind were seen, retried, and resolved.

---

## 4. Information that isn't there

**What it shows:** when a required field is genuinely absent, the model says so explicitly, the
pipeline does **not** retry, and a human gets the document with the reason.

```bash
uv run extractor extract data/documents/doc-003.md     # an internal technical note, no authors
```

```text
  · a1: tool_call
  · needs_review: only unresolvable issues (information absent)
  "authors": ["unknown"],
  "authors": 0.0,                         ← field_confidence
needs_review → human_review
  - unresolved missing_required issue on authors: the document names no authors
  - low confidence on authors: 0.00 < 0.75
```

One request, no retry: re-asking cannot produce an author the document doesn't name, and it would
invite the model to invent one.

---

## 5. Oversized documents and chunking

**What it shows:** a document whose output doesn't fit in `max_tokens` (a 400-entry reference
list) is detected by its `stop_reason`, split into chunks, and merged.

```bash
uv run extractor extract data/documents/doc-008.md
```

```text
round 1: 1 request(s) via sync (synchronous mode requested)
round 2: 3 request(s) via sync (synchronous mode requested)
  · a1: truncated (hit max_tokens)
  · response truncated at max_tokens: resubmitting as 3 chunks
  · a1c1: tool_call            ← part 1: every field, record_study
  · a1c2: tool_call            ← parts 2-3: cited works only, record_cited_works
  · a1c3: tool_call
  · accepted: valid extraction
accepted → auto_accept
```

Chunks are cut on line boundaries, so a reference entry is never split. The chunk size comes from
a characters-per-token ratio measured once with `count_tokens`. Cost: about $0.40 (two rounds on a
15k-token document).

---

## 6. A 100-document batch

**What it shows:** the Message Batches API at scale: submission keyed by `custom_id`, polling,
results in any order, resubmission rounds, and SLA accounting.

```bash
uv run extractor run --sla-hours 4
```

Result on Opus 5.5 (the run behind the README numbers):

```text
round 1: 100 request(s) via batch (estimated batch time 1.0 h fits in the remaining 4.0 h)
  submitted batch msgbatch_014L5D2niW17zHk1F2VpnMx8
  msgbatch_014L5D2niW17zHk1F2VpnMx8: 100 still processing        ← polled every 30 s
  batch ended: {'processing': 0, 'succeeded': 100, 'errored': 0, 'canceled': 0, 'expired': 0}
round 2: 15 request(s) via sync (estimated batch time 3.4 h exceeds the remaining 1.6 h)
```

**What to look for:**
- The planner chose a batch for round 1, then **synchronous for round 2**, because round 1
  took 2.26 h and another batch would have overrun the 4 h SLA.
- Round 2 holds 15 requests: 3 oversized reports resubmitted as 5 chunks each.
- The report ends with total time **3.11 h (77.7% of the SLA), SLA met**, and the worst case if
  every round took the 24 h maximum.

Batches are slow and unpredictable: this one took 2.26 h, and a second batch launched at the same
time was still unstarted after 4 hours. Use `--sync` when you need results now.

---

## 7. Few-shot vs zero-shot

**What it shows:** the effect of the five few-shot examples, measured on the same 100 documents in
the same batch (200 requests, `fs-doc-NNN__a1` and `zs-doc-NNN__a1`).

```bash
uv run extractor ab-test          # both variants in one batch (slow queue) ...
uv run extractor ab-test --sync   # ... or synchronously
```

The measured comparison ran on Claude Haiku 4.5 over the first 40 documents, because Opus 5.5 is
at 100% and cannot show an effect (`EXTRACTOR_MODEL=claude-haiku-4-5 EXTRACTOR_EFFORT=none
uv run extractor run --sync --no-few-shot --limit 40`):

| | Few-shot | Zero-shot |
|---|---|---|
| Overall accuracy | 99.2% | 98.9% |
| Values invented for absent fields | **1** | **4** |
| `primary_outcome` | 100% | 92.5% |

The signal is in null handling: without examples the model invented an outcome or a sample size
where the document states none. Accuracy overall is within noise on 40 documents, and the runs
used different validator versions, so read the [full table](results/few-shot-vs-zero-shot.md)
for the caveats.

---

## 8. Reading the report

`runs/<run_id>/report.md` has five sections:

| Section | Answers |
|---|---|
| **Outcome** | How many documents were accepted, sent to review, or failed; cost; prompt-cache hit rate |
| **Processing time and SLA** | Each round's mode, size, and duration; total time against the SLA; worst case |
| **Validation issues and retries** | Per issue kind: seen, retried, resolved by a retry, persisted, never retried |
| **Accuracy against ground truth** | Field accuracy per document type, null handling, calibration, and flagged inconsistencies |
| **Confidence calibration and routing** | Are high-confidence fields more accurate, and is what's auto-accepted more accurate than what's reviewed? |

The published reports behind the README's numbers are in [`docs/results/`](results/).

---

## 9. Experiments to try

**Turn off grounding pressure.** Run with zero-shot prompts and compare null handling:

```bash
uv run extractor run --no-few-shot --limit 30
```

**Force the SLA fallback.** With a tiny SLA the planner sends every round synchronously:

```bash
uv run extractor run --limit 10 --sla-hours 0.01
```

The report's round table shows `sync` with the reason `estimated batch time 1.0 h exceeds the
remaining 36 s`.

**Make everything need review.** Raise the bar and watch the review queue grow:

```bash
EXTRACTOR_REVIEW_THRESHOLD=0.99 uv run extractor run --limit 20
```

**Retry the unretryable.** Set `retry_unresolvable=True` in `Settings` and rerun the technical
notes. The `missing_required` row of the retry table shows retries that never resolve, which is
the evidence for not retrying them.

**Change the chunk size.** `EXTRACTOR_CHUNK_TOKENS=3000` splits the long reports into more parts.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `AuthenticationError` | Set `ANTHROPIC_API_KEY` in `.env` (not `.env.example`) or export it. |
| `No documents found` | Run `uv run extractor corpus` to (re)generate `data/`. |
| A batch sits at "still processing" | Normal: most batches finish within an hour, and the maximum is 24 hours. The run resumes routing as soon as it ends. |
| `test_committed_data_matches_the_generator` fails | You changed `corpus.py` without regenerating: run `make corpus`. |
| `test_published_schema_file_matches_the_code` fails | You changed `schema.py` without regenerating: run `make schema`. |
| Diagrams don't render on the GitHub mobile app | They do: every diagram is also a PNG in `docs/diagrams/`. Re-render with `make diagrams`. |
