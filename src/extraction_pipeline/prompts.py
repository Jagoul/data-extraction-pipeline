"""System prompt, few-shot examples, and the user messages for each kind of request.

The system prompt (with its few-shot examples) is identical for every request, so it is marked
for prompt caching: in a 100-document batch it is written once and read from cache afterwards.
Everything that varies per document goes in the user message, after the cached prefix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from extraction_pipeline.schema import TOOL_NAME

CITATIONS_TOOL_NAME = "record_cited_works"

INSTRUCTIONS = f"""\
You extract structured data from research documents for a systematic-review database. \
Accuracy matters more than completeness: a wrong value is worse than a null.

Rules:
1. Call the {TOOL_NAME} tool exactly once with the complete extraction. Do not answer in prose.
2. Copy text values as written. Normalise formats only: DOIs are bare (10.xxxx/...), \
years and sample sizes are integers.
3. Nullable fields: when the document does not state the information, use null. Never infer, \
estimate, or borrow a value from elsewhere. A cited work's year is not the publication year; \
a count of studies is not a sample size of participants.
4. Evidence: for every non-null field listed in `evidence`, copy the exact sentence, line, or \
table row that supports it. A null field gets null evidence.
5. study_type: choose the listed design the document states. Use "other" only when none fits, \
and put the document's own name for the design in study_type_detail.
6. cited_works: include every cited work, whether it appears as an inline citation such as \
(Smith et al., 2019) or in a numbered reference list. Use the first author's surname; year is \
null when not given (for example "n.d.").
7. If the document has no title or names no author at all, use "unknown" (authors: ["unknown"]) \
with confidence 0.0 so a human can review it. Never invent a name.
8. field_confidence: 0.9-1.0 when the value, or its absence, is explicit; 0.5-0.8 when you had \
to interpret; below 0.5 when unsure.
"""


@dataclass(frozen=True)
class FewShotExample:
    name: str
    teaches: str
    document: str
    extraction: dict[str, Any]


def _confidence(**overrides: float) -> dict[str, float]:
    base = {
        "title": 0.98,
        "authors": 0.97,
        "publication_year": 0.95,
        "doi": 0.97,
        "study_type": 0.9,
        "sample_size": 0.93,
        "funding_source": 0.93,
        "primary_outcome": 0.9,
    }
    return base | overrides


FEW_SHOT_EXAMPLES: tuple[FewShotExample, ...] = (
    FewShotExample(
        name="narrative_inline_citations",
        teaches="narrative prose, inline author-year citations, funding absent -> null",
        document="""\
# Walking groups and knee osteoarthritis: a randomised controlled trial

Hannah Ortiz, Peter Lund

Published in Journal of Musculoskeletal Care, 2019.
doi:10.5555/example.2019.0101

**Background.** Regular walking reduces knee pain (Garcia et al., 2015; Brown and Lee, 2017).

**Methods.** We randomised 212 adults to weekly walking groups or usual care. The primary \
outcome was pain score at 6 months.

**Results.** Pain fell more in the walking group, in line with Chen, 2018.""",
        extraction={
            "title": "Walking groups and knee osteoarthritis: a randomised controlled trial",
            "authors": ["Hannah Ortiz", "Peter Lund"],
            "publication_year": 2019,
            "doi": "10.5555/example.2019.0101",
            "study_type": "randomized_controlled_trial",
            "study_type_detail": None,
            "sample_size": 212,
            "funding_source": None,
            "primary_outcome": "pain score at 6 months",
            "key_findings": ["Pain fell more in the walking group than with usual care."],
            "cited_works": [
                {"first_author": "Garcia", "year": 2015},
                {"first_author": "Brown", "year": 2017},
                {"first_author": "Chen", "year": 2018},
            ],
            "field_confidence": _confidence(funding_source=0.9),
            "evidence": {
                "publication_year": "Published in Journal of Musculoskeletal Care, 2019.",
                "doi": "doi:10.5555/example.2019.0101",
                "sample_size": "We randomised 212 adults to weekly walking groups or usual care.",
                "funding_source": None,
                "primary_outcome": "The primary outcome was pain score at 6 months.",
            },
        },
    ),
    FewShotExample(
        name="table_numbered_bibliography",
        teaches="structured table, 'Not reported' -> null, URL DOI -> bare DOI, '1,204' -> "
        "1204, numbered reference list",
        document="""\
