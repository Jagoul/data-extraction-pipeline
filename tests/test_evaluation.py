"""Accuracy scoring, null-handling metrics, calibration, and retry statistics."""

from dataclasses import asdict

import pytest

from extraction_pipeline.evaluation import (
    cited_works_f1,
    evaluate,
    field_correct,
    retry_statistics,
)
from tests.conftest import first_of, oracle_extraction


def _truth(doc):
    return asdict(doc.truth)


def test_a_perfect_extraction_scores_every_field(corpus):
    for doc in corpus:
        extraction, truth = oracle_extraction(doc), _truth(doc)
        wrong = [f for f in truth if f in extraction and f not in ("key_findings",)]
        for field in (
            "title",
            "authors",
            "publication_year",
            "doi",
            "study_type",
            "sample_size",
            "funding_source",
            "primary_outcome",
            "cited_works",
        ):
            assert field_correct(field, extraction, truth), (doc.truth.doc_id, field, wrong)


@pytest.mark.parametrize(
    ("field", "pred", "truth", "expected"),
    [
        ("title", "Evidence summary: Walking and pain", "Walking and pain", True),
        ("title", "WALKING AND PAIN", "Walking and pain", True),
        ("title", "Something else entirely", "Walking and pain", False),
        ("doi", "10.5555/ABC.1", "10.5555/abc.1", True),
        ("doi", "10.5555/abc.1", None, False),  # fabricated
        ("sample_size", None, 240, False),  # missed
        ("funding_source", "Coastal Research Council", "the Coastal Research Council", True),
        ("primary_outcome", None, None, True),
    ],
)
def test_field_comparators(field, pred, truth, expected):
    truth_row = {field: truth, "title": truth if field == "title" else "t"}

    assert field_correct(field, {field: pred}, truth_row) is expected


def test_anonymous_documents_expect_an_explicit_placeholder():
    truth = {"authors": []}

    assert field_correct("authors", {"authors": ["unknown"]}, truth)
    assert not field_correct("authors", {"authors": ["Jane Doe"]}, truth)


def test_other_study_type_also_needs_the_detail():
    truth = {"study_type": "other", "study_type_detail": "Delphi consensus study"}

    assert field_correct(
        "study_type", {"study_type": "other", "study_type_detail": "Delphi consensus study"}, truth
    )
    assert not field_correct(
        "study_type", {"study_type": "other", "study_type_detail": "survey"}, truth
    )


def test_cited_works_f1():
    truth = [{"first_author": "Silva", "year": 2019}, {"first_author": "Okafor", "year": None}]

    assert cited_works_f1(truth, truth) == 1.0
    assert cited_works_f1([], []) == 1.0
    assert cited_works_f1(truth[:1], truth) == pytest.approx(2 / 3)
    assert cited_works_f1([{"first_author": "Silva", "year": 2020}], truth) == 0.0


def test_evaluate_reports_fabrications_misses_and_inconsistent_cells(corpus):
    posters = [d for d in corpus if d.truth.doc_type == "conference_poster"][:4]
    tables = [d for d in corpus if d.truth.doc_type == "structured_table"][:4]
    results = []
    for doc in posters + tables:
        extraction = oracle_extraction(doc)
        if doc in posters:  # posters get every sample size wrong
            extraction["sample_size"] = 99999
        results.append(
            {"doc_id": doc.truth.doc_id, "doc_type": doc.truth.doc_type, "extraction": extraction}
        )
    truth = {d.truth.doc_id: _truth(d) for d in posters + tables}

    report = evaluate(results, truth)

    assert report["documents"] == 8
    assert report["doc_type_field_accuracy"]["structured_table"]["sample_size"] == 1.0
    assert report["doc_type_field_accuracy"]["conference_poster"]["sample_size"] == 0.0
    assert {"doc_type": "conference_poster", "field": "sample_size"}.items() <= report[
        "inconsistent_cells"
    ][0].items()
    assert report["field_accuracy"]["title"] == 1.0


def test_null_handling_counts_a_fabricated_value(corpus):
    doc = first_of(corpus, "narrative_abstract", doi=None)
    extraction = oracle_extraction(doc) | {"doi": "10.5555/made.up"}
    result = {"doc_id": doc.truth.doc_id, "doc_type": doc.truth.doc_type, "extraction": extraction}

    report = evaluate([result], {doc.truth.doc_id: _truth(doc)})

    assert report["null_handling"]["doi"]["fabricated"] == 1
    assert report["null_handling"]["doi"]["fabrication_rate"] == 1.0


def test_retry_statistics_separate_resolved_persisted_and_not_retried():
    results = [
        {
            "attempts": [
                {
                    "issues": [
                        {"field": "doi", "kind": "format"},
                        {"field": "sample_size", "kind": "format"},
                    ]
                },
                {"issues": [{"field": "sample_size", "kind": "format"}]},
                {"issues": []},
            ]
        },
        {"attempts": [{"issues": [{"field": "authors", "kind": "missing_required"}]}]},
    ]

    stats = retry_statistics(results)

    assert stats["format"] == {"seen": 3, "retried": 3, "resolved": 2, "persisted": 1}
    assert stats["missing_required"] == {"seen": 1, "not_retried": 1}
