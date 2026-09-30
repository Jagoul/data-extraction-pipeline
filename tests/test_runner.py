"""The batch runner, the SLA planner, and human-review routing."""

import itertools

import pytest

from extraction_pipeline.jobs import ExtractionJob, JobStatus
from extraction_pipeline.llm import CallStatus
from extraction_pipeline.review import route
from extraction_pipeline.runner import MAX_ROUNDS, PipelineRunner, split_custom_id
from extraction_pipeline.sla import (
    DEFAULT_BATCH_ESTIMATE_SECONDS,
    RoundRecord,
    SlaPolicy,
    sla_report,
)
from tests.conftest import (
    FakeBatchClient,
    FakeSyncClient,
    as_document,
    first_of,
    oracle_extraction,
    outcome,
    tool_call,
)


@pytest.fixture
def docs(corpus):
    return [as_document(d) for d in corpus if not d.truth.oversized][:6]


def oracle_responder(corpus):
    by_id = {d.truth.doc_id: d for d in corpus}

    def respond(cid, _params):
        job_id, _key = split_custom_id(cid)
        return tool_call(oracle_extraction(by_id[job_id]))

    return respond


def test_a_clean_batch_finishes_in_one_round(settings, corpus, docs):
    batch = FakeBatchClient(oracle_responder(corpus))
    runner = PipelineRunner(settings, batch_client=batch, sleep=lambda _: None)

    result = runner.run([ExtractionJob(d, settings) for d in docs])

    assert len(batch.batches) == 1
    assert set(batch.batches[0]) == {f"{d.doc_id}__a1" for d in docs}
    assert {j.status for j in result.jobs} <= {JobStatus.ACCEPTED, JobStatus.NEEDS_REVIEW}
    assert result.rounds[0].mode == "batch"
    assert result.rounds[0].seconds == 600


def test_failures_are_resubmitted_by_custom_id_with_their_modification(settings, corpus, docs):
    """Round 1: one validation failure, one expiry, one refusal. Round 2 fixes what it can."""
    oracle = oracle_responder(corpus)
    broken, expired, refused = docs[0].doc_id, docs[1].doc_id, docs[2].doc_id

    def respond(cid, params):
        job_id, key = split_custom_id(cid)
        if key == "a1" and job_id == broken:
            return tool_call(oracle(cid, params).tool_input | {"sample_size": -5})
        if key == "a1" and job_id == expired:
            return outcome(CallStatus.API_ERROR, "expired")
        if job_id == refused:
            return outcome(CallStatus.REFUSAL, "refusal (other)")
        return oracle(cid, params)

    batch = FakeBatchClient(respond)
    result = PipelineRunner(settings, batch_client=batch, sleep=lambda _: None).run(
        ExtractionJob(d, settings) for d in docs
    )

    assert set(batch.batches[1]) == {f"{broken}__a2", f"{expired}__a1"}
    assert "<validation_errors>" in batch.batches[1][f"{broken}__a2"]["messages"][0]["content"]
    assert batch.batches[1][f"{expired}__a1"] == batch.batches[0][f"{expired}__a1"]  # unchanged
    status = {j.job_id: j.status for j in result.jobs}
    assert status[refused] is JobStatus.FAILED
    assert status[broken] in {JobStatus.ACCEPTED, JobStatus.NEEDS_REVIEW}


def test_oversized_documents_come_back_as_chunk_requests(settings, corpus):
    long_doc = first_of(corpus, "long_report")

    def respond(cid, params):
        _job, key = split_custom_id(cid)
        if key == "a1":
            return outcome(CallStatus.TRUNCATED)
        return tool_call(oracle_extraction(long_doc) if key.endswith("c1") else {"cited_works": []})

    batch = FakeBatchClient(respond)
    PipelineRunner(settings, batch_client=batch, sleep=lambda _: None).run(
        [ExtractionJob(as_document(long_doc), settings)]
    )

    round_two = list(batch.batches[1])
    assert round_two[0] == f"{long_doc.truth.doc_id}__a1c1"
    assert len(round_two) >= 2


def test_a_missing_result_is_treated_as_retryable(settings, corpus, docs):
    batch = FakeBatchClient(oracle_responder(corpus), drop={f"{docs[0].doc_id}__a1"})
    runner = PipelineRunner(settings, batch_client=batch, sleep=lambda _: None)

    runner.run([ExtractionJob(docs[0], settings)])

    assert f"{docs[0].doc_id}__a1" in batch.batches[1]


def test_runner_polls_until_the_batch_ends(settings, corpus, docs):
    naps = []
    batch = FakeBatchClient(oracle_responder(corpus), polls_before_end=3)

    PipelineRunner(settings, batch_client=batch, sleep=naps.append).run(
        [ExtractionJob(docs[0], settings)]
    )

    assert len(naps) == 3


def test_sync_mode_uses_the_sync_client(settings, corpus, docs):
    by_id = {d.truth.doc_id: d for d in corpus}
    sync = FakeSyncClient(lambda params: tool_call(oracle_extraction(by_id[docs[0].doc_id])))

    result = PipelineRunner(settings, sync_client=sync).run(
        [ExtractionJob(docs[0], settings)], force_sync=True
    )

    assert len(sync.calls) == 1
    assert result.rounds[0].mode == "sync"


