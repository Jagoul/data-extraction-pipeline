"""Validation layers and issue classification: which problems a retry can fix."""

import copy
from typing import Any

import pytest

from extraction_pipeline.prompts import FEW_SHOT_EXAMPLES
from extraction_pipeline.validation import IssueKind, normalise, validate_extraction

EXAMPLE = next(e for e in FEW_SHOT_EXAMPLES if e.name == "table_numbered_bibliography")


def _with(**changes: Any) -> dict[str, Any]:
    data = copy.deepcopy(EXAMPLE.extraction)
    for key, value in changes.items():
        if "." in key:
            outer, inner = key.split(".")
            data[outer][inner] = value
        else:
            data[key] = value
    return data


def _kinds(data: dict[str, Any], document: str = EXAMPLE.document) -> dict[str, set[IssueKind]]:
    outcome = validate_extraction(data, document)
    kinds: dict[str, set[IssueKind]] = {}
    for issue in outcome.issues:
        kinds.setdefault(issue.field.split(".")[0], set()).add(issue.kind)
    return kinds


def test_a_correct_extraction_is_valid():
    outcome = validate_extraction(EXAMPLE.extraction, EXAMPLE.document)

    assert outcome.valid
    assert outcome.record is not None
    assert outcome.record.sample_size == 1204


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"doi": "https://doi.org/10.5555/example.2021.0042"}, "doi"),
        ({"publication_year": 21}, "publication_year"),
        ({"sample_size": 0}, "sample_size"),
        ({"sample_size": "1,204"}, "sample_size"),
        ({"study_type": "rct"}, "study_type"),
        ({"field_confidence.doi": 1.3}, "field_confidence"),
        ({"key_findings": []}, "key_findings"),
    ],
)
def test_format_mismatches_are_retryable(changes, field):
    outcome = validate_extraction(_with(**changes), EXAMPLE.document)

    assert IssueKind.FORMAT in _kinds(_with(**changes))[field]
    assert outcome.retryable
    assert outcome.record is None


def test_other_without_detail_is_a_format_issue():
    kinds = _kinds(_with(study_type="other", study_type_detail=None))

    assert any(IssueKind.FORMAT in k for k in kinds.values())


def test_missing_key_is_reported_by_name():
    data = _with()
    del data["funding_source"]

    outcome = validate_extraction(data, EXAMPLE.document)

    assert any(i.field == "funding_source" and i.kind is IssueKind.FORMAT for i in outcome.issues)


def test_a_value_with_no_evidence_is_ungrounded():
    kinds = _kinds(_with(**{"evidence.funding_source": None}))

    assert kinds["funding_source"] == {IssueKind.UNGROUNDED}


def test_a_quote_not_in_the_document_is_ungrounded():
    data = _with(primary_outcome="mortality", **{"evidence.primary_outcome": "Mortality at 1 year"})

    assert _kinds(data)["primary_outcome"] == {IssueKind.UNGROUNDED}


def test_a_value_its_own_quote_does_not_support_is_ungrounded():
    data = _with(sample_size=1500)  # quote still says 1,204

    assert _kinds(data)["sample_size"] == {IssueKind.UNGROUNDED}


@pytest.mark.parametrize(
    "changes", [{"authors": ["unknown"]}, {"authors": []}, {"title": "Unknown"}]
)
def test_absent_required_information_is_not_retryable(changes):
    outcome = validate_extraction(_with(**changes), EXAMPLE.document)
    missing = [i for i in outcome.issues if i.kind is IssueKind.MISSING_REQUIRED]

    assert missing
    assert not IssueKind.MISSING_REQUIRED.retryable
    assert not validate_extraction(_with(authors=["unknown"]), EXAMPLE.document).retryable


def test_mixed_issues_are_retryable_while_any_is():
    outcome = validate_extraction(_with(authors=["unknown"], doi="doi:10.5555/x"), EXAMPLE.document)

    assert outcome.kinds() >= {IssueKind.MISSING_REQUIRED, IssueKind.FORMAT}
    assert outcome.retryable


def test_the_same_field_is_not_reported_twice_by_both_layers():
    outcome = validate_extraction(_with(doi="https://doi.org/10.5555/x"), EXAMPLE.document)

    assert len([i for i in outcome.issues if i.field == "doi"]) == 1


def test_normalise_makes_quotes_comparable_with_the_source():
    typographic = f"{chr(0x201C)}Pain{chr(0x201D)} fell {chr(0x2013)} a lot"

    assert normalise("| Participants | 1,204 |") == "participants 1,204"
    assert normalise(typographic) == "'pain' fell - a lot"
    assert normalise("  **Methods.**\n  We ") == "methods. we"


# --- Rules added after the Haiku 4.5 stress run --------------------------------------------------


def _year_case(year, quote):
    data = _with(publication_year=year, **{"evidence.publication_year": quote})
    document = EXAMPLE.document + f"\n{quote}\n"
    return validate_extraction(data, document)


@pytest.mark.parametrize(
    "quote",
    [
        "Abstract: https://doi.org/10.5555/coq.2014.6797",
        "doi:10.5555/jace.2014.0042",
        "10.5555/ghm.2014.7",
    ],
)
def test_a_year_buried_in_a_doi_is_not_a_publication_date(quote):
    outcome = _year_case(2014, quote)

    assert [i.kind for i in outcome.issues if i.field == "publication_year"] == [
        IssueKind.UNGROUNDED
    ]


@pytest.mark.parametrize(
    "quote",
    [
        "Published in Journal of Musculoskeletal Care, 2014.",
        "PRESS RELEASE - May 2014",
        "| Year | 2014 |",
        "Presented at the World Forum on Population Health 2014",
        "Abstract 2014 (doi:10.5555/x.1999.3)",
    ],
)
def test_a_year_stated_as_a_date_is_grounded(quote):
    outcome = _year_case(2014, quote)

    assert not [i for i in outcome.issues if i.field == "publication_year"]


def _numbered_document(count):
    return "## References\n" + "\n".join(
        f"{n}. Author {n}. A study. 2010." for n in range(1, count + 1)
    )


def test_far_fewer_cited_works_than_numbered_references_is_incomplete():
    data = _with(cited_works=[{"first_author": "A", "year": 2010}] * 40)

    outcome = validate_extraction(data, EXAMPLE.document + _numbered_document(100))
    issue = next(i for i in outcome.issues if i.kind is IssueKind.INCOMPLETE)

    assert issue.field == "cited_works"
    assert "40 cited works" in issue.message
    assert "about 102 numbered" in issue.message  # 100 generated + the fixture's own 2
    assert not IssueKind.INCOMPLETE.retryable  # chunking fixes it; asking again would not


def test_a_complete_or_short_reference_list_is_not_flagged():
    complete = _with(cited_works=[{"first_author": "A", "year": 2010}] * 100)
    short = _with(cited_works=[])

    assert not [
        i
        for i in validate_extraction(complete, _numbered_document(100)).issues
        if i.kind is IssueKind.INCOMPLETE
    ]
    assert (
        not [  # under 10 numbered entries: the check does not apply
            i
            for i in validate_extraction(short, _numbered_document(6)).issues
            if i.kind is IssueKind.INCOMPLETE
        ]
    )
