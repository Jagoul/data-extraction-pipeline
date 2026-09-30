"""Deterministic synthetic corpus of research documents with exact ground truth.

Every document is generated from a ground-truth record and rendered in one of seven formats, so
accuracy can be measured field by field instead of eyeballed. The corpus deliberately contains
the cases that break naive extractors:

- nullable fields that are absent (DOI, funding, sample size, year, outcome)
- DOIs written as URLs, sample sizes written as "N = 1,204"
- inline author-year citations vs numbered reference lists, citation years that look like the
  publication year
- study designs outside the enum (the `other` + detail pattern)
- anonymous technical notes with no authors (information genuinely missing)
- oversized reports whose reference list is too long for one response (chunking)

All names, journals, funders and DOIs (prefix 10.5555, reserved for examples) are fictitious.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

DOC_TYPE_COUNTS: Final = {
    "narrative_abstract": 22,
    "structured_table": 22,
    "bibliography_article": 20,
    "conference_poster": 16,
    "press_release": 12,
    "long_report": 5,
    "technical_note": 3,
}
LONG_REPORT_REFERENCES: Final = 400

SURNAMES = (
    "Okafor Lindqvist Moreau Tanaka Silva Novak Haddad Kowalski Brennan Achterberg Mensah Ferreira "
    "Varga Oyelaran Castellanos Nakamura Duarte Whitfield Petrov Ibsen Rahman Albrecht Quinlan "
    "Sørensen Bianchi Adeyemi Holloway Marchetti Kaur Delacroix Ivanova Mbeki Takahashi Fischer "
    "Ncube Almeida Hartmann Obi Laurent Szabo Kimura Eriksen Gallagher Romero Chowdhury Novotny "
    "Vasquez Lindgren Osei Yamada"
).split()
GIVEN_NAMES = (
    "Maria Anna Tomasz Chidi Ingrid Kenji Leila Rafael Siobhan Ama Priya Lucas Noor Elena Kwame "
    "Hana Diego Freya Omar Yuki Sofia Mateo Aisha Jonas Clara Ravi Nadia Felix Zanele Marta"
).split()
JOURNALS = (
    "Journal of Applied Clinical Evidence",
    "Global Health Methods",
    "Northern Journal of Public Health",
    "Clinical Outcomes Quarterly",
    "Annals of Community Medicine",
    "Evidence and Practice",
)
CONGRESSES = (
    "European Congress of Clinical Research",
    "International Primary Care Summit",
    "World Forum on Population Health",
)
FUNDERS = (
    "the Northbridge Health Foundation",
    "the Coastal Research Council",
    "Helix Therapeutics Ltd",
    "the Meridian Charitable Trust",
    "the Alder Institute for Public Health",
    "the Riverside University Hospital research fund",
)
TOPICS = (
    ("type 2 diabetes", "text-message coaching", "change in HbA1c at 12 months"),
    ("hypertension", "pharmacist-led medication review", "systolic blood pressure at 6 months"),
    ("chronic low back pain", "group physiotherapy", "disability score at 3 months"),
    ("childhood asthma", "school-based inhaler education", "emergency visits within 1 year"),
    ("depression in older adults", "telephone befriending", "depressive symptom score"),
    ("heart failure", "remote weight monitoring", "30-day readmission"),
    ("smoking cessation", "financial incentives", "biochemically verified abstinence"),
    ("post-operative delirium", "early mobilisation", "incidence of delirium"),
    ("adolescent obesity", "family-based lifestyle coaching", "change in BMI z-score"),
    ("falls in care homes", "vitamin D supplementation", "number of falls per resident"),
    ("antibiotic prescribing", "clinician audit and feedback", "antibiotic prescription rate"),
    ("sleep problems in shift workers", "light-exposure scheduling", "total sleep time"),
)
SETTINGS = ("primary care", "three urban hospitals", "rural clinics", "community settings")

DESIGNS: Final = {
    "randomized_controlled_trial": ("randomised controlled trial", "RCT"),
    "cohort_study": ("prospective cohort study", "Cohort"),
    "case_control_study": ("case-control study", "Case-control"),
    "cross_sectional_study": ("cross-sectional survey", "Cross-sectional"),
    "systematic_review": ("systematic review", "Systematic review"),
    "meta_analysis": ("meta-analysis of randomised trials", "Meta-analysis"),
    "qualitative_study": ("qualitative interview study", "Qualitative"),
}
OTHER_DESIGNS = (
    "Delphi consensus study",
    "discrete choice experiment",
    "agent-based simulation study",
    "health technology assessment",
)
MONTHS = ("January", "March", "May", "June", "September", "November")


@dataclass
class GroundTruth:
    doc_id: str
    doc_type: str
    title: str
    authors: list[str]
    publication_year: int | None
    doi: str | None
    study_type: str
    study_type_detail: str | None
    sample_size: int | None
    funding_source: str | None
    primary_outcome: str | None
    cited_works: list[dict[str, Any]]
    oversized: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class GeneratedDocument:
    truth: GroundTruth
    text: str


# --- Ground truth ------------------------------------------------------------------------------


def _person(rng: random.Random) -> tuple[str, str]:
    return rng.choice(GIVEN_NAMES), rng.choice(SURNAMES)


def _citations(rng: random.Random, count: int, before: int) -> list[dict[str, Any]]:
    works: list[dict[str, Any]] = []
    for _ in range(count):
        if rng.random() < 0.05:
            works.append({"first_author": "World Health Organization", "year": None})
        else:
            works.append({"first_author": rng.choice(SURNAMES), "year": rng.randint(1998, before)})
    return works


def _doi(rng: random.Random, year: int) -> str:
    journal = rng.choice(("jace", "ghm", "njph", "coq"))
    return f"10.5555/{journal}.{year}.{rng.randint(100, 9999):04d}"


def _truth(rng: random.Random, doc_id: str, doc_type: str) -> GroundTruth:
    condition, intervention, outcome = rng.choice(TOPICS)
    design_key = rng.choices([*DESIGNS, "other"], weights=[26, 14, 8, 10, 8, 8, 10, 16], k=1)[0]
    detail = rng.choice(OTHER_DESIGNS) if design_key == "other" else None
    design_phrase = detail or DESIGNS[design_key][0]
    year = rng.randint(2012, 2025)

    anonymous = doc_type == "technical_note"
    authors = [] if anonymous else [" ".join(_person(rng)) for _ in range(rng.randint(1, 6))]
    if doc_type == "press_release":
        authors = authors[:2]  # a press release names the lead and at most one colleague
    setting = rng.choice(SETTINGS)
    if rng.random() < 0.5:
        title = f"{intervention.capitalize()} for {condition}: a {design_phrase} in {setting}"
    else:  # neutral title: the design must be read from the methods
        title = f"{intervention.capitalize()} and {condition}: findings from {setting}"

    if design_key == "systematic_review":
        sample_size = None  # counts studies, not participants
    elif rng.random() < 0.15 and doc_type != "structured_table":
        sample_size = None
    else:
        sample_size = rng.choice((24, 60, 118, 240, 312, 486, 1204, 2350, 4512))

    notes = []
    has_doi = doc_type not in ("press_release", "technical_note") and rng.random() < 0.7
    has_year = rng.random() >= 0.06
    has_funding = rng.random() >= 0.35
    has_outcome = design_key not in ("qualitative_study",) and rng.random() >= 0.08
    if not has_year:
        notes.append("publication year absent; citation years are distractors")

    cite_count = {
        "narrative_abstract": rng.randint(3, 6),
        "structured_table": rng.randint(4, 10),
        "bibliography_article": rng.randint(6, 14),
        "conference_poster": rng.randint(2, 4),
        "press_release": 0,
        "long_report": LONG_REPORT_REFERENCES,
        "technical_note": rng.randint(0, 3),
    }[doc_type]

    return GroundTruth(
        doc_id=doc_id,
        doc_type=doc_type,
        title=title,
        authors=authors,
        publication_year=year if has_year else None,
        doi=_doi(rng, year) if has_doi else None,
        study_type=design_key,
        study_type_detail=detail,
        sample_size=sample_size,
        funding_source=rng.choice(FUNDERS) if has_funding else None,
        primary_outcome=outcome if has_outcome else None,
        cited_works=_citations(rng, cite_count, year - 1),
        oversized=doc_type == "long_report",
        notes=notes,
    )


# --- Rendering ---------------------------------------------------------------------------------


def _n(value: int) -> str:
    return f"{value:,}"


def _inline_cite(work: dict[str, Any], rng: random.Random) -> str:
    year = work["year"] if work["year"] is not None else "n.d."
    style = rng.random()
    if style < 0.5:
        return f"{work['first_author']} et al., {year}"
    if style < 0.8:
        return f"{work['first_author']} and {rng.choice(SURNAMES)}, {year}"
    return f"{work['first_author']}, {year}"


def _reference_entry(n: int, work: dict[str, Any], rng: random.Random) -> str:
    initials = "".join(rng.sample("ABCDEFGHJKLMNPRST", 2))
    year = work["year"] if work["year"] is not None else "n.d."
    co = f", {rng.choice(SURNAMES)} {rng.choice('ABCDEFGH')}" if rng.random() < 0.6 else ""
    return (
        f"{n}. {work['first_author']} {initials}{co}. "
        f"{rng.choice(TOPICS)[1].capitalize()} and outcomes in {rng.choice(TOPICS)[0]}. "
        f"{rng.choice(JOURNALS)}. {year};{rng.randint(3, 48)}:{rng.randint(10, 400)}-"
        f"{rng.randint(401, 900)}."
    )


def _design_sentence(t: GroundTruth) -> str:
    if t.study_type == "other":
        return f"We conducted a {t.study_type_detail}"
    return f"We conducted a {DESIGNS[t.study_type][0]}"


def _sample_sentence(t: GroundTruth, rng: random.Random) -> str:
    if t.study_type == "systematic_review":
        return f"We identified {rng.randint(8, 31)} eligible studies."
    if t.sample_size is None:
        return "Participants were recruited through routine appointments."
    return rng.choice(
        (
            f"A total of {_n(t.sample_size)} participants were included in the analysis.",
            f"The analysis included {_n(t.sample_size)} participants.",
        )
    )


def render_narrative(t: GroundTruth, rng: random.Random) -> str:
    cites = [_inline_cite(w, rng) for w in t.cited_works]
    half = max(1, len(cites) // 2)
    lines = [f"# {t.title}", "", ", ".join(t.authors), ""]
    if t.publication_year:
        lines.append(f"Published in {rng.choice(JOURNALS)}, {t.publication_year}.")
    if t.doi:
        lines.append(f"doi:{t.doi}")
    lines += [
        "",
        f"**Background.** Previous work has examined this question ({'; '.join(cites[:half])}), "
        "but evidence remains inconsistent.",
        "",
        f"**Methods.** {_design_sentence(t)}. {_sample_sentence(t, rng)}"
        + (f" The primary outcome was {t.primary_outcome}." if t.primary_outcome else ""),
        "",
        "**Results.** The intervention group showed a clinically meaningful improvement "
        f"compared with usual care, consistent with earlier reports ({'; '.join(cites[half:])}).",
        "",
        "**Conclusions.** The approach is feasible and merits wider evaluation.",
    ]
    if t.funding_source:
        lines += ["", f"Funding: This work was supported by {t.funding_source}."]
    return "\n".join(lines)


def render_table(t: GroundTruth, rng: random.Random) -> str:
    design = t.study_type_detail or DESIGNS[t.study_type][1]
    rows = [
        ("Design", design),
        ("Participants", _n(t.sample_size) if t.sample_size else "Not reported"),
        ("Primary outcome", t.primary_outcome or "Not reported"),
        ("Funding", t.funding_source or "Not reported"),
        ("Year", str(t.publication_year) if t.publication_year else "Not reported"),
        ("DOI", t.doi or "Not reported"),
    ]
    rng.shuffle(rows)
    lines = [
        f"## Evidence summary: {t.title}",
        "",
        f"Authors: {'; '.join(t.authors)}",
        "",
        "| Characteristic | Value |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in rows],
        "",
        "Summary: the intervention was associated with improved outcomes; see sources below.",
        "",
        "### Sources",
        *[_reference_entry(i, w, rng) for i, w in enumerate(t.cited_works, start=1)],
    ]
    return "\n".join(lines)


def render_bibliography(t: GroundTruth, rng: random.Random, long: bool = False) -> str:
    header = [f"# {t.title}", "", f"{', '.join(t.authors)}"]
    meta = []
    if t.publication_year:
        meta.append(f"{rng.choice(JOURNALS)} · {t.publication_year}")
    if t.doi:
        meta.append(f"https://doi.org/{t.doi}")
    n_refs = len(t.cited_works)
    body = [
        "",
        " · ".join(meta),
        "",
        "## Introduction",
        "Interest in this area has grown [1, 2], with mixed findings across settings "
        f"[{min(3, n_refs)}].",
        "",
        "## Methods",
        f"{_design_sentence(t)} following established guidance [{min(4, n_refs)}]. "
        f"{_sample_sentence(t, rng)}"
        + (f" Our primary outcome was {t.primary_outcome}." if t.primary_outcome else ""),
    ]
    if long:
        body += [
            "",
            "## Extended methods",
            *[
                f"Section {i}: sensitivity analysis {i} re-estimated the main effect under "
                f"alternative assumptions, as recommended previously [{rng.randint(1, n_refs)}]."
                for i in range(1, 41)
            ],
        ]
    body += [
        "",
        "## Results",
        "Effects favoured the intervention and were robust to sensitivity analyses "
        f"[{min(5, n_refs)}-{n_refs}].",
        "",
        "## Acknowledgements",
        f"This study was funded by {t.funding_source}."
        if t.funding_source
        else "We thank all participants and staff.",
        "",
        "## References",
        *[_reference_entry(i, w, rng) for i, w in enumerate(t.cited_works, start=1)],
    ]
    return "\n".join(header + body)


def render_poster(t: GroundTruth, rng: random.Random) -> str:
    design = t.study_type_detail or DESIGNS[t.study_type][1]
    refs = "; ".join(
        f"{w['first_author']} {w['year'] if w['year'] is not None else 'n.d.'}"
        for w in t.cited_works
    )
    lines = [
        f"POSTER P-{rng.randint(100, 399)}",
        t.title.upper(),
        f"Authors: {' / '.join(t.authors)}",
    ]
    if t.publication_year:
        lines.append(f"Presented at the {rng.choice(CONGRESSES)} {t.publication_year}")
    lines.append(f"Design: {design}")
    if t.sample_size:
        lines.append(f"N = {_n(t.sample_size)}")
    if t.primary_outcome:
        lines.append(f"Primary endpoint: {t.primary_outcome}")
    lines.append("Result: favourable effect, full paper in preparation")
    if t.funding_source:
        lines.append(f"Funded by {t.funding_source}")
    if t.doi:
        lines.append(f"Abstract: https://doi.org/{t.doi}")
    lines.append(f"Key refs: {refs}")
    return "\n".join(lines)


def render_press(t: GroundTruth, rng: random.Random) -> str:
    lead, *others = t.authors
    team = f"Dr {lead}" + (f" and colleagues including Dr {others[0]}" if others else "")
    when = f"{rng.choice(MONTHS)} {t.publication_year}" if t.publication_year else "This week"
    design = t.study_type_detail or DESIGNS[t.study_type][0]
    size = (
        f"The team followed {_n(t.sample_size)} participants."
        if t.sample_size
        else "The team worked with patients and clinicians across the region."
    )
    lines = [
        f"PRESS RELEASE — {when}",
        "",
        f"New research: {t.title}",
        "",
        f"A {design} led by {team} suggests that the programme could benefit patients.",
        size,
    ]
    if t.primary_outcome:
        lines.append(f"The researchers mainly measured {t.primary_outcome}.")
    if t.funding_source:
        lines.append(f"The study was funded by {t.funding_source}.")
    lines.append("The full paper is available from the journal.")
    return "\n".join(lines)


def render_technical_note(t: GroundTruth, rng: random.Random) -> str:
    lines = [
        "TECHNICAL NOTE — for internal circulation",
        "",
        f"Subject: {t.title}",
        "",
        f"{_design_sentence(t)}. {_sample_sentence(t, rng)}",
    ]
    if t.primary_outcome:
        lines.append(f"The main outcome of interest was {t.primary_outcome}.")
    if t.publication_year:
        lines.append(f"Circulated {rng.choice(MONTHS)} {t.publication_year}.")
    if t.funding_source:
        lines.append(f"Supported by {t.funding_source}.")
    if t.cited_works:
        lines.append(
            "Background reading: " + "; ".join(f"({_inline_cite(w, rng)})" for w in t.cited_works)
        )
    lines.append("No author attribution is given for internal notes.")
    return "\n".join(lines)


RENDERERS = {
    "narrative_abstract": render_narrative,
    "structured_table": render_table,
    "bibliography_article": render_bibliography,
    "conference_poster": render_poster,
    "press_release": render_press,
    "long_report": lambda t, rng: render_bibliography(t, rng, long=True),
    "technical_note": render_technical_note,
}


def generate(seed: int = 7) -> list[GeneratedDocument]:
    """Generate the corpus. Same seed, same corpus, byte for byte."""
    rng = random.Random(seed)
    plan = [doc_type for doc_type, count in DOC_TYPE_COUNTS.items() for _ in range(count)]
    rng.shuffle(plan)
    documents = []
    for index, doc_type in enumerate(plan, start=1):
        truth = _truth(rng, f"doc-{index:03d}", doc_type)
        documents.append(GeneratedDocument(truth=truth, text=RENDERERS[doc_type](truth, rng)))
    return documents


def write_corpus(directory: Path, seed: int = 7) -> list[GeneratedDocument]:
    """Write `<id>.md` files plus `ground_truth.jsonl` into `directory`."""
    docs_dir = directory / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    documents = generate(seed)
    for doc in documents:
        (docs_dir / f"{doc.truth.doc_id}.md").write_text(doc.text + "\n", encoding="utf-8")
    with (directory / "ground_truth.jsonl").open("w", encoding="utf-8") as out:
        for doc in documents:
            out.write(json.dumps(asdict(doc.truth), ensure_ascii=False) + "\n")
    return documents
