"""The per-document state machine: every transition in the jobs.py diagram."""

import copy
import re

import pytest

from extraction_pipeline.documents import Document
from extraction_pipeline.jobs import ExtractionJob, JobStatus
from extraction_pipeline.llm import CallStatus
from extraction_pipeline.prompts import CITATIONS_TOOL_NAME, FEW_SHOT_EXAMPLES
from tests.conftest import as_document, oracle_extraction, outcome, tool_call

EXAMPLE = next(e for e in FEW_SHOT_EXAMPLES if e.name == "table_numbered_bibliography")
GOOD = EXAMPLE.extraction


@pytest.fixture
def job(settings):
    return ExtractionJob(Document("doc-001", "structured_table", EXAMPLE.document), settings)


def user_text(params):
    return params["messages"][0]["content"]


def test_a_new_job_asks_for_one_extraction(job):
    assert list(job.pending) == ["a1"]
    assert job.pending["a1"]["tools"][0]["strict"] is True
    assert job.pending["a1"]["tool_choice"] == {"type": "auto"}


def test_valid_first_answer_is_accepted(job):
    job.observe("a1", tool_call(GOOD))

    assert job.status is JobStatus.ACCEPTED
    assert not job.pending
    assert [a.request for a in job.attempts] == ["extract"]


def test_format_error_triggers_a_correction_request_with_the_error(job):
    bad = GOOD | {"doi": "https://doi.org/10.5555/example.2021.0042"}

    job.observe("a1", tool_call(bad))

    assert job.status is JobStatus.ACTIVE
    correction = user_text(job.pending["a2"])
    assert "<previous_extraction>" in correction
    assert "https://doi.org/10.5555/example.2021.0042" in correction  # the failed extraction
    assert "- doi [format]" in correction  # the specific error
    assert EXAMPLE.document in correction  # the document itself

    job.observe("a2", tool_call(GOOD))
    assert job.status is JobStatus.ACCEPTED
    assert [a.request for a in job.attempts] == ["extract", "correct"]


def test_information_absent_from_source_is_not_retried(job):
    job.observe("a1", tool_call(GOOD | {"authors": ["unknown"]}))

    assert job.status is JobStatus.NEEDS_REVIEW
    assert not job.pending
    assert job.issues[0].kind == "missing_required"


def test_retry_unresolvable_flag_retries_anyway(settings):
    job = ExtractionJob(
        Document("d", "t", EXAMPLE.document), settings.with_(retry_unresolvable=True)
    )

    job.observe("a1", tool_call(GOOD | {"authors": ["unknown"]}))

    assert "a2" in job.pending


def test_attempt_budget_ends_in_review(job):
    bad = GOOD | {"sample_size": 0}
    for key in ("a1", "a2", "a3"):
        job.observe(key, tool_call(bad))

    assert job.status is JobStatus.NEEDS_REVIEW
    assert len(job.attempts) == job.settings.max_attempts


def test_no_tool_call_is_asked_again_then_fails(job):
    job.observe("a1", outcome(CallStatus.NO_TOOL_CALL))
    assert "call the tool" in user_text(job.pending["a2"])

    job.observe("a2", outcome(CallStatus.NO_TOOL_CALL))
    job.observe("a3", outcome(CallStatus.NO_TOOL_CALL))
    assert job.status is JobStatus.FAILED


def test_api_error_resubmits_the_same_request_then_gives_up(job):
    original = job.pending["a1"]

    job.observe("a1", outcome(CallStatus.API_ERROR, "expired"))
    assert job.pending["a1"] is original

    job.observe("a1", outcome(CallStatus.API_ERROR, "overloaded"))
    job.observe("a1", outcome(CallStatus.API_ERROR, "overloaded"))
    assert job.status is JobStatus.FAILED
    assert "API errors" in job.failure


@pytest.mark.parametrize(
    ("status", "detail"),
    [(CallStatus.REFUSAL, "refusal (cyber)"), (CallStatus.INVALID_REQUEST, "bad field")],
)
def test_refusal_and_invalid_request_fail_the_job(job, status, detail):
    job.observe("a1", outcome(status, detail))

    assert job.status is JobStatus.FAILED
    assert detail in job.failure


