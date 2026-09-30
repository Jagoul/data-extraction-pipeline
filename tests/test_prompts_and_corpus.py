"""Few-shot examples, request messages, and the synthetic corpus with its ground truth."""

import json
from collections import Counter

import pytest

from extraction_pipeline.corpus import DOC_TYPE_COUNTS, LONG_REPORT_REFERENCES, generate
from extraction_pipeline.documents import approx_tokens, load_corpus, split_into_chunks
from extraction_pipeline.prompts import (
    FEW_SHOT_EXAMPLES,
    correction_message,
    extraction_message,
    system_prompt,
)
from extraction_pipeline.validation import IssueKind, validate_extraction
from tests.conftest import PROJECT_ROOT

# --- Few-shot examples -------------------------------------------------------------------------


@pytest.mark.parametrize("example", FEW_SHOT_EXAMPLES, ids=lambda e: e.name)
def test_every_example_passes_the_pipelines_own_validation(example):
    outcome = validate_extraction(example.extraction, example.document)

    if example.name == "anonymous_note":  # teaches the explicit placeholder, routed to a human
        assert outcome.kinds() == {IssueKind.MISSING_REQUIRED}
    else:
        assert outcome.valid, outcome.issues


def test_examples_cover_the_structural_variety_the_corpus_contains():
    taught = " ".join(example.teaches for example in FEW_SHOT_EXAMPLES)

    for pattern in ("inline", "numbered reference list", "table", "narrative", "other", "URL DOI"):
        assert pattern in taught


def test_examples_are_not_copies_of_corpus_documents(corpus):
    titles = {doc.truth.title.lower() for doc in corpus}

    for example in FEW_SHOT_EXAMPLES:
        assert example.extraction["title"].lower() not in titles


def test_system_prompt_with_and_without_examples():
    with_examples, without = system_prompt(few_shot=True), system_prompt(few_shot=False)

    assert "<examples>" in with_examples
    assert "<examples>" not in without
    assert without in with_examples  # same instructions, examples appended after them


def test_system_prompt_is_stable_for_caching():
    assert system_prompt(few_shot=True) == system_prompt(few_shot=True)


def test_correction_message_carries_document_failed_extraction_and_errors():
    message = correction_message(
        "doc-9", "Some text", {"doi": "https://doi.org/10.1/x"}, ["- doi [format]: bad pattern"]
    )

    assert "Some text" in message
    assert "https://doi.org/10.1/x" in message
    assert "- doi [format]: bad pattern" in message
    assert "Never make up a value" in message


def test_extraction_message_wraps_the_document():
    assert '<document id="doc-1">\nBody\n</document>' in extraction_message("doc-1", "Body")


# --- Corpus ------------------------------------------------------------------------------------


def test_corpus_is_deterministic():
    assert [d.text for d in generate(7)] == [d.text for d in generate(7)]
    assert [d.text for d in generate(7)] != [d.text for d in generate(8)]


def test_corpus_has_one_hundred_documents_in_the_planned_mix(corpus):
    assert len(corpus) == 100
    assert Counter(d.truth.doc_type for d in corpus) == Counter(DOC_TYPE_COUNTS)


def test_committed_data_matches_the_generator(corpus):
    committed = {d.doc_id: d.text for d in load_corpus(PROJECT_ROOT / "data")}
    truth_lines = (PROJECT_ROOT / "data" / "ground_truth.jsonl").read_text().splitlines()

    assert {d.truth.doc_id: d.text + "\n" for d in corpus} == committed
    assert [json.loads(line)["doc_id"] for line in truth_lines] == [d.truth.doc_id for d in corpus]


def test_every_non_null_ground_truth_value_is_stated_in_the_document(corpus):
    for doc in corpus:
        t, text = doc.truth, doc.text
        if t.doi:
            assert t.doi in text
        if t.sample_size:
            assert f"{t.sample_size:,}" in text
        for value in (t.funding_source, t.primary_outcome, t.study_type_detail):
            if value:
                assert value in text
        for author in t.authors:
            assert author in text


def test_absent_values_really_are_absent(corpus):
    for doc in corpus:
        if doc.truth.doi is None:
            assert "10.5555" not in doc.text
        if doc.truth.funding_source is None:
            assert "funded" not in doc.text.lower() or "Not reported" in doc.text


def test_edge_cases_are_present(corpus):
    types = Counter(d.truth.study_type for d in corpus)
    assert types["other"] >= 10
    assert sum(d.truth.doi is None for d in corpus) >= 30
    assert sum(d.truth.publication_year is None for d in corpus) >= 3
    assert sum(not d.truth.authors for d in corpus) == DOC_TYPE_COUNTS["technical_note"]
    assert any("https://doi.org/" in d.text for d in corpus)  # DOI written as a URL


def test_oversized_documents_split_into_several_chunks(corpus):
    long_doc = next(d for d in corpus if d.truth.oversized)
    chunks = split_into_chunks(long_doc.text, 6000)

    assert len(long_doc.truth.cited_works) == LONG_REPORT_REFERENCES
    assert len(chunks) >= 2
    assert all(approx_tokens(chunk) <= 6000 for chunk in chunks)
    assert "\n".join(chunks) == long_doc.text  # nothing lost, nothing reordered


def test_chunking_never_splits_a_line():
    text = "\n".join(f"{i}. Reference entry number {i}" for i in range(200))

    chunks = split_into_chunks(text, 50)

    assert all(line in text.splitlines() for chunk in chunks for line in chunk.splitlines())
