# Extraction run `batch-100`

Model `claude-opus-5-5` · effort `medium` · few-shot on · 100 documents

## Outcome

| Metric | Value |
|---|---|
| Accepted (valid on first or later attempt) | 97 |
| Needs review (issues left after retries) | 3 |
| Failed (no usable extraction) | 0 |
| Auto-accepted after routing | 79 |
| Routed to human review | 21 |
| Chunked (oversized) documents | 3 |
| Cost (USD, batch discount applied) | $2.86 |
| Prompt-cache reads / (reads + writes) | 77% |

## Processing time and SLA

| Round | Mode | Requests | Duration | Why this mode |
|---|---|---|---|---|
| 1 | batch | 100 | 2.26 h | estimated batch time 1.0 h fits in the remaining 4.0 h |
| 2 | sync | 15 | 45.3 min | estimated batch time 3.4 h exceeds the remaining 1.6 h |

Total processing time **3.11 h** against an SLA of **4.00 h** (77.7% used) · SLA met. Worst case if every batch round took the 24 h maximum: 24.00 h.

## Validation issues and retries

| Issue kind | Seen | Retried | Resolved by retry | Persisted | Not retried |
|---|---|---|---|---|---|
| missing_required | 3 | 0 | 0 | 0 | 3 |

## Accuracy against ground truth

Overall field accuracy **100%** across 100 documents · mean cited-works F1 1.0.

| Field | bibliography_article | conference_poster | long_report | narrative_abstract | press_release | structured_table | technical_note | All |
|---|---|---|---|---|---|---|---|---|
| `title` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `authors` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `publication_year` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `doi` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `study_type` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `sample_size` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `funding_source` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `primary_outcome` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `cited_works` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |

### Null handling

| Field | Truly absent | Returned null | Fabricated | Present | Missed |
|---|---|---|---|---|---|
| `publication_year` | 7 | 7 | 0 | 93 | 0 |
| `doi` | 44 | 44 | 0 | 56 | 0 |
| `sample_size` | 18 | 18 | 0 | 82 | 0 |
| `funding_source` | 45 | 45 | 0 | 55 | 0 |
| `primary_outcome` | 21 | 21 | 0 | 79 | 0 |

### Confidence calibration and routing

| Group | Fields | Accuracy |
|---|---|---|
| Confidence ≥ threshold | 788 | 100% |
| Confidence < threshold | 12 | 100% |
| Auto-accepted documents | 711 | 100% |
| Routed to review | 189 | 100% |

No document type falls 15+ points below a field's overall accuracy.
