# Few-shot vs zero-shot (Claude Haiku 4.5, first 40 documents)

Same 40 documents (`doc-001` to `doc-040`), same model, synchronous API.

| Field | Few-shot | Zero-shot | Delta (points) |
|---|---|---|---|
| `title` | 100% | 100% | 0 |
| `authors` | 100% | 100% | 0 |
| `publication_year` | 97.5% | 100% | -2.5 |
| `doi` | 100% | 100% | 0 |
| `study_type` | 100% | 100% | 0 |
| `sample_size` | 100% | 97.5% | +2.5 |
| `funding_source` | 100% | 100% | 0 |
| `primary_outcome` | 100% | 92.5% | +7.5 |
| `cited_works` | 95% | 100% | -5 |
| **Overall** | **99.2%** | **98.9%** | +0.3 |

**Fabricated values for fields the document does not state** (the behaviour the examples teach):
few-shot **1**, zero-shot **4** (3 x `primary_outcome`, 1 x `sample_size`).

## How to read this

- **Not a controlled comparison.** The few-shot results were produced before two validator fixes
  (a year inside a DOI no longer counts as a publication date, and a far-too-short
  `cited_works` list triggers chunking). The zero-shot run used the fixed pipeline, so its retries
  repaired some errors the few-shot run kept. The two `-2.5` / `-5` rows are that effect, not an
  advantage for zero-shot.
- **Small sample.** 40 documents, one run each. Differences of a few points are within noise.
- **The signal is in null handling.** Without examples, the model invented a primary outcome or a
  sample size 4 times where the document states none. With examples: once.
- Zero-shot needed a retry or chunking on 6 of 40 documents; every one ended accepted or routed.
- Claude Opus 5.5 scored 100% on all 100 documents with examples, so it is at ceiling and cannot
  show a few-shot effect on this corpus.
