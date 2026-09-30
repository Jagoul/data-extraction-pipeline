# Extraction run `haiku-fix`

Model `claude-haiku-4-5` · effort `None` · few-shot on · 6 documents

## Outcome

| Metric | Value |
|---|---|
| Accepted (valid on first or later attempt) | 4 |
| Needs review (issues left after retries) | 2 |
| Failed (no usable extraction) | 0 |
| Auto-accepted after routing | 3 |
| Routed to human review | 3 |
| Chunked (oversized) documents | 4 |
| Cost (USD, batch discount applied) | $0.50 |
| Prompt-cache reads / (reads + writes) | 92.3% |

## Processing time and SLA

| Round | Mode | Requests | Duration | Why this mode |
|---|---|---|---|---|
| 1 | sync | 6 | 59.4 min | synchronous mode requested |
| 2 | sync | 12 | 3.5 min | synchronous mode requested |
| 3 | sync | 4 | 1.0 min | synchronous mode requested |

Total processing time **1.06 h** against an SLA of **4.00 h** (26.6% used) · SLA met. Worst case if every batch round took the 24 h maximum: 0 s.

## Validation issues and retries

| Issue kind | Seen | Retried | Resolved by retry | Persisted | Not retried |
|---|---|---|---|---|---|
| incomplete | 5 | 3 | 2 | 3 | 0 |
| ungrounded | 4 | 4 | 3 | 1 | 0 |

## Accuracy against ground truth

Overall field accuracy **96.3%** across 6 documents · mean cited-works F1 0.976.

| Field | conference_poster | long_report | All |
|---|---|---|---|
| `title` | 100% | 100% | **100%** |
| `authors` | 100% | 100% | **100%** |
| `publication_year` | 100% | 100% | **100%** |
| `doi` | 100% | 100% | **100%** |
| `study_type` | 100% | 100% | **100%** |
| `sample_size` | 100% | 100% | **100%** |
| `funding_source` | 100% | 100% | **100%** |
| `primary_outcome` | 100% | 100% | **100%** |
| `cited_works` | 100% | 50.0% | **66.7%** |

### Null handling

| Field | Truly absent | Returned null | Fabricated | Present | Missed |
|---|---|---|---|---|---|
| `publication_year` | 3 | 3 | 0 | 3 | 0 |
| `doi` | 1 | 1 | 0 | 5 | 0 |
| `sample_size` | 0 | 0 | 0 | 6 | 0 |
| `funding_source` | 1 | 1 | 0 | 5 | 0 |
| `primary_outcome` | 1 | 1 | 0 | 5 | 0 |

### Confidence calibration and routing

| Group | Fields | Accuracy |
|---|---|---|
| Confidence ≥ threshold | 48 | 100% |
| Confidence < threshold | 0 | - |
| Auto-accepted documents | 27 | 100% |
| Routed to review | 27 | 92.6% |

**Inconsistent cells** (15+ points below the field's overall accuracy):
- `cited_works` on long_report: 50.0% vs 66.7% overall
