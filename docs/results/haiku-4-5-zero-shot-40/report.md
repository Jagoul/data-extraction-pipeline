# Extraction run `haiku-zeroshot-40`

Model `claude-haiku-4-5` · effort `None` · few-shot off · 40 documents

## Outcome

| Metric | Value |
|---|---|
| Accepted (valid on first or later attempt) | 38 |
| Needs review (issues left after retries) | 2 |
| Failed (no usable extraction) | 0 |
| Auto-accepted after routing | 36 |
| Routed to human review | 4 |
| Chunked (oversized) documents | 2 |
| Cost (USD, batch discount applied) | $0.46 |
| Prompt-cache reads / (reads + writes) | - |

## Processing time and SLA

| Round | Mode | Requests | Duration | Why this mode |
|---|---|---|---|---|
| 1 | sync | 40 | 4.2 min | synchronous mode requested |
| 2 | sync | 10 | 2.2 min | synchronous mode requested |

Total processing time **6.4 min** against an SLA of **4.00 h** (2.6% used) · SLA met. Worst case if every batch round took the 24 h maximum: 0 s.

## Validation issues and retries

| Issue kind | Seen | Retried | Resolved by retry | Persisted | Not retried |
|---|---|---|---|---|---|
| format | 3 | 3 | 3 | 0 | 0 |
| incomplete | 2 | 2 | 2 | 0 | 0 |
| missing_required | 4 | 2 | 0 | 2 | 2 |
| ungrounded | 1 | 1 | 1 | 0 | 0 |

## Accuracy against ground truth

Overall field accuracy **98.9%** across 40 documents · mean cited-works F1 1.0.

| Field | bibliography_article | conference_poster | long_report | narrative_abstract | press_release | structured_table | technical_note | All |
|---|---|---|---|---|---|---|---|---|
| `title` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `authors` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `publication_year` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `doi` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `study_type` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `sample_size` | 100% | 100% | 100% | 85.7% | 100% | 100% | 100% | **97.5%** |
| `funding_source` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `primary_outcome` | 87.5% | 100% | 100% | 71.4% | 100% | 100% | 100% | **92.5%** |
| `cited_works` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |

### Null handling

| Field | Truly absent | Returned null | Fabricated | Present | Missed |
|---|---|---|---|---|---|
| `publication_year` | 4 | 4 | 0 | 36 | 0 |
| `doi` | 14 | 14 | 0 | 26 | 0 |
| `sample_size` | 7 | 6 | 1 | 33 | 0 |
| `funding_source` | 16 | 16 | 0 | 24 | 0 |
| `primary_outcome` | 11 | 8 | 3 | 29 | 0 |

### Confidence calibration and routing

| Group | Fields | Accuracy |
|---|---|---|
| Confidence ≥ threshold | 316 | 99.4% |
| Confidence < threshold | 4 | 50.0% |
| Auto-accepted documents | 324 | 99.7% |
| Routed to review | 36 | 91.7% |

**Inconsistent cells** (15+ points below the field's overall accuracy):
- `primary_outcome` on narrative_abstract: 71.4% vs 92.5% overall