def test_truncated_short_document_cannot_be_chunked(job):
    job.observe("a1", outcome(CallStatus.TRUNCATED))

    assert job.status is JobStatus.FAILED
    assert "cannot be split" in job.failure


# --- Oversized documents -----------------------------------------------------------------------


def _works_in(chunk, truth_works):
    numbers = [int(m) for m in re.findall(r"^(\d+)\. ", chunk, flags=re.MULTILINE)]
    return [truth_works[n - 1] for n in numbers]


@pytest.fixture
def long_job(settings, corpus):
    generated = next(d for d in corpus if d.truth.oversized)
    return ExtractionJob(as_document(generated), settings), generated


def test_truncation_resubmits_the_document_as_chunks(long_job):
    job, _ = long_job

    job.observe("a1", outcome(CallStatus.TRUNCATED))

    assert job.mode == "chunked"
    assert len(job.pending) == len(job.chunks) >= 2
    assert job.pending["a1c1"]["tools"][0]["name"] == "record_study"
    assert job.pending["a1c2"]["tools"][0]["name"] == CITATIONS_TOOL_NAME
    assert "part 2 of" in user_text(job.pending["a1c2"])


def test_chunk_results_merge_in_order_into_one_valid_record(long_job):
    job, generated = long_job
    job.observe("a1", outcome(CallStatus.TRUNCATED))
    truth_works = generated.truth.cited_works
    full = oracle_extraction(generated)

    keys = sorted(job.pending, key=lambda k: int(k.rsplit("c", 1)[1]), reverse=True)
    for key in keys:  # deliver in reverse order: merging must not depend on arrival order
        index = int(key.rsplit("c", 1)[1])
        works = _works_in(job.chunks[index - 1], truth_works)
        answer = (
            copy.deepcopy(full) | {"cited_works": works} if index == 1 else {"cited_works": works}
        )
        job.observe(key, tool_call(answer))

    assert job.status is JobStatus.ACCEPTED
    assert job.extraction["cited_works"] == truth_works


def test_a_truncated_chunk_fails_the_job(long_job):
    job, _ = long_job
    job.observe("a1", outcome(CallStatus.TRUNCATED))

    job.observe("a1c2", outcome(CallStatus.TRUNCATED))

    assert job.status is JobStatus.FAILED


def test_context_overflow_error_also_switches_to_chunks(long_job):
    job, _ = long_job

    job.observe("a1", outcome(CallStatus.INVALID_REQUEST, "prompt is too long"))

    assert job.mode == "chunked"


def test_result_is_serialisable_and_complete(job):
    import json

    job.observe("a1", tool_call(GOOD))
    result = json.loads(json.dumps(job.result()))

    assert result["status"] == "accepted"
    assert result["usage"]["input_tokens"] == 10
    assert result["events"][-1].startswith("accepted")


def test_an_incomplete_first_answer_is_resubmitted_as_chunks(long_job):
    job, generated = long_job
    short = oracle_extraction(generated) | {"cited_works": generated.truth.cited_works[:120]}

    job.observe("a1", tool_call(short))

    assert job.mode == "chunked"  # no truncation signal, but the list is visibly short
    assert job.status is JobStatus.ACTIVE
    assert [a.request for a in job.attempts] == ["extract"]
    assert "a2c1" in job.pending
    assert "incomplete" in job.events[-1] or "cited works" in job.events[-1]


def test_still_incomplete_after_chunking_goes_to_a_human(long_job):
    job, generated = long_job
    job.observe("a1", tool_call(oracle_extraction(generated) | {"cited_works": []}))
    for key in sorted(job.pending):
        index = int(key.rsplit("c", 1)[1])
        first = oracle_extraction(generated) | {"cited_works": []}
        job.observe(key, tool_call(first if index == 1 else {"cited_works": []}))

    assert job.status is JobStatus.NEEDS_REVIEW
    assert job.issues[0].kind == "incomplete"