## Evidence summary: Community pharmacist follow-up after hospital discharge

Authors: Ines Kovac; Samuel Adebayo; Mei Lin

| Characteristic | Value |
|---|---|
| Design | Cohort |
| Participants | 1,204 |
| Primary outcome | Not reported |
| Funding | Harbor Health Trust |
| Year | 2021 |
| DOI | https://doi.org/10.5555/example.2021.0042 |

### Sources
1. Patel RK, Owens J. Discharge medication errors. Pharmacy Practice Review. 2016;9:12-19.
2. Nowak A. Readmission after heart failure. Clinical Outcomes Quarterly. 2019;14:88-95.""",
        extraction={
            "title": "Community pharmacist follow-up after hospital discharge",
            "authors": ["Ines Kovac", "Samuel Adebayo", "Mei Lin"],
            "publication_year": 2021,
            "doi": "10.5555/example.2021.0042",
            "study_type": "cohort_study",
            "study_type_detail": None,
            "sample_size": 1204,
            "funding_source": "Harbor Health Trust",
            "primary_outcome": None,
            "key_findings": ["Pharmacist follow-up after discharge was evaluated in a cohort."],
            "cited_works": [
                {"first_author": "Patel", "year": 2016},
                {"first_author": "Nowak", "year": 2019},
            ],
            "field_confidence": _confidence(study_type=0.85, primary_outcome=0.95),
            "evidence": {
                "publication_year": "| Year | 2021 |",
                "doi": "| DOI | https://doi.org/10.5555/example.2021.0042 |",
                "sample_size": "| Participants | 1,204 |",
                "funding_source": "| Funding | Harbor Health Trust |",
                "primary_outcome": None,
            },
        },
    ),
    FewShotExample(
        name="poster_other_design",
        teaches="terse poster, design outside the enum -> other + detail, 'n.d.' -> null year",
        document="""\
POSTER P-042
CORE OUTCOMES FOR PAEDIATRIC TELEHEALTH: AN INTERNATIONAL DELPHI CONSENSUS
Authors: Rosa Amani / Theo Varga
Presented at the World Forum on Population Health 2021
Design: Delphi consensus study
N = 36 experts
Key refs: Morris 2019; Unicef n.d.""",
        extraction={
            "title": "CORE OUTCOMES FOR PAEDIATRIC TELEHEALTH: AN INTERNATIONAL DELPHI CONSENSUS",
            "authors": ["Rosa Amani", "Theo Varga"],
            "publication_year": 2021,
            "doi": None,
            "study_type": "other",
            "study_type_detail": "Delphi consensus study",
            "sample_size": 36,
            "funding_source": None,
            "primary_outcome": None,
            "key_findings": [
                "An international panel agreed core outcomes for paediatric telehealth."
            ],
            "cited_works": [
                {"first_author": "Morris", "year": 2019},
                {"first_author": "Unicef", "year": None},
            ],
            "field_confidence": _confidence(publication_year=0.85, primary_outcome=0.9),
            "evidence": {
                "publication_year": "Presented at the World Forum on Population Health 2021",
                "doi": None,
                "sample_size": "N = 36 experts",
                "funding_source": None,
                "primary_outcome": None,
            },
        },
    ),
    FewShotExample(
        name="press_release_review",
        teaches="journalistic prose, a count of studies is not a sample size, no DOI",
        document="""\
PRESS RELEASE — May 2020

New review: Sugar taxes and dental health

A systematic review led by Dr Joao Pinto finds that sugar taxes are linked to fewer cavities. \
The team pooled evidence from 18 studies.
The review was funded by the Seaside Dental Foundation.""",
        extraction={
            "title": "Sugar taxes and dental health",
            "authors": ["Joao Pinto"],
            "publication_year": 2020,
            "doi": None,
            "study_type": "systematic_review",
            "study_type_detail": None,
            "sample_size": None,
            "funding_source": "the Seaside Dental Foundation",
            "primary_outcome": None,
            "key_findings": ["Sugar taxes are linked to fewer cavities."],
            "cited_works": [],
            "field_confidence": _confidence(
                title=0.85, authors=0.8, publication_year=0.75, sample_size=0.85
            ),
            "evidence": {
                "publication_year": "PRESS RELEASE — May 2020",
                "doi": None,
                "sample_size": None,
                "funding_source": "The review was funded by the Seaside Dental Foundation.",
                "primary_outcome": None,
            },
        },
    ),
    FewShotExample(
        name="anonymous_note",
        teaches="required information genuinely absent -> 'unknown' with confidence 0.0",
        document="""\