def test_duplicate_job_ids_are_rejected(settings, docs):
    with pytest.raises(ValueError, match="unique"):
        PipelineRunner(settings).run([ExtractionJob(docs[0], settings)] * 2)


def test_endless_failures_stop_at_the_round_limit(settings, docs):
    batch = FakeBatchClient(lambda cid, p: outcome(CallStatus.API_ERROR, "overloaded"))
    lenient = settings.with_(max_transport_retries=100)

    result = PipelineRunner(lenient, batch_client=batch, sleep=lambda _: None).run(
        [ExtractionJob(docs[0], lenient)]
    )

    assert len(batch.batches) == MAX_ROUNDS
    assert "unfinished" in result.jobs[0].failure


def test_sla_planner_falls_back_to_sync_when_a_batch_no_longer_fits(settings, corpus, docs):
    """First batch takes 3 h against a 4 h SLA, so round 2 must go synchronous."""
    oracle = oracle_responder(corpus)

    def respond(cid, params):
        job_id, key = split_custom_id(cid)
        if key == "a1" and job_id == docs[0].doc_id:
            return tool_call(oracle(cid, params).tool_input | {"doi": "not-a-doi"})
        return oracle(cid, params)

    # run start, round-1 plan at t=0, then 3 h have passed for every later reading
    ticks = itertools.chain([0.0, 0.0], itertools.repeat(3 * 3600.0))
    batch = FakeBatchClient(respond, seconds_per_batch=3 * 3600)
    sync = FakeSyncClient(
        lambda params: tool_call(oracle(f"{docs[0].doc_id}__a2", params).tool_input)
    )
    runner = PipelineRunner(
        settings.with_(sla_hours=4),
        batch_client=batch,
        sync_client=sync,
        clock=lambda: next(ticks),
        sleep=lambda _: None,
    )

    result = runner.run([ExtractionJob(d, settings) for d in docs[:2]])

    assert [r.mode for r in result.rounds] == ["batch", "sync"]
    assert "exceeds the remaining" in result.rounds[1].reason
    assert len(sync.calls) == 1


# --- SLA arithmetic ----------------------------------------------------------------------------


def _round(mode, seconds, start=0.0):
    return RoundRecord(1, mode, 10, start, start + seconds)


def test_estimate_uses_the_slowest_observed_batch_with_a_safety_margin():
    policy = SlaPolicy(sla_seconds=10 * 3600, safety_factor=1.5)

    assert policy.estimate_batch_seconds([]) == DEFAULT_BATCH_ESTIMATE_SECONDS
    assert policy.estimate_batch_seconds([_round("batch", 600), _round("batch", 1200)]) == 1800
    assert policy.estimate_batch_seconds([_round("sync", 99999)]) == DEFAULT_BATCH_ESTIMATE_SECONDS


@pytest.mark.parametrize(
    ("elapsed", "expected"), [(0, "batch"), (3 * 3600 + 1, "sync"), (5 * 3600, "sync")]
)
def test_plan_by_remaining_time(elapsed, expected):
    policy = SlaPolicy(sla_seconds=4 * 3600)

    assert policy.plan(elapsed, []).mode == expected


def test_sla_report_totals_and_worst_case():
    rounds = [_round("batch", 1800, 0), RoundRecord(2, "sync", 2, 1800, 1900)]

    report = sla_report(rounds, sla_seconds=3600)

    assert report["total_seconds"] == 1900
    assert report["met"] is True
    assert report["sla_used_pct"] == 52.8
    assert report["worst_case_batch_only_seconds"] == 24 * 3600


# --- Review routing ----------------------------------------------------------------------------


def _result(**overrides):
    base = {
        "doc_id": "d",
        "status": "accepted",
        "failure": None,
        "issues": [],
        "mode": "single",
        "extraction": {"field_confidence": {"title": 0.99, "sample_size": 0.95}},
    }
    return base | overrides


def test_confident_valid_extraction_is_auto_accepted(settings):
    decision = route(_result(), settings)

    assert decision.route == "auto_accept"
    assert decision.reasons == []


def test_low_confidence_field_is_routed_with_that_field_named(settings):
    decision = route(_result(extraction={"field_confidence": {"title": 0.6}}), settings)

    assert decision.route == "human_review"
    assert decision.fields_to_check == ["title"]


def test_critical_fields_use_the_stricter_threshold(settings):
    # 0.8 passes the general threshold (0.75) but not the critical one (0.85)
    general = route(_result(extraction={"field_confidence": {"title": 0.8}}), settings)
    critical = route(_result(extraction={"field_confidence": {"sample_size": 0.8}}), settings)

    assert general.route == "auto_accept"
    assert critical.route == "human_review"


def test_unresolved_issues_and_failures_go_to_review(settings):
    issue = {"field": "authors", "kind": "missing_required", "message": "no authors"}

    with_issue = route(_result(status="needs_review", issues=[issue]), settings)
    failed = route(_result(status="failed", failure="refusal", extraction=None), settings)

    assert with_issue.route == failed.route == "human_review"
    assert "missing_required" in with_issue.reasons[0]
    assert "refusal" in failed.reasons[0]
