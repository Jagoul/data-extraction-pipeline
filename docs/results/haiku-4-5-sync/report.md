# Extraction run `haiku-100`

Model `claude-haiku-4-5` · effort `None` · few-shot on · 100 documents

## Outcome

| Metric | Value |
|---|---|
| Accepted (valid on first or later attempt) | 97 |
| Needs review (issues left after retries) | 3 |
| Failed (no usable extraction) | 0 |
| Auto-accepted after routing | 95 |
| Routed to human review | 5 |
| Chunked (oversized) documents | 0 |
| Cost (USD, batch discount applied) | $0.63 |
| Prompt-cache reads / (reads + writes) | 100% |

## Processing time and SLA

| Round | Mode | Requests | Duration | Why this mode |
|---|---|---|---|---|
| 1 | sync | 100 | 11.0 min | synchronous mode requested |

Total processing time **11.0 min** against an SLA of **4.00 h** (4.6% used) · SLA met. Worst case if every batch round took the 24 h maximum: 0 s.

## Validation issues and retries

| Issue kind | Seen | Retried | Resolved by retry | Persisted | Not retried |
|---|---|---|---|---|---|
| missing_required | 3 | 0 | 0 | 0 | 3 |

## Accuracy against ground truth

Overall field accuracy **99%** across 100 documents · mean cited-works F1 0.979.

| Field | bibliography_article | conference_poster | long_report | narrative_abstract | press_release | structured_table | technical_note | All |
|---|---|---|---|---|---|---|---|---|
| `title` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `authors` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `publication_year` | 100% | 88% | 80% | 100% | 100% | 100% | 100% | **97%** |
| `doi` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `study_type` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `sample_size` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `funding_source` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `primary_outcome` | 100% | 100% | 100% | 100% | 100% | 100% | 100% | **100%** |
| `cited_works` | 100% | 100% | 0% | 100% | 100% | 100% | 100% | **95%** |

### Null handling

| Field | Truly absent | Returned null | Fabricated | Present | Missed |
|---|---|---|---|---|---|
| `publication_year` | 7 | 4 | 3 | 93 | 0 |
| `doi` | 44 | 44 | 0 | 56 | 0 |
| `sample_size` | 18 | 18 | 0 | 82 | 0 |
| `funding_source` | 45 | 45 | 0 | 55 | 0 |
| `primary_outcome` | 21 | 21 | 0 | 79 | 0 |

### Confidence calibration and routing

| Group | Fields | Accuracy |
|---|---|---|
| Confidence ≥ threshold | 796 | 100% |
| Confidence < threshold | 4 | 100% |
| Auto-accepted documents | 855 | 99% |
| Routed to review | 45 | 96% |

**Inconsistent cells** (15+ points below the field's overall accuracy):
- `publication_year` on long_report: 80% vs 97% overall
- `cited_works` on long_report: 0% vs 95% overall