TECHNICAL NOTE — for internal circulation

Subject: Hand hygiene audits in outpatient clinics

We conducted a cross-sectional survey of 140 clinic visits.
No author attribution is given for internal notes.""",
        extraction={
            "title": "Hand hygiene audits in outpatient clinics",
            "authors": ["unknown"],
            "publication_year": None,
            "doi": None,
            "study_type": "cross_sectional_study",
            "study_type_detail": None,
            "sample_size": 140,
            "funding_source": None,
            "primary_outcome": None,
            "key_findings": ["Hand hygiene was audited across outpatient clinic visits."],
            "cited_works": [],
            "field_confidence": _confidence(authors=0.0, title=0.9, sample_size=0.8),
            "evidence": {
                "publication_year": None,
                "doi": None,
                "sample_size": "We conducted a cross-sectional survey of 140 clinic visits.",
                "funding_source": None,
                "primary_outcome": None,
            },
        },
    ),
)


def _render_example(example: FewShotExample) -> str:
    return (
        f'<example name="{example.name}">\n'
        f"<document>\n{example.document}\n</document>\n"
        f"<{TOOL_NAME}_input>\n{json.dumps(example.extraction, indent=1, ensure_ascii=False)}\n"
        f"</{TOOL_NAME}_input>\n</example>"
    )


def system_prompt(*, few_shot: bool) -> str:
    if not few_shot:
        return INSTRUCTIONS
    examples = "\n\n".join(_render_example(example) for example in FEW_SHOT_EXAMPLES)
    return (
        f"{INSTRUCTIONS}\n"
        "The examples below show correct extractions from documents with different structures. "
        "They illustrate the rules; they are not part of any document you will receive.\n\n"
        f"<examples>\n{examples}\n</examples>"
    )


# --- User messages -----------------------------------------------------------------------------


def extraction_message(doc_id: str, text: str) -> str:
    return (
        f'<document id="{doc_id}">\n{text}\n</document>\n\n'
        f"Extract this document by calling the {TOOL_NAME} tool once."
    )


def correction_message(
    doc_id: str, text: str, previous: dict[str, Any] | None, errors: list[str]
) -> str:
    """The follow-up request: the document, the failed extraction, and the exact errors."""
    previous_block = (
        json.dumps(previous, indent=1, ensure_ascii=False) if previous else "(no tool call)"
    )
    return (
        f'<document id="{doc_id}">\n{text}\n</document>\n\n'
        f"<previous_extraction>\n{previous_block}\n</previous_extraction>\n\n"
        "<validation_errors>\n" + "\n".join(errors) + "\n</validation_errors>\n\n"
        "The previous extraction of this document failed validation. Call "
        f"{TOOL_NAME} again with a complete, corrected extraction. Fix what each error "
        "describes and keep the other fields unless they are wrong. For an [ungrounded] "
        "error, either copy the exact supporting text from the document into evidence, or set "
        "the field and its evidence to null if the document does not state it. Never make up a "
        "value to pass validation."
    )


def first_chunk_message(doc_id: str, text: str, part_count: int) -> str:
    return (
        f'<document id="{doc_id}" part="1 of {part_count}">\n{text}\n</document>\n\n'
        f"This is part 1 of {part_count} of a long document. Extract every field from this "
        f"part by calling {TOOL_NAME} once. The later parts contain the rest of the reference "
        "list and are processed separately, so list only the cited works that appear in this "
        "part."
    )


def citations_chunk_message(doc_id: str, text: str, part: int, part_count: int) -> str:
    return (
        f'<document id="{doc_id}" part="{part} of {part_count}">\n{text}\n</document>\n\n'
        f"This is part {part} of {part_count} of a long document and contains only part of its "
        f"reference list. Call {CITATIONS_TOOL_NAME} once with every cited work in this part, "
        "in order."
    )
